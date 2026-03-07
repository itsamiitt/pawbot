# PHASE 12 — ENHANCED SUBAGENT SYSTEM
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)
> **Implementation Day:** Day 13 (12.1 Core), Weeks 5–8 (12.2 Specialised Subagents)
> **Primary File:** `~/nanobot/agent/subagent.py` (enhance existing)
> **Test File:** `~/nanobot/tests/test_subagents.py`
> **Depends on:** Phase 1 (MemoryRouter — inbox_write), Phase 2 (AgentLoop — spawn point), Phase 4 (ModelRouter — subagent model routing), Phase 11 (TaskWatcher — completion heartbeat)

---

## BEFORE YOU START — READ THESE FILES

```bash
cat ~/nanobot/agent/subagent.py    # existing SubagentRunner — preserve interface
cat ~/nanobot/agent/loop.py        # where subagents are spawned from
cat ~/nanobot/agent/memory.py      # inbox_write / inbox_review interface from Phase 1
cat ~/nanobot/pyproject.toml
```

**Existing interfaces to preserve:** Whatever public methods `SubagentRunner` currently exposes. The `AgentLoop` calls `SubagentRunner.spawn()` or similar — keep that signature.

---

## WHAT YOU ARE BUILDING

A production-grade subagent system where the main orchestrator agent can spawn specialised sub-agents to work on parallel subtasks. Each subagent:
- Runs in its own thread with isolated context
- Has a defined role and capability set
- Reports findings to the protected `subagent_inbox` (Phase 1.5) — never writes directly to main memory
- Can use the full MCP tool suite
- Is killed if it exceeds its time or token budget

---

## CANONICAL NAMES — ALL NEW CLASSES IN THIS PHASE

| Class Name | File | Purpose |
|---|---|---|
| `SubagentRunner` | `agent/subagent.py` | Manages spawning and lifecycle of subagents |
| `Subagent` | `agent/subagent.py` | Single subagent instance with isolated context |
| `SubagentRole` | `agent/subagent.py` | Dataclass defining a subagent's capabilities |
| `SubagentResult` | `agent/subagent.py` | Structured return from a completed subagent |
| `SubagentBudget` | `agent/subagent.py` | Token/time limits per subagent |
| `SubagentPool` | `agent/subagent.py` | Manages concurrent subagent execution |

---

## FEATURE 12.1 — CORE SUBAGENT INFRASTRUCTURE

### `SubagentRole` dataclass

```python
from dataclasses import dataclass, field
from typing import Optional, Callable
import time, threading, uuid, logging, json

logger = logging.getLogger("nanobot")

@dataclass
class SubagentRole:
    """
    Defines the capabilities and constraints of a subagent type.
    Roles are registered at startup and reused across spawns.
    """
    name: str                          # "researcher" | "coder" | "planner" | "critic" etc.
    system_prompt: str                 # role-specific system instructions
    allowed_tools: list[str]           # MCP tool names this role can use
    model_preference: str = "sonnet"   # "haiku" | "sonnet" | "opus" | "local"
    max_iterations: int = 10
    description: str = ""
```

**Built-in roles to register at startup:**

```python
BUILTIN_ROLES = {
    "researcher": SubagentRole(
        name="researcher",
        system_prompt="You are a focused research agent. Search for information, "
                      "summarise findings concisely, and report what you find. "
                      "Do not take actions — only gather and analyse information.",
        allowed_tools=["web_search", "browser_open", "browser_extract", "code_search"],
        model_preference="sonnet",
        max_iterations=15,
        description="Web research and information gathering",
    ),
    "coder": SubagentRole(
        name="coder",
        system_prompt="You are a focused coding agent. Write, edit, and test code. "
                      "Do not make architectural decisions — implement what you are told.",
        allowed_tools=["code_write", "code_edit", "code_run_checks",
                       "code_search", "server_read_file", "server_write_file"],
        model_preference="sonnet",
        max_iterations=20,
        description="Code implementation and testing",
    ),
    "planner": SubagentRole(
        name="planner",
        system_prompt="You are a planning agent. Break down complex tasks into "
                      "numbered steps. Do not execute — only plan.",
        allowed_tools=[],
        model_preference="opus",
        max_iterations=5,
        description="Task decomposition and planning",
    ),
    "critic": SubagentRole(
        name="critic",
        system_prompt="You are a critical review agent. Find problems, edge cases, "
                      "and improvements. Be specific and constructive.",
        allowed_tools=["code_search", "server_read_file"],
        model_preference="sonnet",
        max_iterations=8,
        description="Critical review and quality checking",
    ),
    "deployer": SubagentRole(
        name="deployer",
        system_prompt="You are a deployment agent. Execute deployment steps precisely. "
                      "Verify each step before proceeding.",
        allowed_tools=["deploy_app", "deploy_status", "deploy_logs",
                       "server_run", "service_control"],
        model_preference="sonnet",
        max_iterations=15,
        description="Deployment execution",
    ),
}
```

