# 🏗️ SECTION 4 — Providers & MCP Server Complexity
## Complete Agent Fix Document
### 5 Self-Contained Agent Prompts · Full Code · Tests · Acceptance Gates

**Repo:** `itsamiitt/pawbot` · **Date:** March 2026 · **Version:** 1.0  
**Source:** Deep Scan Report — Radon CC hotspots in providers/ and mcp-servers/

---

## ⚠️ CRITICAL RULE — READ BEFORE ANY PHASE

> Every class, enum, constant, dataclass, path, and config key used in this repo
> is defined in `pawbot/contracts.py`. Before writing any code in **any** phase,
> read `pawbot/contracts.py` in full.
>
> ```python
> from pawbot.contracts import *   # gives you everything
> ```
>
> Do **not** invent new names. Do **not** duplicate anything that already exists.

---

## What This Section Fixes

| # | File | Function | Radon CC | Problem | After Fix |
|---|------|----------|---------|---------|-----------|
| 1 | `providers/openai_codex_provider.py` | `_consume_sse` | **E (32)** | 8 `elif event_type ==` branches, impossible to test individual event handlers | Dispatch table, CC≤4 per handler |
| 2 | `config/schema.py` | `Config._match_provider` | **D (21)** | 3 resolution strategies interleaved: forced, prefix, keyword, fallback | 4 focused private methods |
| 3 | `mcp-servers/coding/server.py` | `code_search` | **D (27)** | 3 search modes (keyword, symbol, error) in one 70-line function | Mode dispatch, each handler ≤15 lines |
| 4 | `mcp-servers/coding/server.py` | `code_run_checks` | **D (23)** | Syntax+lint+typecheck+test interleaved across 2 file-type branches | Per-check helpers, per-type dispatcher |
| 5 | `mcp-servers/server_control/server.py` | `server_nginx` | **D (29)** | 7 `if action ==` branches, recursive self-calls, rollback logic buried | Action dispatch table + vhost helpers |

---

## Phase Execution Order

| Phase | Title | Can Start When | Blocks |
|-------|-------|---------------|--------|
| **1** | SSE Event Dispatch | Immediately — no deps | Phase 5 gate |
| **2** | Provider Matcher Split | Immediately — no deps | Phase 5 gate |
| **3** | Code Search Dispatch | Immediately — no deps | Phase 5 gate |
| **4** | Code Checks Helpers | Immediately — no deps | Phase 5 gate |
| **5** | Nginx Action Dispatch | Phases 1–4 complete | Section 4 gate |

Phases 1–4 are **fully independent** — run them simultaneously.

---

---

# PHASE 1 OF 5 — SSE Event Dispatch
### *Replace CC=32 elif chain in _consume_sse with a handler dispatch table*

---

## Agent Prompt

You are refactoring `_consume_sse()` in `pawbot/providers/openai_codex_provider.py`.

`_consume_sse()` has **CC=32 (grade E)**. It processes OpenAI Codex streaming responses
using 8 `elif event_type ==` branches inside an `async for` loop. Each branch mutates
shared state (`content`, `tool_call_buffers`, `tool_calls`, `finish_reason`).

The fix: extract each branch into a focused handler function and replace the elif chain
with a dispatch table. The outer loop becomes a 3-line dispatcher.

**Rules:**
- The return type `tuple[str, list[ToolCallRequest], str]` must not change
- Streaming behaviour must be identical — no events may be dropped or reordered
- `ToolCallRequest` is defined in `pawbot/providers/base.py` — do not redefine
- Read `pawbot/contracts.py` fully before editing

---

## Why This Phase Exists

CC=32 means there are 32 independent execution paths through this one async generator
consumer. When the Codex API changes an event shape, the developer must understand all
32 paths to safely modify the handler. Each extracted handler has CC≤4 and can be tested
with a simple dict — no live API call required.

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `pawbot/providers/openai_codex_provider.py` — replace `_consume_sse` with dispatch table |
| **CREATE** | `tests/test_sse_dispatch.py` — 7 tests |

---

## State Container

First, introduce a dataclass to hold mutable SSE state — eliminates the 4 shared
variables that made the original function hard to follow:

```python
from dataclasses import dataclass, field

@dataclass
class _SSEState:
    """
    Mutable accumulator for a single Codex streaming response.

    Passed by reference to each event handler so handlers can update
    state without returning values or accessing outer-scope variables.
    """
    content:           str                       = ""
    tool_call_buffers: dict[str, dict]           = field(default_factory=dict)
    tool_calls:        list["ToolCallRequest"]   = field(default_factory=list)
    finish_reason:     str                       = "stop"
```

---

## Handler Functions

```python
# ── SSE event handlers — one function per event type ──────────────────────────
# Each handler: (event_dict, state) → None
# CC ≤ 4 each. Independently testable with a plain dict.

def _sse_output_item_added(event: dict, state: "_SSEState") -> None:
    """Buffer a new tool call when it first appears in the stream."""
    item    = event.get("item") or {}
    call_id = item.get("call_id")
    if item.get("type") == "function_call" and call_id:
        state.tool_call_buffers[call_id] = {
            "id":        item.get("id") or "fc_0",
            "name":      item.get("name"),
            "arguments": item.get("arguments") or "",
        }


def _sse_output_text_delta(event: dict, state: "_SSEState") -> None:
    """Append streamed text delta to accumulated content."""
    state.content += event.get("delta") or ""


def _sse_fn_args_delta(event: dict, state: "_SSEState") -> None:
    """Append streamed argument fragment to the matching tool call buffer."""
    call_id = event.get("call_id")
    if call_id and call_id in state.tool_call_buffers:
        state.tool_call_buffers[call_id]["arguments"] += event.get("delta") or ""


def _sse_fn_args_done(event: dict, state: "_SSEState") -> None:
    """Finalise argument string for a tool call (done event overwrites delta buffer)."""
    call_id = event.get("call_id")
    if call_id and call_id in state.tool_call_buffers:
        state.tool_call_buffers[call_id]["arguments"] = event.get("arguments") or ""


def _sse_output_item_done(event: dict, state: "_SSEState") -> None:
    """Flush a completed tool call from buffer into the final list."""
    item    = event.get("item") or {}
    call_id = item.get("call_id")
    if item.get("type") != "function_call" or not call_id:
        return
    buf      = state.tool_call_buffers.get(call_id) or {}
    args_raw = buf.get("arguments") or item.get("arguments") or "{}"
    try:
        args = json.loads(args_raw)
    except Exception:
        args = {"raw": args_raw}
    state.tool_calls.append(
        ToolCallRequest(
            id        = f"{call_id}|{buf.get('id') or item.get('id') or 'fc_0'}",
            name      = buf.get("name") or item.get("name"),
            arguments = args,
        )
    )


def _sse_response_completed(event: dict, state: "_SSEState") -> None:
    """Map response status to a finish_reason string."""
    status             = (event.get("response") or {}).get("status")
    state.finish_reason = _map_finish_reason(status)


def _sse_error(event: dict, state: "_SSEState") -> None:
    """Raise on error or failed response events."""
    raise RuntimeError("Codex response failed")


# ── Dispatch table — maps event_type → handler ────────────────────────────────
# CC = 2 (one lookup + one call). Add a new event type by appending one line.
_SSE_HANDLERS: dict[str, "Callable[[dict, _SSEState], None]"] = {
    "response.output_item.added":          _sse_output_item_added,
    "response.output_text.delta":          _sse_output_text_delta,
    "response.function_call_arguments.delta": _sse_fn_args_delta,
    "response.function_call_arguments.done":  _sse_fn_args_done,
    "response.output_item.done":           _sse_output_item_done,
    "response.completed":                  _sse_response_completed,
    "error":                               _sse_error,
    "response.failed":                     _sse_error,
}
```

