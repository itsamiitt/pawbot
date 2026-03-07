# Phase 1 — Agent Loop Hardening

> **Goal:** Make the core agent loop production-reliable with proper retry, sandboxing, and isolation.  
> **Duration:** 7-10 days  
> **Risk Level:** Medium (behavioral changes, but behind feature flags)  
> **Depends On:** Phase 0 (clean imports, typed exceptions)

---

## Prerequisites

```bash
# Install new dependency
pip install "tenacity>=9.0.0"
```

Add to `pyproject.toml` dependencies:
```toml
"tenacity>=9.0.0",
```

---

## 1.1 — LLM Call Retry with Exponential Backoff

### Problem
`agent/loop.py` line 307 calls `self.provider.chat()` with zero retry and zero timeout. A hung API blocks the entire agent forever.

### Solution
Wrap LLM calls with `tenacity` retry. Create `pawbot/providers/resilience.py`:

```python
"""LLM call resilience — retry, timeout, circuit breaker."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Awaitable

from loguru import logger
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from pawbot.errors import ProviderError, ProviderUnavailableError


# Exceptions that should trigger retry
_RETRYABLE = (
    asyncio.TimeoutError,
    ConnectionError,
    ProviderError,
)

# Exceptions that should NOT retry (bad request, auth failure, etc.)
_NON_RETRYABLE = (
    ValueError,
    ProviderUnavailableError,
)


class ResilientLLMCaller:
    """Wraps LLM provider calls with retry, timeout, and metrics."""

    def __init__(
        self,
        max_retries: int = 3,
        timeout_seconds: float = 120.0,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
    ):
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._call_count = 0
        self._error_count = 0
        self._total_latency = 0.0

    async def call(self, provider_fn: Callable[..., Awaitable[Any]], **kwargs) -> Any:
        """Call an async LLM provider function with retry and timeout.
        
        Args:
            provider_fn: The async provider method to call (e.g., provider.chat)
            **kwargs: Arguments to pass to the provider function
            
        Returns:
            The provider response
            
        Raises:
            ProviderUnavailableError: If all retries are exhausted
            asyncio.TimeoutError: If the call exceeds timeout_seconds
        """
        last_error: Exception | None = None
        
        for attempt in range(1, self.max_retries + 1):
            self._call_count += 1
            start = time.monotonic()
            
            try:
                result = await asyncio.wait_for(
                    provider_fn(**kwargs),
                    timeout=self.timeout_seconds,
                )
                elapsed = time.monotonic() - start
                self._total_latency += elapsed
                
                if attempt > 1:
                    logger.info(
                        "LLM call succeeded on attempt {}/{} after {:.1f}s",
                        attempt, self.max_retries, elapsed,
                    )
                return result
                
            except _NON_RETRYABLE as e:
                # Don't retry bad requests or auth failures
                logger.error("LLM call failed (non-retryable): {}", e)
                raise
                
            except _RETRYABLE as e:
                last_error = e
                self._error_count += 1
                elapsed = time.monotonic() - start
                
                if attempt < self.max_retries:
                    delay = min(
                        self.base_delay * (2 ** (attempt - 1)),
                        self.max_delay,
                    )
                    logger.warning(
                        "LLM call failed (attempt {}/{}): {} — retrying in {:.1f}s",
                        attempt, self.max_retries, e, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "LLM call failed after {} attempts: {}",
                        self.max_retries, e,
                    )
                    
            except Exception as e:
                # Unexpected errors — log and raise without retry
                logger.error("LLM call failed with unexpected error: {}", e)
                raise

        raise ProviderUnavailableError(
            f"LLM provider unavailable after {self.max_retries} attempts. "
            f"Last error: {last_error}"
        )

    @property
    def stats(self) -> dict[str, Any]:
        """Return call statistics."""
        return {
            "total_calls": self._call_count,
            "total_errors": self._error_count,
            "avg_latency_ms": (
                (self._total_latency / max(self._call_count - self._error_count, 1)) * 1000
            ),
        }
```

### Integration with `AgentLoop.__init__`

In `pawbot/agent/loop.py`, add to `__init__`:

```python
# After line 131 (self._processing_lock = asyncio.Lock())
from pawbot.providers.resilience import ResilientLLMCaller
self._llm_caller = ResilientLLMCaller(
    max_retries=3,
    timeout_seconds=120.0,
    base_delay=1.0,
)
```

### Update `_run_agent_loop` (line 307)

Replace the bare provider.chat call:

```python
# BEFORE (line 307):
response = await self.provider.chat(
    messages=messages,
    tools=self.tools.get_definitions(),
    model=self.model,
    temperature=self.temperature,
    max_tokens=self.max_tokens,
    reasoning_effort=self.reasoning_effort,
)

# AFTER:
response = await self._llm_caller.call(
    self.provider.chat,
    messages=messages,
    tools=self.tools.get_definitions(),
    model=self.model,
    temperature=self.temperature,
    max_tokens=self.max_tokens,
    reasoning_effort=self.reasoning_effort,
)
```

