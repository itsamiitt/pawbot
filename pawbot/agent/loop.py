"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import weakref
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from loguru import logger

from pawbot.agent.context import ContextBuilder
from pawbot.agent.memory import MemoryStore
from pawbot.agent.subagent import SubagentManager
from pawbot.agent.tools.cron import CronTool
from pawbot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from pawbot.agent.tools.message import MessageTool
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.agent.tools.shell import ExecTool
from pawbot.agent.tools.spawn import SpawnTool
from pawbot.agent.tools.web import WebFetchTool, WebSearchTool
from pawbot.bus.events import InboundMessage, OutboundMessage
from pawbot.bus.queue import MessageBus
from pawbot.providers.base import LLMProvider
from pawbot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from pawbot.config.schema import ChannelsConfig, ExecToolConfig
    from pawbot.cron.service import CronService


# â”€â”€â”€ Phase 2: Complexity Score Thresholds â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# These constants are referenced in loop.py, context.py, and router.py
# DO NOT change these values â€” they are in MASTER_REFERENCE.md

SYSTEM_1_MAX = 0.3    # fast path
SYSTEM_1_5_MAX = 0.7  # ReAct path
SYSTEM_2_MIN = 0.7    # deliberative path

SYSTEM_PATHS = {
    "system_1": {
        "max_iterations": 20,   # raised from 5 — simple tasks still need tool calls
        "context_mode": "minimal",
        "model_hint": "cheap",
    },
    "system_1_5": {
        "max_iterations": 40,
        "context_mode": "standard",
        "model_hint": "balanced",
    },
    "system_2": {
        "max_iterations": 100,
        "context_mode": "full",
        "model_hint": "best",
        "use_tree_of_thoughts": True,
        "pre_task_reflection": True,
    },
}


def get_system_path(complexity_score: float) -> str:
    """Map a complexity score to a system path name."""
    if complexity_score <= SYSTEM_1_MAX:
        return "system_1"
    elif complexity_score <= SYSTEM_1_5_MAX:
        return "system_1_5"
    else:
        return "system_2"


# â”€â”€â”€ Phase 2.1: Dual System Router â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ComplexityClassifier:
    """
    Scores an incoming message from 0.0 (trivial) to 1.0 (maximum complexity).
    Score determines which execution path the agent takes:
    - System 1 (â‰¤0.3): Fast path â€” minimal context, cheap model, max 5 iterations
    - System 1.5 (0.3â€“0.7): ReAct path â€” standard context, balanced model
    - System 2 (>0.7): Deliberative â€” full context, best model, Tree of Thoughts
    """

    KEYWORD_SIGNALS = {
        "refactor", "deploy", "debug", "architect", "design",
        "implement", "migrate", "integrate", "analyze", "optimize"
    }

    URGENCY_SIGNALS = {"urgent", "asap", "broken", "down"}

    FAILURE_SIGNALS = {"error", "failed", "broke", "crash", "exception", "traceback"}

    def score(self, message: str) -> float:
        """Score an incoming message for complexity (0.0 to 1.0)."""
        score = 0.0
        words = message.lower().split()
        word_set = set(words)

        # Signal: long message
        if len(words) > 100:
            score += 0.2

        # Signal: contains complexity keywords
        if word_set & self.KEYWORD_SIGNALS:
            score += 0.2

        # Signal: references multiple files/components (look for .py, .js, / patterns)
        file_refs = re.findall(r'\b\w+\.\w{2,4}\b|/\w+', message)
        if len(file_refs) >= 2:
            score += 0.15

        # Signal: deep "why" or "how does" questions
        if any(message.lower().startswith(p) for p in ["why ", "how does "]):
            score += 0.1

        # Signal: references past failure
        if word_set & self.FAILURE_SIGNALS:
            score += 0.15

        # Signal: spans multiple topics (crude check: sentence count > 3)
        sentences = re.split(r'[.!?]', message)
        if len(sentences) > 3:
            score += 0.1

        # Signal: urgency
        if word_set & self.URGENCY_SIGNALS:
            score += 0.1

        return min(1.0, round(score, 2))


