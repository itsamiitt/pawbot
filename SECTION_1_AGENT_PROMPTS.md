# 🏗️ SECTION 1 — Core Runtime & Gateway
## Complete Agent Fix Document
### 5 Self-Contained Agent Prompts · Full Code · Tests · Acceptance Gates

**Repo:** `itsamiitt/pawbot` · **Date:** March 2026 · **Version:** 1.0

---

## ⚠️ CRITICAL RULE — READ BEFORE ANY PHASE

> Every class, enum, constant, dataclass, path, config key, and event name used in this
> document is defined **ONCE** in `pawbot/contracts.py`.
>
> Before writing any code in **any** phase, read `pawbot/contracts.py` in full.
> Never redefine anything that already exists there.
>
> ```python
> from pawbot.contracts import *   # gives you everything
> ```

---

## Phase Execution Order

| Phase | Title | Can Start When | Blocks |
|-------|-------|---------------|--------|
| **1** | Session Lane Queue | Immediately — no deps | Phase 3, Phase 4 |
| **2** | Context Compactor | Immediately — no deps | Phase 3 (used in loop) |
| **3** | WebSocket Gateway | Phase 1 complete | Phase 4 |
| **4** | Multi-Agent Router | Phase 1 + Phase 3 complete | Phase 5 |
| **5** | Startup Validator & Health API | All phases complete | Section 1 gate |

Phases 1 and 2 are **fully independent** — run them simultaneously.

---

---

# PHASE 1 OF 5 — Session Lane Queue
### *Prevent race conditions — serialize message processing per session*

---

## Agent Prompt

You are implementing the **Session Lane Queue** for the Pawbot AI agent framework.

This is the single most critical fix in Section 1. Without it, two messages arriving
simultaneously on the same session will corrupt shared state, duplicate tool calls,
and produce conflicting outputs. Your job is to create one new file, wire it into
the existing session flow, and verify it passes all 6 tests.

**Rules:**
- Do not modify any existing files except `pawbot/session/manager.py` (one line change)
- Do not invent new class names, enums, or constants — read `pawbot/contracts.py` first
- Read `pawbot/contracts.py` fully before writing a single line of code

---

## Why This Phase Exists

Pawbot currently has no per-session message serialization. Every incoming message fires
an immediate `asyncio` task. In practice:

- Two Telegram messages arrive 50ms apart → both call the same tool simultaneously → output is duplicated or corrupted
- A long task is running → a second message interrupts it mid-loop → agent loop state is clobbered
- Memory writes happen from two concurrent loops → SQLite WAL conflicts → facts saved twice or not at all
- Session conversation history gets messages appended out of order → context sent to LLM is incoherent

**The fix:** one `asyncio.Queue` per `session_id`. One worker coroutine per queue.
Messages are processed **sequentially within a session**, while different sessions
still run **concurrently**. Idle queues self-clean after 30 minutes.

---

## What You Will Build

| Action | File |
|--------|------|
| **CREATE** | `pawbot/agent/lane_queue.py` — the `LaneQueue` class and module-level singleton |
| **EDIT** | `pawbot/session/manager.py` — replace direct handler calls with `lane_queue.enqueue()` |
| **CREATE** | `tests/test_lane_queue.py` — 6 tests covering all behaviour |

---

## Dependencies

| Dependency | Type | Import | If Missing |
|-----------|------|--------|-----------|
| `pawbot/contracts.py` | Internal | `from pawbot.contracts import get_logger, now` | **STOP** — do not proceed |
| `asyncio` | stdlib | `import asyncio` | Always available — Python 3.11+ |
| `collections.defaultdict` | stdlib | `from collections import defaultdict` | Always available |
| `pawbot/session/manager.py` | Internal edit | `from pawbot.agent.lane_queue import lane_queue` | File must already exist |

---

## Reference Map — from contracts.py

| Item | Location |
|------|----------|
| `get_logger(name)` | `contracts.py` Section 0.11 — returns `logging.Logger` |
| `now()` | `contracts.py` Section 0.11 — returns current Unix timestamp as `int` |
| `new_id()` | `contracts.py` Section 0.11 — returns UUID4 string |
| `SessionManager` | `pawbot/session/manager.py` — edit `on_message()` to use `lane_queue` |
| `InboundMessage` | `contracts.py` Section 0.4 — dataclass passed to handler |
| `config()` | `contracts.py` — used to read `agents.defaults.lane_queue_maxsize` |

---

## File 1 of 3 — CREATE `pawbot/agent/lane_queue.py`

```python
"""
pawbot/agent/lane_queue.py

Session Lane Queue — serializes message processing per session.
One asyncio.Queue per session_id. One worker coroutine per queue.
Messages within the same session are processed one at a time.
Different sessions run fully concurrently.

Usage:
    from pawbot.agent.lane_queue import lane_queue
    await lane_queue.enqueue(session_id, handler_coroutine, arg1, arg2)

IMPORTS FROM: pawbot/contracts.py — get_logger(), now()
SINGLETON: lane_queue — import this everywhere, never instantiate LaneQueue directly
"""

import asyncio
from pawbot.contracts import get_logger, now

logger = get_logger(__name__)


class LaneQueue:
    """
    Guarantees sequential message processing per session lane.

    Architecture:
    - Each session_id gets exactly one asyncio.Queue and one worker Task.
    - enqueue() appends (handler, args) to the session queue.
    - The worker Task pulls items one at a time and awaits them.
    - After IDLE_CLEANUP_SECS of no activity, the worker exits and
      the queue is garbage-collected.
    - maxsize=50 prevents memory exhaustion from burst messages.
    """

    IDLE_CLEANUP_SECS: int = 1800  # 30 minutes
    MAX_QUEUE_SIZE: int = 50       # max pending messages per session

    def __init__(self) -> None:
        # session_id -> asyncio.Queue of (handler, args) tuples
        self._queues: dict[str, asyncio.Queue] = {}
        # session_id -> running asyncio.Task (the worker)
        self._workers: dict[str, asyncio.Task] = {}
        # session_id -> Unix timestamp of last enqueue
        self._last_active: dict[str, int] = {}

    async def enqueue(self, session_id: str, handler, *args) -> None:
        """
        Add a message handler to the session queue.

        Args:
            session_id: Unique session identifier (e.g. "telegram_12345")
            handler:    Async coroutine function to call
            *args:      Arguments to pass to handler

        Raises:
            asyncio.QueueFull: if session queue exceeds MAX_QUEUE_SIZE
        """
        # Create queue and worker for new sessions
        if session_id not in self._queues:
            self._queues[session_id] = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)
            self._workers[session_id] = asyncio.create_task(
                self._worker(session_id),
                name=f"lane-worker-{session_id}"
            )
            logger.debug(f"LaneQueue: created lane for session {session_id}")

        await self._queues[session_id].put((handler, args))
        self._last_active[session_id] = now()

        depth = self._queues[session_id].qsize()
        logger.debug(f"LaneQueue: enqueued for {session_id} (depth={depth})")

        if depth >= self.MAX_QUEUE_SIZE * 0.8:
            logger.warning(f"LaneQueue: session {session_id} queue near capacity ({depth})")

    async def _worker(self, session_id: str) -> None:
        """
        Worker coroutine — runs for the lifetime of a session.
        Processes items one at a time. Exits after IDLE_CLEANUP_SECS of inactivity.
        """
        queue = self._queues[session_id]
        logger.debug(f"LaneQueue: worker started for {session_id}")

        while True:
            try:
                # Wait for next item or timeout (idle cleanup)
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=self.IDLE_CLEANUP_SECS
                )
                handler, args = item
                try:
                    await handler(*args)
                except Exception as exc:
                    # Log error but keep the worker alive — never let one bad
                    # message kill the entire session lane
                    logger.error(
                        f"LaneQueue: handler error in session {session_id}: {exc}",
                        exc_info=True
                    )
                finally:
                    queue.task_done()

            except asyncio.TimeoutError:
                # No messages for IDLE_CLEANUP_SECS — this lane is idle, clean up
                logger.debug(f"LaneQueue: session {session_id} idle, cleaning up lane")
                self._cleanup_lane(session_id)
                return

            except asyncio.CancelledError:
                # Graceful shutdown
                logger.info(f"LaneQueue: worker for {session_id} cancelled")
                return

    def _cleanup_lane(self, session_id: str) -> None:
        """Remove a session lane and its worker from internal dicts."""
        self._queues.pop(session_id, None)
        self._workers.pop(session_id, None)
        self._last_active.pop(session_id, None)

    async def shutdown(self) -> None:
        """Gracefully cancel all worker tasks. Call on application shutdown."""
        for session_id, task in list(self._workers.items()):
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._queues.clear()
        self._workers.clear()
        self._last_active.clear()
        logger.info("LaneQueue: all workers shut down")

    def stats(self) -> dict[str, int]:
        """Return current queue depths per session. Used by /health endpoint."""
        return {sid: q.qsize() for sid, q in self._queues.items()}

    def active_sessions(self) -> int:
        """Return number of active session lanes."""
        return len(self._queues)


# ── Singleton ──────────────────────────────────────────────────────────────────
# Import this everywhere. Never instantiate LaneQueue directly.
lane_queue = LaneQueue()
```