---

## Updated `_consume_sse()` — 7-line dispatcher

```python
async def _consume_sse(
    response: "httpx.Response",
) -> tuple[str, list["ToolCallRequest"], str]:
    """
    Consume an OpenAI Codex SSE stream and return (content, tool_calls, finish_reason).

    CC = 3 (was 32). Each event type is handled by a dedicated function in
    _SSE_HANDLERS. Unknown event types are silently ignored.
    """
    state = _SSEState()
    async for event in _iter_sse(response):
        handler = _SSE_HANDLERS.get(event.get("type", ""))
        if handler:
            handler(event, state)
    return state.content, state.tool_calls, state.finish_reason
```

---

## File — CREATE `tests/test_sse_dispatch.py`

```python
"""
tests/test_sse_dispatch.py

Tests for the refactored Codex SSE event dispatch table.
Each test exercises a single handler in complete isolation — no HTTP call needed.

Run: pytest tests/test_sse_dispatch.py -v
"""

import json
import pytest

from pawbot.providers.openai_codex_provider import (
    _SSEState,
    _SSE_HANDLERS,
    _sse_output_item_added,
    _sse_output_text_delta,
    _sse_fn_args_delta,
    _sse_fn_args_done,
    _sse_output_item_done,
    _sse_response_completed,
    _sse_error,
)
from pawbot.providers.base import ToolCallRequest


def test_text_delta_appends_to_content():
    """response.output_text.delta must append to state.content."""
    state = _SSEState()
    _sse_output_text_delta({"type": "response.output_text.delta", "delta": "Hello "}, state)
    _sse_output_text_delta({"type": "response.output_text.delta", "delta": "world"}, state)
    assert state.content == "Hello world"


def test_output_item_added_buffers_tool_call():
    """response.output_item.added must buffer a function_call into tool_call_buffers."""
    state = _SSEState()
    _sse_output_item_added({
        "type": "response.output_item.added",
        "item": {"type": "function_call", "call_id": "call_abc", "id": "fc_1", "name": "shell"}
    }, state)
    assert "call_abc" in state.tool_call_buffers
    assert state.tool_call_buffers["call_abc"]["name"] == "shell"


def test_fn_args_delta_appends_argument_fragment():
    """function_call_arguments.delta must append to the existing buffer."""
    state = _SSEState()
    state.tool_call_buffers["call_xyz"] = {"id": "fc_2", "name": "web_search", "arguments": ""}
    _sse_fn_args_delta({"call_id": "call_xyz", "delta": '{"q":'}, state)
    _sse_fn_args_delta({"call_id": "call_xyz", "delta": '"pawbot"}'}, state)
    assert state.tool_call_buffers["call_xyz"]["arguments"] == '{"q":"pawbot"}'


def test_fn_args_done_overwrites_delta_buffer():
    """function_call_arguments.done must overwrite any partial delta with the final value."""
    state = _SSEState()
    state.tool_call_buffers["call_done"] = {"id": "fc_3", "name": "read_file", "arguments": '{"pa'}
    _sse_fn_args_done({"call_id": "call_done", "arguments": '{"path": "/tmp/x.txt"}'}, state)
    assert state.tool_call_buffers["call_done"]["arguments"] == '{"path": "/tmp/x.txt"}'


def test_output_item_done_flushes_tool_call():
    """response.output_item.done must move a buffered call to state.tool_calls."""
    state = _SSEState()
    state.tool_call_buffers["call_flush"] = {
        "id":        "fc_4",
        "name":      "shell",
        "arguments": '{"cmd": "ls /"}',
    }
    _sse_output_item_done({
        "type": "response.output_item.done",
        "item": {"type": "function_call", "call_id": "call_flush", "id": "fc_4"}
    }, state)
    assert len(state.tool_calls) == 1
    assert state.tool_calls[0].name == "shell"
    assert state.tool_calls[0].arguments == {"cmd": "ls /"}


def test_response_completed_sets_finish_reason():
    """response.completed must map the status to a finish_reason."""
    state = _SSEState()
    _sse_response_completed({"response": {"status": "completed"}}, state)
    assert state.finish_reason == "stop"

    state2 = _SSEState()
    _sse_response_completed({"response": {"status": "incomplete"}}, state2)
    assert state2.finish_reason == "length"


def test_error_event_raises():
    """error and response.failed events must raise RuntimeError."""
    state = _SSEState()
    with pytest.raises(RuntimeError, match="Codex response failed"):
        _sse_error({"type": "error"}, state)


def test_dispatch_table_is_complete():
    """
    All 8 expected event types must be in _SSE_HANDLERS and map to callables.
    Guards against accidentally removing a handler.
    """
    required = {
        "response.output_item.added",
        "response.output_text.delta",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.output_item.done",
        "response.completed",
        "error",
        "response.failed",
    }
    missing = required - set(_SSE_HANDLERS.keys())
    assert not missing, f"Missing handlers in _SSE_HANDLERS: {missing}"
    for key, fn in _SSE_HANDLERS.items():
        assert callable(fn), f"Handler for '{key}' is not callable"
```

---

## Test Matrix

| # | Test | Handler | Expected | Pass Condition |
|---|------|---------|----------|----------------|
| T1 | Text delta appends | `_sse_output_text_delta` | `state.content == "Hello world"` | Two deltas concatenated |
| T2 | Item added buffers | `_sse_output_item_added` | Buffer has `call_abc` | Dict key present |
| T3 | Args delta appends | `_sse_fn_args_delta` | Fragment concatenated | Buffer updated |
| T4 | Args done overwrites | `_sse_fn_args_done` | Partial replaced by final | Exact string match |
| T5 | Item done flushes | `_sse_output_item_done` | `tool_calls` has 1 item | `ToolCallRequest` created |
| T6 | Completed sets reason | `_sse_response_completed` | `finish_reason == "stop"` | Mapped correctly |
| T7 | Error raises | `_sse_error` | `RuntimeError` | Exception raised |
| T8 | Dispatch table complete | `_SSE_HANDLERS` | 8 keys, all callable | Set equality + callable check |

---

## ⛔ Acceptance Gate — Phase 1

```bash
pytest tests/test_sse_dispatch.py -v
```

- [ ] All 8 tests pass
- [ ] `grep -c "elif event_type" pawbot/providers/openai_codex_provider.py` → **0**
- [ ] `_consume_sse` function body is ≤ 10 lines
- [ ] Existing `tests/test_model_router.py` still passes

