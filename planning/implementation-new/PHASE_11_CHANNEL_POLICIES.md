# Phase 11 — Channel Policies & Media Management

> **Goal:** Add enterprise-grade channel policies — DM allowlists, group policies, message debounce, media limits, ack reactions, and message queuing.  
> **Duration:** 7-10 days  
> **Risk Level:** Medium (extends existing channel system, no breaking changes)  
> **Depends On:** Phase 4 (channel maturity), Phase 0 (config schema)

---

## Why This Phase Exists

OpenClaw has granular per-channel policies:
- **DM policies:** `allowlist` (only specific numbers), `pairing` (device pairing required)
- **Group policies:** `allowlist`, `disabled`
- **Self-chat mode:** Bot can message itself for testing
- **Message debounce:** Configurable per channel (`debounceMs: 0`)
- **Media limits:** `mediaMaxMb: 50`
- **Ack reactions:** `ackReactionScope: "group-mentions"` (react to confirm receipt)

PawBot Phase 4 focuses on reconnection reliability but has **none of these policy features**.

---

## 11.1 — Channel Policy Schema

**File:** `pawbot/config/schema.py` — add:

```python
class DMPolicy(str, Enum):
    """Direct message handling policy."""
    OPEN = "open"               # Accept DMs from anyone
    ALLOWLIST = "allowlist"     # Only from allowed_users list
    PAIRING = "pairing"         # Require device pairing first
    DISABLED = "disabled"       # Reject all DMs


class GroupPolicy(str, Enum):
    """Group/channel message handling policy."""
    OPEN = "open"               # Respond in all groups
    ALLOWLIST = "allowlist"     # Only in allowed_groups list
    MENTION_ONLY = "mention"    # Only when @mentioned
    DISABLED = "disabled"       # Ignore all group messages


class AckReactionScope(str, Enum):
    """When to add "acknowledged" reaction to messages."""
    NONE = "none"               # Never react
    ALL = "all"                 # React to every message
    GROUP_MENTIONS = "group-mentions"  # Only when mentioned in groups
    DMS_ONLY = "dms-only"       # Only in DMs


class MediaConfig(BaseModel):
    """Media handling configuration."""
    max_size_mb: int = 50               # Max inbound media size
    allowed_types: list[str] = Field(
        default_factory=lambda: [
            "image/jpeg", "image/png", "image/gif", "image/webp",
            "audio/ogg", "audio/mpeg", "audio/mp4",
            "video/mp4",
            "application/pdf",
            "text/plain", "text/csv",
        ]
    )
    auto_transcribe_voice: bool = True   # Transcribe voice messages
    auto_ocr_images: bool = False        # OCR images (requires tesseract)
    download_dir: str = ""               # Where to save (empty = temp)
    retention_days: int = 30             # Auto-delete after N days


class ChannelPolicyConfig(BaseModel):
    """Unified policy configuration for any channel."""
    dm_policy: DMPolicy = DMPolicy.OPEN
    allowed_users: list[str] = Field(default_factory=list)   # For allowlist policy
    
    group_policy: GroupPolicy = GroupPolicy.MENTION_ONLY
    allowed_groups: list[str] = Field(default_factory=list)  # For allowlist policy
    require_mention: bool = True         # Require @mention in groups
    
    self_chat_mode: bool = False         # Allow bot to chat with itself
    
    debounce_ms: int = 500               # Wait N ms for follow-up messages before processing
    rate_limit_per_user: int = 30        # Max messages per minute per user
    
    ack_reactions: AckReactionScope = AckReactionScope.NONE
    typing_indicator: bool = True        # Show "typing..." while processing
    
    media: MediaConfig = Field(default_factory=MediaConfig)
    
    max_response_length: int = 4096      # Truncate long responses
    split_long_messages: bool = True     # Split by paragraphs if too long
```

### Example config per channel:

```json
{
  "channels": {
    "whatsapp": {
      "enabled": true,
      "policy": {
        "dm_policy": "allowlist",
        "allowed_users": ["+919881212483", "+918830722871"],
        "group_policy": "allowlist",
        "self_chat_mode": true,
        "debounce_ms": 0,
        "media": {"max_size_mb": 50},
        "ack_reactions": "group-mentions"
      }
    },
    "telegram": {
      "enabled": true,
      "policy": {
        "dm_policy": "open",
        "group_policy": "mention",
        "require_mention": true,
        "rate_limit_per_user": 20,
        "typing_indicator": true
      }
    },
    "slack": {
      "enabled": true,
      "policy": {
        "dm_policy": "pairing",
        "group_policy": "disabled",
        "ack_reactions": "all"
      }
    }
  }
}
```

---

## 11.2 — Policy Enforcement Engine

**Create:** `pawbot/channels/policy_engine.py`