### `SubagentBudget` dataclass

```python
@dataclass
class SubagentBudget:
    max_tokens: int = 50_000       # token budget for this subagent's full run
    max_time_seconds: int = 300    # wall-clock timeout (5 minutes default)
    max_iterations: int = 20       # max AgentLoop turns
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
```

### `SubagentResult` dataclass

```python
@dataclass
class SubagentResult:
    subagent_id: str
    role: str
    task: str
    output: str                    # final response text
    success: bool
    tokens_used: int
    elapsed_seconds: float
    iterations: int
    discoveries: list[dict] = field(default_factory=list)
    # discoveries: findings to propose to main memory via inbox_write
    error: str = ""
```

### `Subagent` class

```python
class Subagent:
    """
    Single subagent instance. Runs in its own thread with isolated context.
    Reports findings to SQLiteFactStore.inbox_write() — never writes to main memory directly.
    """

    def __init__(
        self,
        subagent_id: str,
        role: SubagentRole,
        task: str,
        budget: SubagentBudget,
        model_router,
        memory_router,
        mcp_tools: dict,     # {tool_name: callable} — only tools in role.allowed_tools
    ):
        self.id = subagent_id
        self.role = role
        self.task = task
        self.budget = budget
        self.model_router = model_router
        self.memory = memory_router
        self.tools = {
            name: fn for name, fn in mcp_tools.items()
            if name in role.allowed_tools
        }
        self.iterations = 0
        self._cancelled = False
        self._result: Optional[SubagentResult] = None

    def run(self) -> SubagentResult:
        """
        Execute the task. Returns SubagentResult when complete or budget exceeded.
        Runs synchronously — SubagentPool calls this in a thread.
        """
        start = time.time()
        discoveries = []
        output = ""
        success = False

        try:
            # Build isolated context for this subagent
            context = {
                "role": self.role.name,
                "system_prompt": self.role.system_prompt,
                "task": self.task,
                "subagent_id": self.id,
                "allowed_tools": self.role.allowed_tools,
            }

            # Run agent loop for this subagent
            messages = [{"role": "user", "content": self.task}]
            for i in range(self.role.max_iterations):
                if self._cancelled or self.budget.timed_out or self.budget.over_budget:
                    logger.warning(
                        f"Subagent {self.id[:8]} stopped: "
                        f"cancelled={self._cancelled}, "
                        f"timeout={self.budget.timed_out}, "
                        f"over_budget={self.budget.over_budget}"
                    )
                    break

                self.iterations += 1
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
                            "content": f"Error: tool '{tool_name}' not in allowed tools for role '{self.role.name}'"
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
            for discovery in discoveries:
                self.memory.sqlite.inbox_write(
                    subagent_id=self.id,
                    content=discovery["content"],
                    confidence=discovery["confidence"],
                    proposed_type=discovery["type"],
                )

        except Exception as e:
            output = f"Subagent error: {e}"
            logger.warning(f"Subagent {self.id[:8]} failed: {e}")

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
        )
        return self._result

    def cancel(self):
        """Signal this subagent to stop at next iteration check."""
        self._cancelled = True
        logger.info(f"Subagent {self.id[:8]} cancel requested")

    def _extract_discoveries(self, output: str) -> list[dict]:
        """
        Parse subagent output for facts worth proposing to main memory.
        Returns list of {"content": dict, "confidence": float, "type": str}

        Heuristic: any sentence starting with "I found:", "Note:", "Important:",
        "Remember:" is a discovery candidate with confidence 0.7.
        Anything else is 0.5 confidence.
        """
        discoveries = []
        HIGH_CONF_PREFIXES = ("I found:", "Note:", "Important:", "Remember:")
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            confidence = 0.7 if any(line.startswith(p) for p in HIGH_CONF_PREFIXES) else 0.5
            if len(line) > 20:  # skip trivially short lines
                discoveries.append({
                    "content": {"text": line, "source": f"subagent:{self.id[:8]}"},
                    "confidence": confidence,
                    "type": "fact",
                })
        return discoveries[:5]  # cap at 5 discoveries per subagent run
```