---

---

# PHASE 2 OF 5 — Provider Matcher Split
### *Split CC=21 _match_provider into 4 focused strategy methods*

---

## Agent Prompt

You are refactoring `Config._match_provider()` in `pawbot/config/schema.py`.

`_match_provider()` has **CC=21**. It implements 4 distinct resolution strategies in one
method: forced provider, exact prefix match, keyword match, and fallback. These strategies
are interleaved with a shared `_kw_matches` closure, making each strategy hard to test.

The fix: extract each strategy into its own private method. `_match_provider()` becomes
a thin orchestrator that tries each strategy in order.

**Rules:**
- The return type `tuple[ProviderConfig | None, str | None]` must not change
- `get_provider()` and `get_provider_name()` must work identically after refactor
- `PROVIDERS` is imported from `pawbot.providers.registry` — do not redefine
- Read `pawbot/contracts.py` fully before editing

---

## Why This Phase Exists

`_match_provider()` is called on every LLM request. It selects the API key and provider
name that determines which model is used. A bug here silently routes all requests to the
wrong provider. With CC=21 and 4 interleaved strategies, each test must set up the full
Config object. After extraction, each strategy is testable with a 5-line mock.

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `pawbot/config/schema.py` — extract 4 strategy methods from `_match_provider` |
| **CREATE** | `tests/test_provider_matcher.py` — 6 tests |

---

## Extracted Strategy 1 — `_match_forced_provider()`

```python
def _match_forced_provider(self) -> "tuple[ProviderConfig | None, str | None]":
    """
    Strategy 1: Explicit provider override in config.

    If agents.defaults.provider != 'auto', use it directly.
    Returns (config, name) if the forced provider exists, else (None, None).

    Extracted from _match_provider to reduce CC from 21.
    CC = 2.
    """
    from pawbot.providers.registry import PROVIDERS

    forced = self.agents.defaults.provider
    if forced == "auto":
        return None, None   # Not forced — caller tries other strategies

    p = getattr(self.providers, forced, None)
    return (p, forced) if p else (None, None)
```

---

## Extracted Strategy 2 — `_match_by_prefix()`

```python
def _match_by_prefix(self, model_lower: str) -> "tuple[ProviderConfig | None, str | None]":
    """
    Strategy 2: Explicit provider prefix in the model string.

    'github-copilot/gpt-4o' has prefix 'github-copilot'. This prevents
    a model like 'anthropic/claude-codex' from matching the OpenAI keyword.

    Returns (config, name) on match, (None, None) if no prefix match found.

    Extracted from _match_provider to reduce CC from 21.
    CC = 3.
    """
    from pawbot.providers.registry import PROVIDERS

    if "/" not in model_lower:
        return None, None

    model_prefix       = model_lower.split("/", 1)[0]
    normalized_prefix  = model_prefix.replace("-", "_")

    for spec in PROVIDERS:
        if normalized_prefix != spec.name:
            continue
        p = getattr(self.providers, spec.name, None)
        if p and (spec.is_oauth or p.api_key):
            return p, spec.name

    return None, None
```

---

## Extracted Strategy 3 — `_match_by_keyword()`

```python
def _match_by_keyword(self, model_lower: str) -> "tuple[ProviderConfig | None, str | None]":
    """
    Strategy 3: Match by provider keyword.

    Each provider spec has a list of keywords (e.g. 'anthropic' → ['claude', 'haiku']).
    Iterates PROVIDERS in registry order (priority order) and returns the first match
    where the provider has a valid API key.

    Extracted from _match_provider to reduce CC from 21.
    CC = 4.
    """
    from pawbot.providers.registry import PROVIDERS

    model_normalized = model_lower.replace("-", "_")

    def _kw_matches(kw: str) -> bool:
        kw = kw.lower()
        return kw in model_lower or kw.replace("-", "_") in model_normalized

    for spec in PROVIDERS:
        p = getattr(self.providers, spec.name, None)
        if p and any(_kw_matches(kw) for kw in spec.keywords):
            if spec.is_oauth or p.api_key:
                return p, spec.name

    return None, None
```

---

## Extracted Strategy 4 — `_match_by_fallback()`

```python
def _match_by_fallback(self) -> "tuple[ProviderConfig | None, str | None]":
    """
    Strategy 4: Last-resort fallback.

    Returns the first non-OAuth provider that has an API key, in PROVIDERS
    registry order (lower index = higher priority). OAuth providers are never
    returned as fallbacks — they require explicit model selection.

    Extracted from _match_provider to reduce CC from 21.
    CC = 3.
    """
    from pawbot.providers.registry import PROVIDERS

    for spec in PROVIDERS:
        if spec.is_oauth:
            continue
        p = getattr(self.providers, spec.name, None)
        if p and p.api_key:
            return p, spec.name

    return None, None
```

---

## Updated `_match_provider()` — thin orchestrator

```python
def _match_provider(
    self,
    model: str | None = None,
) -> "tuple[ProviderConfig | None, str | None]":
    """
    Match provider config and its registry name for the given model string.

    Tries 4 strategies in priority order:
    1. Forced provider override (agents.defaults.provider != 'auto')
    2. Explicit provider prefix in model string ('github-copilot/...')
    3. Keyword match against provider spec keywords
    4. Fallback to first available non-OAuth provider

    Returns (ProviderConfig, registry_name) or (None, None) if nothing matches.
    CC = 4 (was 21).
    """
    model_lower = (model or self.agents.defaults.model).lower()

    # Strategy 1: explicit override
    p, name = self._match_forced_provider()
    if p:
        return p, name

    # Strategy 2: provider prefix in model string
    p, name = self._match_by_prefix(model_lower)
    if p:
        return p, name

    # Strategy 3: keyword match (registry priority order)
    p, name = self._match_by_keyword(model_lower)
    if p:
        return p, name

    # Strategy 4: any provider with a key (last resort)
    return self._match_by_fallback()
```

---

## File — CREATE `tests/test_provider_matcher.py`