```python
"""Channel policy enforcement — applies DM/group/rate-limit rules."""

from __future__ import annotations

import time
from typing import Any

from loguru import logger


class PolicyEngine:
    """Evaluates channel policies against incoming messages."""

    def __init__(self, policy_config: dict[str, Any]):
        self.config = policy_config
        self._user_message_counts: dict[str, list[float]] = {}  # user_id -> timestamps
        self._rate_limit = policy_config.get("rate_limit_per_user", 30)
        self._dm_policy = policy_config.get("dm_policy", "open")
        self._group_policy = policy_config.get("group_policy", "mention")
        self._allowed_users = set(policy_config.get("allowed_users", []))
        self._allowed_groups = set(policy_config.get("allowed_groups", []))
        self._self_chat = policy_config.get("self_chat_mode", False)

    def check_dm(self, sender_id: str, bot_id: str = "") -> tuple[bool, str]:
        """Check if a DM should be processed.
        
        Returns:
            (allowed, reason)
        """
        # Self-chat check
        if sender_id == bot_id:
            if self._self_chat:
                return True, "self-chat mode enabled"
            return False, "self-chat mode disabled"

        if self._dm_policy == "open":
            return True, ""
        elif self._dm_policy == "allowlist":
            if sender_id in self._allowed_users:
                return True, ""
            return False, f"user '{sender_id}' not in DM allowlist"
        elif self._dm_policy == "pairing":
            # Check if user has a paired device
            return self._check_pairing(sender_id)
        elif self._dm_policy == "disabled":
            return False, "DMs are disabled"
        return True, ""

    def check_group(
        self,
        group_id: str,
        sender_id: str,
        is_mention: bool = False,
    ) -> tuple[bool, str]:
        """Check if a group message should be processed.
        
        Returns:
            (allowed, reason)
        """
        if self._group_policy == "open":
            pass  # Allow all
        elif self._group_policy == "allowlist":
            if group_id not in self._allowed_groups:
                return False, f"group '{group_id}' not in allowlist"
        elif self._group_policy == "mention":
            if not is_mention:
                return False, "bot not mentioned in group"
        elif self._group_policy == "disabled":
            return False, "group messages are disabled"

        return True, ""

    def check_rate_limit(self, sender_id: str) -> tuple[bool, str]:
        """Check if the user is within their rate limit.
        
        Returns:
            (allowed, reason)
        """
        now = time.time()
        window_start = now - 60  # 1-minute window

        # Get or create user's message history
        if sender_id not in self._user_message_counts:
            self._user_message_counts[sender_id] = []

        # Clean old entries
        timestamps = self._user_message_counts[sender_id]
        self._user_message_counts[sender_id] = [
            t for t in timestamps if t > window_start
        ]

        # Check limit
        if len(self._user_message_counts[sender_id]) >= self._rate_limit:
            return False, f"rate limit exceeded ({self._rate_limit}/min)"

        # Record this message
        self._user_message_counts[sender_id].append(now)
        return True, ""

    def check_media(
        self, file_size_bytes: int, mime_type: str
    ) -> tuple[bool, str]:
        """Check if a media file is allowed.
        
        Returns:
            (allowed, reason)
        """
        media_config = self.config.get("media", {})
        max_bytes = media_config.get("max_size_mb", 50) * 1024 * 1024
        allowed_types = media_config.get("allowed_types", [])

        if file_size_bytes > max_bytes:
            return False, f"file too large ({file_size_bytes / 1024 / 1024:.1f}MB > {media_config.get('max_size_mb', 50)}MB)"

        if allowed_types and mime_type not in allowed_types:
            return False, f"file type '{mime_type}' not allowed"

        return True, ""

    def _check_pairing(self, sender_id: str) -> tuple[bool, str]:
        """Check if user has a paired device."""
        import json
        from pathlib import Path

        paired_file = Path.home() / ".pawbot" / "devices" / "paired.json"
        if not paired_file.exists():
            return False, "no paired devices"

        try:
            data = json.loads(paired_file.read_text())
            paired_users = [d.get("user_id") for d in data.get("devices", [])]
            if sender_id in paired_users:
                return True, ""
            return False, f"user '{sender_id}' has no paired device"
        except Exception:
            return False, "could not read paired devices"
```

---

## 11.3 — Message Debounce

**Create:** `pawbot/channels/debounce.py`