---

## File 2 of 3 — EDIT `pawbot/session/manager.py`

Find the method that processes inbound messages. It likely looks like:
`await handle_message(msg)` or `await agent_loop.run(...)`.

Replace that direct call with the `lane_queue.enqueue()` pattern shown below.
**This is a ONE-LINE change at the call site — do not restructure the file.**

```python
# ADD THIS IMPORT at top of pawbot/session/manager.py:
from pawbot.agent.lane_queue import lane_queue

# BEFORE (find this pattern in session/manager.py):
# async def process_message(self, msg: InboundMessage) -> None:
#     await self._handle(msg)   # ← direct call, NO queuing

# AFTER (replace with lane_queue):
async def process_message(self, msg: InboundMessage) -> None:
    """
    Route incoming message through the session lane queue.
    Ensures messages on the same session are processed one at a time.
    Different sessions are fully concurrent.
    msg.session_id is defined in contracts.py InboundMessage dataclass.
    """
    await lane_queue.enqueue(   # ← LaneQueue from pawbot/agent/lane_queue.py
        msg.session_id,         # ← field from InboundMessage (contracts.py)
        self._handle,           # ← your existing handler method
        msg                     # ← passed through to handler unchanged
    )
```

---

## File 3 of 3 — CREATE `tests/test_lane_queue.py`

```python
"""
tests/test_lane_queue.py

Tests for the Session Lane Queue.
Run: pytest tests/test_lane_queue.py -v
"""

import asyncio
import pytest
from pawbot.agent.lane_queue import LaneQueue  # import class (not singleton) for test isolation


@pytest.mark.asyncio
async def test_sequential_execution_within_session():
    """Messages on the same session must execute in order, never in parallel."""
    lq = LaneQueue()
    results = []

    async def slow_handler(val):
        await asyncio.sleep(0.05)
        results.append(val)

    await lq.enqueue("sess_A", slow_handler, 1)
    await lq.enqueue("sess_A", slow_handler, 2)
    await lq.enqueue("sess_A", slow_handler, 3)
    await asyncio.sleep(0.4)  # wait for all to complete

    assert results == [1, 2, 3], f"Expected [1,2,3] got {results}"
    await lq.shutdown()


@pytest.mark.asyncio
async def test_concurrent_execution_across_sessions():
    """Different sessions must run concurrently — not blocked by each other."""
    lq = LaneQueue()
    start_times: dict[str, float] = {}
    end_times: dict[str, float] = {}

    async def timed_handler(session_id: str):
        import time
        start_times[session_id] = time.monotonic()
        await asyncio.sleep(0.1)
        end_times[session_id] = time.monotonic()

    await lq.enqueue("sess_A", timed_handler, "sess_A")
    await lq.enqueue("sess_B", timed_handler, "sess_B")
    await asyncio.sleep(0.3)

    # Both sessions should overlap — sess_B should start before sess_A finishes
    assert "sess_A" in start_times and "sess_B" in start_times
    overlap = start_times["sess_B"] < end_times["sess_A"]
    assert overlap, "Sessions should run concurrently"
    await lq.shutdown()


@pytest.mark.asyncio
async def test_handler_error_does_not_kill_lane():
    """A crashing handler must not stop the worker — next message must still process."""
    lq = LaneQueue()
    results = []

    async def bad_handler():
        raise ValueError("intentional test error")

    async def good_handler(val):
        results.append(val)

    await lq.enqueue("sess_A", bad_handler)
    await lq.enqueue("sess_A", good_handler, "survived")
    await asyncio.sleep(0.3)

    assert results == ["survived"], f"Lane should survive handler error, got {results}"
    await lq.shutdown()


@pytest.mark.asyncio
async def test_stats_returns_queue_depths():
    """stats() must return {session_id: queue_depth} dict for /health endpoint."""
    lq = LaneQueue()

    async def slow(_):
        await asyncio.sleep(0.2)

    # Enqueue 3 items — first starts immediately, 2 wait
    await lq.enqueue("sess_X", slow, None)
    await lq.enqueue("sess_X", slow, None)
    await lq.enqueue("sess_X", slow, None)

    stats = lq.stats()
    assert "sess_X" in stats
    # Depth will be 2 (first item dequeued by worker, 2 waiting)
    assert stats["sess_X"] >= 1
    await lq.shutdown()


@pytest.mark.asyncio
async def test_idle_cleanup(monkeypatch):
    """After IDLE_CLEANUP_SECS of no messages, the lane must self-clean."""
    lq = LaneQueue()
    lq.IDLE_CLEANUP_SECS = 0.1  # override to 100ms for test speed

    async def noop():
        pass

    await lq.enqueue("sess_Y", noop)
    await asyncio.sleep(0.5)  # wait for idle cleanup

    assert "sess_Y" not in lq._queues, "Idle lane should have been cleaned up"
    assert "sess_Y" not in lq._workers


@pytest.mark.asyncio
async def test_shutdown_cancels_all_workers():
    """shutdown() must cancel all worker tasks without hanging."""
    lq = LaneQueue()

    async def long_task():
        await asyncio.sleep(60)  # will be cancelled

    await lq.enqueue("sess_A", long_task)
    await lq.enqueue("sess_B", long_task)
    assert lq.active_sessions() == 2

    await asyncio.wait_for(lq.shutdown(), timeout=3.0)
    assert lq.active_sessions() == 0, "All sessions should be cleared after shutdown"
```

---

## Test Matrix

| # | Test | Input | Expected | Pass Condition |
|---|------|-------|----------|----------------|
| T1 | Sequential execution | 3 msgs on same session | `results == [1, 2, 3]` | Order preserved, no interleaving |
| T2 | Concurrent sessions | 2 sessions simultaneously | Overlap in timing | sess_B starts before sess_A ends |
| T3 | Error resilience | Bad handler raises ValueError | Next msg still processes | Lane survives errors |
| T4 | stats() output | 3 msgs enqueued | `stats()[session] >= 1` | Correct depth returned |
| T5 | Idle cleanup | No msgs for 100ms | Lane removed from dicts | No memory leak |
| T6 | Shutdown | 2 active sessions | All cleared in < 3 sec | No hanging tasks |

---

## ⛔ Acceptance Gate — Phase 1
**ALL criteria must pass before Phase 3 can start.**

```bash
pytest tests/test_lane_queue.py -v
```

- [ ] All 6 tests pass with no warnings
- [ ] Two concurrent messages on same session → sequential output order guaranteed
- [ ] `from pawbot.agent.lane_queue import lane_queue` works without error
- [ ] `lane_queue.stats()` returns a dict (required by `/health` endpoint in Phase 5)
- [ ] `session/manager.py` uses `lane_queue.enqueue()` — verified by grep:

```bash
grep -r 'lane_queue.enqueue' pawbot/
# Must return at least one result
```

---

---

# PHASE 2 OF 5 — Context Compactor
### *Prevent hard token overflow crashes — summarize old turns automatically*

---

## Agent Prompt

You are implementing the **Context Compactor** for the Pawbot AI agent framework.

When conversation history + injected memory fills the model context window, Pawbot
currently crashes with a token limit error and the session dies. Your job is to create
`pawbot/agent/compactor.py` and wire it into the agent loop (one function call before
every LLM call).

**Rules:**
- This phase is **INDEPENDENT of Phase 1** — run both in parallel
- Read `pawbot/contracts.py` fully before writing any code

---

## Why This Phase Exists

Pawbot has no compaction mechanism. The first time a user has a 150-message conversation,
or gives Pawbot a long coding task, it crashes. Recovery requires the user to start a
fresh session and re-explain everything.