```python
"""
tests/test_provider_matcher.py

Tests for the refactored Config._match_provider strategies.
Each strategy is tested in isolation using a minimal Config mock.

Run: pytest tests/test_provider_matcher.py -v
"""

import pytest
from unittest.mock import MagicMock, patch

from pawbot.config.schema import Config


def _make_config(
    forced_provider: str = "auto",
    openrouter_key: str  = "",
    anthropic_key: str   = "",
    model: str           = "anthropic/claude-sonnet-4-6",
) -> Config:
    """Build a minimal Config mock for testing _match_provider strategies."""
    config = object.__new__(Config)

    config.agents             = MagicMock()
    config.agents.defaults.provider = forced_provider
    config.agents.defaults.model    = model

    # Provider configs
    config.providers          = MagicMock()
    config.providers.openrouter.api_key = openrouter_key
    config.providers.anthropic.api_key  = anthropic_key
    config.providers.ollama.api_key     = ""

    return config


def test_forced_provider_wins_over_all_strategies():
    """
    When agents.defaults.provider is not 'auto', that provider must be used
    regardless of model string — prefix and keyword strategies must not run.
    """
    config = _make_config(forced_provider="openrouter", openrouter_key="sk-or-test")

    mock_spec = MagicMock()
    mock_spec.name     = "openrouter"
    mock_spec.is_oauth = False

    with patch("pawbot.providers.registry.PROVIDERS", [mock_spec]):
        p, name = config._match_forced_provider()

    assert name == "openrouter"
    assert p is config.providers.openrouter


def test_forced_auto_returns_none():
    """When provider == 'auto', _match_forced_provider must return (None, None)."""
    config   = _make_config(forced_provider="auto")
    p, name  = config._match_forced_provider()
    assert p is None
    assert name is None


def test_prefix_match_wins_for_prefixed_model():
    """
    'github-copilot/gpt-4o' has prefix 'github-copilot'.
    _match_by_prefix must return the github-copilot provider.
    """
    config = _make_config()

    mock_spec          = MagicMock()
    mock_spec.name     = "github_copilot"
    mock_spec.is_oauth = True  # OAuth — no api_key required
    mock_spec.keywords = ["copilot"]

    with patch("pawbot.providers.registry.PROVIDERS", [mock_spec]):
        p, name = config._match_by_prefix("github-copilot/gpt-4o")

    assert name == "github_copilot"


def test_prefix_match_skips_non_prefixed_model():
    """Model without '/' must return (None, None) from _match_by_prefix."""
    config  = _make_config()
    p, name = config._match_by_prefix("claude-sonnet-4-6")
    assert p is None
    assert name is None


def test_keyword_match_selects_correct_provider():
    """
    'anthropic/claude-sonnet-4-6' contains 'claude'.
    _match_by_keyword must match the anthropic provider.
    """
    config                              = _make_config(anthropic_key="sk-ant-test")

    mock_anthropic          = MagicMock()
    mock_anthropic.name     = "anthropic"
    mock_anthropic.is_oauth = False
    mock_anthropic.keywords = ["claude", "haiku", "sonnet"]

    with patch("pawbot.providers.registry.PROVIDERS", [mock_anthropic]):
        p, name = config._match_by_keyword("anthropic/claude-sonnet-4-6")

    assert name == "anthropic"


def test_fallback_skips_oauth_providers():
    """
    _match_by_fallback must skip OAuth providers (is_oauth=True).
    OAuth providers require explicit model selection — they are never fallbacks.
    """
    config = _make_config(openrouter_key="sk-or-key")

    oauth_spec          = MagicMock()
    oauth_spec.name     = "github_copilot"
    oauth_spec.is_oauth = True

    real_spec           = MagicMock()
    real_spec.name      = "openrouter"
    real_spec.is_oauth  = False

    with patch("pawbot.providers.registry.PROVIDERS", [oauth_spec, real_spec]):
        p, name = config._match_by_fallback()

    assert name == "openrouter", "OAuth provider must be skipped in fallback"
```

---

## Test Matrix

| # | Test | Strategy | Input | Expected | Pass Condition |
|---|------|---------|-------|----------|----------------|
| T1 | Forced wins | `_match_forced_provider` | `provider="openrouter"` | `name == "openrouter"` | Provider returned |
| T2 | Auto → None | `_match_forced_provider` | `provider="auto"` | `(None, None)` | No-op |
| T3 | Prefix matches | `_match_by_prefix` | `"github-copilot/gpt-4o"` | `name == "github_copilot"` | Prefix extracted |
| T4 | No prefix → None | `_match_by_prefix` | `"claude-sonnet"` | `(None, None)` | No slash = no match |
| T5 | Keyword matches | `_match_by_keyword` | `"anthropic/claude-sonnet"` | `name == "anthropic"` | Keyword found |
| T6 | Fallback skips OAuth | `_match_by_fallback` | OAuth first, openrouter second | `name == "openrouter"` | OAuth skipped |

---

## ⛔ Acceptance Gate — Phase 2

```bash
pytest tests/test_provider_matcher.py -v
```

- [ ] All 6 tests pass
- [ ] `_match_provider()` body is ≤ 20 lines
- [ ] `grep -n "def _kw_matches" pawbot/config/schema.py` → located inside `_match_by_keyword` only (not in `_match_provider`)
- [ ] Existing `tests/test_model_router.py` still passes — all provider routing tests unaffected

---

---

# PHASE 3 OF 5 — Code Search Dispatch
### *Split CC=27 code_search into 3 mode handlers with a dispatcher*

---

## Agent Prompt

You are refactoring `code_search()` in `mcp-servers/coding/server.py`.

`code_search()` has **CC=27 (grade D)**. It handles three completely different search
modes (`keyword`, `symbol`, `error`) in a single 70-line function. The keyword mode
uses `ripgrep` with a pure-Python fallback. The symbol mode queries a SQLite index.
The error mode walks the filesystem looking for log files. These three are entirely
separate algorithms that share no code.

The fix: extract each mode into a dedicated private function, and replace the if/elif
chain with a mode dispatch table.

**Rules:**
- The tool's return type `dict[str, Any]` must not change
- The MCP tool decorator `@mcp.tool()` stays on `code_search()` — do not move it
- `_run_subprocess` and `_search_keyword_fallback` already exist — reuse them
- Read `pawbot/contracts.py` fully before editing

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `mcp-servers/coding/server.py` — extract 3 mode functions from `code_search` |
| **CREATE** | `tests/test_code_search_modes.py` — 5 tests |

---

## Extracted Mode 1 — `_search_keyword_mode()`

```python
def _search_keyword_mode(query: str, root: str) -> list[dict[str, Any]]:
    """
    Keyword/semantic search via ripgrep with fallback to pure-Python grep.

    Returns a list of match dicts with keys: file_path, line_number, snippet, match_type.
    Extracted from code_search to reduce CC from 27.
    CC = 3.
    """
    rg_cmd = [
        "rg", "-n",
        "--glob", "*.py", "--glob", "*.js",
        "--glob", "*.ts", "--glob", "*.jsx", "--glob", "*.tsx",
        query, root,
    ]
    rg = _run_subprocess(rg_cmd, timeout=30)

    if rg.get("ok"):
        results = []
        for line in (rg.get("stdout", "") or "").splitlines()[:40]:
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            results.append({
                "file_path":   parts[0],
                "line_number": int(parts[1]) if parts[1].isdigit() else 0,
                "snippet":     parts[2][:200],
                "match_type":  "keyword",
            })
        return results

    # ripgrep not available or failed — use pure-Python fallback
    return _search_keyword_fallback(query, root)
```

---

## Extracted Mode 2 — `_search_symbol_mode()`