```python
"""Message debouncing — collect rapid follow-up messages into one."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Awaitable

from loguru import logger


class MessageDebouncer:
    """Debounce rapid messages from the same user into a single batch.
    
    When a user sends multiple messages quickly (e.g. typing line-by-line),
    this collects them and delivers as one combined message.
    """

    def __init__(
        self,
        delay_ms: int = 500,
        on_deliver: Callable[[str, str, str], Awaitable[None]] | None = None,
    ):
        self._delay_ms = delay_ms
        self._on_deliver = on_deliver
        self._buffers: dict[str, list[str]] = {}  # session_key -> messages
        self._timers: dict[str, asyncio.Task] = {}

    async def push(self, session_key: str, sender_id: str, content: str) -> None:
        """Push a message into the debounce buffer."""
        if self._delay_ms <= 0:
            # Debounce disabled — deliver immediately
            if self._on_deliver:
                await self._on_deliver(session_key, sender_id, content)
            return

        # Add to buffer
        if session_key not in self._buffers:
            self._buffers[session_key] = []
        self._buffers[session_key].append(content)

        # Cancel existing timer
        if session_key in self._timers:
            self._timers[session_key].cancel()

        # Start new timer
        self._timers[session_key] = asyncio.create_task(
            self._flush_after_delay(session_key, sender_id)
        )

    async def _flush_after_delay(self, session_key: str, sender_id: str) -> None:
        """Wait for the debounce delay, then deliver combined message."""
        await asyncio.sleep(self._delay_ms / 1000.0)

        messages = self._buffers.pop(session_key, [])
        self._timers.pop(session_key, None)

        if not messages:
            return

        combined = "\n".join(messages)
        if len(messages) > 1:
            logger.debug(
                "Debounced {} messages from '{}' into one",
                len(messages), session_key,
            )

        if self._on_deliver:
            await self._on_deliver(session_key, sender_id, combined)

    async def flush_all(self) -> None:
        """Flush all pending buffers (used during shutdown)."""
        for session_key in list(self._timers.keys()):
            timer = self._timers.pop(session_key, None)
            if timer:
                timer.cancel()
        self._buffers.clear()
```

---

## 11.4 — Ack Reactions

**Create:** `pawbot/channels/reactions.py`

```python
"""Acknowledgment reactions — confirm message receipt with emoji reactions."""

from __future__ import annotations

from typing import Any

from loguru import logger


class AckReactor:
    """Adds 'acknowledged' reactions to messages based on policy."""

    # Reaction emojis per channel
    REACTION_MAP = {
        "telegram": "👀",
        "whatsapp": "👀",
        "slack": "eyes",
        "discord": "👀",
        "default": "👀",
    }

    # "Processing complete" reaction
    DONE_REACTION_MAP = {
        "telegram": "✅",
        "whatsapp": "✅",
        "slack": "white_check_mark",
        "discord": "✅",
        "default": "✅",
    }

    def __init__(self, scope: str = "none", channel_type: str = "default"):
        self.scope = scope
        self.channel_type = channel_type
        self._ack_emoji = self.REACTION_MAP.get(channel_type, self.REACTION_MAP["default"])
        self._done_emoji = self.DONE_REACTION_MAP.get(channel_type, self.DONE_REACTION_MAP["default"])

    def should_ack(self, is_dm: bool, is_mention: bool) -> bool:
        """Should we add an acknowledgment reaction?"""
        if self.scope == "none":
            return False
        if self.scope == "all":
            return True
        if self.scope == "group-mentions":
            return is_mention and not is_dm
        if self.scope == "dms-only":
            return is_dm
        return False

    @property
    def ack_emoji(self) -> str:
        return self._ack_emoji

    @property
    def done_emoji(self) -> str:
        return self._done_emoji
```

---

## 11.5 — Long Message Splitting

**Create:** `pawbot/channels/message_splitter.py`

```python
"""Split long messages into channel-appropriate chunks."""

from __future__ import annotations


# Channel-specific message length limits
CHANNEL_LIMITS = {
    "telegram": 4096,
    "whatsapp": 65536,
    "slack": 40000,       # Slack blocks have a 40K char limit
    "discord": 2000,
    "email": 0,           # No limit
    "default": 4096,
}


def split_message(
    text: str,
    channel: str = "default",
    max_length: int = 0,
) -> list[str]:
    """Split a message into chunks appropriate for the channel.
    
    Splits at paragraph boundaries when possible, falls back to
    sentence boundaries, then hard splits at max_length.
    
    Args:
        text: The full message text
        channel: Channel name for looking up limits
        max_length: Override length limit (0 = use channel default)
    
    Returns:
        List of message chunks
    """
    limit = max_length or CHANNEL_LIMITS.get(channel, CHANNEL_LIMITS["default"])

    if limit <= 0 or len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        # Try to split at a paragraph boundary
        split_pos = remaining[:limit].rfind("\n\n")
        if split_pos > limit * 0.3:
            chunks.append(remaining[:split_pos].rstrip())
            remaining = remaining[split_pos:].lstrip()
            continue

        # Try to split at a newline
        split_pos = remaining[:limit].rfind("\n")
        if split_pos > limit * 0.3:
            chunks.append(remaining[:split_pos].rstrip())
            remaining = remaining[split_pos:].lstrip()
            continue

        # Try to split at a sentence boundary
        for sep in [". ", "! ", "? ", "; "]:
            split_pos = remaining[:limit].rfind(sep)
            if split_pos > limit * 0.3:
                chunks.append(remaining[:split_pos + 1].rstrip())
                remaining = remaining[split_pos + 1:].lstrip()
                break
        else:
            # Hard split at a word boundary
            split_pos = remaining[:limit].rfind(" ")
            if split_pos > limit * 0.3:
                chunks.append(remaining[:split_pos])
                remaining = remaining[split_pos + 1:]
            else:
                # Absolute hard split (no word boundary found)
                chunks.append(remaining[:limit])
                remaining = remaining[limit:]

    # Add continuation markers
    if len(chunks) > 1:
        for i in range(len(chunks)):
            chunks[i] = f"{chunks[i]}\n\n_({i + 1}/{len(chunks)})_"

    return chunks
```

