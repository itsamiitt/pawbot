# 🏗️ SECTION 3 — Channels & Communication Reliability
## Complete Agent Fix Document
### 5 Self-Contained Agent Prompts · Full Code · Tests · Acceptance Gates

**Repo:** `itsamiitt/pawbot` · **Date:** March 2026 · **Version:** 1.0  
**Source:** Deep Scan Report — Radon CC hotspots in channels/ · Dead code (Vulture) · Missing channel manager tests

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

| # | File | Problem | Radon CC | After Fix |
|---|------|---------|---------|-----------|
| 1 | `channels/manager.py` | `_init_channels` is CC=21 — 10 hardcoded `if` blocks | D (21) | Registry pattern, CC≤5 per channel |
| 2 | `channels/feishu.py` | `_extract_element_content` CC=31, `_on_message` CC=22 | E (31), D (22) | Dispatch table + focused helpers |
| 3 | `channels/slack.py` | `_on_socket_request` CC=26 | D (26) | Extracted guards + event dispatcher |
| 4 | `channels/email.py` | `_fetch_messages` CC=22 — monolithic IMAP loop | D (22) | Extracted fetch/parse/dedupe helpers |
| 5 | `channels/discord.py` | `_handle_message_create` CC=21 + dead vars | D (21) | Attachment handler extracted |

**Dead code fixed (Vulture):**
- `mcp-servers/browser/server.py` — `restore_session`, `full_page` unused vars
- `mcp-servers/app_control/server.py` — unused `Image` import
- `pawbot/channels/feishu.py` — unused `P2ImMessageReceiveV1` import
- `pawbot/channels/matrix.py` — unused `ContentRepositoryConfigError` import
- `pawbot/channels/qq.py` — duplicate `C2CMessage` import

---

## Phase Execution Order

| Phase | Title | Can Start When | Blocks |
|-------|-------|---------------|--------|
| **1** | ChannelManager Registry | Immediately — no deps | Phase 5 gate |
| **2** | Feishu Handler Refactor | Immediately — no deps | Phase 5 gate |
| **3** | Slack Socket Refactor | Immediately — no deps | Phase 5 gate |
| **4** | Email IMAP Refactor | Immediately — no deps | Phase 5 gate |
| **5** | Discord + Dead Code Cleanup | Phases 1–4 complete | Section 3 gate |

Phases 1–4 are **fully independent** — run them simultaneously.

---

---

# PHASE 1 OF 5 — ChannelManager Registry
### *Replace 10 hardcoded if-blocks with a data-driven registry pattern*

---

## Agent Prompt

You are refactoring `pawbot/channels/manager.py`'s `_init_channels()` method.

Currently `_init_channels()` has CC=21 because it contains 10 separate `if channel.enabled`
blocks, each with a `try/except ImportError`. Adding an 11th channel means editing this
function. The fix is a **channel registry** — a list of `(config_attr, class_path, kwargs_fn)`
tuples that drives a single generic loading loop.

The public API of `ChannelManager` must not change. `start_all()`, `stop_all()`,
`get_channel()`, `get_status()`, and `enabled_channels` must all work identically.

**Rules:**
- Do not change `BaseChannel`, `MessageBus`, or any channel implementation file
- The registry must be a module-level constant, not a class attribute
- Each channel class is still lazy-imported (inside the loader) to avoid import-time failures
- Read `pawbot/contracts.py` fully before editing

---

## Why This Phase Exists

The 10-block `_init_channels()` violates the Open/Closed Principle. Every new channel
requires modifying a function that already works correctly — a prime source of regressions.
The registry pattern means new channels are added by appending one line to a list.
`_init_channels()` goes from CC=21 to CC=3.

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `pawbot/channels/manager.py` — replace `_init_channels()` with registry loader |
| **CREATE** | `tests/test_channel_manager.py` — 5 focused tests |

---

## Complete Implementation — EDIT `pawbot/channels/manager.py`

Replace only `_init_channels()`. Leave everything else unchanged.

```python
# ── ADD at module level (before ChannelManager class): ─────────────────────────

from typing import NamedTuple, Callable, Any

class _ChannelEntry(NamedTuple):
    """Registry entry for a single channel type."""
    config_attr:  str            # attribute path on config.channels, e.g. "telegram"
    import_path:  str            # dotted module path, e.g. "pawbot.channels.telegram"
    class_name:   str            # class name in that module, e.g. "TelegramChannel"
    extra_kwargs: Callable[[Any], dict]  # function(full_config) -> extra kwargs dict


def _no_extra(cfg: Any) -> dict:
    """Most channels need no extra kwargs beyond (channel_cfg, bus)."""
    return {}


# ── Channel registry — append here to add new channels ────────────────────────
# Format: config_attr, module, class_name, extra_kwargs_fn
_CHANNEL_REGISTRY: list[_ChannelEntry] = [
    _ChannelEntry(
        "telegram",
        "pawbot.channels.telegram",
        "TelegramChannel",
        lambda cfg: {"groq_api_key": cfg.providers.groq.api_key},
    ),
    _ChannelEntry("whatsapp", "pawbot.channels.whatsapp",  "WhatsAppChannel",  _no_extra),
    _ChannelEntry("discord",  "pawbot.channels.discord",   "DiscordChannel",   _no_extra),
    _ChannelEntry("feishu",   "pawbot.channels.feishu",    "FeishuChannel",    _no_extra),
    _ChannelEntry("mochat",   "pawbot.channels.mochat",    "MochatChannel",    _no_extra),
    _ChannelEntry("dingtalk", "pawbot.channels.dingtalk",  "DingTalkChannel",  _no_extra),
    _ChannelEntry("email",    "pawbot.channels.email",     "EmailChannel",     _no_extra),
    _ChannelEntry("slack",    "pawbot.channels.slack",     "SlackChannel",     _no_extra),
    _ChannelEntry("qq",       "pawbot.channels.qq",        "QQChannel",        _no_extra),
    _ChannelEntry("matrix",   "pawbot.channels.matrix",    "MatrixChannel",    _no_extra),
]
```

Now replace `_init_channels()`:

```python
    def _init_channels(self) -> None:
        """
        Initialize all enabled channels via the registry.

        Iterates _CHANNEL_REGISTRY — one entry per channel type.
        Lazy-imports each class so a missing optional dependency (e.g. python-telegram-bot)
        only prevents that channel from loading, not the entire manager.

        CC = 3 (was 21).  To add a new channel: append to _CHANNEL_REGISTRY above.
        """
        for entry in _CHANNEL_REGISTRY:
            channel_cfg = getattr(self.config.channels, entry.config_attr, None)
            if channel_cfg is None or not getattr(channel_cfg, "enabled", False):
                continue

            try:
                module     = __import__(entry.import_path, fromlist=[entry.class_name])
                cls        = getattr(module, entry.class_name)
                extra      = entry.extra_kwargs(self.config)
                instance   = cls(channel_cfg, self.bus, **extra)
                self.channels[entry.config_attr] = instance
                logger.info("{} channel enabled", entry.config_attr)
            except ImportError as exc:
                logger.warning("{} channel not available: {}", entry.config_attr, exc)
            except Exception as exc:
                logger.error("{} channel failed to initialise: {}", entry.config_attr, exc)
```

---

## File — CREATE `tests/test_channel_manager.py`

```python
"""
tests/test_channel_manager.py

Tests for ChannelManager registry refactor.
Run: pytest tests/test_channel_manager.py -v
"""

import pytest
from unittest.mock import MagicMock, patch

from pawbot.channels.manager import ChannelManager, _CHANNEL_REGISTRY


def _make_config(enabled_channels: list[str]) -> MagicMock:
    """Build a mock Config with specified channels enabled."""
    config = MagicMock()
    config.providers.groq.api_key = "test-groq-key"

    all_channels = [
        "telegram", "whatsapp", "discord", "feishu",
        "mochat", "dingtalk", "email", "slack", "qq", "matrix"
    ]
    for ch in all_channels:
        ch_cfg = MagicMock()
        ch_cfg.enabled = ch in enabled_channels
        setattr(config.channels, ch, ch_cfg)

    return config


def test_registry_covers_all_known_channels():
    """
    _CHANNEL_REGISTRY must contain all 10 channel types.
    Adding a channel = adding one registry entry.
    """
    names = {e.config_attr for e in _CHANNEL_REGISTRY}
    expected = {
        "telegram", "whatsapp", "discord", "feishu",
        "mochat", "dingtalk", "email", "slack", "qq", "matrix"
    }
    assert names == expected, (
        f"Registry missing channels: {expected - names}\n"
        f"Registry has unexpected: {names - expected}"
    )


def test_no_channels_enabled_loads_empty():
    """When all channels are disabled, channels dict must be empty."""
    config  = _make_config(enabled_channels=[])
    bus     = MagicMock()
    manager = ChannelManager(config, bus)
    assert manager.channels == {}, "No channels should be loaded when all are disabled"


def test_enabled_channel_is_loaded():
    """An enabled channel must appear in manager.channels after init."""
    config = _make_config(enabled_channels=["telegram"])
    bus    = MagicMock()

    mock_telegram_cls = MagicMock()
    mock_telegram_cls.return_value = MagicMock()

    with patch.dict("sys.modules", {
        "pawbot.channels.telegram": MagicMock(TelegramChannel=mock_telegram_cls)
    }):
        manager = ChannelManager(config, bus)

    assert "telegram" in manager.channels
    mock_telegram_cls.assert_called_once()


def test_import_error_is_handled_gracefully():
    """
    If a channel package is not installed (ImportError), the manager must
    log a warning and continue — not crash the entire startup.
    """
    config  = _make_config(enabled_channels=["slack"])
    bus     = MagicMock()

    with patch("builtins.__import__", side_effect=ImportError("slack_sdk not found")):
        # Should not raise
        try:
            manager = ChannelManager(config, bus)
            # If we get here with an empty channels dict, the error was handled
            assert "slack" not in manager.channels
        except ImportError:
            pytest.fail("ChannelManager must not propagate ImportError from channel loading")


def test_get_status_returns_all_running_channels():
    """get_status() must return a dict with is_running for each loaded channel."""
    config = _make_config(enabled_channels=["discord"])
    bus    = MagicMock()

    mock_discord = MagicMock()
    mock_discord.is_running = True

    with patch.dict("sys.modules", {
        "pawbot.channels.discord": MagicMock(DiscordChannel=MagicMock(return_value=mock_discord))
    }):
        manager = ChannelManager(config, bus)
        status  = manager.get_status()

    assert "discord" in status
    assert status["discord"]["running"] is True
```

---

## Test Matrix

| # | Test | Input | Expected | Pass Condition |
|---|------|-------|----------|----------------|
| T1 | Registry covers all channels | Set comparison | 10 entries, names match | Exact set equality |
| T2 | No channels enabled | All disabled config | `channels == {}` | Empty dict |
| T3 | Enabled channel loads | Telegram enabled | `"telegram" in channels` | Class instantiated |
| T4 | ImportError is graceful | `ImportError` on import | No crash, channel absent | No exception raised |
| T5 | `get_status()` | Discord loaded + running | `status["discord"]["running"] == True` | Correct dict shape |

---

## ⛔ Acceptance Gate — Phase 1

```bash
pytest tests/test_channel_manager.py -v
```

- [ ] All 5 tests pass
- [ ] `grep -c "if self.config.channels" pawbot/channels/manager.py` → **0** (no more hardcoded if-blocks)
- [ ] `wc -l pawbot/channels/manager.py` is shorter than before (removed ~80 lines of duplication)
- [ ] Adding a hypothetical 11th channel requires **only one line change** in `_CHANNEL_REGISTRY`

---

---

# PHASE 2 OF 5 — Feishu Handler Refactor
### *Split CC=31 element extractor and CC=22 message handler into focused helpers*

---

## Agent Prompt

You are refactoring two functions in `pawbot/channels/feishu.py`:

1. `_extract_element_content()` — **CC=31 (grade E)** — a module-level function with
   11 `elif` branches, one per Feishu card element tag type
2. `FeishuChannel._on_message()` — **CC=22 (grade D)** — handles text, post, image,
   audio, file, media, and interactive card types in a single method

Also fix dead code: `from lark_oapi.api.im.v1 import P2ImMessageReceiveV1` is imported
but the type annotation uses a string literal — the import is unused (Vulture finding).

**Rules:**
- The external behaviour of `_extract_element_content()` must not change — same input/output
- `_on_message()` must still handle all message types after refactor
- Do not change the Feishu API client initialisation
- Read `pawbot/contracts.py` fully before editing

---

## Why This Phase Exists