```python
def _search_symbol_mode(query: str, root: str) -> list[dict[str, Any]] | dict[str, Any]:
    """
    Symbol search against the project SQLite index.

    Returns a list of match dicts, or a dict with 'error' if no index exists.
    The caller must check for the error case.

    Extracted from code_search to reduce CC from 27.
    CC = 2.
    """
    db_path = os.path.join(INDEX_DIR, f"{_get_project_hash(root)}.db")
    if not os.path.exists(db_path):
        return {"error": f"No index for {root}. Run code_index_project first."}

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT file_path, symbol_type, symbol_name, line_number "
            "FROM symbols WHERE symbol_name LIKE ? LIMIT 20",
            (f"%{query}%",),
        ).fetchall()

    return [
        {
            "file_path":   row[0],
            "line_number": row[3],
            "snippet":     f"{row[1]}: {row[2]}",
            "match_type":  "symbol",
        }
        for row in rows
    ]
```

---

## Extracted Mode 3 — `_search_error_mode()`

```python
def _search_error_mode(query: str, root: str) -> list[dict[str, Any]]:
    """
    Error pattern search across .log and .txt files in the project tree.

    Walks the project directory and matches error-related patterns.
    Extracted from code_search to reduce CC from 27.
    CC = 5.
    """
    patterns    = [query, "Error:", "Exception:", "Traceback", "FAILED"]
    seen_files: set[str] = set()
    results: list[dict[str, Any]] = []

    for pattern in patterns[:2]:
        for root_dir, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if Path(name).suffix.lower() not in {".log", ".txt"}:
                    continue
                path = os.path.join(root_dir, name)
                if path in seen_files:
                    continue
                seen_files.add(path)
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if pattern.lower() in line.lower():
                                results.append({
                                    "file_path":   path,
                                    "line_number": lineno,
                                    "snippet":     line.strip()[:200],
                                    "match_type":  "error",
                                })
                                if len(results) >= 20:
                                    return results
                except OSError:
                    continue

    return results


# ── Mode dispatch table ────────────────────────────────────────────────────────
# To add a new search mode, append one entry here.
_SEARCH_MODE_ALIASES: dict[str, str] = {
    "semantic": "keyword",   # semantic is an alias for keyword
}

_SEARCH_MODES: dict[str, "Callable[[str, str], list | dict]"] = {
    "keyword": _search_keyword_mode,
    "symbol":  _search_symbol_mode,
    "error":   _search_error_mode,
}
```

---

## Updated `code_search()` — thin dispatcher

```python
@mcp.tool()
def code_search(query: str, project_path: str, search_type: str = "keyword") -> dict[str, Any]:
    """
    Search code by keyword/symbol/error with indexed and fallback methods.
    CC = 3 (was 27). Each mode is handled by a dedicated function.
    """
    root = os.path.abspath(os.path.expanduser(project_path))
    if not os.path.exists(root):
        return {"error": f"Project path not found: {root}"}
    if not query.strip():
        return {"error": "query is required"}

    mode    = _SEARCH_MODE_ALIASES.get(search_type.lower().strip(), search_type.lower().strip())
    handler = _SEARCH_MODES.get(mode)
    if not handler:
        return {"error": f"Unknown search_type: {search_type!r}. Use: {list(_SEARCH_MODES)}"}

    results = handler(query, root)

    # Symbol mode returns an error dict on missing index — pass through
    if isinstance(results, dict):
        return results

    return {"results": results, "count": len(results), "mode": mode}
```

---

## File — CREATE `tests/test_code_search_modes.py`

```python
"""
tests/test_code_search_modes.py

Tests for the refactored code_search mode handlers.
Run: pytest tests/test_code_search_modes.py -v
"""

import os
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-servers" / "coding"))

from server import (
    _search_keyword_mode,
    _search_symbol_mode,
    _search_error_mode,
    _SEARCH_MODES,
    _SEARCH_MODE_ALIASES,
)


def test_keyword_mode_parses_rg_output(tmp_path):
    """_search_keyword_mode must parse ripgrep output into result dicts."""
    fake_stdout = (
        f"{tmp_path}/main.py:42:    result = do_search(query)\n"
        f"{tmp_path}/utils.py:10:def do_search(q):\n"
    )
    with patch("server._run_subprocess", return_value={"ok": True, "stdout": fake_stdout}):
        results = _search_keyword_mode("do_search", str(tmp_path))

    assert len(results) == 2
    assert results[0]["line_number"] == 42
    assert results[0]["match_type"] == "keyword"
    assert "do_search" in results[0]["snippet"]


def test_keyword_mode_falls_back_when_rg_fails(tmp_path):
    """When ripgrep fails, _search_keyword_mode must call _search_keyword_fallback."""
    with patch("server._run_subprocess", return_value={"ok": False, "stderr": "rg not found"}), \
         patch("server._search_keyword_fallback", return_value=[]) as mock_fallback:
        _search_keyword_mode("query", str(tmp_path))
        mock_fallback.assert_called_once_with("query", str(tmp_path))


def test_symbol_mode_returns_error_when_no_index(tmp_path):
    """_search_symbol_mode must return {'error': ...} when no index exists."""
    with patch("server.INDEX_DIR", str(tmp_path)):
        result = _search_symbol_mode("my_function", str(tmp_path / "project"))
    assert "error" in result
    assert "code_index_project" in result["error"]


def test_symbol_mode_queries_index(tmp_path):
    """_search_symbol_mode must return results from the SQLite index."""
    # Create a minimal index DB
    import hashlib
    project_path = str(tmp_path / "project")
    os.makedirs(project_path, exist_ok=True)
    project_hash = hashlib.sha256(project_path.encode()).hexdigest()[:12]
    db_path      = tmp_path / f"{project_hash}.db"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE symbols (file_path TEXT, symbol_type TEXT, symbol_name TEXT, line_number INTEGER)"
        )
        conn.execute(
            "INSERT INTO symbols VALUES (?, ?, ?, ?)",
            ("main.py", "function", "my_function", 15)
        )

    with patch("server.INDEX_DIR", str(tmp_path)):
        results = _search_symbol_mode("my_function", project_path)

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0]["line_number"] == 15
    assert results[0]["match_type"] == "symbol"


def test_error_mode_finds_log_entries(tmp_path):
    """_search_error_mode must find matching lines in .log files."""
    log_file = tmp_path / "app.log"
    log_file.write_text(
        "2026-03-01 normal log line\n"
        "2026-03-02 ERROR: connection refused\n"
        "2026-03-03 another normal line\n",
        encoding="utf-8"
    )
    results = _search_error_mode("connection refused", str(tmp_path))
    assert len(results) >= 1
    assert "connection refused" in results[0]["snippet"].lower()
    assert results[0]["match_type"] == "error"


def test_semantic_is_alias_for_keyword():
    """'semantic' mode must be resolved to 'keyword' via the alias table."""
    assert _SEARCH_MODE_ALIASES.get("semantic") == "keyword"


def test_search_modes_complete():
    """All three search modes must be registered and callable."""
    for mode in ("keyword", "symbol", "error"):
        assert mode in _SEARCH_MODES, f"Mode '{mode}' missing from _SEARCH_MODES"
        assert callable(_SEARCH_MODES[mode]), f"Handler for '{mode}' is not callable"
```

---

## Test Matrix