---

## 11.6 — Integration with BaseChannel

**File:** `pawbot/channels/base.py` — update `BaseChannel`:

```python
class BaseChannel(ABC):
    """Base class for all channel adapters with policy enforcement."""

    def __init__(self, config, bus, memory_router=None):
        self.config = config
        self.bus = bus
        self.memory_router = memory_router
        self._running = True

        # Initialize policy engine
        policy_cfg = getattr(config, "policy", {})
        if isinstance(policy_cfg, dict):
            self.policy = PolicyEngine(policy_cfg)
        else:
            self.policy = PolicyEngine(policy_cfg.model_dump() if hasattr(policy_cfg, 'model_dump') else {})

        # Initialize debouncer
        debounce_ms = getattr(config, "debounce_ms", 500)
        self.debouncer = MessageDebouncer(
            delay_ms=debounce_ms,
            on_deliver=self._process_debounced_message,
        )

        # Initialize ack reactor
        ack_scope = policy_cfg.get("ack_reactions", "none") if isinstance(policy_cfg, dict) else "none"
        self.ack_reactor = AckReactor(scope=ack_scope, channel_type=self._channel_type())

        # Initialize message splitter
        self._max_response_length = policy_cfg.get("max_response_length", 4096) if isinstance(policy_cfg, dict) else 4096

    async def _handle_incoming(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        is_dm: bool = True,
        is_mention: bool = False,
        media: list | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Process an incoming message through the policy pipeline."""

        # 1. DM/Group policy check
        if is_dm:
            allowed, reason = self.policy.check_dm(sender_id)
        else:
            allowed, reason = self.policy.check_group(chat_id, sender_id, is_mention)
        
        if not allowed:
            logger.debug("Message blocked by policy: {}", reason)
            return

        # 2. Rate limit check
        allowed, reason = self.policy.check_rate_limit(sender_id)
        if not allowed:
            logger.warning("Rate limited user '{}': {}", sender_id, reason)
            return

        # 3. Ack reaction
        if self.ack_reactor.should_ack(is_dm, is_mention):
            await self._add_reaction(chat_id, metadata, self.ack_reactor.ack_emoji)

        # 4. Debounce
        session_key = f"{self._channel_type()}:{chat_id}:{sender_id}"
        await self.debouncer.push(session_key, sender_id, content)

    async def _send_response(self, chat_id: str, text: str, metadata: dict | None = None) -> None:
        """Send a response, splitting if necessary."""
        chunks = split_message(text, channel=self._channel_type(), max_length=self._max_response_length)
        for chunk in chunks:
            await self._send_message(chat_id, chunk, metadata)

    @abstractmethod
    def _channel_type(self) -> str:
        """Return the channel type string (e.g. 'telegram', 'whatsapp')."""
        ...

    @abstractmethod
    async def _send_message(self, chat_id: str, text: str, metadata: dict | None = None) -> None:
        """Send a single message chunk (implemented by each channel)."""
        ...

    async def _add_reaction(self, chat_id: str, metadata: dict | None, emoji: str) -> None:
        """Add a reaction to a message. Override in channel subclass if supported."""
        pass  # Default: no-op (not all channels support reactions)
```

---

## Verification Checklist — Phase 11 Complete

- [ ] `ChannelPolicyConfig` in schema with DM/group/media/ack policies
- [ ] `PolicyEngine` enforces allowlists, rate limits, and media checks
- [ ] `MessageDebouncer` batches rapid messages with configurable delay
- [ ] `AckReactor` adds reaction emojis based on scope policy
- [ ] `split_message()` splits long responses at paragraph/sentence boundaries
- [ ] `BaseChannel` integrates policy, debounce, ack, and splitting pipeline
- [ ] Self-chat mode allows bot to message itself when enabled
- [ ] Rate limiting tracks per-user message counts with 1-minute window
- [ ] Media type and size validation works
- [ ] Device pairing check for `pairing` DM policy
- [ ] All tests pass: `pytest tests/ -v --tb=short`