`_extract_element_content()` with CC=31 has more branches than a junior developer can
hold in working memory. It cannot be unit tested without mocking the entire Feishu card
payload. Replacing the `elif` chain with a dispatch table reduces CC to 3 and makes each
handler independently testable.

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `pawbot/channels/feishu.py` — replace `_extract_element_content` with dispatch table |
| **EDIT** | `pawbot/channels/feishu.py` — extract `_parse_message_content()` from `_on_message` |
| **EDIT** | `pawbot/channels/feishu.py` — remove unused `P2ImMessageReceiveV1` import |
| **CREATE** | `tests/test_feishu_helpers.py` — 6 tests |

---

## Fix 1 — Replace `_extract_element_content` with dispatch table

```python
# ── REMOVE the existing _extract_element_content function entirely ─────────────
# ── REPLACE with this dispatch-table version ──────────────────────────────────

def _element_markdown(element: dict) -> list[str]:
    content = element.get("content", "")
    return [content] if content else []


def _element_div(element: dict) -> list[str]:
    parts = []
    text = element.get("text", {})
    if isinstance(text, dict):
        c = text.get("content", "") or text.get("text", "")
        if c:
            parts.append(c)
    elif isinstance(text, str):
        parts.append(text)
    for field in element.get("fields", []):
        if isinstance(field, dict):
            ft = field.get("text", {})
            if isinstance(ft, dict) and ft.get("content"):
                parts.append(ft["content"])
    return parts


def _element_anchor(element: dict) -> list[str]:
    parts = []
    href = element.get("href", "")
    text = element.get("text", "")
    if href:
        parts.append(f"link: {href}")
    if text:
        parts.append(text)
    return parts


def _element_button(element: dict) -> list[str]:
    parts = []
    text = element.get("text", {})
    if isinstance(text, dict) and text.get("content"):
        parts.append(text["content"])
    url = element.get("url", "") or element.get("multi_url", {}).get("url", "")
    if url:
        parts.append(f"link: {url}")
    return parts


def _element_image(element: dict) -> list[str]:
    alt = element.get("alt", {})
    label = alt.get("content", "[image]") if isinstance(alt, dict) else "[image]"
    return [label]


def _element_note(element: dict) -> list[str]:
    parts: list[str] = []
    for child in element.get("elements", []):
        parts.extend(_extract_element_content(child))
    return parts


# Dispatch table: tag → handler function
# CC = 3 (was 31)
_ELEMENT_HANDLERS: dict[str, "Callable[[dict], list[str]]"] = {
    "markdown":  _element_markdown,
    "lark_md":   _element_markdown,
    "div":       _element_div,
    "a":         _element_anchor,
    "button":    _element_button,
    "img":       _element_image,
    "note":      _element_note,
}


def _extract_element_content(element: dict) -> list[str]:
    """
    Extract text content from a single Feishu card element.

    Dispatches to a per-tag handler. Unknown tags are silently ignored.
    CC = 3 (down from 31). Behaviour is identical to the original implementation.
    """
    if not isinstance(element, dict):
        return []
    tag     = element.get("tag", "")
    handler = _ELEMENT_HANDLERS.get(tag)
    return handler(element) if handler else []
```

---

## Fix 2 — Extract `_parse_message_content()` from `_on_message`

Find the block in `_on_message` that parses `msg_type` and builds `content_parts` / `media_paths`.
Extract it into a standalone async method:

```python
async def _parse_message_content(
    self,
    msg_type: str,
    content_json: dict,
    message_id: str,
) -> tuple[list[str], list[str]]:
    """
    Parse Feishu message content by type.

    Returns (content_parts, media_paths).
    Extracted from _on_message to reduce CC from 22.

    Handles: text, post, image, audio, file, media, interactive card.
    All other types produce an empty result — caller decides how to handle.
    """
    content_parts: list[str] = []
    media_paths:   list[str] = []

    if msg_type == "text":
        text = content_json.get("text", "")
        if text:
            content_parts.append(text)

    elif msg_type == "post":
        text, image_keys = _extract_post_content(content_json)
        if text:
            content_parts.append(text)
        for img_key in image_keys:
            file_path, content_text = await self._download_and_save_media(
                "image", {"image_key": img_key}, message_id
            )
            if file_path:
                media_paths.append(file_path)
            content_parts.append(content_text)

    elif msg_type in ("image", "audio", "file", "media"):
        file_path, content_text = await self._download_and_save_media(
            msg_type, content_json, message_id
        )
        if file_path:
            media_paths.append(file_path)
        content_parts.append(content_text)

    elif msg_type == "interactive":
        # Card messages — extract text from card elements
        card = content_json.get("card", content_json)
        body = card.get("body", {})
        for element in body.get("elements", []):
            content_parts.extend(_extract_element_content(element))

    return content_parts, media_paths
```

Then in `_on_message`, replace the parsing block with a single call:

```python
# REPLACE the content-parsing block with:
content_parts, media_paths = await self._parse_message_content(
    msg_type, content_json, message_id
)
```

---

## Fix 3 — Remove unused import

```python
# FIND at top of feishu.py:
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1   # ← Vulture: unused import

# REMOVE that line entirely.
# The type annotation in _on_message uses a string literal "P2ImMessageReceiveV1"
# which does not require a runtime import.
```

---

## File — CREATE `tests/test_feishu_helpers.py`

```python
"""
tests/test_feishu_helpers.py

Tests for the refactored Feishu helper functions.
Run: pytest tests/test_feishu_helpers.py -v
"""

import pytest
from pawbot.channels.feishu import (
    _extract_element_content,
    _ELEMENT_HANDLERS,
)


def test_markdown_element_extracts_content():
    """markdown/lark_md elements must return the content string."""
    element = {"tag": "markdown", "content": "Hello **world**"}
    result  = _extract_element_content(element)
    assert result == ["Hello **world**"]


def test_lark_md_alias_works():
    """lark_md is an alias for markdown — both must work."""
    element = {"tag": "lark_md", "content": "Feishu text"}
    assert _extract_element_content(element) == ["Feishu text"]


def test_div_element_extracts_text():
    """div elements with nested text dict must return content."""
    element = {"tag": "div", "text": {"content": "div content"}}
    result  = _extract_element_content(element)
    assert "div content" in result


def test_button_element_extracts_label_and_url():
    """button elements must return both label text and url."""
    element = {
        "tag":  "button",
        "text": {"content": "Click me"},
        "url":  "https://example.com",
    }
    result = _extract_element_content(element)
    assert "Click me" in result
    assert "link: https://example.com" in result


def test_unknown_tag_returns_empty():
    """Unknown element tags must return [] — never crash."""
    element = {"tag": "unknown_future_tag_xyz", "content": "something"}
    result  = _extract_element_content(element)
    assert result == [], f"Unknown tag should produce [], got {result}"


def test_non_dict_input_returns_empty():
    """Non-dict input (e.g. None, string) must return [] — never crash."""
    assert _extract_element_content(None) == []  # type: ignore
    assert _extract_element_content("string") == []  # type: ignore
    assert _extract_element_content([]) == []  # type: ignore


def test_note_element_recurses_into_children():
    """note elements must recursively extract content from child elements."""
    element = {
        "tag": "note",
        "elements": [
            {"tag": "markdown", "content": "Note text"},
            {"tag": "img",      "alt": {"content": "Note image"}},
        ],
    }
    result = _extract_element_content(element)
    assert "Note text" in result
    assert "Note image" in result


def test_dispatch_table_is_complete():
    """
    Every tag in _ELEMENT_HANDLERS must be callable.
    Guards against someone accidentally adding a non-callable value.
    """
    for tag, handler in _ELEMENT_HANDLERS.items():
        assert callable(handler), f"Handler for tag '{tag}' is not callable"
```

