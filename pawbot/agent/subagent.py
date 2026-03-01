"""Subagent manager for background task execution.

Phase 12 additions:
  - SubagentRole        (capability definition dataclass)
  - SubagentBudget      (token/time limits dataclass)
  - SubagentResult      (structured return dataclass)
  - Subagent            (single subagent instance with isolated context)
  - SubagentPool        (manages concurrent subagent threads)
  - SubagentRunner      (main interface for spawning subagents)
  - SubagentMessageBus  (inter-subagent message passing)
  - BUILTIN_ROLES       (researcher, coder, planner, critic, deployer)

Original SubagentManager class is preserved at the bottom for backward
compatibility with existing asyncio-based callers.
"""

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger as loguru_logger

# Phase 12 uses stdlib logging (matches spec); loguru is used by existing code.
p12_logger = logging.getLogger("pawbot.subagent")


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 12 — Dataclasses
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class SubagentRole:
    """Defines the capabilities and constraints of a subagent type.

    Roles are registered at startup and reused across spawns.
    """

    name: str                           # "researcher" | "coder" | "planner" | "critic" etc.
    system_prompt: str                  # role-specific system instructions
    allowed_tools: list[str]            # MCP tool names this role can use
    model_preference: str = "sonnet"    # "haiku" | "sonnet" | "opus" | "local"
    max_iterations: int = 10
    description: str = ""


@dataclass
class SubagentBudget:
    """Token/time limits for a single subagent run."""

    max_tokens: int = 50_000            # token budget for this subagent's full run
    max_time_seconds: int = 300         # wall-clock timeout (5 minutes default)
    max_iterations: int = 20            # max AgentLoop turns
    tokens_used: int = 0
    started_at: float = field(default_factory=time.time)

    @property
    def time_remaining(self) -> float:
        return self.max_time_seconds - (time.time() - self.started_at)

    @property
    def timed_out(self) -> bool:
        return time.time() - self.started_at > self.max_time_seconds

    @property
    def over_budget(self) -> bool:
        return self.tokens_used >= self.max_tokens


@dataclass
class SubagentResult:
    """Structured return from a completed subagent."""

    subagent_id: str
    role: str
    task: str
    output: str                         # final response text
    success: bool
    tokens_used: int
    elapsed_seconds: float
    iterations: int
    discoveries: list[dict] = field(default_factory=list)
    error: str = ""


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 12 — Built-in Roles
# ══════════════════════════════════════════════════════════════════════════════