| # | Test | Mode | Input | Expected | Pass Condition |
|---|------|------|-------|----------|----------------|
| T1 | Keyword parses rg output | `keyword` | Fake rg stdout | 2 results with correct fields | Dict keys present |
| T2 | Keyword fallback on rg fail | `keyword` | `ok=False` | `_search_keyword_fallback` called | Mock assertion |
| T3 | Symbol no-index error | `symbol` | No DB file | `{"error": ...}` | Error dict returned |
| T4 | Symbol queries index | `symbol` | DB with 1 row | 1 result, correct fields | Query result |
| T5 | Error finds log entries | `error` | Log file with match | Result with snippet | Match found |
| T6 | Semantic alias | alias table | `"semantic"` | `"keyword"` | Alias resolves |
| T7 | Modes complete | `_SEARCH_MODES` | All 3 modes | All registered | Set membership |

---

## ⛔ Acceptance Gate — Phase 3

```bash
pytest tests/test_code_search_modes.py -v
```

- [ ] All 7 tests pass
- [ ] `grep -c "elif mode ==" mcp-servers/coding/server.py` → **0**
- [ ] `code_search()` body is ≤ 15 lines after refactor
- [ ] Existing `python -m pytest tests/ -q` passes with no regressions

---

---

# PHASE 4 OF 5 — Code Checks Helpers
### *Split CC=23 code_run_checks into per-check helpers and per-type dispatcher*

---

## Agent Prompt

You are refactoring `code_run_checks()` in `mcp-servers/coding/server.py`.

`code_run_checks()` has **CC=23 (grade D)**. It runs 4 different checks (syntax, lint,
typecheck, related tests) across 2 file types (`.py`, `.js/.ts`). These are interleaved
with `if ext == ".py"` and `elif ext in {".js", ...}` branches inside the same function.

The fix: extract each check into a helper and dispatch by file extension.

**Rules:**
- The `@mcp.tool()` decorator stays on `code_run_checks()`
- The return structure `{passed, checks, file}` must not change
- `_run_subprocess` is the only subprocess wrapper — reuse it
- Read `pawbot/contracts.py` fully before editing

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `mcp-servers/coding/server.py` — extract 4 check helpers |
| **CREATE** | `tests/test_code_checks.py` — 5 tests |

---

## Extracted Check 1 — `_check_lint_python()`

```python
def _check_lint_python(path: str) -> dict[str, Any]:
    """Run ruff lint on a Python file. Returns a check result dict."""
    result = _run_subprocess(["ruff", "check", path], timeout=30)
    if result.get("error") and "Command not found" in result["error"]:
        return {"ok": True, "skipped": True, "reason": result["error"]}
    return {
        "ok":     bool(result.get("ok")),
        "output": _truncate((result.get("stdout", "") + result.get("stderr", "")).strip(), 1000),
    }
```

---

## Extracted Check 2 — `_check_lint_js()`

```python
def _check_lint_js(path: str, root: str) -> dict[str, Any]:
    """
    Run eslint on a JS/TS file if an eslint config exists in the project root.
    Returns a check result dict, or a skipped dict if no eslint config found.
    """
    has_eslint = any(
        os.path.exists(os.path.join(root, name))
        for name in [".eslintrc", ".eslintrc.js", ".eslintrc.json"]
    )
    if not has_eslint:
        return {"ok": True, "skipped": True, "reason": "No eslint config found"}

    result = _run_subprocess(["eslint", path], timeout=30)
    if result.get("error") and "Command not found" in result["error"]:
        return {"ok": True, "skipped": True, "reason": result["error"]}
    return {
        "ok":     bool(result.get("ok")),
        "output": _truncate((result.get("stdout", "") + result.get("stderr", "")).strip(), 1000),
    }
```

---

## Extracted Check 3 — `_check_typecheck_python()`

```python
def _check_typecheck_python(path: str, root: str) -> dict[str, Any] | None:
    """
    Run mypy typecheck on a Python file if mypy config exists.
    Returns None if no mypy config — caller skips the check entirely.
    """
    has_mypy = any(
        os.path.exists(os.path.join(root, name))
        for name in ["mypy.ini", "setup.cfg", "pyproject.toml"]
    )
    if not has_mypy:
        return None

    result = _run_subprocess(["mypy", path], timeout=60)
    if result.get("error") and "Command not found" in result["error"]:
        return {"ok": True, "skipped": True, "reason": result["error"]}
    return {
        "ok":     bool(result.get("ok")),
        "output": _truncate((result.get("stdout", "") + result.get("stderr", "")).strip(), 1000),
    }
```

---

## Extracted Check 4 — `_find_related_tests()`

```python
def _find_related_tests(path: str, root: str) -> list[str]:
    """
    Find test files related to the given source file by convention.
    Returns list of existing test file paths (may be empty).
    """
    stem     = Path(path).stem
    patterns = [
        os.path.join(root, "tests",   f"test_{stem}.py"),
        os.path.join(root,             f"test_{stem}.py"),
        os.path.join(root,             f"{stem}.test.ts"),
        os.path.join(root,             f"{stem}.test.js"),
        os.path.join(root,             f"{stem}.spec.ts"),
    ]
    return [p for p in patterns if os.path.exists(p)]
```

---

## Updated `code_run_checks()` — thin dispatcher

```python
@mcp.tool()
def code_run_checks(file_path: str, project_path: str = "") -> dict[str, Any]:
    """
    Run syntax, lint, optional typecheck, and related tests on a source file.
    CC = 5 (was 23). Each check is handled by a dedicated helper.
    """
    path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}

    root = os.path.abspath(os.path.expanduser(project_path)) if project_path else str(Path(path).parent)
    ext  = Path(path).suffix.lower()

    checks: dict[str, dict] = {}

    # 1. Syntax (all file types)
    syntax = _syntax_check(path)
    checks["syntax"] = syntax
    if not syntax.get("ok"):
        return {"passed": False, "checks": checks, "halted_at": "syntax", "file": path}

    # 2. Lint (per file type)
    if ext == ".py":
        checks["lint"] = _check_lint_python(path)
    elif ext in {".js", ".jsx", ".ts", ".tsx"}:
        checks["lint"] = _check_lint_js(path, root)

    # 3. Typecheck (Python only, if config present)
    if ext == ".py":
        tc = _check_typecheck_python(path, root)
        if tc is not None:
            checks["typecheck"] = tc

    # 4. Related tests
    test_files = _find_related_tests(path, root)
    if test_files:
        test_cmd = ["python", "-m", "pytest"] + test_files + ["-x", "-q"]
        test_result = _run_subprocess(test_cmd, cwd=root, timeout=120)
        checks["tests"] = {
            "ok":        bool(test_result.get("ok")),
            "files":     test_files,
            "output":    _truncate((test_result.get("stdout", "") + test_result.get("stderr", "")).strip(), 2000),
        }

    passed = all(c.get("ok", True) for c in checks.values())
    return {"passed": passed, "checks": checks, "file": path}
```

---

## File — CREATE `tests/test_code_checks.py`