---

## Test Matrix

| # | Test | Input | Expected | Pass Condition |
|---|------|-------|----------|----------------|
| T1 | `markdown` tag extracts content | `{"tag":"markdown","content":"Hello"}` | `["Hello"]` | Exact list match |
| T2 | `lark_md` alias | `{"tag":"lark_md","content":"text"}` | `["text"]` | Alias works |
| T3 | `div` text extraction | Nested text dict | Content present in result | String in list |
| T4 | `button` label + URL | `text` + `url` keys | Both in result | Two items |
| T5 | Unknown tag | `{"tag":"xyz"}` | `[]` | Empty list, no crash |
| T6 | Non-dict input | `None`, `"str"`, `[]` | `[]` | No exception |
| T7 | `note` recursion | Children with content | All child content | Recursive extraction |
| T8 | Dispatch table handlers callable | All entries | `callable(handler) == True` | No non-callable entries |

---

## ⛔ Acceptance Gate — Phase 2

```bash
pytest tests/test_feishu_helpers.py -v
```

- [ ] All 8 tests pass
- [ ] `grep -n "P2ImMessageReceiveV1" pawbot/channels/feishu.py` → **no import line** (only string annotation if any)
- [ ] `grep -c "elif tag ==" pawbot/channels/feishu.py` → **0** (dispatch table replaces elif chain)
- [ ] Existing `tests/test_channels.py` still passes — `python -m pytest tests/test_channels.py -q`

---

---

# PHASE 3 OF 5 — Slack Socket Handler Refactor
### *Split CC=26 _on_socket_request into guard + dispatcher + per-event handlers*

---

## Agent Prompt

You are refactoring `SlackChannel._on_socket_request()` in `pawbot/channels/slack.py`.

`_on_socket_request()` currently has **CC=26 (grade D)**. It handles:
1. Request type filtering (non-`events_api` requests rejected)
2. Acknowledgement
3. Event type filtering (only `message`, `app_mention`)
4. Bot/subtype filtering
5. Duplicate message detection (mentions sent as both `message` and `app_mention`)
6. Allow-list checking
7. Thread vs channel routing
8. Final `_handle_message()` call

All 8 responsibilities are interleaved in one 60-line function with 8+ early returns.

**Rules:**
- The external Slack API contract does not change
- All 8 behaviours must be preserved exactly — this is a pure refactor
- Read `pawbot/contracts.py` fully before editing

---

## Why This Phase Exists

CC=26 means there are 26 independent paths through this one function.
Any Slack-related bug requires mentally simulating all 26 paths to understand impact.
After refactor: each extracted method has CC≤5 and can be tested independently.

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `pawbot/channels/slack.py` — extract 3 helpers from `_on_socket_request` |
| **CREATE** | `tests/test_slack_helpers.py` — 5 tests |

---

## Extracted Helper 1 — `_should_process_slack_event()`

```python
def _should_process_slack_event(
    self,
    event: dict,
    event_type: str,
) -> bool:
    """
    Guard: return False if this Slack event should be silently skipped.

    Skips:
    - Events that are not 'message' or 'app_mention'
    - Messages with any subtype (bot messages, edits, etc.)
    - Messages from the bot itself (by user_id)
    - 'message' events that are actually mentions (avoid double-processing)

    Extracted from _on_socket_request to reduce CC from 26.
    CC = 5.
    """
    if event_type not in ("message", "app_mention"):
        return False

    # Any subtype = not a normal user message (bot_message, message_changed, etc.)
    if event.get("subtype"):
        return False

    sender_id = event.get("user", "")
    if self._bot_user_id and sender_id == self._bot_user_id:
        return False

    # Slack sends both 'message' and 'app_mention' for channel mentions.
    # Process only 'app_mention' to avoid double-processing.
    text = event.get("text") or ""
    if event_type == "message" and self._bot_user_id and f"<@{self._bot_user_id}>" in text:
        return False

    return True
```

---

## Extracted Helper 2 — `_extract_slack_message()`

```python
def _extract_slack_message(self, event: dict) -> tuple[str, str, str, str | None]:
    """
    Extract routing fields from a Slack event dict.

    Returns (sender_id, chat_id, text, thread_ts).
    Returns empty strings if required fields are missing — caller must check.

    Extracted from _on_socket_request to reduce CC from 26.
    CC = 2.
    """
    sender_id  = event.get("user", "")
    chat_id    = event.get("channel", "")
    text       = (event.get("text") or "").strip()
    thread_ts  = event.get("thread_ts")
    return sender_id, chat_id, text, thread_ts
```

---

## Updated `_on_socket_request()` — thin orchestrator

```python
async def _on_socket_request(
    self,
    client: "SocketModeClient",
    req: "SocketModeRequest",
) -> None:
    """
    Handle incoming Slack Socket Mode requests.
    CC = 4 (was 26). Delegates to focused helpers.
    """
    if req.type != "events_api":
        return

    # Acknowledge immediately — Slack requires <3s ack
    await client.send_socket_mode_response(
        SocketModeResponse(envelope_id=req.envelope_id)
    )

    payload    = req.payload or {}
    event      = payload.get("event") or {}
    event_type = event.get("type", "")

    if not self._should_process_slack_event(event, event_type):
        return

    sender_id, chat_id, text, thread_ts = self._extract_slack_message(event)

    if not sender_id or not chat_id:
        return

    channel_type = event.get("channel_type") or ""
    if not self._is_allowed(sender_id, chat_id, channel_type):
        return

    session_key = f"slack:{chat_id}:{thread_ts}" if thread_ts else None

    await self._handle_message(
        sender_id   = sender_id,
        chat_id     = chat_id,
        content     = text or "[empty message]",
        metadata    = {
            "message_id":  event.get("ts"),
            "event_type":  event_type,
            "channel_type": channel_type,
        },
        session_key = session_key,
    )
```