### `SubagentPool` class

```python
class SubagentPool:
    """
    Manages concurrent subagent execution.
    Enforces max_concurrent limit.
    """

    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent
        self._active: dict[str, threading.Thread] = {}
        self._results: dict[str, SubagentResult] = {}
        self._lock = threading.Lock()

    def submit(self, subagent: Subagent, on_complete: Callable = None) -> str:
        """
        Submit a subagent for execution.
        Returns subagent_id immediately.
        Blocks if pool is at max_concurrent until a slot opens.
        """
        while len(self._active) >= self.max_concurrent:
            time.sleep(0.5)

        def _run():
            result = subagent.run()
            with self._lock:
                self._results[subagent.id] = result
                self._active.pop(subagent.id, None)
            if on_complete:
                on_complete(result)
            logger.info(
                f"Subagent {subagent.id[:8]} completed: "
                f"success={result.success}, "
                f"iterations={result.iterations}, "
                f"tokens={result.tokens_used}"
            )

        thread = threading.Thread(target=_run, daemon=True)
        with self._lock:
            self._active[subagent.id] = thread
        thread.start()
        return subagent.id

    def get_result(self, subagent_id: str, timeout: float = 0) -> Optional[SubagentResult]:
        """
        Get result for a completed subagent.
        If timeout > 0, blocks until result available or timeout.
        Returns None if not yet complete or timed out.
        """
        if timeout > 0:
            deadline = time.time() + timeout
            while subagent_id not in self._results and time.time() < deadline:
                time.sleep(0.5)
        return self._results.get(subagent_id)

    def cancel(self, subagent_id: str):
        """Cancel a running subagent."""
        # Find the subagent object via active threads — requires subagent reference
        logger.info(f"Pool: cancel requested for {subagent_id[:8]}")

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def active_ids(self) -> list[str]:
        return list(self._active.keys())
```

### Enhanced `SubagentRunner` class

Enhance the existing `SubagentRunner` to use the new infrastructure:

```python
class SubagentRunner:
    """
    Main interface for spawning subagents.
    Called by AgentLoop when a task needs delegation.
    """

    def __init__(self, model_router, memory_router, mcp_tools: dict, config: dict):
        self.model_router = model_router
        self.memory = memory_router
        self.mcp_tools = mcp_tools
        self.config = config
        max_concurrent = config.get("subagents", {}).get("max_concurrent", 3)
        self.pool = SubagentPool(max_concurrent=max_concurrent)
        self.roles = {**BUILTIN_ROLES}

    def register_role(self, role: SubagentRole):
        """Register a custom subagent role."""
        self.roles[role.name] = role

    def spawn(
        self,
        task: str,
        role: str = "researcher",
        budget: SubagentBudget = None,
        on_complete: Callable = None,
        watch_task: bool = True,
    ) -> str:
        """
        Spawn a subagent for the given task.
        Returns subagent_id immediately (non-blocking).
        """
        if role not in self.roles:
            raise ValueError(f"Unknown role: '{role}'. Available: {list(self.roles.keys())}")

        subagent_id = str(uuid.uuid4())
        budget = budget or SubagentBudget()

        subagent = Subagent(
            subagent_id=subagent_id,
            role=self.roles[role],
            task=task,
            budget=budget,
            model_router=self.model_router,
            memory_router=self.memory,
            mcp_tools=self.mcp_tools,
        )

        logger.info(f"Spawning subagent {subagent_id[:8]}: role={role}, task={task[:50]}")

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
                error="Timed out waiting for subagent"
            )
        return result

    def review_inbox(self) -> list[str]:
        """
        Review subagent memory inbox.
        Called by AgentLoop after each subgoal completes.
        Returns list of accepted memory IDs.
        """
        return self.memory.sqlite.inbox_review()

    def status(self) -> dict:
        return {
            "active_subagents": self.pool.active_count,
            "active_ids": [id[:8] for id in self.pool.active_ids],
            "available_roles": list(self.roles.keys()),
        }
```