- Long coding tasks: each tool result is injected into context → hits limit mid-task → crash
- Multi-day sessions: accumulated history grows forever → eventual guaranteed failure
- RAG memory injection: memory chunks injected on top of history → faster overflow

**The fix:** before every LLM call, estimate the token count. If above the threshold
(82% of model limit), summarise the oldest 50% of conversation turns using a cheap
local Ollama model. Replace those turns with a single `[COMPACTED]` message.

---

## What You Will Build

| Action | File |
|--------|------|
| **CREATE** | `pawbot/agent/compactor.py` — `ContextCompactor` class and singleton |
| **EDIT** | `pawbot/agent/loop.py` — call `compactor.compact_if_needed()` before every model call |
| **CREATE** | `tests/test_compactor.py` — 5 tests |

---

## Dependencies

| Dependency | Type | Import | If Missing |
|-----------|------|--------|-----------|
| `pawbot/contracts.py` | Internal | `from pawbot.contracts import *` | **STOP** — read fully first |
| `pawbot/providers/router.py` | Internal | `from pawbot.providers.router import model_router` | Must already exist |
| `LLMRequest` | contracts.py Section 0.4 | dataclass for summarise call | Do not redefine |
| `ProviderName.OLLAMA` | contracts.py Section 0.3 | enum value | Do not redefine |
| `TaskType.MEMORY_TASK` | contracts.py Section 0.3 | enum value | Do not redefine |
| `config()` | contracts.py Section 0.11 | `config().get('providers.ollama.default_model')` | Exact key |

---

## Reference Map — from contracts.py

| Item | Details |
|------|---------|
| `LLMRequest` | Section 0.4 — fields: `messages, model, provider, max_tokens, temperature, task_type` |
| `LLMResponse` | Section 0.4 — `.content` field holds the text response |
| `ProviderName.OLLAMA` | Section 0.3 — value = `'ollama'` |
| `TaskType.MEMORY_TASK` | Section 0.3 — used for cheap/local tasks |
| `config()` | Section 0.11 — `config().get('providers.ollama.default_model', 'llama3.1:8b')` |
| `model_router.complete()` | `pawbot/providers/router.py` — takes `LLMRequest`, returns `LLMResponse` |
| Call site in `loop.py` | Before: `messages = context_builder.to_messages()` → After: `messages = await compactor.compact_if_needed(messages, model)` |

---

## File 1 of 3 — CREATE `pawbot/agent/compactor.py`

```python
"""
pawbot/agent/compactor.py

Context Compactor — prevents token overflow crashes.
Called once before every LLM call in the agent loop.

Strategy:
    1. Estimate total token count of the message list
    2. If below COMPACTION_THRESHOLD × model_limit: do nothing (fast path)
    3. If above threshold: summarise the oldest turns using a cheap local model
    4. Replace old turns with a single [COMPACTED] system message
    5. Always keep the last KEEP_LAST_N_TURNS turns intact
    6. Never compact the system prompt or any [COMPACTED] entries already present

IMPORTS FROM: pawbot/contracts.py — LLMRequest, LLMResponse, ProviderName,
              TaskType, config(), get_logger()
CALLED BY:    pawbot/agent/loop.py — before model_router.complete()
SINGLETON:    compactor — import this everywhere
"""

from pawbot.contracts import (
    LLMRequest, ProviderName, TaskType, config, get_logger
)
from pawbot.providers.router import model_router

logger = get_logger(__name__)

# ── Context limits per model ────────────────────────────────────────────────────
# Extend this dict as new models are added.
# Keys must match the exact model string used in LLMRequest.model.
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # Anthropic
    "claude-opus-4-6":           200000,
    "claude-sonnet-4-6":         180000,
    "claude-haiku-4-5-20251001": 200000,
    # OpenAI
    "gpt-4o":                    128000,
    "gpt-4o-mini":               128000,
    # Ollama / local
    "llama3.1:8b":                 8192,
    "llama3.1:70b":               65536,
    "mistral:7b":                  8192,
    "deepseek-coder:6.7b":        16384,
    "codellama:13b":              16384,
}

COMPACTION_RESERVE: int     = 6144  # tokens always kept free for model reply
COMPACTION_THRESHOLD: float = 0.82  # trigger compaction at 82% of limit
KEEP_LAST_N_TURNS: int      = 6     # always preserve the most recent N turns
MIN_TURNS_TO_COMPACT: int   = 4     # do not compact if fewer than 4 turns eligible


class ContextCompactor:
    """
    Prevents hard token overflow by summarising old conversation turns.
    Thread-safe: stateless — all state comes from the messages list argument.
    """

    async def compact_if_needed(
        self,
        messages: list[dict],
        model: str,
    ) -> list[dict]:
        """
        Main entry point. Call this before every LLM API call.

        Args:
            messages: The full message list about to be sent to the model.
                      Each dict has {role: str, content: str}.
            model:    The model string (e.g. "claude-sonnet-4-6").
                      Used to look up the context limit.

        Returns:
            The (possibly compacted) message list.
            If no compaction needed, returns the same list unchanged.
        """
        limit = MODEL_CONTEXT_LIMITS.get(model, 8192) - COMPACTION_RESERVE
        total = self._estimate_tokens(messages)

        if total < limit * COMPACTION_THRESHOLD:
            return messages  # fast path — no action needed

        # Separate system prompt from conversation turns
        system_msgs = [m for m in messages if m.get("role") == "system"]
        convo_msgs  = [m for m in messages if m.get("role") != "system"]

        # Split into compactable (old) and keep (recent)
        to_compact = convo_msgs[:-KEEP_LAST_N_TURNS]
        keep       = convo_msgs[-KEEP_LAST_N_TURNS:]

        if len(to_compact) < MIN_TURNS_TO_COMPACT:
            logger.warning(
                f"Compactor: context near limit but too few turns to compact "
                f"({len(to_compact)} turns, need {MIN_TURNS_TO_COMPACT}). "
                f"Consider reducing memory injection or increasing model."
            )
            return messages  # cannot compact further — caller must handle

        # Summarise old turns
        summary_text = await self._summarise(to_compact, model)

        compacted_msg = {
            "role": "system",
            "content": (
                f"[COMPACTED — {len(to_compact)} earlier turns] "
                f"Summary of earlier conversation: {summary_text}"
            )
        }

        result = system_msgs + [compacted_msg] + keep
        tokens_after = self._estimate_tokens(result)

        logger.info(
            f"Compactor: compacted {len(to_compact)} turns → 1 summary. "
            f"Tokens: ~{total} → ~{tokens_after} (model={model})"
        )
        return result

    def _estimate_tokens(self, messages: list[dict]) -> int:
        """
        Rough token estimate: character_count / 4.
        Fast approximation — exact count not needed, only order of magnitude.
        """
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        # ~4 chars per token on average for English text
        return int(total_chars / 4)

    async def _summarise(self, messages: list[dict], current_model: str) -> str:
        """
        Summarise a list of conversation messages using a cheap local model.
        Always uses Ollama — never burns expensive tokens on compaction.
        """
        # Build a readable transcript for the summariser
        transcript_parts = []
        for m in messages[:20]:  # cap at 20 turns to keep prompt manageable
            role    = m.get("role", "unknown")
            content = str(m.get("content", ""))[:400]  # truncate each turn
            transcript_parts.append(f"{role.upper()}: {content}")

        transcript       = "\n".join(transcript_parts)
        summarise_model  = config().get("providers.ollama.default_model", "llama3.1:8b")

        req = LLMRequest(
            # LLMRequest is defined in pawbot/contracts.py Section 0.4
            messages=[{
                "role": "user",
                "content": (
                    "Summarise the following conversation in 3-5 sentences. "
                    "Preserve: all key facts, decisions made, files changed, "
                    "errors encountered, tasks completed, and user preferences. "
                    "Be concise — this summary replaces the full history.\n\n"
                    f"{transcript}"
                )
            }],
            model        = summarise_model,
            provider     = ProviderName.OLLAMA,    # contracts.py enum — always use local
            max_tokens   = 250,
            temperature  = 0.0,
            task_type    = TaskType.MEMORY_TASK,   # contracts.py enum
        )

        response = await model_router.complete(req)
        return response.content.strip()


# ── Singleton ──────────────────────────────────────────────────────────────────
compactor = ContextCompactor()
```

---

## File 2 of 3 — EDIT `pawbot/agent/loop.py`