---

## File — CREATE `tests/test_slack_helpers.py`

```python
"""
tests/test_slack_helpers.py

Tests for the refactored Slack socket event helpers.
Run: pytest tests/test_slack_helpers.py -v
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from pawbot.channels.slack import SlackChannel


def _make_slack_channel(bot_user_id: str = "BOT123") -> SlackChannel:
    """Create a minimal SlackChannel with mocked dependencies."""
    channel              = object.__new__(SlackChannel)
    channel.config       = MagicMock()
    channel.config.allow_from = []
    channel.bus          = MagicMock()
    channel._bot_user_id = bot_user_id
    channel._running     = False
    return channel


def test_skip_non_events_api_type():
    """Events that are not 'message' or 'app_mention' must be skipped."""
    ch     = _make_slack_channel()
    event  = {"type": "reaction_added", "user": "U123"}
    assert ch._should_process_slack_event(event, "reaction_added") is False


def test_skip_messages_with_subtype():
    """Any subtype (bot_message, message_changed) must be skipped."""
    ch    = _make_slack_channel()
    event = {"type": "message", "subtype": "bot_message", "user": "U123"}
    assert ch._should_process_slack_event(event, "message") is False


def test_skip_bot_own_messages():
    """Messages from the bot itself must be skipped."""
    ch    = _make_slack_channel(bot_user_id="BOT123")
    event = {"type": "message", "user": "BOT123", "text": "I am bot"}
    assert ch._should_process_slack_event(event, "message") is False


def test_skip_duplicate_mention_in_message_event():
    """
    Slack sends both 'message' and 'app_mention' for @mentions.
    The 'message' copy must be skipped to avoid double-processing.
    """
    ch    = _make_slack_channel(bot_user_id="BOT123")
    event = {"type": "message", "user": "U456", "text": "Hey <@BOT123> help"}
    assert ch._should_process_slack_event(event, "message") is False


def test_allow_normal_app_mention():
    """A normal app_mention must pass all guards."""
    ch    = _make_slack_channel(bot_user_id="BOT123")
    event = {"type": "app_mention", "user": "U456", "text": "<@BOT123> do something"}
    assert ch._should_process_slack_event(event, "app_mention") is True


def test_extract_slack_message_returns_fields():
    """_extract_slack_message must return all four fields correctly."""
    ch    = _make_slack_channel()
    event = {
        "user":      "U789",
        "channel":   "C001",
        "text":      "  hello  ",
        "thread_ts": "1234567890.000100",
    }
    sender_id, chat_id, text, thread_ts = ch._extract_slack_message(event)
    assert sender_id  == "U789"
    assert chat_id    == "C001"
    assert text       == "hello"   # stripped
    assert thread_ts  == "1234567890.000100"
```

---

## Test Matrix

| # | Test | Input | Expected | Pass Condition |
|---|------|-------|----------|----------------|
| T1 | Skip non-`events_api` | `reaction_added` event | `False` | Filtered at type check |
| T2 | Skip subtype | `subtype: bot_message` | `False` | Subtype filter |
| T3 | Skip own bot | `user == bot_user_id` | `False` | Self-filter |
| T4 | Skip duplicate mention | `message` event with `<@BOT>` in text | `False` | Dedup filter |
| T5 | Allow `app_mention` | Normal `app_mention` | `True` | All guards pass |
| T6 | Extract fields | Event with all fields | Correct tuple | 4-field extraction |

---

## ⛔ Acceptance Gate — Phase 3

```bash
pytest tests/test_slack_helpers.py -v
```

- [ ] All 6 tests pass
- [ ] `grep -n "def _on_socket_request" pawbot/channels/slack.py` — function exists but is shorter
- [ ] `wc -l pawbot/channels/slack.py` is ≤ 290 lines (removed inline logic → extracted helpers)
- [ ] Existing channel tests still pass

---

---

# PHASE 4 OF 5 — Email IMAP Refactor
### *Split CC=22 _fetch_messages into connection, fetch, parse, and dedupe helpers*

---

## Agent Prompt

You are refactoring `EmailChannel._fetch_messages()` in `pawbot/channels/email.py`.

`_fetch_messages()` has **CC=22 (grade D)**. The function does five completely separate
things: IMAP connection setup, mailbox selection, message ID search, per-message fetching,
and per-message parsing. These are all interleaved in one 60+ line method with a nested
loop, making it impossible to test the parser without setting up a real IMAP connection.

**Rules:**
- The external IMAP protocol behaviour must not change
- `_fetch_messages()` must still accept the same arguments and return the same type
- Do not change SMTP/send logic — only IMAP fetch
- Read `pawbot/contracts.py` fully before editing

---

## Why This Phase Exists

A CC=22 email-fetching function cannot be tested without a live IMAP server. Extracting
`_parse_raw_email()` makes it testable in complete isolation. This is how you catch encoding
bugs, malformed headers, and edge cases before they hit production.

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `pawbot/channels/email.py` — extract 3 helpers from `_fetch_messages` |
| **CREATE** | `tests/test_email_helpers.py` — 5 tests |

---

## Extracted Helper 1 — `_open_imap_connection()`

```python
def _open_imap_connection(self) -> "imaplib.IMAP4 | imaplib.IMAP4_SSL":
    """
    Open and authenticate an IMAP connection.

    Returns an authenticated IMAP client object.
    Raises imaplib.IMAP4.error on authentication failure.

    Extracted from _fetch_messages to reduce CC from 22.
    """
    import imaplib
    if self.config.imap_use_ssl:
        client = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
    else:
        client = imaplib.IMAP4(self.config.imap_host, self.config.imap_port)
    client.login(self.config.imap_username, self.config.imap_password)
    return client
```

---

## Extracted Helper 2 — `_parse_raw_email()`