# â”€â”€â”€ Phase 2.4: Tree of Thoughts Planner â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ThoughtTreePlanner:
    """
    Generate 3 candidate approaches, evaluate each, return best.
    Falls back to second-best if first fails during execution.
    Only activates when complexity > 0.7 AND task_type in [coding_task, architecture].
    """

    def __init__(self, provider: LLMProvider, model: str, memory):
        self.provider = provider
        self.model = model
        self.memory = memory

    async def plan(self, task: str) -> dict:
        """
        Generate 3 candidate approaches, evaluate each, return best.
        Falls back to second-best if first fails during execution.
        """
        approaches = await self._generate_approaches(task)
        scored = [self._score_approach(a, task) for a in approaches]
        scored.sort(key=lambda x: x["score"], reverse=True)

        # Log rejected approaches to reasoning log
        logger.info(
            "ToT: selected '{}' (rejected: {})",
            scored[0]["name"],
            [a["name"] for a in scored[1:]],
        )

        return {
            "primary": scored[0],
            "fallback": scored[1] if len(scored) > 1 else None,
            "all": scored,
        }

    async def _generate_approaches(self, task: str) -> list[dict]:
        """Use LLM to generate 3 candidate approaches for the task."""
        prompt = f"""Given this task, propose 3 different technical approaches.
Task: {task}

For each approach respond in JSON array:
[
  {{
    "name": "Approach name",
    "core_idea": "One sentence description",
    "trade_offs": "Pros and cons",
    "estimated_complexity": "low|medium|high",
    "risk_level": "low|medium|high"
  }}
]
Respond with ONLY the JSON array."""

        try:
            response = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.7,
                max_tokens=1024,
            )
            content = response.content or ""
            # Try to extract JSON from response
            # Some models wrap in ```json ... ```
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return json.loads(content)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("ToT approach generation failed: {}", e)
            return [{
                "name": "default",
                "core_idea": task,
                "trade_offs": "",
                "estimated_complexity": "medium",
                "risk_level": "medium",
            }]

    def _score_approach(self, approach: dict, task: str) -> dict:
        """Score an approach based on heuristics and memory."""
        score = 0.5  # baseline

        # Penalty for high risk irreversible approaches
        if approach.get("risk_level") == "high":
            score -= 0.2

        # Bonus for low complexity when task is not architecture
        if approach.get("estimated_complexity") == "low":
            score += 0.1

        # Check consistency with past decisions in memory
        if self.memory is not None:
            try:
                past_decisions = self.memory.search(
                    query=f"{task} {approach['name']}",
                    limit=3,
                )
                relevant_decisions = [
                    d for d in past_decisions if d.get("type") == "decision"
                ]
                if relevant_decisions:
                    score += 0.15  # past precedent supports this approach
            except Exception as e:  # noqa: F841
                pass  # graceful degradation

        approach["score"] = round(score, 2)
        return approach


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Classifies message complexity (System 1/1.5/2 routing)
    3. Runs pre-task reflection for complex tasks
    4. Builds context with history, memory, skills
    5. Calls the LLM
    6. Executes tool calls with self-correction protocol
    7. Sends responses back
    8. Runs post-task learning asynchronously
    """

    _TOOL_RESULT_MAX_CHARS = 500

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 100,
        reasoning_effort: str | None = None,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
    ):
        from pawbot.config.schema import ExecToolConfig
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.reasoning_effort = reasoning_effort
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=reasoning_effort,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._consolidation_tasks: set[asyncio.Task] = set()  # Strong refs to in-flight tasks
        self._consolidation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._processing_lock = asyncio.Lock()

        # â”€â”€ Phase 2: Complexity classifier â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self._classifier = ComplexityClassifier()

        # â”€â”€ Phase 2.5: Self-correction state (reset per-message) â”€â”€â”€â”€â”€â”€â”€
        self.failure_count = 0
        self.failure_log: list[dict] = []
        self.current_step = 0

        # â”€â”€ Phase 2: Memory router for Phase 2.2/2.3 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self._memory_router = None  # Lazily initialized

        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            path_append=self.exec_config.path_append,
        ))
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from pawbot.agent.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception as e:  # noqa: F841
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>â€¦</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}â€¦")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict], list[dict]]:
        """Run the agent iteration loop.

        Returns:
            (final_content, tools_used, messages, execution_trace)
            execution_trace is a list of step dicts for post-task learning.
        """
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        execution_trace: list[dict] = []

        while iteration < self.max_iterations:
            iteration += 1
            self.current_step = iteration

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
            )

            if response.has_tool_calls:
                if on_progress:
                    clean = self._strip_think(response.content)
                    if clean:
                        await on_progress(clean)
                    await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )

                    # â”€â”€ Phase 2.5: Track execution trace and detect failures â”€â”€
                    trace_entry = {
                        "step": iteration,
                        "action": f"{tool_call.name}({args_str[:100]})",
                        "result": str(result)[:200] if result else "done",
                        "timestamp": int(time.time()),
                    }
                    execution_trace.append(trace_entry)

                    # Detect failure: error responses from tools
                    if result and isinstance(result, str) and (
                        result.startswith("Error:") or result.startswith("error:")
                        or "traceback" in result.lower()[:200]
                    ):
                        self._record_failure(result[:200], tool_call.name)

                    # â”€â”€ Phase 2.4: ToT fallback check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                    selected_approach = self._session_meta.get("selected_approach")
                    if selected_approach and self.failure_count >= 5:
                        fallback = selected_approach.get("fallback")
                        if fallback and not self._session_meta.get("using_fallback_approach"):
                            logger.info("ToT: switching to fallback approach '{}'", fallback.get("name"))
                            self._session_meta["using_fallback_approach"] = True
                            self._session_meta["active_approach"] = fallback
                            self.failure_count = 0

            else:
                clean = self._strip_think(response.content)
                # Don't persist error responses to session history â€” they can
                # poison the context and cause permanent 400 loops (#1303).
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    self._record_failure(final_content[:200], "llm_call")
                    break
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages, execution_trace

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if msg.content.strip().lower() == "/stop":
                await self._handle_stop(msg)
            else:
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"â¹ Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the global lock."""
        async with self._processing_lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception as e:  # noqa: F841
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
                ))

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # â”€â”€ Phase 2.5: Reset self-correction state for each message â”€â”€â”€â”€â”€
        self.failure_count = 0
        self.failure_log = []
        self.current_step = 0
        self._session_meta = {}  # Per-message metadata for Phase 2

        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(max_messages=self.memory_window)
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
            )
            final_content, _, all_msgs, _ = await self._run_agent_loop(messages)
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        # Slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
            self._consolidating.add(session.key)
            try:
                async with lock:
                    snapshot = session.messages[session.last_consolidated:]
                    if snapshot:
                        temp = Session(key=session.key)
                        temp.messages = list(snapshot)
                        if not await self._consolidate_memory(temp, archive_all=True):
                            return OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content="Memory archival failed, session not cleared. Please try again.",
                            )
            except Exception as e:  # noqa: F841
                logger.exception("/new archival failed for {}", session.key)
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Memory archival failed, session not cleared. Please try again.",
                )
            finally:
                self._consolidating.discard(session.key)

            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started.")
        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="ðŸˆ pawbot commands:\n/new â€” Start a new conversation\n/stop â€” Stop the current task\n/help â€” Show available commands")

        # â”€â”€ Phase 2.1: Classify complexity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        complexity_score = self._classifier.score(msg.content)
        system_path = get_system_path(complexity_score)
        path_config = SYSTEM_PATHS[system_path]

        # Store in session metadata so context.py can read it
        self._session_meta["complexity_score"] = complexity_score
        self._session_meta["system_path"] = system_path
        self._session_meta["context_mode"] = path_config["context_mode"]
        session.metadata["complexity_score"] = complexity_score
        session.metadata["system_path"] = system_path
        session.metadata["context_mode"] = path_config["context_mode"]

        logger.info("Complexity: {:.2f} â†’ {}", complexity_score, system_path)

        # â”€â”€ Phase 2.2: Pre-task reflection (System 2 only) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if path_config.get("pre_task_reflection") and system_path == "system_2":
            self._run_pre_task_reflection(msg.content, session)
        # Phase 13: load relevant runtime skills for System 2 tasks.
        skill_names: list[str] = []
        if system_path == "system_2":
            try:
                loaded_skills = self.context.skills.load(msg.content)
                skill_names = [s.name for s in loaded_skills]
                if skill_names:
                    session.metadata["active_skills"] = skill_names
                    self._session_meta["active_skills"] = skill_names
            except Exception as e:
                logger.warning("Skill loading failed, proceeding without skills: {}", e)

        # â”€â”€ Phase 2.4: Plan with Tree of Thoughts (System 2 only) â”€â”€â”€â”€â”€
        if path_config.get("use_tree_of_thoughts"):
            task_type = session.metadata.get("task_type", "general")
            if task_type in ["coding_task", "architecture"]:
                try:
                    memory = self._get_memory_router()
                    planner = ThoughtTreePlanner(self.provider, self.model, memory)
                    selected_approach = await planner.plan(msg.content)
                    self._session_meta["selected_approach"] = selected_approach
                    session.metadata["selected_approach_name"] = selected_approach["primary"]["name"]
                except Exception as e:
                    logger.warning("ToT planning failed, proceeding without: {}", e)

        # â”€â”€ Phase 2.1: Override max_iterations from path config â”€â”€â”€â”€â”€â”€â”€
        saved_max_iterations = self.max_iterations
        self.max_iterations = max(path_config.get("max_iterations", self.max_iterations), self.max_iterations)

        unconsolidated = len(session.messages) - session.last_consolidated
        if (unconsolidated >= self.memory_window and session.key not in self._consolidating):
            self._consolidating.add(session.key)
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())

            async def _consolidate_and_unlock():
                try:
                    async with lock:
                        await self._consolidate_memory(session)
                finally:
                    self._consolidating.discard(session.key)
                    _task = asyncio.current_task()
                    if _task is not None:
                        self._consolidation_tasks.discard(_task)

            _task = asyncio.create_task(_consolidate_and_unlock())
            self._consolidation_tasks.add(_task)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=self.memory_window)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            skill_names=skill_names,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        final_content, _, all_msgs, execution_trace = await self._run_agent_loop(
            initial_messages, on_progress=on_progress or _bus_progress,
        )

        # Restore max_iterations
        self.max_iterations = saved_max_iterations

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        # Determine success for post-task learning
        loop_success = self.failure_count == 0
        last_error = self.failure_log[-1]["error"] if self.failure_log else None

        self._save_turn(session, all_msgs, 1 + len(history))
        self.sessions.save(session)

        # â”€â”€ Phase 2.3: Post-task learning (async, never blocks response) â”€â”€
        self._run_post_task_learning(
            task=msg.content,
            success=loop_success,
            execution_trace=execution_trace,
            failure_reason=last_error,
            session_key=key,
        )

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=msg.metadata or {},
        )

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Phase 2 â€” Helper Methods
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _get_memory_router(self):
        """Lazily initialize and return a MemoryRouter instance."""
        if self._memory_router is None:
            try:
                from pawbot.agent.memory import MemoryRouter
                self._memory_router = MemoryRouter(
                    session_id="agent_loop",
                    config={},
                )
            except Exception as e:
                logger.warning("MemoryRouter init failed: {} â€” Phase 2 memory features degraded", e)
        return self._memory_router

    # â”€â”€ Phase 2.2: Pre-Task Reflection Check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _run_pre_task_reflection(self, task_description: str, session: Session) -> None:
        """
        Loads relevant past lessons and procedures into session context.
        Called before the main loop for System 2 tasks.
        """
        memory = self._get_memory_router()
        if memory is None:
            return

        try:
            # Query reflections
            reflections = memory.search(query=task_description, limit=5)
            relevant_reflections = [
                r for r in reflections
                if r.get("type") == "reflection"
                and r.get("confidence", 0) > 0.7
            ][:3]  # top 3 only

            # Query procedures
            procedures = memory.search(query=task_description, limit=5)
            relevant_procedures = [
                p for p in procedures
                if p.get("type") == "procedure"
                and p.get("success_count", 0) > 2
            ][:1]  # top 1 only

            # Store in session for context.py to inject
            if relevant_reflections:
                session.metadata["pre_task_reflections"] = relevant_reflections
                self._session_meta["pre_task_reflections"] = relevant_reflections
                logger.info("Pre-task check: {} reflections loaded", len(relevant_reflections))

            if relevant_procedures:
                session.metadata["pre_task_procedure"] = relevant_procedures[0]
                self._session_meta["pre_task_procedure"] = relevant_procedures[0]
                logger.info(
                    "Pre-task check: procedure '{}' loaded",
                    relevant_procedures[0].get("name", "unknown"),
                )
        except Exception as e:
            logger.warning("Pre-task reflection failed: {}", e)

    # â”€â”€ Phase 2.3: Post-Task Learning â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _run_post_task_learning(
        self,
        task: str,
        success: bool,
        execution_trace: list[dict],
        failure_reason: Optional[str] = None,
        session_key: str = "",
    ) -> None:
        """
        Fires in a background thread after agent responds.
        Never blocks the user-facing response.
        """
        thread = threading.Thread(
            target=self.__post_task_learning_sync,
            args=(task, success, execution_trace, failure_reason, session_key),
            daemon=True,
        )
        thread.start()

    def __post_task_learning_sync(
        self,
        task: str,
        success: bool,
        execution_trace: list[dict],
        failure_reason: Optional[str],
        session_key: str,
    ) -> None:
        """Synchronous post-task learning worker running in background thread."""
        memory = self._get_memory_router()
        if memory is None:
            return

        try:
            if success:
                system_path = self._session_meta.get("system_path", "system_1")
                if system_path == "system_2":
                    # Check if this task type has an existing procedure
                    existing_procs = memory.search(query=task, limit=3)
                    existing_proc = next(
                        (p for p in existing_procs
                         if p.get("type") == "procedure"
                         and p.get("similarity", 0) > 0.8),
                        None,
                    )

                    if existing_proc:
                        # Increment success count
                        memory.update(existing_proc["id"], {
                            **existing_proc,
                            "success_count": existing_proc.get("success_count", 0) + 1,
                            "last_used": int(time.time()),
                        })
                    else:
                        # Extract and save new procedure
                        steps = self._extract_steps_from_trace(execution_trace)
                        if steps:
                            memory.save("procedure", {
                                "name": task[:80],
                                "triggers": [task],
                                "steps": steps,
                                "preconditions": [],
                                "success_count": 1,
                                "last_used": int(time.time()),
                            })

                # Save episode summary
                memory.save("episode", {
                    "text": f"Completed: {task[:200]}. Steps: {len(execution_trace)}",
                    "goal": task[:200],
                    "success": True,
                    "timestamp": int(time.time()),
                    "session_id": session_key,
                })
            else:
                # Generate and save reflection
                reflection = self._generate_reflection_sync(execution_trace, failure_reason)
                if reflection:
                    memory.save("reflection", reflection)
                    logger.info("Reflection saved: {}", reflection.get("rule", "unknown"))

                # Save episode summary
                memory.save("episode", {
                    "text": f"Failed: {task[:200]}. Reason: {failure_reason}",
                    "goal": task[:200],
                    "success": False,
                    "timestamp": int(time.time()),
                    "session_id": session_key,
                })

        except Exception as e:
            logger.warning("Post-task learning failed: {}", e)

    def _extract_steps_from_trace(self, trace: list[dict]) -> list[str]:
        """Summarize execution trace into a list of step strings."""
        return [
            f"{i+1}. {step.get('action', 'unknown')} â†’ {step.get('result', 'done')[:80]}"
            for i, step in enumerate(trace)
            if step.get("action")
        ]

    def _generate_reflection_sync(
        self,
        trace: list[dict],
        failure_reason: Optional[str],
    ) -> Optional[dict]:
        """
        Generate a structured reflection from a failed task.
        Uses simple heuristics rather than an LLM call from background thread.
        """
        if not failure_reason:
            return None

        # Classify failure type
        failure_lower = failure_reason.lower()
        if "timeout" in failure_lower:
            failure_type = "timeout"
        elif "not found" in failure_lower or "missing" in failure_lower:
            failure_type = "missing_check"
        elif "permission" in failure_lower or "denied" in failure_lower:
            failure_type = "wrong_tool"
        elif "assume" in failure_lower:
            failure_type = "assumption_error"
        else:
            failure_type = "other"

        return {
            "failure_type": failure_type,
            "lesson": f"Task failed: {failure_reason[:200]}",
            "rule": f"Verify prerequisites before attempting: {failure_reason[:100]}",
            "applies_to": ["general"],
            "confidence": 0.7,
            "timestamp": int(time.time()),
        }

    # â”€â”€ Phase 2.5: Self-Correction Protocol â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _record_failure(self, error: str, action: str) -> None:
        """Record a failure event and apply escalating correction."""
        self.failure_count += 1
        self.failure_log.append({
            "step": self.current_step,
            "error": error[:200],
            "action": action,
            "timestamp": int(time.time()),
        })
        self._session_meta["execution_trace_errors"] = self.failure_log
        self._apply_correction_level()

    def _apply_correction_level(self) -> None:
        """Apply escalating correction strategy based on failure count."""
        fc = self.failure_count

        if fc <= 2:
            # Level 1: Retry with slight variation
            logger.info("Correction L1: retrying (attempt {})", fc)
            self._session_meta["retry_hint"] = (
                f"Attempt {fc}: try a slightly different approach"
            )

        elif fc <= 4:
            # Level 2: Replan the current subgoal
            logger.info("Correction L2: replanning subgoal")
            memory = self._get_memory_router()
            if memory is not None:
                try:
                    recent_errors = " ".join(
                        f["error"] for f in self.failure_log[-2:]
                    )
                    reflections = memory.search(query=recent_errors, limit=3)
                    correction_reflections = [
                        r for r in reflections if r.get("type") == "reflection"
                    ]
                    self._session_meta["correction_reflections"] = correction_reflections
                except Exception as e:  # noqa: F841
                    pass
            self._session_meta["replan_signal"] = True

        elif fc <= 6:
            # Level 3: Switch strategy / activate ToT fallback
            logger.info("Correction L3: changing strategy")
            selected = self._session_meta.get("selected_approach")
            if selected and selected.get("fallback"):
                self._session_meta["active_approach"] = selected["fallback"]
                self._session_meta["using_fallback_approach"] = True
                self.failure_count = 0  # reset for new approach
            else:
                self._session_meta["strategy_change_signal"] = True

        else:
            # Level 4: Escalate to user â€” pause and wait
            logger.warning(
                "Correction L4: escalating to user after {} failures", fc
            )
            self._escalate_to_user()

    def _escalate_to_user(self) -> None:
        """
        Sends structured escalation message to user.
        Marks the session as paused for escalation.
        """
        task = self._session_meta.get("current_task", "current task")
        errors_summary = "; ".join(
            set(f["error"][:80] for f in self.failure_log[-3:])
        )

        options = [
            "A) Retry with more information â€” describe any constraints I should know",
            "B) Simplify the task â€” tell me a smaller first step to try",
            "C) Cancel this task and try a different approach",
        ]

        message = (
            f"I've tried {self.failure_count} approaches on '{task}' and "
            f"encountered: {errors_summary}.\n\n"
            f"I need your guidance. Options:\n" +
            "\n".join(options)
        )

        logger.warning("Escalation to user: {}", message[:200])
        self._session_meta["paused_for_escalation"] = True
        self._session_meta["escalation_message"] = message

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages â€” they poison session context
            if role == "tool" and isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    continue
                if isinstance(content, list):
                    entry["content"] = [
                        {"type": "text", "text": "[image]"} if (
                            c.get("type") == "image_url"
                            and c.get("image_url", {}).get("url", "").startswith("data:image/")
                        ) else c for c in content
                    ]
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def _consolidate_memory(self, session, archive_all: bool = False) -> bool:
        """Delegate to MemoryStore.consolidate(). Returns True on success."""
        return await MemoryStore(self.workspace).consolidate(
            session, self.provider, self.model,
            archive_all=archive_all, memory_window=self.memory_window,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)
        return response.content if response else ""