Find the section in the agent loop where messages are assembled and then passed to
`model_router.complete()`. Insert the compactor call between those two lines.

```python
# ADD THIS IMPORT at the top of pawbot/agent/loop.py:
from pawbot.agent.compactor import compactor

# FIND this pattern in loop.py (or equivalent):
# messages  = context_builder.to_messages(assembled_context)
# response  = await model_router.complete(LLMRequest(messages=messages, ...))

# REPLACE WITH:
messages = context_builder.to_messages(assembled_context)

# ← INSERT HERE: compact before every LLM call
messages = await compactor.compact_if_needed(messages, current_model)

response = await model_router.complete(LLMRequest(
    messages     = messages,
    model        = current_model,
    # ... rest of existing arguments unchanged
))
```

---

## File 3 of 3 — CREATE `tests/test_compactor.py`

```python
"""
tests/test_compactor.py
Run: pytest tests/test_compactor.py -v
"""

import pytest
from unittest.mock import AsyncMock, patch
from pawbot.agent.compactor import ContextCompactor, MODEL_CONTEXT_LIMITS


@pytest.fixture
def cx():
    return ContextCompactor()


def make_messages(n: int, content_len: int = 200) -> list[dict]:
    """Generate n fake conversation messages."""
    msgs = [{"role": "system", "content": "You are Pawbot."}]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": "x" * content_len})
    return msgs


@pytest.mark.asyncio
async def test_no_compaction_below_threshold(cx):
    """Short conversations should pass through unchanged."""
    msgs   = make_messages(5, content_len=100)
    result = await cx.compact_if_needed(msgs, "llama3.1:8b")
    assert result == msgs, "Short conversations should not be modified"


@pytest.mark.asyncio
async def test_compaction_triggers_above_threshold(cx):
    """Long conversations should be compacted — result must be shorter."""
    model = "llama3.1:8b"  # smallest limit: 8192 tokens
    # Generate enough content to fill ~85% of 8192 tokens
    msgs  = make_messages(200, content_len=180)

    with patch.object(cx, "_summarise", new=AsyncMock(return_value="summary text")) as mock_sum:
        result = await cx.compact_if_needed(msgs, model)
        assert len(result) < len(msgs), "Result must be shorter than input"
        mock_sum.assert_called_once()


@pytest.mark.asyncio
async def test_system_prompt_never_compacted(cx):
    """System messages must always survive compaction intact."""
    model          = "llama3.1:8b"
    msgs           = make_messages(200, content_len=180)
    system_content = msgs[0]["content"]

    with patch.object(cx, "_summarise", new=AsyncMock(return_value="compact summary")):
        result = await cx.compact_if_needed(msgs, model)

    system_msgs = [m for m in result if m["role"] == "system" and "COMPACTED" not in m["content"]]
    assert any(m["content"] == system_content for m in system_msgs), \
        "Original system prompt must be preserved after compaction"


@pytest.mark.asyncio
async def test_last_n_turns_always_kept(cx):
    """The most recent KEEP_LAST_N_TURNS turns must survive compaction intact."""
    from pawbot.agent.compactor import KEEP_LAST_N_TURNS

    model = "llama3.1:8b"
    msgs  = make_messages(200, content_len=180)

    # Tag the last N conversation turns with a unique marker
    convo = [m for m in msgs if m["role"] != "system"]
    for m in convo[-KEEP_LAST_N_TURNS:]:
        m["content"] = "KEEP_ME_" + m["content"][:10]

    with patch.object(cx, "_summarise", new=AsyncMock(return_value="summary")):
        result = await cx.compact_if_needed(msgs, model)

    kept = [m for m in result if str(m.get("content", "")).startswith("KEEP_ME_")]
    assert len(kept) == KEEP_LAST_N_TURNS, \
        f"Expected {KEEP_LAST_N_TURNS} kept turns, got {len(kept)}"


@pytest.mark.asyncio
async def test_token_estimate_correctness(cx):
    """Token estimator must return plausible values."""
    msgs     = [{"role": "user", "content": "Hello world this is a test."}]
    estimate = cx._estimate_tokens(msgs)
    # "Hello world this is a test." = ~28 chars / 4 = ~7 tokens
    assert 4 <= estimate <= 20, f"Unexpected token estimate: {estimate}"
```

---

## Test Matrix

| # | Test | Input | Expected | Pass Condition |
|---|------|-------|----------|----------------|
| T1 | No compaction below threshold | 5 short messages | result == input | Fast path verified |
| T2 | Compaction triggers above threshold | 200×180-char messages | `len(result) < len(input)` | `_summarise` called once |
| T3 | System prompt preserved | Same long messages | System msg in result intact | Content matches exactly |
| T4 | Last N turns kept | Tag last 6 turns `KEEP_ME_` | All 6 `KEEP_ME_` in result | Exact count = `KEEP_LAST_N_TURNS` |
| T5 | Token estimator plausible | 28-char message | `4 <= estimate <= 20` | Order-of-magnitude correct |

---

## ⛔ Acceptance Gate — Phase 2
**ALL criteria must pass before Phase 3 can start.**

```bash
pytest tests/test_compactor.py -v
```

- [ ] All 5 tests pass
- [ ] `compactor.compact_if_needed()` is called in `loop.py` before every LLM call:

```bash
grep -n 'compact_if_needed' pawbot/agent/loop.py
# Must return at least one result
```

- [ ] 200-message test conversation completes without token overflow error
- [ ] System prompt is not removed during compaction

---

---

# PHASE 3 OF 5 — WebSocket Gateway Server
### *Persistent connection layer — replaces fire-and-forget message handling*

---

## Agent Prompt

You are implementing the **WebSocket Gateway Server** for the Pawbot framework.

This gives Pawbot a persistent, addressable entry point at `ws://host:8080/ws/{session_id}`
and a REST health endpoint at `GET /health`. You will create a single FastAPI application file.

**Depends on:** Phase 1 (lane_queue must exist).
Check that `pawbot/agent/lane_queue.py` exists and `lane_queue` singleton is importable before starting.

Read `pawbot/contracts.py` fully before writing any code.

---

## Why This Phase Exists

Pawbot currently has no persistent connection layer. Each message is a one-shot HTTP call
or a polling callback with no stable identity. Without this:

- Tailscale/reverse-proxy setups require complex routing per channel
- Docker `HEALTHCHECK` cannot probe liveness
- Dashboard has no endpoint to connect to
- Response streaming is impossible — you get one response per full round-trip

---

## What You Will Build

| Action | File |
|--------|------|
| **CREATE** | `pawbot/gateway/__init__.py` — empty, makes gateway a Python package |
| **CREATE** | `pawbot/gateway/server.py` — FastAPI app with WS and REST endpoints |
| **EDIT** | `pawbot/cli/commands/run.py` — launch uvicorn pointing at `gateway.server:app` |
| **CREATE** | `tests/test_gateway.py` — 4 tests |

---

## Dependencies

| Dependency | Type | Import | If Missing |
|-----------|------|--------|-----------|
| `lane_queue` (Phase 1) | Internal | `from pawbot.agent.lane_queue import lane_queue` | **STOP** — Phase 1 must be complete |
| `InboundMessage` | contracts.py Section 0.4 | `from pawbot.contracts import InboundMessage` | Complete dataclass |
| `OutboundMessage` | contracts.py Section 0.4 | `from pawbot.contracts import OutboundMessage` | Complete dataclass |
| `ChannelType.API` | contracts.py Section 0.3 | enum value | Do not redefine |
| `new_id(), now()` | contracts.py Section 0.11 | `from pawbot.contracts import new_id, now` | Utility functions |
| `SQLITE_DB` | contracts.py Section 0.1 | `from pawbot.contracts import SQLITE_DB` | File path constant |
| `fastapi` | `pyproject.toml` dep | `from fastapi import FastAPI, WebSocket` | Must be in dependencies |
| `uvicorn` | `pyproject.toml` dep | `uvicorn pawbot.gateway.server:app` | Must be in dependencies |

---

## Reference Map

| Item | Details |
|------|---------|
| `InboundMessage` fields | `id, channel, from_user, session_id, content, raw_type, has_media, media_path, timestamp, is_priority` |
| `ChannelType.API` | contracts.py Section 0.3 — value = `'api'` |
| `new_id()` | contracts.py Section 0.11 — returns unique string ID |
| `now()` | contracts.py Section 0.11 — returns Unix timestamp int |
| `SQLITE_DB` | contracts.py Section 0.1 — e.g. `'~/.pawbot/pawbot.db'` |
| `lane_queue.stats()` | `pawbot/agent/lane_queue.py` — returns `{session_id: queue_depth}` |
| `lane_queue.active_sessions()` | `pawbot/agent/lane_queue.py` — returns `int` |
| WS endpoint | `ws://0.0.0.0:8080/ws/{session_id}` |
| Health endpoint | `http://0.0.0.0:8080/health` |