```python
def _parse_raw_email(
    self,
    raw_bytes: bytes,
    uid: str | None,
    dedupe: bool,
) -> "dict[str, Any] | None":
    """
    Parse a raw RFC-2822 email into a structured dict.

    Returns None if the message should be skipped (dedupe, no sender, etc.).
    Returns a dict with keys: sender, subject, date, message_id, body, uid.

    Extracted from _fetch_messages to reduce CC from 22.
    CC = 4.
    """
    from email import policy
    from email.headerregistry import Address
    from email.parser import BytesParser
    from email.utils import parseaddr

    if dedupe and uid and uid in self._processed_uids:
        return None

    parsed  = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    sender  = parseaddr(parsed.get("From", ""))[1].strip().lower()
    if not sender:
        return None

    subject    = self._decode_header_value(parsed.get("Subject", ""))
    date_value = parsed.get("Date", "")
    message_id = parsed.get("Message-ID", "").strip()
    body       = self._extract_text_body(parsed) or "(empty email body)"

    return {
        "sender":     sender,
        "subject":    subject,
        "date":       date_value,
        "message_id": message_id,
        "body":       body,
        "uid":        uid,
    }
```

---

## Updated `_fetch_messages()` — thin orchestrator

```python
def _fetch_messages(
    self,
    search_criteria: tuple[str, ...],
    mark_seen: bool,
    dedupe: bool,
    limit: int,
) -> list[dict[str, Any]]:
    """
    Fetch messages by arbitrary IMAP search criteria.
    CC = 5 (was 22). Delegates to focused helpers.
    """
    import imaplib

    messages = []
    mailbox  = self.config.imap_mailbox or "INBOX"

    try:
        client = self._open_imap_connection()
    except imaplib.IMAP4.error as exc:
        logger.error("Email IMAP authentication failed: {}", exc)
        return messages

    try:
        status, _ = client.select(mailbox)
        if status != "OK":
            logger.warning("Email: could not select mailbox '{}'", mailbox)
            return messages

        status, data = client.search(None, *search_criteria)
        if status != "OK" or not data:
            return messages

        ids = data[0].split()
        if limit > 0 and len(ids) > limit:
            ids = ids[-limit:]

        for imap_id in ids:
            status, fetched = client.fetch(imap_id, "(BODY.PEEK[] UID)")
            if status != "OK" or not fetched:
                continue

            raw_bytes = self._extract_message_bytes(fetched)
            if raw_bytes is None:
                continue

            uid    = self._extract_uid(fetched)
            parsed = self._parse_raw_email(raw_bytes, uid, dedupe)
            if parsed is None:
                continue

            if mark_seen and uid:
                client.store(imap_id, "+FLAGS", "\\Seen")
            if uid:
                self._processed_uids.add(uid)

            messages.append(parsed)

    finally:
        try:
            client.logout()
        except Exception:
            pass

    return messages
```

---

## File — CREATE `tests/test_email_helpers.py`

```python
"""
tests/test_email_helpers.py

Tests for the extracted EmailChannel helpers.
Run: pytest tests/test_email_helpers.py -v
"""

import email
import pytest
from unittest.mock import MagicMock, patch

from pawbot.channels.email import EmailChannel


def _make_email_channel() -> EmailChannel:
    """Create a minimal EmailChannel with mocked SMTP/IMAP config."""
    channel                          = object.__new__(EmailChannel)
    channel.config                   = MagicMock()
    channel.config.imap_host         = "imap.example.com"
    channel.config.imap_port         = 993
    channel.config.imap_use_ssl      = True
    channel.config.imap_username     = "bot@example.com"
    channel.config.imap_password     = "password"
    channel.config.imap_mailbox      = "INBOX"
    channel._processed_uids: set     = set()
    channel.bus                      = MagicMock()
    channel.rate_limiter             = MagicMock()
    channel.rate_limiter.consume.return_value = True
    return channel


def _make_raw_email(
    sender: str = "alice@example.com",
    subject: str = "Test subject",
    body: str    = "Hello from Alice",
) -> bytes:
    """Build a minimal RFC-2822 email as bytes."""
    return (
        f"From: {sender}\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"Message-ID: <test-id-001@example.com>\r\n"
        f"\r\n"
        f"{body}\r\n"
    ).encode("utf-8")


def test_parse_raw_email_returns_structured_dict():
    """_parse_raw_email must return dict with sender, subject, body keys."""
    ch     = _make_email_channel()
    ch._decode_header_value = lambda v: v  # passthrough
    ch._extract_text_body   = lambda p: p.get_payload(decode=True).decode("utf-8", "replace") if isinstance(p.get_payload(decode=True), bytes) else "body"

    raw    = _make_raw_email("alice@example.com", "Hello", "Test body")
    result = ch._parse_raw_email(raw, uid="UID001", dedupe=False)

    assert result is not None
    assert result["sender"]  == "alice@example.com"
    assert result["subject"] == "Hello"
    assert result["uid"]     == "UID001"


def test_parse_raw_email_skips_on_dedupe():
    """Already-processed UIDs must return None when dedupe=True."""
    ch     = _make_email_channel()
    ch._processed_uids.add("UID002")
    ch._decode_header_value = lambda v: v
    ch._extract_text_body   = lambda p: "body"

    raw    = _make_raw_email()
    result = ch._parse_raw_email(raw, uid="UID002", dedupe=True)
    assert result is None, "Deduplication: already-seen UID must return None"


def test_parse_raw_email_skips_empty_sender():
    """Emails with no From header must return None."""
    ch  = _make_email_channel()
    ch._decode_header_value = lambda v: v
    ch._extract_text_body   = lambda p: "body"

    # Build email with no From header
    raw    = b"Subject: No Sender\r\nContent-Type: text/plain\r\n\r\nBody\r\n"
    result = ch._parse_raw_email(raw, uid="UID003", dedupe=False)
    assert result is None, "Email with no sender must return None"


def test_parse_raw_email_no_dedupe_allows_repeat():
    """With dedupe=False, already-seen UIDs must still be parsed."""
    ch  = _make_email_channel()
    ch._processed_uids.add("UID004")
    ch._decode_header_value = lambda v: v
    ch._extract_text_body   = lambda p: "body text"

    raw    = _make_raw_email("bob@example.com", "Repeat", "content")
    result = ch._parse_raw_email(raw, uid="UID004", dedupe=False)
    assert result is not None, "dedupe=False must allow re-processing"
    assert result["sender"] == "bob@example.com"


def test_open_imap_uses_ssl_when_configured():
    """_open_imap_connection must use IMAP4_SSL when imap_use_ssl=True."""
    ch = _make_email_channel()

    with patch("imaplib.IMAP4_SSL") as mock_ssl, \
         patch("imaplib.IMAP4") as mock_plain:
        mock_ssl.return_value.login.return_value = ("OK", [])
        ch._open_imap_connection()
        mock_ssl.assert_called_once_with("imap.example.com", 993)
        mock_plain.assert_not_called()
```