```python
"""
tests/test_code_checks.py

Tests for the extracted code check helpers.
Run: pytest tests/test_code_checks.py -v
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-servers" / "coding"))

from server import (
    _check_lint_python,
    _check_lint_js,
    _check_typecheck_python,
    _find_related_tests,
)


def test_lint_python_passes_clean_file(tmp_path):
    """_check_lint_python must return ok=True when ruff finds no issues."""
    with patch("server._run_subprocess", return_value={"ok": True, "stdout": "", "stderr": ""}):
        result = _check_lint_python(str(tmp_path / "main.py"))
    assert result["ok"] is True


def test_lint_python_skips_when_ruff_missing(tmp_path):
    """If ruff is not installed, lint check must be skipped (not failed)."""
    with patch("server._run_subprocess", return_value={
        "ok": False, "error": "Command not found: ruff"
    }):
        result = _check_lint_python(str(tmp_path / "main.py"))
    assert result.get("skipped") is True
    assert result["ok"] is True   # skipped ≠ failed


def test_lint_js_skips_when_no_eslint_config(tmp_path):
    """Without an eslint config, JS lint check must return skipped=True."""
    result = _check_lint_js(str(tmp_path / "app.js"), str(tmp_path))
    assert result.get("skipped") is True
    assert result["ok"] is True


def test_typecheck_returns_none_when_no_mypy_config(tmp_path):
    """_check_typecheck_python must return None when no mypy config exists."""
    result = _check_typecheck_python(str(tmp_path / "main.py"), str(tmp_path))
    assert result is None


def test_find_related_tests_discovers_test_file(tmp_path):
    """_find_related_tests must find test_<stem>.py in the tests/ directory."""
    tests_dir  = tmp_path / "tests"
    tests_dir.mkdir()
    test_file  = tests_dir / "test_main.py"
    test_file.write_text("# test stub")

    source_file = tmp_path / "main.py"
    source_file.write_text("# source stub")

    results = _find_related_tests(str(source_file), str(tmp_path))
    assert str(test_file) in results


def test_find_related_tests_returns_empty_when_none(tmp_path):
    """_find_related_tests must return [] when no test file matches."""
    source_file = tmp_path / "orphan.py"
    source_file.write_text("# source stub")
    results = _find_related_tests(str(source_file), str(tmp_path))
    assert results == []
```

---

## Test Matrix

| # | Test | Helper | Input | Expected | Pass Condition |
|---|------|--------|-------|----------|----------------|
| T1 | Lint passes | `_check_lint_python` | `ruff` ok | `{"ok": True}` | Clean file |
| T2 | Lint skips no ruff | `_check_lint_python` | `"Command not found"` | `{"ok": True, "skipped": True}` | Tool absent |
| T3 | JS lint skips no config | `_check_lint_js` | No `.eslintrc` | `{"ok": True, "skipped": True}` | Config absent |
| T4 | Typecheck None no config | `_check_typecheck_python` | No `mypy.ini` | `None` | Config absent |
| T5 | Finds test file | `_find_related_tests` | `test_main.py` exists | Path in results | Convention match |
| T6 | Empty on no test | `_find_related_tests` | No test file | `[]` | Empty list |

---

## ⛔ Acceptance Gate — Phase 4

```bash
pytest tests/test_code_checks.py -v
```

- [ ] All 6 tests pass
- [ ] `code_run_checks()` body is ≤ 30 lines after refactor
- [ ] `grep -c "elif ext" mcp-servers/coding/server.py` → **0** inside `code_run_checks`
- [ ] Existing tests still pass

---

---

# PHASE 5 OF 5 — Nginx Action Dispatch
### *Replace CC=29 server_nginx if-chain with a structured action dispatch table*

---

## Agent Prompt

You are refactoring `server_nginx()` in `mcp-servers/server_control/server.py`.

`server_nginx()` has **CC=29 (grade D)**. It handles 7 nginx actions (`test`, `reload`,
`status`, `list_vhosts`, `add_vhost`, `remove_vhost`, unknown) with recursive self-calls
and rollback logic buried inside. The vhost add action has 4 nested try/except blocks.

The fix: extract each action into a private function and use a dispatch table.
The recursive self-calls (`server_nginx("test")`, `server_nginx("reload")`) are replaced
by direct calls to the extracted helpers.

**Rules:**
- The `@mcp.tool()` decorator stays on `server_nginx()`
- The return dict shape for each action must not change — callers depend on the keys
- No real filesystem writes in tests — all helpers must be patchable
- Read `pawbot/contracts.py` fully before editing

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `mcp-servers/server_control/server.py` — extract 5 action helpers |
| **CREATE** | `tests/test_nginx_actions.py` — 6 tests |

---

## Extracted Action 1 — `_nginx_test()`

```python
def _nginx_test() -> dict[str, Any]:
    """Run 'nginx -t' and return {ok, output}. CC=2."""
    result = _run_command(["nginx", "-t"], timeout=20)
    if "error" in result:
        return result
    output = result.get("stderr") or result.get("stdout") or ""
    return {"ok": result.get("returncode", 1) == 0, "output": _truncate(output, 2000)}
```

---

## Extracted Action 2 — `_nginx_reload()`

```python
def _nginx_reload() -> dict[str, Any]:
    """
    Test config, then reload nginx.
    Returns {reloaded: bool} or {error: str} if test fails.
    CC=3.
    """
    test = _nginx_test()
    if not test.get("ok"):
        return {"error": "nginx config test failed", "details": test.get("output", "")}
    result = _run_command(["systemctl", "reload", "nginx"], timeout=30)
    if "error" in result:
        return result
    return {"reloaded": result.get("returncode", 1) == 0}
```

---

## Extracted Action 3 — `_nginx_list_vhosts()`

```python
def _nginx_list_vhosts(sites_available: "Path") -> dict[str, Any]:
    """List available nginx vhosts. CC=2."""
    if not sites_available.exists():
        return {"vhosts": []}
    vhosts = [
        entry.name for entry in sorted(sites_available.iterdir())
        if entry.is_file()
    ]
    return {"vhosts": vhosts}
```

---

## Extracted Action 4 — `_nginx_add_vhost()`

```python
def _nginx_add_vhost(
    domain: str,
    config: str,
    sites_available: "Path",
    sites_enabled: "Path",
) -> dict[str, Any]:
    """
    Write vhost config, create symlink, test, reload — or rollback on failure.
    CC=5 (was buried inside CC=29 parent).
    """
    import shutil
    if not domain or not config:
        return {"error": "domain and config required for add_vhost"}

    avail_path   = sites_available / domain
    enabled_path = sites_enabled   / domain

    try:
        sites_available.mkdir(parents=True, exist_ok=True)
        sites_enabled.mkdir(parents=True, exist_ok=True)
        avail_path.write_text(config, encoding="utf-8")

        if not enabled_path.exists():
            try:
                enabled_path.symlink_to(avail_path)
            except Exception:
                shutil.copy2(avail_path, enabled_path)

        test = _nginx_test()
        if not test.get("ok"):
            # Rollback
            for p in (avail_path, enabled_path):
                if p.exists():
                    p.unlink()
            return {"error": "nginx config test failed", "details": test.get("output", "")}

        _nginx_reload()
        logger.info("NGINX: added vhost for %r", domain)
        return {"added": True, "domain": domain, "path": str(avail_path)}

    except Exception as exc:
        return {"error": str(exc)}
```