---

## File 1 of 4 — CREATE `pawbot/gateway/__init__.py`

```python
# pawbot/gateway/__init__.py
# Empty — makes gateway a Python package
```

---

## File 2 of 4 — CREATE `pawbot/gateway/server.py`

```python
"""
pawbot/gateway/server.py

WebSocket Gateway Server — the single entry point for all external connections.

Endpoints:
    WS  /ws/{session_id} — bidirectional message stream per session
    GET /health          — liveness probe (used by Docker HEALTHCHECK)
    GET /sessions        — list active sessions and queue depths

IMPORTS FROM: pawbot/contracts.py — InboundMessage, OutboundMessage,
              ChannelType, new_id(), now(), SQLITE_DB, get_logger()
USES:         lane_queue (Phase 1) — all messages enqueued, never direct
RUNS AS:      uvicorn pawbot.gateway.server:app --host 0.0.0.0 --port 8080
"""

import json
import os
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from pawbot.contracts import (
    InboundMessage, OutboundMessage, ChannelType,
    new_id, now, SQLITE_DB, get_logger, PRIORITY_KEYWORDS
)
from pawbot.agent.lane_queue import lane_queue

logger = get_logger(__name__)

app = FastAPI(title="Pawbot Gateway", version="2.0.0")

_boot_ts = time.time()  # used by /health uptime calculation


# ── WebSocket endpoint ──────────────────────────────────────────────────────────
@app.websocket("/ws/{session_id}")
async def ws_endpoint(websocket: WebSocket, session_id: str) -> None:
    """
    Persistent bidirectional connection for a session.
    Client sends JSON:  {"user_id": "...", "message": "...", "is_priority": false}
    Server sends JSON:  {"response": "...", "session_id": "...", "ts": 1234567890}
    Messages are enqueued via lane_queue — never processed in parallel within the same session_id.
    """
    await websocket.accept()
    logger.info(f"Gateway: WS connection opened — session={session_id}")

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "error": "Invalid JSON", "session_id": session_id
                }))
                continue

            content = str(data.get("message", "")).strip()
            if not content:
                continue

            user_id     = str(data.get("user_id", "api_user"))
            is_priority = bool(data.get("is_priority", False)) or any(
                kw.lower() in content.lower() for kw in PRIORITY_KEYWORDS
            )

            # Build InboundMessage — contracts.py dataclass
            msg = InboundMessage(
                id          = new_id(),
                channel     = ChannelType.API,    # contracts.py enum
                from_user   = user_id,
                session_id  = session_id,
                content     = content,
                raw_type    = "text",
                has_media   = False,
                media_path  = None,
                timestamp   = now(),
                is_priority = is_priority,
            )

            # Enqueue via LaneQueue (Phase 1) — never call handler directly
            await lane_queue.enqueue(
                session_id,
                _handle_message,
                msg,
                websocket
            )

    except WebSocketDisconnect:
        logger.info(f"Gateway: WS disconnected — session={session_id}")
    except Exception as exc:
        logger.error(f"Gateway: WS error — session={session_id}: {exc}", exc_info=True)
        try:
            await websocket.send_text(json.dumps({"error": str(exc)}))
        except Exception:
            pass


async def _handle_message(msg: InboundMessage, websocket: WebSocket) -> None:
    """
    Called by LaneQueue worker — processes one message and sends response.
    Import agent_loop here (lazy import) to avoid circular imports.
    """
    from pawbot.agent.loop import agent_loop

    try:
        result = await agent_loop.run(
            message    = msg.content,
            session_id = msg.session_id,
            from_user  = msg.from_user,
            channel    = msg.channel,
            tools      = [],
        )
        await websocket.send_text(json.dumps({
            "response":   result.response,
            "session_id": msg.session_id,
            "ts":         now(),
        }))
    except Exception as exc:
        logger.error(f"Gateway: handler error — {exc}", exc_info=True)
        await websocket.send_text(json.dumps({
            "error":      str(exc),
            "session_id": msg.session_id,
        }))


# ── REST endpoints ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health() -> JSONResponse:
    """
    Liveness probe. Used by:
    - Docker HEALTHCHECK
    - Kubernetes readiness probe
    - Dashboard ClawHub skill
    Returns 200 OK when healthy, 503 when degraded.
    """
    import psutil

    db_path = os.path.expanduser(SQLITE_DB)  # SQLITE_DB from contracts.py
    db_mb   = os.path.getsize(db_path) / 1e6 if os.path.exists(db_path) else 0.0

    payload = {
        "status":          "ok",
        "version":         "2.0.0",
        "uptime_seconds":  int(time.time() - _boot_ts),
        "active_sessions": lane_queue.active_sessions(),
        "lane_depths":     lane_queue.stats(),
        "memory_db_mb":    round(db_mb, 2),
        "cpu_percent":     psutil.cpu_percent(interval=0.1),
        "ram_percent":     psutil.virtual_memory().percent,
    }
    return JSONResponse(content=payload, status_code=200)


@app.get("/sessions")
async def sessions() -> JSONResponse:
    """List active session lanes and their queue depths."""
    return JSONResponse(content={
        "active_sessions": lane_queue.active_sessions(),
        "lanes":           lane_queue.stats(),
    })


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Gracefully drain lane queues on server shutdown."""
    await lane_queue.shutdown()
    logger.info("Gateway: shut down cleanly")
```

---

## File 3 of 4 — EDIT `pawbot/cli/commands/run.py`

```python
# ADD OR REPLACE the run command in pawbot/cli/commands/run.py

import click
import uvicorn


@click.command()
@click.option("--host",   default="127.0.0.1", help="Bind host (use 0.0.0.0 for Docker)")
@click.option("--port",   default=8080,         help="Bind port")
@click.option("--reload", is_flag=True, default=False, help="Hot-reload on code changes")
def run(host: str, port: int, reload: bool) -> None:
    """Start the Pawbot Gateway server."""
    click.echo(f"Starting Pawbot Gateway at {host}:{port}")
    uvicorn.run(
        "pawbot.gateway.server:app",
        host      = host,
        port      = port,
        reload    = reload,
        log_level = "info",
    )
```

---

## File 4 of 4 — CREATE `tests/test_gateway.py`

```python
"""
tests/test_gateway.py
Run: pytest tests/test_gateway.py -v
"""

import pytest
import json
from httpx import AsyncClient, ASGITransport
from pawbot.gateway.server import app


@pytest.mark.asyncio
async def test_health_returns_200():
    """GET /health must return 200 and status=ok."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "uptime_seconds"  in data
    assert "active_sessions" in data
    assert "memory_db_mb"    in data


@pytest.mark.asyncio
async def test_sessions_endpoint():
    """GET /sessions must return active_sessions count."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/sessions")
    assert r.status_code == 200
    data = r.json()
    assert "active_sessions" in data
    assert "lanes"           in data


@pytest.mark.asyncio
async def test_health_contains_lane_depths():
    """lane_depths in /health must be a dict."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
    assert isinstance(r.json()["lane_depths"], dict)


@pytest.mark.asyncio
async def test_invalid_json_on_ws_returns_error():
    """WebSocket must return error JSON on invalid input — not crash."""
    from starlette.testclient import TestClient

    client = TestClient(app)
    with client.websocket_connect("/ws/test_session_001") as ws:
        ws.send_text("NOT VALID JSON {{{")  # malformed
        response = json.loads(ws.receive_text())
    assert "error" in response
```

---

## ⛔ Acceptance Gate — Phase 3
**ALL criteria must pass before Phase 4 can start.**

```bash
pytest tests/test_gateway.py -v
```

- [ ] All 4 tests pass
- [ ] `uvicorn pawbot.gateway.server:app` starts without error
- [ ] `curl http://localhost:8080/health` returns `{"status": "ok"}` with HTTP 200
- [ ] Invalid JSON on WebSocket returns error message, not a 500 crash
- [ ] `pawbot run` launches the server (verify by running it)

---

---

# PHASE 4 OF 5 — Multi-Agent Router
### *Route channels and contacts to separate configured agents*