---

## Test Matrix

| # | Test | Input | Expected | Pass Condition |
|---|------|-------|----------|----------------|
| T1 | Parse returns structured dict | Valid RFC-2822 bytes | Dict with sender/subject/uid | Keys present |
| T2 | Dedupe skips known UID | UID in `_processed_uids`, `dedupe=True` | `None` | Skip existing |
| T3 | No sender skipped | Email with no `From` | `None` | Safety check |
| T4 | `dedupe=False` allows repeat | UID in set, `dedupe=False` | Dict returned | Not filtered |
| T5 | SSL connection | `imap_use_ssl=True` | `IMAP4_SSL` called | Correct class used |

---

## ⛔ Acceptance Gate — Phase 4

```bash
pytest tests/test_email_helpers.py -v
```

- [ ] All 5 tests pass
- [ ] `_parse_raw_email` is testable without a live IMAP server
- [ ] `_fetch_messages` is under 40 lines after refactor
- [ ] Existing channel tests still pass

---

---

# PHASE 5 OF 5 — Discord Cleanup + Dead Code Removal
### *Refactor CC=21 attachment handler, remove all Vulture dead-code findings*

---

## Agent Prompt

You are doing two things in this phase:
1. Refactoring `DiscordChannel._handle_message_create()` (CC=21) — extract the attachment
   download loop into a helper
2. Removing all dead code identified by Vulture across the codebase

**Vulture findings to fix:**
- `mcp-servers/browser/server.py:259` — `restore_session` assigned but never used
- `mcp-servers/browser/server.py:772` — `full_page` assigned but never used
- `mcp-servers/app_control/server.py:41` — `Image` imported but never used
- `pawbot/channels/feishu.py` — `P2ImMessageReceiveV1` (fixed in Phase 2)
- `pawbot/channels/matrix.py:14` — `ContentRepositoryConfigError` imported but never used
- `pawbot/channels/qq.py:16,25` — duplicate `C2CMessage` import

**Depends on:** Phases 1–4 complete (feishu import already fixed in Phase 2).
Read `pawbot/contracts.py` fully before editing.

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `pawbot/channels/discord.py` — extract `_download_attachments()` |
| **EDIT** | `mcp-servers/browser/server.py` — remove 2 unused variable assignments |
| **EDIT** | `mcp-servers/app_control/server.py` — remove unused `Image` import |
| **EDIT** | `pawbot/channels/matrix.py` — remove unused `ContentRepositoryConfigError` |
| **EDIT** | `pawbot/channels/qq.py` — remove duplicate `C2CMessage` import |
| **CREATE** | `tests/test_discord_helpers.py` — 4 tests |
| **CREATE** | `tests/test_dead_code_removal.py` — 4 verification tests |

---

## Fix 1 — Extract `_download_attachments()` from Discord

```python
async def _download_attachments(
    self,
    attachments: list[dict],
    media_dir: "Path",
) -> tuple[list[str], list[str]]:
    """
    Download all Discord message attachments to local storage.

    Returns (content_descriptions, media_paths).
    content_descriptions: list of strings describing each attachment
    media_paths:          list of local file paths for downloaded files

    Extracted from _handle_message_create to reduce CC from 21.
    CC = 4.
    """
    from pathlib import Path
    content_parts: list[str] = []
    media_paths:   list[str] = []

    for attachment in attachments:
        url      = attachment.get("url")
        filename = attachment.get("filename") or "attachment"
        size     = attachment.get("size") or 0

        if not url or not self._http:
            continue
        if size and size > self.MAX_ATTACHMENT_BYTES:
            content_parts.append(f"[attachment: {filename} - too large]")
            continue

        try:
            media_dir.mkdir(parents=True, exist_ok=True)
            safe_name  = filename.replace("/", "_")
            file_path  = media_dir / f"{attachment.get('id', 'file')}_{safe_name}"
            resp       = await self._http.get(url)
            resp.raise_for_status()
            file_path.write_bytes(resp.content)
            media_paths.append(str(file_path))
            content_parts.append(f"[attachment: {file_path}]")
        except Exception as exc:
            logger.warning("Failed to download Discord attachment {}: {}", filename, exc)
            content_parts.append(f"[attachment: {filename} - download failed]")

    return content_parts, media_paths
```

Then in `_handle_message_create`, replace the attachment loop with:
```python
# REPLACE the attachment loop with:
media_dir   = Path.home() / ".pawbot" / "media"
attachment_descs, media_paths = await self._download_attachments(
    payload.get("attachments") or [], media_dir
)
content_parts.extend(attachment_descs)
```

---

## Fix 2 — browser/server.py dead variables

```python
# Line 259: FIND and REMOVE the unused variable assignment
# BEFORE:
restore_session = await browser.contexts[0].storage_state()   # unused, never read

# AFTER: simply remove the assignment entirely.
# If the storage_state() call itself is needed, keep the call without assignment:
await browser.contexts[0].storage_state()

# Line 772: FIND and REMOVE
# BEFORE:
full_page = kwargs.get("full_page", False)   # assigned but never passed to screenshot()

# AFTER: pass the value directly where needed, or remove if the option is not used:
# Check if full_page is actually needed in the screenshot call. If not, remove the line.
```

---

## Fix 3 — Remove unused imports

```python
# mcp-servers/app_control/server.py line 41:
# REMOVE:
from PIL import Image   # ← Vulture: never used

# pawbot/channels/matrix.py line 14:
# REMOVE:
from nio import ContentRepositoryConfigError   # ← Vulture: never used

# pawbot/channels/qq.py — duplicate C2CMessage:
# FIND: two separate imports of C2CMessage (lines 16 and 25)
# KEEP only one. Remove the duplicate.
```

---

## File — CREATE `tests/test_discord_helpers.py`