---

## FEATURE 12.2 — SUBAGENT COMMUNICATION (MULTI-AGENT COORDINATION)

For complex tasks requiring multiple coordinated subagents, implement a simple message-passing system:

```python
class SubagentMessageBus:
    """
    Allows subagents to pass messages to each other via a shared queue.
    Orchestrator reads the bus to coordinate.
    This is intentionally simple — not a full actor system.
    """

    def __init__(self):
        self._bus: dict[str, list[dict]] = {}   # {subagent_id: [messages]}
        self._lock = threading.Lock()

    def send(self, from_id: str, to_id: str, content: str):
        with self._lock:
            if to_id not in self._bus:
                self._bus[to_id] = []
            self._bus[to_id].append({
                "from": from_id, "content": content,
                "timestamp": int(time.time())
            })

    def receive(self, subagent_id: str) -> list[dict]:
        with self._lock:
            msgs = self._bus.pop(subagent_id, [])
        return msgs
```

---

## CONFIG KEYS TO ADD

```json
{
  "subagents": {
    "enabled": true,
    "max_concurrent": 3,
    "default_budget_tokens": 50000,
    "default_budget_seconds": 300,
    "inbox_review_after_subgoal": true,
    "auto_accept_confidence": 0.9,
    "auto_accept_after_hours": 24
  }
}
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_subagents.py`

```python
class TestSubagentRole:
    def test_builtin_roles_registered()
    def test_custom_role_registration()
    def test_tools_filtered_by_allowed_list()

class TestSubagentBudget:
    def test_timed_out_after_limit()
    def test_over_budget_after_tokens()
    def test_time_remaining_decreases()

class TestSubagent:
    def test_run_returns_result()
    def test_cancel_stops_at_next_iteration()
    def test_discoveries_written_to_inbox()
    def test_disallowed_tool_blocked()
    def test_budget_exceeded_stops_run()

class TestSubagentPool:
    def test_submit_runs_in_background()
    def test_get_result_blocks_until_done()
    def test_max_concurrent_respected()
    def test_active_count_tracks_running()

class TestSubagentRunner:
    def test_spawn_returns_id_immediately()
    def test_spawn_and_wait_blocks()
    def test_unknown_role_raises()
    def test_review_inbox_called_after_subgoal()
    def test_status_reports_active()

class TestSubagentMessageBus:
    def test_send_and_receive()
    def test_receive_empty_returns_empty_list()
    def test_thread_safe_concurrent_access()
```

---

## CROSS-REFERENCES

- **Phase 1** (SQLiteFactStore.inbox_write): `Subagent._extract_discoveries()` calls `memory.sqlite.inbox_write()`. SQLiteFactStore must expose `inbox_write()` method (implemented in Phase 1.5)
- **Phase 1** (inbox_review): `SubagentRunner.review_inbox()` calls `memory.sqlite.inbox_review()` — must call this after every subgoal
- **Phase 2** (AgentLoop): AgentLoop calls `subagent_runner.spawn(task, role)` for System 2 tasks — the `spawn()` signature must be stable
- **Phase 4** (ModelRouter): `Subagent.run()` calls `model_router.call(task_type="subagent", ...)` — ModelRouter must handle `"subagent"` as a task type
- **Phase 11** (TaskWatcher): `SubagentRunner.spawn()` should call `task_watcher.watch(subagent_id, task)` when `watch_task=True`
- **Phase 14** (Security): Every tool call inside `Subagent.run()` must pass through `ActionGate.check()` — add as wrapper around `self.tools[tool_name]` call

All canonical names are in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