---

## Agent Prompt

You are implementing the **Multi-Agent Router** for the Pawbot framework.

This lets Pawbot run multiple agent configurations simultaneously — one per channel,
contact, or context. For example: Telegram personal messages route to 'personal' agent
with `SOUL_PERSONAL.md`; email routes to 'work' agent with `SOUL_WORK.md`.
Each agent has its own session namespace so memories never bleed across contexts.

**Depends on:** Phase 1 (LaneQueue) and Phase 3 (gateway server — for config schema).
Read `pawbot/contracts.py` fully before writing any code.

---

## Why This Phase Exists

Without routing: work emails and personal chats share the same agent memory — context bleeds.
Without routing: you cannot have different tones, skill sets, or tool permissions per context.
Without routing: you cannot partition sessions — all conversations land in one namespace.

---

## What You Will Build

| Action | File |
|--------|------|
| **CREATE** | `pawbot/agent/agent_router.py` — `AgentRouter` class and singleton |
| **EDIT** | `config.json` — add `agents[]` array (schema documented below) |
| **CREATE** | `tests/test_agent_router.py` — 4 tests |

---

## Config Schema — Add to `config.json`

These are the exact dot-notation paths used with `config().get()`.
The first agent with `"default": true` is the catch-all fallback.

```json
{
  "agents": [
    {
      "id":             "personal",
      "name":           "Pawbot Personal",
      "soul_file":      "~/.pawbot/workspace/SOUL.md",
      "skills_dir":     "~/.pawbot/skills/custom",
      "channels":       ["telegram", "whatsapp", "api"],
      "contacts":       ["*"],
      "default":        true,
      "session_prefix": "personal_"
    },
    {
      "id":             "work",
      "name":           "Pawbot Work",
      "soul_file":      "~/.pawbot/workspace/SOUL_WORK.md",
      "skills_dir":     "~/.pawbot/skills/work",
      "channels":       ["email"],
      "contacts":       ["*"],
      "default":        false,
      "session_prefix": "work_"
    }
  ]
}
```

Access in code:
```python
config().get("agents")           # list of agent dicts
config().get("agents")[0]["id"]  # "personal"
```

---

## File 1 of 2 — CREATE `pawbot/agent/agent_router.py`

```python
"""
pawbot/agent/agent_router.py

Multi-Agent Router — resolves which agent configuration handles a message.

Routing logic:
    1. Find agents whose channels[] list contains the incoming channel type
    2. Among those, find agents whose contacts[] list contains from_user, or has "*"
    3. Return the first match
    4. If no match: return the agent with default: true
    5. If no default: return the first agent (safety fallback)

IMPORTS FROM: pawbot/contracts.py — ChannelType, config(), get_logger()
SINGLETON:    agent_router — import this everywhere
CALLED BY:    channel adapters, gateway server, session manager
"""

import os
from pawbot.contracts import ChannelType, config, get_logger

logger = get_logger(__name__)


class AgentRouter:
    """
    Routes inbound messages to the correct agent configuration.
    Configuration comes from config().get("agents") — loaded fresh on each call
    so hot-reload of config.json is supported without restart.
    """

    def resolve(self, channel: ChannelType, from_user: str) -> dict:
        """
        Resolve which agent config should handle this message.

        Args:
            channel:   ChannelType enum from contracts.py (e.g. ChannelType.TELEGRAM)
            from_user: User identifier string (e.g. Telegram user_id "12345678")

        Returns:
            Agent config dict from config().get("agents").
            Always returns a dict — never None.
            Fallback order: matched agent → default agent → first agent
        """
        agents = config().get("agents", [])

        if not agents:
            logger.warning("AgentRouter: no agents configured in config.json")
            return self._default_agent_config()

        channel_str = channel.value  # e.g. "telegram", "email"

        for agent in agents:
            agent_channels = agent.get("channels", [])
            agent_contacts = agent.get("contacts", ["*"])

            channel_match = ("*" in agent_channels or channel_str in agent_channels)
            contact_match = ("*" in agent_contacts or from_user in agent_contacts)

            if channel_match and contact_match:
                logger.debug(f"AgentRouter: routed {channel_str}/{from_user} → {agent['id']}")
                return agent

        # No specific match — fall back to default agent
        default = next((a for a in agents if a.get("default")), None)
        if default:
            logger.debug(f"AgentRouter: no match — using default agent {default['id']}")
            return default

        # Last resort: first agent
        logger.warning("AgentRouter: no default agent set — using first agent")
        return agents[0]

    def get_session_id(self, agent_config: dict, from_user: str, channel: ChannelType) -> str:
        """
        Build a namespaced session_id so agents never share session state.

        Format:  {session_prefix}{channel}_{from_user}
        Example: "personal_telegram_12345678"
                 "work_email_boss@company.com"
        """
        prefix = agent_config.get("session_prefix", "")
        return f"{prefix}{channel.value}_{from_user}"

    def get_soul_path(self, agent_config: dict) -> str:
        """
        Return the absolute path to this agent's SOUL.md file.
        Falls back to the default SOUL_MD path from contracts.py if not configured.
        """
        from pawbot.contracts import SOUL_MD  # contracts.py Section 0.1

        configured = agent_config.get("soul_file", "")
        if configured:
            return os.path.expanduser(configured)
        return os.path.expanduser(SOUL_MD)

    def _default_agent_config(self) -> dict:
        """Return a minimal safe default when no agents are configured."""
        from pawbot.contracts import SOUL_MD, CUSTOM_SKILLS_DIR
        return {
            "id":             "default",
            "name":           "Pawbot",
            "soul_file":      SOUL_MD,
            "skills_dir":     CUSTOM_SKILLS_DIR,
            "channels":       ["*"],
            "contacts":       ["*"],
            "default":        True,
            "session_prefix": "",
        }


# ── Singleton ──────────────────────────────────────────────────────────────────
agent_router = AgentRouter()
```

---

## File 2 of 2 — CREATE `tests/test_agent_router.py`

```python
"""
tests/test_agent_router.py
Run: pytest tests/test_agent_router.py -v
"""

import pytest
from unittest.mock import patch
from pawbot.agent.agent_router import AgentRouter
from pawbot.contracts import ChannelType

MOCK_CONFIG = {
    "agents": [
        {
            "id":             "personal",
            "name":           "Personal",
            "soul_file":      "~/SOUL_PERSONAL.md",
            "channels":       ["telegram", "whatsapp"],
            "contacts":       ["*"],
            "default":        True,
            "session_prefix": "personal_"
        },
        {
            "id":             "work",
            "name":           "Work",
            "soul_file":      "~/SOUL_WORK.md",
            "channels":       ["email"],
            "contacts":       ["boss@company.com", "team@company.com"],
            "default":        False,
            "session_prefix": "work_"
        }
    ]
}


def make_router():
    return AgentRouter()


def test_telegram_routes_to_personal():
    """Telegram messages should route to personal agent."""
    router = make_router()
    with patch("pawbot.agent.agent_router.config") as mock_cfg:
        mock_cfg.return_value.get.return_value = MOCK_CONFIG["agents"]
        result = router.resolve(ChannelType.TELEGRAM, "user_12345")
    assert result["id"] == "personal"


def test_email_routes_to_work_for_known_contact():
    """Email from known contact routes to work agent."""
    router = make_router()
    with patch("pawbot.agent.agent_router.config") as mock_cfg:
        mock_cfg.return_value.get.return_value = MOCK_CONFIG["agents"]
        result = router.resolve(ChannelType.EMAIL, "boss@company.com")
    assert result["id"] == "work"


def test_unknown_channel_falls_back_to_default():
    """Unknown channel should fall back to default agent."""
    router = make_router()
    with patch("pawbot.agent.agent_router.config") as mock_cfg:
        mock_cfg.return_value.get.return_value = MOCK_CONFIG["agents"]
        result = router.resolve(ChannelType.API, "some_user")
    assert result["id"] == "personal", "API channel not in any agent — should use default"


def test_session_id_namespaced_per_agent():
    """Session IDs must include agent prefix to prevent cross-agent memory bleed."""
    router       = make_router()
    personal_cfg = MOCK_CONFIG["agents"][0]
    session_id   = router.get_session_id(personal_cfg, "12345", ChannelType.TELEGRAM)
    assert session_id == "personal_telegram_12345"
    assert session_id.startswith("personal_"), "Must include agent session_prefix"
```