BUILTIN_ROLES: dict[str, SubagentRole] = {
    "researcher": SubagentRole(
        name="researcher",
        system_prompt=(
            "You are a focused research agent. Search for information, "
            "summarise findings concisely, and report what you find. "
            "Do not take actions — only gather and analyse information."
        ),
        allowed_tools=["web_search", "browser_open", "browser_extract", "code_search"],
        model_preference="sonnet",
        max_iterations=15,
        description="Web research and information gathering",
    ),
    "coder": SubagentRole(
        name="coder",
        system_prompt=(
            "You are a focused coding agent. Write, edit, and test code. "
            "Do not make architectural decisions — implement what you are told."
        ),
        allowed_tools=[
            "code_write", "code_edit", "code_run_checks",
            "code_search", "server_read_file", "server_write_file",
        ],
        model_preference="sonnet",
        max_iterations=20,
        description="Code implementation and testing",
    ),
    "planner": SubagentRole(
        name="planner",
        system_prompt=(
            "You are a planning agent. Break down complex tasks into "
            "numbered steps. Do not execute — only plan."
        ),
        allowed_tools=[],
        model_preference="opus",
        max_iterations=5,
        description="Task decomposition and planning",
    ),
    "critic": SubagentRole(
        name="critic",
        system_prompt=(
            "You are a critical review agent. Find problems, edge cases, "
            "and improvements. Be specific and constructive."
        ),
        allowed_tools=["code_search", "server_read_file"],
        model_preference="sonnet",
        max_iterations=8,
        description="Critical review and quality checking",
    ),
    "deployer": SubagentRole(
        name="deployer",
        system_prompt=(
            "You are a deployment agent. Execute deployment steps precisely. "
            "Verify each step before proceeding."
        ),
        allowed_tools=[
            "deploy_app", "deploy_status", "deploy_logs",
            "server_run", "service_control",
        ],
        model_preference="sonnet",
        max_iterations=15,
        description="Deployment execution",
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 12 — Subagent (single instance)
# ══════════════════════════════════════════════════════════════════════════════


class Subagent:
    """Single subagent instance. Runs in its own thread with isolated context.

    Reports findings to SQLiteFactStore.inbox_write() — never writes to
    main memory directly.
    """

    def __init__(
        self,
        subagent_id: str,
        role: SubagentRole,
        task: str,
        budget: SubagentBudget,
        model_router: Any = None,
        memory_router: Any = None,
        mcp_tools: dict | None = None,
    ):
        self.id = subagent_id
        self.role = role
        self.task = task
        self.budget = budget
        self.model_router = model_router
        self.memory = memory_router
        # Filter tools to only those allowed by the role
        all_tools = mcp_tools or {}
        self.tools: dict[str, Callable] = {
            name: fn for name, fn in all_tools.items()
            if name in role.allowed_tools
        }
        self.iterations = 0
        self._cancelled = False
        self._result: Optional[SubagentResult] = None

    def run(self) -> SubagentResult:
        """Execute the task. Returns SubagentResult when complete or budget exceeded.

        Runs synchronously — SubagentPool calls this in a thread.
        """
        start = time.time()
        discoveries: list[dict] = []
        output = ""
        success = False
        error = ""

        try:
            # Build isolated context for this subagent
            messages: list[dict[str, Any]] = [
                {"role": "user", "content": self.task},
            ]

            for i in range(self.role.max_iterations):
                if self._cancelled or self.budget.timed_out or self.budget.over_budget:
                    p12_logger.warning(
                        "Subagent %s stopped: cancelled=%s, timeout=%s, over_budget=%s",
                        self.id[:8], self._cancelled,
                        self.budget.timed_out, self.budget.over_budget,
                    )
                    break

                self.iterations += 1

                # Call model router
                if self.model_router is None:
                    output = f"No model_router configured for subagent {self.id[:8]}"
                    break

                response = self.model_router.call(
                    task_type="subagent",
                    messages=messages,
                    system=self.role.system_prompt,
                    tools=list(self.tools.keys()),
                )

                self.budget.tokens_used += response.get("usage", {}).get("total_tokens", 0)
                content = response.get("content", "")
                messages.append({"role": "assistant", "content": content})

                # Check if subagent is done (no more tool calls)
                if not response.get("tool_calls"):
                    output = content
                    success = True
                    break

                # Execute allowed tool calls
                for tool_call in response.get("tool_calls", []):
                    tool_name = tool_call["name"]
                    if tool_name not in self.tools:
                        messages.append({
                            "role": "tool",
                            "content": (
                                f"Error: tool '{tool_name}' not in allowed tools "
                                f"for role '{self.role.name}'"
                            ),
                        })
                        continue
                    try:
                        result = self.tools[tool_name](**tool_call.get("args", {}))
                        messages.append({"role": "tool", "content": json.dumps(result)})
                    except Exception as e:
                        messages.append({"role": "tool", "content": f"Tool error: {e}"})

            # Extract discoveries (facts worth proposing to main memory)
            discoveries = self._extract_discoveries(output)

            # Write discoveries to inbox — NEVER to main memory directly
            if self.memory and hasattr(self.memory, "sqlite"):
                for discovery in discoveries:
                    try:
                        self.memory.sqlite.inbox_write(
                            subagent_id=self.id,
                            content=discovery["content"],
                            confidence=discovery["confidence"],
                            proposed_type=discovery["type"],
                        )
                    except Exception as exc:
                        p12_logger.warning(
                            "Subagent %s: inbox_write failed: %s",
                            self.id[:8], exc,
                        )

        except Exception as e:
            error = str(e)
            output = f"Subagent error: {e}"
            p12_logger.warning("Subagent %s failed: %s", self.id[:8], e)

        self._result = SubagentResult(
            subagent_id=self.id,
            role=self.role.name,
            task=self.task,
            output=output,
            success=success,
            tokens_used=self.budget.tokens_used,
            elapsed_seconds=time.time() - start,
            iterations=self.iterations,
            discoveries=discoveries,
            error=error,
        )
        return self._result

    def cancel(self) -> None:
        """Signal this subagent to stop at next iteration check."""
        self._cancelled = True
        p12_logger.info("Subagent %s cancel requested", self.id[:8])

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def _extract_discoveries(self, output: str) -> list[dict]:
        """Parse subagent output for facts worth proposing to main memory.

        Returns list of {"content": dict, "confidence": float, "type": str}

        Heuristic: any sentence starting with "I found:", "Note:", "Important:",
        "Remember:" is a discovery candidate with confidence 0.7.
        Anything else is 0.5 confidence.
        """
        discoveries: list[dict] = []
        HIGH_CONF_PREFIXES = ("I found:", "Note:", "Important:", "Remember:")

        for line in output.split("\n"):
            line = line.strip()
            if not line or len(line) <= 20:
                continue
            confidence = 0.7 if any(line.startswith(p) for p in HIGH_CONF_PREFIXES) else 0.5
            discoveries.append({
                "content": {"text": line, "source": f"subagent:{self.id[:8]}"},
                "confidence": confidence,
                "type": "fact",
            })

        return discoveries[:5]  # cap at 5 discoveries per subagent run


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 12 — SubagentPool
# ══════════════════════════════════════════════════════════════════════════════


class SubagentPool:
    """Manages concurrent subagent execution.

    Enforces max_concurrent limit.
    """

    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent
        self._active: dict[str, threading.Thread] = {}
        self._subagents: dict[str, Subagent] = {}
        self._results: dict[str, SubagentResult] = {}
        self._lock = threading.Lock()

    def submit(self, subagent: Subagent, on_complete: Callable | None = None) -> str:
        """Submit a subagent for execution.

        Returns subagent_id immediately.
        Blocks if pool is at max_concurrent until a slot opens.
        """
        # Wait for a slot (with configurable back-off)
        deadline = time.time() + 60  # Don't block forever
        while len(self._active) >= self.max_concurrent and time.time() < deadline:
            time.sleep(0.1)

        def _run():
            result = subagent.run()
            with self._lock:
                self._results[subagent.id] = result
                self._active.pop(subagent.id, None)
                self._subagents.pop(subagent.id, None)
            if on_complete:
                try:
                    on_complete(result)
                except Exception as exc:
                    p12_logger.warning("on_complete callback error: %s", exc)
            p12_logger.info(
                "Subagent %s completed: success=%s, iterations=%d, tokens=%d",
                subagent.id[:8], result.success,
                result.iterations, result.tokens_used,
            )

        thread = threading.Thread(target=_run, daemon=True, name=f"subagent-{subagent.id[:8]}")
        with self._lock:
            self._active[subagent.id] = thread
            self._subagents[subagent.id] = subagent
        thread.start()
        return subagent.id

    def get_result(self, subagent_id: str, timeout: float = 0) -> Optional[SubagentResult]:
        """Get result for a completed subagent.

        If timeout > 0, blocks until result available or timeout.
        Returns None if not yet complete or timed out.
        """
        if timeout > 0:
            deadline = time.time() + timeout
            while subagent_id not in self._results and time.time() < deadline:
                time.sleep(0.1)
        return self._results.get(subagent_id)

    def cancel(self, subagent_id: str) -> bool:
        """Cancel a running subagent. Returns True if found."""
        with self._lock:
            subagent = self._subagents.get(subagent_id)
        if subagent:
            subagent.cancel()
            p12_logger.info("Pool: cancel requested for %s", subagent_id[:8])
            return True
        return False

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def active_ids(self) -> list[str]:
        return list(self._active.keys())

    @property
    def completed_count(self) -> int:
        return len(self._results)


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 12 — SubagentMessageBus
# ══════════════════════════════════════════════════════════════════════════════


class SubagentMessageBus:
    """Simple message-passing between subagents.

    Allows subagents to pass messages to each other via a shared queue.
    Orchestrator reads the bus to coordinate.
    This is intentionally simple — not a full actor system.
    """

    def __init__(self):
        self._bus: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def send(self, from_id: str, to_id: str, content: str) -> None:
        """Post a message to another subagent's inbox."""
        with self._lock:
            if to_id not in self._bus:
                self._bus[to_id] = []
            self._bus[to_id].append({
                "from": from_id,
                "content": content,
                "timestamp": int(time.time()),
            })

    def receive(self, subagent_id: str) -> list[dict]:
        """Pop all pending messages for this subagent."""
        with self._lock:
            msgs = self._bus.pop(subagent_id, [])
        return msgs

    @property
    def pending_count(self) -> int:
        """Total messages across all inboxes."""
        with self._lock:
            return sum(len(v) for v in self._bus.values())


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 12 — SubagentRunner (main interface)
# ══════════════════════════════════════════════════════════════════════════════


class SubagentRunner:
    """Main interface for spawning subagents.

    Called by AgentLoop when a task needs delegation.
    """

    def __init__(
        self,
        model_router: Any = None,
        memory_router: Any = None,
        mcp_tools: dict | None = None,
        config: dict | None = None,
    ):
        self.model_router = model_router
        self.memory = memory_router
        self.mcp_tools = mcp_tools or {}
        self.config = config or {}

        sub_cfg = self.config.get("subagents", {})
        max_concurrent = sub_cfg.get("max_concurrent", 3)
        self.pool = SubagentPool(max_concurrent=max_concurrent)
        self.roles: dict[str, SubagentRole] = {**BUILTIN_ROLES}
        self.message_bus = SubagentMessageBus()

    def register_role(self, role: SubagentRole) -> None:
        """Register a custom subagent role."""
        self.roles[role.name] = role

    def spawn(
        self,
        task: str,
        role: str = "researcher",
        budget: SubagentBudget | None = None,
        on_complete: Callable | None = None,
        watch_task: bool = True,
    ) -> str:
        """Spawn a subagent for the given task.

        Returns subagent_id immediately (non-blocking).
        """
        if role not in self.roles:
            raise ValueError(
                f"Unknown role: '{role}'. Available: {list(self.roles.keys())}"
            )

        subagent_id = str(uuid.uuid4())
        if budget is None:
            sub_cfg = self.config.get("subagents", {})
            budget = SubagentBudget(
                max_tokens=sub_cfg.get("default_budget_tokens", 50_000),
                max_time_seconds=sub_cfg.get("default_budget_seconds", 300),
            )

        subagent = Subagent(
            subagent_id=subagent_id,
            role=self.roles[role],
            task=task,
            budget=budget,
            model_router=self.model_router,
            memory_router=self.memory,
            mcp_tools=self.mcp_tools,
        )

        p12_logger.info(
            "Spawning subagent %s: role=%s, task=%.50s",
            subagent_id[:8], role, task,
        )

        self.pool.submit(subagent, on_complete=on_complete)
        return subagent_id

    def spawn_and_wait(
        self,
        task: str,
        role: str = "researcher",
        timeout: float = 300,
    ) -> SubagentResult:
        """Spawn subagent and block until complete or timeout."""
        subagent_id = self.spawn(task, role)
        result = self.pool.get_result(subagent_id, timeout=timeout)
        if result is None:
            return SubagentResult(
                subagent_id=subagent_id, role=role, task=task,
                output="", success=False, tokens_used=0,
                elapsed_seconds=timeout, iterations=0,
                error="Timed out waiting for subagent",
            )
        return result

    def review_inbox(self) -> list[str]:
        """Review subagent memory inbox.

        Called by AgentLoop after each subgoal completes.
        Returns list of accepted memory IDs.
        """
        if self.memory and hasattr(self.memory, "sqlite"):
            try:
                return self.memory.sqlite.inbox_review()
            except Exception as exc:
                p12_logger.warning("inbox_review failed: %s", exc)
        return []

    def status(self) -> dict:
        """Return current subagent system status."""
        return {
            "active_subagents": self.pool.active_count,
            "active_ids": [sid[:8] for sid in self.pool.active_ids],
            "completed_count": self.pool.completed_count,
            "available_roles": list(self.roles.keys()),
            "message_bus_pending": self.message_bus.pending_count,
        }

    def cancel(self, subagent_id: str) -> bool:
        """Cancel a running subagent by ID."""
        return self.pool.cancel(subagent_id)


# ══════════════════════════════════════════════════════════════════════════════
#  Original SubagentManager — preserved for backward compatibility
# ══════════════════════════════════════════════════════════════════════════════


class SubagentManager:
    """Manages background subagent execution (original async-based class).

    This is the original implementation used by existing AgentLoop callers.
    Phase 12 adds SubagentRunner alongside it; both can coexist.
    """

    def __init__(
        self,
        provider: Any,
        workspace: Path,
        bus: Any,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        brave_api_key: str | None = None,
        exec_config: Any = None,
        restrict_to_workspace: bool = False,
    ):
        from pawbot.config.schema import ExecToolConfig
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.restrict_to_workspace = restrict_to_workspace
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_tasks: dict[str, set[str]] = {}

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin)
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        loguru_logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
    ) -> None:
        """Execute the subagent task and announce the result."""
        from pawbot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
        from pawbot.agent.tools.registry import ToolRegistry
        from pawbot.agent.tools.shell import ExecTool
        from pawbot.agent.tools.web import WebFetchTool, WebSearchTool
        from pawbot.bus.events import InboundMessage

        loguru_logger.info("Subagent [{}] starting task: {}", task_id, label)

        try:
            tools = ToolRegistry()
            allowed_dir = self.workspace if self.restrict_to_workspace else None
            tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                path_append=self.exec_config.path_append,
            ))
            tools.register(WebSearchTool(api_key=self.brave_api_key))
            tools.register(WebFetchTool())

            system_prompt = self._build_subagent_prompt()
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            max_iterations = 15
            iteration = 0
            final_result: str | None = None

            while iteration < max_iterations:
                iteration += 1
                response = await self.provider.chat(
                    messages=messages,
                    tools=tools.get_definitions(),
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    reasoning_effort=self.reasoning_effort,
                )

                if response.has_tool_calls:
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in response.tool_calls
                    ]
                    messages.append({
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": tool_call_dicts,
                    })

                    for tool_call in response.tool_calls:
                        args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                        loguru_logger.debug(
                            "Subagent [{}] executing: {} with arguments: {}",
                            task_id, tool_call.name, args_str,
                        )
                        result = await tools.execute(tool_call.name, tool_call.arguments)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": result,
                        })
                else:
                    final_result = response.content
                    break

            if final_result is None:
                final_result = "Task completed but no final response was generated."

            loguru_logger.info("Subagent [{}] completed successfully", task_id)
            await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            loguru_logger.error("Subagent [{}] failed: {}", task_id, e)
            await self._announce_result(task_id, label, task, error_msg, origin, "error")

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        from pawbot.bus.events import InboundMessage

        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = f"""[Subagent '{label}' {status_text}]

Task: {task}

Result:
{result}

Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""

        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )

        await self.bus.publish_inbound(msg)
        loguru_logger.debug(
            "Subagent [{}] announced result to {}:{}",
            task_id, origin['channel'], origin['chat_id'],
        )

    def _build_subagent_prompt(self) -> str:
        """Build a focused system prompt for the subagent."""
        from pawbot.agent.context import ContextBuilder
        from pawbot.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        parts = [f"""# Subagent

{time_ctx}

You are a subagent spawned by the main agent to complete a specific task.
Stay focused on the assigned task. Your final response will be reported back to the main agent.

## Workspace
{self.workspace}"""]

        skills_summary = SkillsLoader(self.workspace).build_skills_summary()
        if skills_summary:
            parts.append(f"## Skills\n\nRead SKILL.md with read_file to use a skill.\n\n{skills_summary}")

        return "\n\n".join(parts)

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        tasks = [
            self._running_tasks[tid]
            for tid in self._session_tasks.get(session_key, [])
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        ]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)