---

## 1.2 — Tool Execution Timeout

### Problem
Tool execution in `_process_tool_calls` (line 249) has no timeout. A browser tool that hangs blocks the agent indefinitely.

### Solution
Add per-tool timeout to `ToolRegistry.execute()`.

In `pawbot/agent/tools/registry.py`, modify the `execute` method:

```python
async def execute(
    self,
    tool_name: str,
    arguments: dict,
    timeout: float = 60.0,
) -> str:
    """Execute a tool with timeout protection.
    
    Args:
        tool_name: Name of the tool to execute
        arguments: Tool arguments
        timeout: Maximum seconds to wait (default: 60s)
        
    Returns:
        Tool result as string
    """
    tool = self.get(tool_name)
    if tool is None:
        return f"Error: Unknown tool '{tool_name}'"

    try:
        result = await asyncio.wait_for(
            tool.run(**arguments),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        logger.warning("Tool '{}' timed out after {}s", tool_name, timeout)
        return f"Error: Tool '{tool_name}' timed out after {timeout}s"
    except Exception as e:
        logger.error("Tool '{}' failed: {}", tool_name, e)
        return f"Error: {e}"
```

### Tool-specific timeouts (add to config/schema.py):

```python
class ToolTimeouts(BaseModel):
    """Per-tool timeout overrides."""
    default: int = 60
    exec: int = 120       # shell commands may be slow
    web_search: int = 30
    web_fetch: int = 45
    browser: int = 120    # browser automation is slow
    read_file: int = 10
    write_file: int = 10
    list_dir: int = 10
```

---

## 1.3 — Tool Sandbox Improvements for `exec`

### Problem
`agent/tools/shell.py` blocks dangerous commands by keyword matching — trivially bypassable.

### Solution
In `pawbot/agent/tools/shell.py`, add a proper command validator:

```python
import re
import shlex

# Patterns that are ALWAYS blocked (even in non-restricted mode)
_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r'\brm\s+(-[rfR]+\s+)?/(?!\w)'),  # rm -rf /
    re.compile(r'\bfind\s+/\s+.*-delete\b'),       # find / -delete
    re.compile(r'\bmkfs\b'),                         # format disk
    re.compile(r'\bdd\s+.*of=/dev/'),                # dd to device
    re.compile(r'\b:(){ :\|:& };:\b'),               # fork bomb
    re.compile(r'>\s*/dev/sd[a-z]'),                  # overwrite disk
    re.compile(r'\bchmod\s+-R\s+777\s+/'),            # chmod 777 /
    re.compile(r'\bshutdown\b'),                       # system shutdown
    re.compile(r'\breboot\b'),                          # system reboot
    re.compile(r'\bcurl\b.*\|\s*(ba)?sh'),             # curl | bash
]


def validate_command(command: str, restrict_to_workspace: bool = False, workspace: str = "") -> tuple[bool, str]:
    """Validate a shell command before execution.
    
    Returns:
        (allowed, reason) — if not allowed, reason explains why.
    """
    cmd_lower = command.lower().strip()
    
    # Check blocked patterns
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(cmd_lower):
            return False, f"Blocked: command matches dangerous pattern '{pattern.pattern}'"
    
    # If restricted to workspace, verify all file paths
    if restrict_to_workspace and workspace:
        # Attempt to parse command for file arguments
        try:
            parts = shlex.split(command)
            for part in parts:
                if part.startswith('/') and not part.startswith(workspace):
                    return False, f"Blocked: path '{part}' is outside workspace '{workspace}'"
        except ValueError:
            pass  # shlex parse error — allow (might be piped/complex command)
    
    return True, ""
```

---

## 1.4 — Graceful Shutdown with Signal Handling

### Problem
`KeyboardInterrupt` in the agent loop leaves partial state, orphaned subagents, and unflushed memory.

### Solution
Add proper signal handling to `AgentLoop.run()`:

```python
# Add to AgentLoop class in loop.py

async def run(self) -> None:
    """Run the agent loop with proper signal handling."""
    self._running = True
    await self._connect_mcp()
    logger.info("Agent loop started")

    # Register shutdown handler
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self._graceful_shutdown()))
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for SIGTERM
            pass

    while self._running:
        try:
            msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break

        if msg.content.strip().lower() == "/stop":
            await self._handle_stop(msg)
        else:
            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(msg.session_key, []).append(task)
            task.add_done_callback(
                lambda t, k=msg.session_key: (
                    self._active_tasks.get(k, []).remove(t)
                    if t in self._active_tasks.get(k, [])
                    else None
                )
            )

async def _graceful_shutdown(self) -> None:
    """Graceful shutdown: drain queue, cancel tasks, flush state."""
    logger.info("Graceful shutdown initiated...")
    self._running = False

    # 1. Cancel all active tasks
    all_tasks = []
    for session_tasks in self._active_tasks.values():
        all_tasks.extend(session_tasks)
    
    for task in all_tasks:
        if not task.done():
            task.cancel()
    
    if all_tasks:
        await asyncio.gather(*all_tasks, return_exceptions=True)
        logger.info("Cancelled {} active tasks", len(all_tasks))

    # 2. Cancel subagents
    try:
        cancelled = await self.subagents.cancel_all()
        if cancelled:
            logger.info("Cancelled {} subagents", cancelled)
    except Exception:
        logger.exception("Error cancelling subagents during shutdown")

    # 3. Save all sessions
    try:
        self.sessions.save_all()
        logger.info("Sessions saved")
    except Exception:
        logger.exception("Error saving sessions during shutdown")

    # 4. Close MCP connections
    await self.close_mcp()

    logger.info("Graceful shutdown complete")
```

---

## 1.5 — Context Window Overflow Protection

### Problem
`ContextBudget` enforces a 1,800-token ceiling but never verifies against the model's actual context window.

### Solution

Create `pawbot/providers/context_limits.py`:

```python
"""Model context window limits — prevents overflow before LLM call."""

# Known context window sizes (in tokens)
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # Anthropic
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "o1": 200_000,
    "o3-mini": 200_000,
    # DeepSeek
    "deepseek-chat": 64_000,
    "deepseek-reasoner": 64_000,
    # Ollama (local)
    "llama3.1:8b": 8_192,
    "nomic-embed-text": 8_192,
    "deepseek-coder:6.7b": 16_384,
    # Gemini
    "gemini-2.0-flash": 1_048_576,
    "gemini-2.0-pro": 1_048_576,
}

# Default if model not in table
DEFAULT_CONTEXT_WINDOW = 32_000

# Safety margin — never use more than this % of the context window
SAFETY_MARGIN = 0.85


def get_context_limit(model: str) -> int:
    """Get the effective context limit for a model (with safety margin)."""
    # Try exact match first
    if model in MODEL_CONTEXT_WINDOWS:
        return int(MODEL_CONTEXT_WINDOWS[model] * SAFETY_MARGIN)
    
    # Try partial match (for prefixed models like "anthropic/claude-sonnet-4-5")
    for key, value in MODEL_CONTEXT_WINDOWS.items():
        if key in model:
            return int(value * SAFETY_MARGIN)
    
    return int(DEFAULT_CONTEXT_WINDOW * SAFETY_MARGIN)


def estimate_message_tokens(messages: list[dict]) -> int:
    """Rough token count for a message list (4 chars ≈ 1 token)."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(str(block.get("text", ""))) // 4
        # Tool calls add overhead
        if msg.get("tool_calls"):
            total += len(str(msg["tool_calls"])) // 4
    return total


def check_context_overflow(messages: list[dict], model: str) -> tuple[bool, int, int]:
    """Check if messages would overflow the model's context window.
    
    Returns:
        (is_overflow, estimated_tokens, context_limit)
    """
    limit = get_context_limit(model)
    estimated = estimate_message_tokens(messages)
    return estimated > limit, estimated, limit
```

### Integration point in `_run_agent_loop` (before the LLM call):

```python
# Add after line 305 (messages = await compactor.compact_if_needed(...))
from pawbot.providers.context_limits import check_context_overflow
is_overflow, est_tokens, ctx_limit = check_context_overflow(messages, self.model)
if is_overflow:
    logger.warning(
        "Context overflow detected: ~{} tokens > {} limit. Forcing compaction.",
        est_tokens, ctx_limit,
    )
    messages = await compactor.force_compact(messages, target_tokens=int(ctx_limit * 0.7))
```

---

## Verification Checklist — Phase 1 Complete

- [ ] `tenacity>=9.0.0` in `pyproject.toml` dependencies
- [ ] `pawbot/providers/resilience.py` exists with `ResilientLLMCaller`
- [ ] LLM calls in `_run_agent_loop` use `self._llm_caller.call()`
- [ ] Tool execution has per-tool timeouts (default 60s)
- [ ] `pawbot/agent/tools/shell.py` uses regex-based command validation
- [ ] Graceful shutdown handles SIGINT/SIGTERM
- [ ] Context overflow protection prevents token limit exceeded errors
- [ ] `pawbot/providers/context_limits.py` has model → context window mapping
- [ ] All tests pass: `pytest tests/ -v --tb=short`
- [ ] Agent survives `Ctrl+C` without leaving orphaned state