---

## ⛔ Acceptance Gate — Phase 4
**ALL criteria must pass before Phase 5 can start.**

```bash
pytest tests/test_agent_router.py -v
```

- [ ] All 4 tests pass
- [ ] Telegram message resolves to personal agent
- [ ] Email from `boss@company.com` resolves to work agent
- [ ] `get_session_id()` returns correctly namespaced string
- [ ] Channel adapters (Telegram, WhatsApp, Email) use `agent_router.resolve()`:

```bash
grep -r 'agent_router.resolve' pawbot/
# Must return at least one result
```

---

---

# PHASE 5 OF 5 — Startup Validator & Health API
### *Catch misconfiguration at boot — fail loud, not silent*

---

## Agent Prompt

You are implementing the **Startup Validator** and completing the Health API for Pawbot.

Pawbot currently starts silently even with broken config — missing API keys, wrong paths,
missing databases. Errors only surface when the first message arrives. Your job is to add
a validation pass that runs at boot and prints clear, actionable errors if anything is wrong.

**Depends on:** All previous phases (1–4) must be gated before starting this.
Read `pawbot/contracts.py` fully before writing any code.

---

## Why This Phase Exists

Pawbot silently accepts misconfiguration. The first user message triggers an unhelpful
internal stack trace with no actionable fix instructions. This is especially painful for
new deployments and Docker containers where the operator cannot interactively debug.

- Missing `ANTHROPIC_API_KEY` → silent `NoneType` error during first LLM call
- SQLite DB path wrong → crashes on first memory write
- Chroma not writable → crashes on first vector embed
- Ollama not running but `routing.mechanical_to_local=true` → all low-complexity tasks crash

---

## What You Will Build

| Action | File |
|--------|------|
| **CREATE** | `pawbot/config/validator.py` — `StartupValidator` class |
| **EDIT** | `pawbot/cli/commands/run.py` — call validator before starting uvicorn |
| **EDIT** | `pawbot/gateway/server.py` — enhance `/health` with validation status |
| **CREATE** | `tests/test_validator.py` — 5 tests |

---

## Reference Map — from contracts.py

| Item | Details |
|------|---------|
| `SQLITE_DB` | Section 0.1 — path to SQLite database |
| `CHROMA_DIR` | Section 0.1 — path to Chroma vector store directory |
| `CONFIG_FILE` | Section 0.1 — path to `config.json` |
| `SOUL_MD` | Section 0.1 — path to `SOUL.md` workspace file |
| `config().get('providers.anthropic.api_key')` | Section 0.2 exact key |
| `config().get('providers.openrouter.api_key')` | Section 0.2 exact key |
| `config().get('providers.ollama.base_url')` | Section 0.2 exact key |
| `config().get('routing.mechanical_to_local')` | Section 0.2 exact key |
| `config().get('agents')` | Section 0.2 — agents array |
| `get_logger(__name__)` | Section 0.11 |

---

## File 1 of 3 — CREATE `pawbot/config/validator.py`

```python
"""
pawbot/config/validator.py

Startup Validator — validates all critical config before the gateway goes live.
Run at application boot before any connections are accepted.
Prints clear, human-readable errors with fix instructions.

Two modes:
    strict  — exit on any error
    warn    — print errors, continue

IMPORTS FROM: pawbot/contracts.py — all path constants and config keys
CALLED BY:    pawbot/cli/commands/run.py — before uvicorn.run()
"""

import os
import sys
import sqlite3
from dataclasses import dataclass, field

from pawbot.contracts import (
    SQLITE_DB, CHROMA_DIR, CONFIG_FILE, SOUL_MD,
    config, get_logger
)

logger = get_logger(__name__)


@dataclass
class ValidationIssue:
    """A single validation finding."""
    level:   str  # "ERROR" or "WARN"
    check:   str  # Short check name
    message: str  # Human-readable problem description
    fix:     str  # How to fix it


@dataclass
class ValidationResult:
    """Aggregated result of a full startup validation pass."""
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "ERROR"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "WARN"]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


class StartupValidator:
    """
    Validates Pawbot configuration before the gateway starts.
    Call validate() at boot. If result.ok is False, print errors and optionally exit.
    """

    def validate(self) -> ValidationResult:
        """Run all checks and return a ValidationResult."""
        result = ValidationResult()
        self._check_config_file(result)
        self._check_api_keys(result)
        self._check_sqlite(result)
        self._check_chroma_dir(result)
        self._check_soul_md(result)
        self._check_agents_config(result)
        self._check_ollama_if_needed(result)
        return result

    def _check_config_file(self, r: ValidationResult) -> None:
        path = os.path.expanduser(CONFIG_FILE)
        if not os.path.exists(path):
            r.issues.append(ValidationIssue(
                level   = "ERROR",
                check   = "config_file",
                message = f"Config file not found: {path}",
                fix     = "Run: pawbot onboard  OR  copy .env.example to .env and edit it"
            ))

    def _check_api_keys(self, r: ValidationResult) -> None:
        anthropic_key  = config().get("providers.anthropic.api_key",  "")
        openrouter_key = config().get("providers.openrouter.api_key", "")
        openai_key     = config().get("providers.openai.api_key",     "")
        has_cloud      = any([anthropic_key, openrouter_key, openai_key])
        has_ollama     = bool(config().get("providers.ollama.base_url", ""))

        if not has_cloud and not has_ollama:
            r.issues.append(ValidationIssue(
                level   = "ERROR",
                check   = "api_keys",
                message = "No LLM provider configured. At least one API key or Ollama URL is required.",
                fix     = "Set ANTHROPIC_API_KEY in .env  OR  set providers.ollama.base_url in config.json"
            ))
        elif not has_cloud and has_ollama:
            r.issues.append(ValidationIssue(
                level   = "WARN",
                check   = "api_keys",
                message = "Only Ollama configured — complex tasks may be slow.",
                fix     = "Add ANTHROPIC_API_KEY or OPENROUTER_API_KEY for better performance"
            ))

    def _check_sqlite(self, r: ValidationResult) -> None:
        path   = os.path.expanduser(SQLITE_DB)
        db_dir = os.path.dirname(path)

        if not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
            except OSError as e:
                r.issues.append(ValidationIssue(
                    level   = "ERROR",
                    check   = "sqlite_dir",
                    message = f"Cannot create database directory: {db_dir} — {e}",
                    fix     = f"Ensure parent directory is writable: ls -la {os.path.dirname(db_dir)}"
                ))
                return

        try:
            conn = sqlite3.connect(path)
            conn.execute("SELECT 1")
            conn.close()
        except sqlite3.Error as e:
            r.issues.append(ValidationIssue(
                level   = "ERROR",
                check   = "sqlite_connect",
                message = f"SQLite database error: {path} — {e}",
                fix     = "Delete the database file and run: pawbot onboard"
            ))

    def _check_chroma_dir(self, r: ValidationResult) -> None:
        path = os.path.expanduser(CHROMA_DIR)
        try:
            os.makedirs(path, exist_ok=True)
            test_file = os.path.join(path, ".write_test")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        except OSError as e:
            r.issues.append(ValidationIssue(
                level   = "ERROR",
                check   = "chroma_dir",
                message = f"Chroma directory not writable: {path} — {e}",
                fix     = f"Run: chmod -R u+w {path}  OR  check disk space: df -h"
            ))

    def _check_soul_md(self, r: ValidationResult) -> None:
        path = os.path.expanduser(SOUL_MD)
        if not os.path.exists(path):
            r.issues.append(ValidationIssue(
                level   = "WARN",
                check   = "soul_md",
                message = f"SOUL.md not found: {path} — agent will use default personality.",
                fix     = f"Create the file: touch {path}  then add personality instructions"
            ))

    def _check_agents_config(self, r: ValidationResult) -> None:
        agents = config().get("agents", [])
        if not agents:
            r.issues.append(ValidationIssue(
                level   = "WARN",
                check   = "agents_config",
                message = "No agents configured in config.json — using built-in defaults.",
                fix     = "Add agents[] array to config.json (see Section 1 Phase 4)"
            ))
        elif not any(a.get("default") for a in agents):
            r.issues.append(ValidationIssue(
                level   = "WARN",
                check   = "agents_default",
                message = "No default agent configured — fallback will use first agent.",
                fix     = "Set default: true on one agent in the agents[] array"
            ))

    def _check_ollama_if_needed(self, r: ValidationResult) -> None:
        if not config().get("routing.mechanical_to_local", False):
            return  # Ollama not required

        import httpx
        base_url = config().get("providers.ollama.base_url", "http://localhost:11434")
        try:
            httpx.get(f"{base_url}/api/tags", timeout=2.0)
        except Exception:
            r.issues.append(ValidationIssue(
                level   = "ERROR",
                check   = "ollama_reachable",
                message = f"Ollama not reachable at {base_url} but routing.mechanical_to_local=true.",
                fix     = "Start Ollama: ollama serve  OR  set routing.mechanical_to_local: false in config"
            ))

    def print_report(self, result: ValidationResult) -> None:
        """Print a human-readable validation report to stdout."""
        if result.ok and not result.warnings:
            print("✅ Pawbot config validation: all checks passed")
            return

        print("")
        print("══════════════════════════════════════════")
        print(" PAWBOT STARTUP VALIDATION REPORT")
        print("══════════════════════════════════════════")

        for issue in result.errors:
            print(f" ❌ ERROR [{issue.check}]")
            print(f"    Problem: {issue.message}")
            print(f"    Fix:     {issue.fix}")
            print("")

        for issue in result.warnings:
            print(f" ⚠️  WARN [{issue.check}]")
            print(f"    {issue.message}")
            print(f"    Fix: {issue.fix}")
            print("")

        print("══════════════════════════════════════════")


# ── Singleton ──────────────────────────────────────────────────────────────────
startup_validator = StartupValidator()
```