```python
"""
tests/test_discord_helpers.py

Tests for the refactored Discord attachment downloader.
Run: pytest tests/test_discord_helpers.py -v
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from pawbot.channels.discord import DiscordChannel


def _make_discord_channel() -> DiscordChannel:
    ch                       = object.__new__(DiscordChannel)
    ch.config                = MagicMock()
    ch.config.allow_from     = []
    ch.bus                   = MagicMock()
    ch._http                 = AsyncMock()
    ch._bot_user_id          = "BOT001"
    ch.MAX_ATTACHMENT_BYTES  = 10 * 1024 * 1024   # 10MB
    return ch


@pytest.mark.asyncio
async def test_downloads_attachment_successfully(tmp_path):
    """Valid attachment must be downloaded and path added to media_paths."""
    ch = _make_discord_channel()

    mock_response           = MagicMock()
    mock_response.content   = b"fake_image_bytes"
    mock_response.raise_for_status = MagicMock()
    ch._http.get            = AsyncMock(return_value=mock_response)

    attachments = [{"id": "123", "url": "https://example.com/img.png",
                    "filename": "img.png", "size": 100}]
    descs, paths = await ch._download_attachments(attachments, tmp_path)

    assert len(paths) == 1
    assert Path(paths[0]).exists()
    assert "[attachment:" in descs[0]


@pytest.mark.asyncio
async def test_too_large_attachment_skipped(tmp_path):
    """Attachments over MAX_ATTACHMENT_BYTES must be skipped with a description."""
    ch           = _make_discord_channel()
    attachments  = [{"id": "456", "url": "https://example.com/big.zip",
                     "filename": "big.zip", "size": 999_999_999}]
    descs, paths = await ch._download_attachments(attachments, tmp_path)

    assert paths == []
    assert "too large" in descs[0]


@pytest.mark.asyncio
async def test_download_failure_handled_gracefully(tmp_path):
    """HTTP failure must produce a 'download failed' description — not raise."""
    ch              = _make_discord_channel()
    ch._http.get    = AsyncMock(side_effect=Exception("network error"))
    attachments     = [{"id": "789", "url": "https://example.com/file.pdf",
                        "filename": "file.pdf", "size": 500}]
    descs, paths = await ch._download_attachments(attachments, tmp_path)

    assert paths == []
    assert "download failed" in descs[0]


@pytest.mark.asyncio
async def test_empty_attachments_returns_empty_lists(tmp_path):
    """No attachments → both return lists must be empty."""
    ch           = _make_discord_channel()
    descs, paths = await ch._download_attachments([], tmp_path)
    assert descs == []
    assert paths == []
```

---

## File — CREATE `tests/test_dead_code_removal.py`

```python
"""
tests/test_dead_code_removal.py

Verifies that all Vulture dead-code findings have been removed.
Run: pytest tests/test_dead_code_removal.py -v
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_app_control_no_pil_image_import():
    """
    PIL.Image was imported but never used in app_control/server.py.
    Removing it eliminates an unnecessary hard dependency on Pillow.
    """
    source = _read("mcp-servers/app_control/server.py")
    assert "from PIL import Image" not in source, \
        "Unused 'from PIL import Image' must be removed from app_control/server.py"


def test_matrix_no_content_repository_config_error_import():
    """
    ContentRepositoryConfigError was imported but never used in channels/matrix.py.
    """
    source = _read("pawbot/channels/matrix.py")
    assert "ContentRepositoryConfigError" not in source, \
        "Unused ContentRepositoryConfigError import must be removed from matrix.py"


def test_qq_no_duplicate_c2c_message_import():
    """
    C2CMessage was imported twice in channels/qq.py.
    Only one import is allowed.
    """
    source = _read("pawbot/channels/qq.py")
    count  = source.count("C2CMessage")
    # It may still be used — we just want no duplicate import
    import_lines = [l for l in source.splitlines() if "C2CMessage" in l and "import" in l]
    assert len(import_lines) <= 1, \
        f"C2CMessage should appear in at most 1 import line, found: {import_lines}"


def test_browser_no_restore_session_assignment():
    """
    'restore_session' was assigned but never read in browser/server.py.
    The variable (not the call) must be removed.
    """
    source = _read("mcp-servers/browser/server.py")
    for i, line in enumerate(source.splitlines()):
        stripped = line.strip()
        if stripped.startswith("restore_session") and "=" in stripped and "==" not in stripped:
            raise AssertionError(
                f"browser/server.py line {i+1}: 'restore_session = ...' — "
                "this variable is never read. Remove the assignment."
            )
```

---

## Test Matrix

| # | Test | Input | Expected | Pass Condition |
|---|------|-------|----------|----------------|
| T1 (discord) | Download succeeds | Valid URL + mock response | File created, path in list | `Path.exists()` |
| T2 (discord) | Oversized skipped | `size > MAX` | Empty paths, "too large" desc | Size guard works |
| T3 (discord) | HTTP failure | `get()` raises | Empty paths, "download failed" | No exception raised |
| T4 (discord) | Empty input | `[]` | `([], [])` | Both lists empty |
| T5 (dead code) | `PIL.Image` removed | Source scan | Not present | String not in file |
| T6 (dead code) | `ContentRepositoryConfigError` removed | Source scan | Not present | String not in file |
| T7 (dead code) | `C2CMessage` not duplicated | Import lines | ≤ 1 import line | Count check |
| T8 (dead code) | `restore_session` assignment removed | Source scan | No assignment | Pattern not found |

---

## ⛔ Acceptance Gate — Phase 5 (Section 3 Final Gate)

```bash
pytest tests/test_channel_manager.py \
       tests/test_feishu_helpers.py \
       tests/test_slack_helpers.py \
       tests/test_email_helpers.py \
       tests/test_discord_helpers.py \
       tests/test_dead_code_removal.py \
       -v
```

- [ ] All **28 tests** pass across all 5 phases
- [ ] PHASE 1: `grep -c "if self.config.channels" pawbot/channels/manager.py` → **0**
- [ ] PHASE 2: `grep -c "elif tag ==" pawbot/channels/feishu.py` → **0**
- [ ] PHASE 3: `_on_socket_request` is ≤ 25 lines (was ~65)
- [ ] PHASE 4: `_fetch_messages` is ≤ 40 lines (was ~65)
- [ ] PHASE 5: `python -m vulture pawbot/ mcp-servers/ --min-confidence 80` → 0 findings from this section
- [ ] COMBINED: `python -m pytest tests/test_channels.py tests/test_channel_manager.py -q` → all pass

---

**Section 3 is complete when all of the above are verified.**
Signal Section 4 agent that it can proceed.

> **Remember:** Every name — every class, enum, constant, path, and config key —
> comes from `pawbot/contracts.py`. Read it first. Never invent new names.

---

*End of Section 3 Fix Document — itsamiitt/pawbot — March 2026*