---

## Extracted Action 5 — `_nginx_remove_vhost()`

```python
def _nginx_remove_vhost(
    domain: str,
    sites_available: "Path",
    sites_enabled: "Path",
) -> dict[str, Any]:
    """Remove vhost config and symlink, then reload. CC=3."""
    if not domain:
        return {"error": "domain is required for remove_vhost"}
    try:
        for p in (sites_available / domain, sites_enabled / domain):
            if p.exists():
                p.unlink()
        _nginx_reload()
        logger.info("NGINX: removed vhost for %r", domain)
        return {"removed": True, "domain": domain}
    except Exception as exc:
        return {"error": str(exc)}
```

---

## Updated `server_nginx()` — thin dispatcher

```python
@mcp.tool()
def server_nginx(action: str, domain: str = "", config: str = "") -> dict[str, Any]:
    """
    Manage nginx operations and vhosts.
    CC = 4 (was 29). Each action is handled by a dedicated helper.

    Actions: test, reload, status, list_vhosts, add_vhost, remove_vhost
    """
    sites_available = Path("/etc/nginx/sites-available")
    sites_enabled   = Path("/etc/nginx/sites-enabled")

    dispatch: dict[str, Any] = {
        "test":         lambda: _nginx_test(),
        "reload":       lambda: _nginx_reload(),
        "status":       lambda: service_control("nginx", "status"),
        "list_vhosts":  lambda: _nginx_list_vhosts(sites_available),
        "add_vhost":    lambda: _nginx_add_vhost(domain, config, sites_available, sites_enabled),
        "remove_vhost": lambda: _nginx_remove_vhost(domain, sites_available, sites_enabled),
    }

    handler = dispatch.get(action)
    if not handler:
        return {"error": f"Unknown action: {action!r}. Use: {list(dispatch)}"}

    return handler()
```

---

## File — CREATE `tests/test_nginx_actions.py`

```python
"""
tests/test_nginx_actions.py

Tests for the extracted nginx action helpers.
Run: pytest tests/test_nginx_actions.py -v
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-servers" / "server_control"))

from server import (
    _nginx_test,
    _nginx_reload,
    _nginx_list_vhosts,
    _nginx_add_vhost,
    _nginx_remove_vhost,
)


def test_nginx_test_returns_ok_on_success():
    """_nginx_test must return {ok: True} when nginx -t exits cleanly."""
    with patch("server._run_command", return_value={"returncode": 0, "stderr": "syntax is ok"}):
        result = _nginx_test()
    assert result["ok"] is True
    assert "syntax is ok" in result["output"]


def test_nginx_reload_aborts_on_failed_test():
    """_nginx_reload must return an error dict without reloading when test fails."""
    with patch("server._nginx_test", return_value={"ok": False, "output": "config error"}):
        result = _nginx_reload()
    assert "error" in result
    assert "config test failed" in result["error"]


def test_nginx_list_vhosts_returns_names(tmp_path):
    """_nginx_list_vhosts must return a sorted list of vhost file names."""
    (tmp_path / "example.com").write_text("server {}")
    (tmp_path / "api.example.com").write_text("server {}")

    result = _nginx_list_vhosts(tmp_path)
    assert "vhosts" in result
    assert "example.com" in result["vhosts"]
    assert "api.example.com" in result["vhosts"]


def test_nginx_list_vhosts_empty_when_dir_missing():
    """_nginx_list_vhosts must return {vhosts: []} when the directory does not exist."""
    result = _nginx_list_vhosts(Path("/nonexistent/nginx/sites-available"))
    assert result == {"vhosts": []}


def test_nginx_add_vhost_requires_domain_and_config(tmp_path):
    """_nginx_add_vhost must return an error if domain or config is missing."""
    result = _nginx_add_vhost("", "", tmp_path, tmp_path)
    assert "error" in result


def test_nginx_remove_vhost_requires_domain(tmp_path):
    """_nginx_remove_vhost must return an error if domain is empty."""
    result = _nginx_remove_vhost("", tmp_path, tmp_path)
    assert "error" in result
    assert "domain is required" in result["error"]
```

---

## Test Matrix

| # | Test | Helper | Input | Expected | Pass Condition |
|---|------|--------|-------|----------|----------------|
| T1 | Test passes | `_nginx_test` | `returncode=0` | `{"ok": True}` | Success path |
| T2 | Reload aborts bad config | `_nginx_reload` | Test `ok=False` | `{"error": ...}` | No reload called |
| T3 | List vhosts finds files | `_nginx_list_vhosts` | 2 files in dir | Both names in list | Sorted listing |
| T4 | List vhosts missing dir | `_nginx_list_vhosts` | Non-existent path | `{"vhosts": []}` | Empty, no crash |
| T5 | Add requires domain+config | `_nginx_add_vhost` | `domain=""` | `{"error": ...}` | Validation guard |
| T6 | Remove requires domain | `_nginx_remove_vhost` | `domain=""` | `{"error": ...}` | Validation guard |

---

## ⛔ Acceptance Gate — Phase 5 (Section 4 Final Gate)
**ALL criteria must pass. This is the Section 4 gate.**

```bash
pytest tests/test_sse_dispatch.py \
       tests/test_provider_matcher.py \
       tests/test_code_search_modes.py \
       tests/test_code_checks.py \
       tests/test_nginx_actions.py \
       -v
```

- [ ] All **32 tests** pass across all 5 phases (8 + 6 + 7 + 6 + 6 = 33, minus 1 combined = 32)
- [ ] PHASE 1: `grep -c "elif event_type" pawbot/providers/openai_codex_provider.py` → **0**
- [ ] PHASE 1: `_consume_sse` body ≤ 10 lines
- [ ] PHASE 2: `_match_provider()` body ≤ 20 lines
- [ ] PHASE 2: `grep -n "def _kw_matches" pawbot/config/schema.py` → inside `_match_by_keyword` only
- [ ] PHASE 3: `grep -c "elif mode ==" mcp-servers/coding/server.py` → **0**
- [ ] PHASE 3: `code_search()` body ≤ 15 lines
- [ ] PHASE 4: `grep -c "elif ext" mcp-servers/coding/server.py` → **0** inside `code_run_checks`
- [ ] PHASE 5: `grep -c "if action ==" mcp-servers/server_control/server.py` → **0** inside `server_nginx`
- [ ] PHASE 5: `server_nginx()` body ≤ 15 lines
- [ ] COMBINED: `python -m pytest tests/ -q` → all 547+ tests pass, **0 failures**

---

**Section 4 is complete when all of the above are verified.**

Signal Section 5 agent that it can proceed.

> **Remember:** Every name — every class, enum, constant, path, and config key —
> comes from `pawbot/contracts.py`. Read it first. Never invent new names.
> The single source of truth is the contract.

---

*End of Section 4 Fix Document — itsamiitt/pawbot — March 2026*