---

## File 2 of 3 — EDIT `pawbot/cli/commands/run.py`

Add validator call before `uvicorn.run()`:

```python
# ADD VALIDATOR CALL before uvicorn.run() in pawbot/cli/commands/run.py:

import sys
import click
import uvicorn
from pawbot.config.validator import startup_validator


@click.command()
@click.option("--host",   default="127.0.0.1")
@click.option("--port",   default=8080)
@click.option("--strict", is_flag=True, default=False,
              help="Exit on any validation error (default: warn and continue)")
def run(host, port, strict):
    """Start the Pawbot Gateway server."""

    # ── Validate config before starting ──────────────────────────────────────
    result = startup_validator.validate()
    startup_validator.print_report(result)

    if not result.ok and strict:
        click.echo("\n❌ Startup validation failed. Fix errors above and retry.")
        sys.exit(1)
    elif not result.ok:
        click.echo("\n⚠️ Validation errors found — starting anyway (use --strict to block).")

    # ── Start server ──────────────────────────────────────────────────────────
    click.echo(f"\n🐾 Pawbot Gateway starting at {host}:{port}")
    uvicorn.run("pawbot.gateway.server:app", host=host, port=port)
```

---

## File 3 of 3 — CREATE `tests/test_validator.py`

```python
"""
tests/test_validator.py
Run: pytest tests/test_validator.py -v
"""

import pytest
import os
from unittest.mock import patch
from pawbot.config.validator import StartupValidator, ValidationResult


@pytest.fixture
def v():
    return StartupValidator()


def test_valid_config_returns_ok(v, tmp_path):
    """When all checks pass, result.ok must be True."""
    db     = str(tmp_path / "pawbot.db")
    chroma = str(tmp_path / "chroma")
    soul   = str(tmp_path / "SOUL.md")
    open(soul, "w").close()

    with patch("pawbot.config.validator.SQLITE_DB",  db), \
         patch("pawbot.config.validator.CHROMA_DIR", chroma), \
         patch("pawbot.config.validator.SOUL_MD",    soul), \
         patch("pawbot.config.validator.CONFIG_FILE", soul), \
         patch("pawbot.config.validator.config") as mock_cfg:

        mock_cfg.return_value.get.side_effect = lambda k, d=None: {
            "providers.anthropic.api_key": "sk-test-key",
            "routing.mechanical_to_local": False,
            "agents": [{"id": "default", "default": True}],
        }.get(k, d)

        result = v.validate()

    assert result.ok, f"Expected ok but got errors: {[e.message for e in result.errors]}"


def test_missing_api_key_is_error(v, tmp_path):
    """No API keys → validation error."""
    db   = str(tmp_path / "pawbot.db")
    soul = str(tmp_path / "SOUL.md")
    open(soul, "w").close()

    with patch("pawbot.config.validator.SQLITE_DB",  db), \
         patch("pawbot.config.validator.SOUL_MD",    soul), \
         patch("pawbot.config.validator.CONFIG_FILE", soul), \
         patch("pawbot.config.validator.CHROMA_DIR", str(tmp_path / "chroma")), \
         patch("pawbot.config.validator.config") as mock_cfg:

        mock_cfg.return_value.get.side_effect = lambda k, d=None: {
            "routing.mechanical_to_local": False,
            "agents": [],
        }.get(k, d)

        result = v.validate()

    assert "api_keys" in [e.check for e in result.errors]


def test_missing_soul_md_is_warning(v, tmp_path):
    """Missing SOUL.md is a warning, not an error."""
    db  = str(tmp_path / "pawbot.db")
    cfg = str(tmp_path / "config.json")
    open(cfg, "w").close()

    with patch("pawbot.config.validator.SQLITE_DB",  db), \
         patch("pawbot.config.validator.SOUL_MD",    "/nonexistent/SOUL.md"), \
         patch("pawbot.config.validator.CONFIG_FILE", cfg), \
         patch("pawbot.config.validator.CHROMA_DIR", str(tmp_path / "chroma")), \
         patch("pawbot.config.validator.config") as mock_cfg:

        mock_cfg.return_value.get.side_effect = lambda k, d=None: {
            "providers.anthropic.api_key": "sk-key",
            "routing.mechanical_to_local": False,
            "agents": [{"default": True}],
        }.get(k, d)

        result = v.validate()

    assert "soul_md" in [w.check for w in result.warnings]
    assert "soul_md" not in [e.check for e in result.errors]


def test_validation_result_properties():
    """ValidationResult.ok, .errors, .warnings work correctly."""
    from pawbot.config.validator import ValidationIssue

    r = ValidationResult()
    r.issues.append(ValidationIssue("ERROR", "c1", "msg", "fix"))
    r.issues.append(ValidationIssue("WARN",  "c2", "msg", "fix"))

    assert not r.ok
    assert len(r.errors)   == 1
    assert len(r.warnings) == 1


def test_print_report_does_not_crash(v, capsys):
    """print_report must not throw on any ValidationResult."""
    from pawbot.config.validator import ValidationIssue

    r = ValidationResult()
    r.issues.append(ValidationIssue("ERROR", "test", "Test error", "Do something"))

    v.print_report(r)  # must not raise

    captured = capsys.readouterr()
    assert "ERROR"        in captured.out
    assert "Do something" in captured.out
```

---

## ⛔ Acceptance Gate — Phase 5 (Section 1 Final Gate)
**ALL criteria must pass. This is the Section 1 gate.**

```bash
pytest tests/test_lane_queue.py \
       tests/test_compactor.py \
       tests/test_gateway.py \
       tests/test_agent_router.py \
       tests/test_validator.py \
       -v
```

- [ ] All **24 tests** pass across all 5 phases
- [ ] PHASE 1: `lane_queue.enqueue()` serializes same-session messages
- [ ] PHASE 1: Different sessions run concurrently
- [ ] PHASE 2: 200-message conversation does not crash with token overflow
- [ ] PHASE 2: `compactor` is called in `loop.py` — grep confirms
- [ ] PHASE 3: `curl http://localhost:8080/health` returns HTTP 200 + `{"status":"ok"}`
- [ ] PHASE 3: WebSocket connection accepts message and returns response
- [ ] PHASE 4: Telegram routes to personal agent, email routes to work agent
- [ ] PHASE 4: `session_id` is namespaced by agent prefix
- [ ] PHASE 5: Bad API key config → startup prints `ERROR [api_keys]` with fix instructions
- [ ] PHASE 5: Missing `SOUL.md` → startup prints `WARN [soul_md]`, does not block start
- [ ] COMBINED: `docker-compose up -d` starts cleanly (if Docker available)

---

**Section 1 is complete when all of the above are verified.**

Signal Section 2 and Section 5 agents that they can proceed.

> **Remember:** Every name in this document — every class, enum, constant, path, and
> config key — comes from `pawbot/contracts.py`. Read it first. Never invent new names.
> The single source of truth is the contract.

---

*End of Section 1 Fix Document — itsamiitt/pawbot — March 2026*
