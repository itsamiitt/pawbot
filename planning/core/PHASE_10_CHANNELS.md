# PHASE 10 — CHANNELS & COMMUNICATION
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)
> **Implementation Days:** Day 19 (10.1 WhatsApp), Weeks 5–8 (10.2 Telegram, 10.3 Email)
> **Primary Files:** `~/nanobot/channels/` (existing adapters — enhance), `~/nanobot/bus/` (MessageBus)
> **Test File:** `~/nanobot/tests/test_channels.py`
> **Depends on:** Phase 1 (MemoryRouter — save channel context), Phase 2 (AgentLoop.process()), Phase 11 (HeartbeatEngine — proactive channel messages)

---

## BEFORE YOU START — READ THESE FILES

```bash
cat ~/nanobot/channels/whatsapp.py   # existing WhatsApp adapter — preserve interface
cat ~/nanobot/channels/telegram.py   # existing Telegram adapter — preserve interface
cat ~/nanobot/bus/                   # MessageBus current implementation
cat ~/nanobot/agent/loop.py          # AgentLoop.process() signature — channels call this
cat ~/.nanobot/config.json           # see existing channel config keys
```

**Existing interfaces to preserve:** Every channel adapter's `send()` and `on_message()` methods. The `MessageBus.publish()` and `MessageBus.subscribe()` methods.

---

## WHAT YOU ARE BUILDING

Enhanced channel adapters that: persist conversation history per contact, support rich message types (images, files, voice), rate-limit outbound messages, queue messages when the agent is busy, and allow the agent to proactively message contacts via HeartbeatEngine triggers.

---

## CANONICAL NAMES — ALL NEW CLASSES IN THIS PHASE

| Class Name | File | Purpose |
|---|---|---|
| `ChannelMessage` | `channels/base.py` | Unified message dataclass across all channels |
| `BaseChannel` | `channels/base.py` | Abstract base class all adapters inherit |
| `WhatsAppChannel` | `channels/whatsapp.py` | Enhanced WhatsApp adapter |
| `TelegramChannel` | `channels/telegram.py` | Enhanced Telegram adapter |
| `EmailChannel` | `channels/email.py` | New email channel |
| `MessageBus` | `bus/message_bus.py` | Central message routing hub |
| `ChannelRouter` | `bus/router.py` | Routes messages to correct channel adapter |
| `RateLimiter` | `channels/base.py` | Per-channel outbound rate limiting |
| `MessageQueue` | `bus/queue.py` | Async queue for when agent is busy |

---

## FEATURE 10.1 — ENHANCED WHATSAPP ADAPTER

### Step 1 — Shared Base Classes

Create `~/nanobot/channels/base.py`:

```python
from dataclasses import dataclass, field
from typing import Optional
from abc import ABC, abstractmethod
import time, json, logging

logger = logging.getLogger("nanobot")

@dataclass
class ChannelMessage:
    """Unified message format across all channel adapters."""
    id: str                          # channel-specific message ID
    channel: str                     # "whatsapp" | "telegram" | "email"
    contact_id: str                  # sender identifier (phone, username, email)
    contact_name: str                # human-readable display name
    text: str                        # message text content
    timestamp: int = field(default_factory=lambda: int(time.time()))
    media_type: Optional[str] = None # "image" | "audio" | "file" | None
    media_path: Optional[str] = None # local path to downloaded media
    reply_to_id: Optional[str] = None
    is_group: bool = False
    group_id: Optional[str] = None
    raw: dict = field(default_factory=dict)  # original payload from channel API

    def to_memory_dict(self) -> dict:
        """Format for saving to MemoryRouter as a 'message' type."""
        return {
            "channel": self.channel,
            "contact_id": self.contact_id,
            "contact_name": self.contact_name,
            "text": self.text,
            "timestamp": self.timestamp,
            "media_type": self.media_type,
        }


class RateLimiter:
    """Token-bucket rate limiter for outbound messages."""

    def __init__(self, messages_per_minute: int = 10):
        self.rate = messages_per_minute
        self.tokens = messages_per_minute
        self.last_refill = time.time()

    def consume(self) -> bool:
        """Returns True if message can be sent now, False if rate-limited."""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.rate, self.tokens + elapsed * (self.rate / 60))
        self.last_refill = now

        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    def wait_time(self) -> float:
        """Seconds to wait until next token available."""
        return max(0, (1 - self.tokens) * (60 / self.rate))


class BaseChannel(ABC):
    """Abstract base class for all channel adapters."""

    def __init__(self, config: dict, memory_router=None):
        self.config = config
        self.memory = memory_router
        self.rate_limiter = RateLimiter(
            config.get("messages_per_minute", 10)
        )

    @abstractmethod
    def send(self, contact_id: str, text: str, **kwargs) -> bool:
        """Send a text message. Returns True if sent."""

    @abstractmethod
    def send_file(self, contact_id: str, file_path: str, caption: str = "") -> bool:
        """Send a file/image attachment."""

    @abstractmethod
    def on_message(self, handler):
        """Register a callback: handler(ChannelMessage) -> str (response text)."""

    def _save_to_memory(self, msg: ChannelMessage, response: str = ""):
        """Save message exchange to MemoryRouter as 'message' type."""
        if self.memory:
            self.memory.save("message", {
                **msg.to_memory_dict(),
                "response": response,
            })
```

### Step 2 — Enhanced WhatsAppChannel

Modify `~/nanobot/channels/whatsapp.py` — keep all existing public method signatures, add these enhancements:

**Contact history persistence:**
```python
def get_contact_history(self, contact_id: str, limit: int = 20) -> list[dict]:
    """
    Return recent message history for a contact from MemoryRouter.
    Used to provide context when agent responds.
    """
    if not self.memory:
        return []
    return self.memory.search(f"contact_id:{contact_id}", limit=limit)
```

**Media download on receive:**
When an incoming message contains media (image, audio, document):
1. Download media to `~/.nanobot/downloads/{contact_id}/{timestamp}_{filename}`
2. Set `ChannelMessage.media_path` to the local file path
3. Set `ChannelMessage.media_type` appropriately
4. If audio: attempt transcription via `faster-whisper` (see Feature 10.1B below)

**Typing indicator:**
```python
def send_typing(self, contact_id: str, duration_seconds: float = 2.0):
    """Send typing indicator before the actual response."""
    # WhatsApp Business API: POST /messages with type="reaction" or typing state
    # Delay actual send by duration_seconds to appear human
    pass
```

**Rate-limited send:**
Wrap existing `send()` to check `self.rate_limiter.consume()` before each outbound message. If rate-limited, log warning and queue the message in `MessageQueue`.

### Step 3 — Voice Transcription (Feature 10.1B)

When a WhatsApp voice message is received:

```python
def _transcribe_audio(self, audio_path: str) -> str:
    """
    Transcribe voice message to text using faster-whisper.
    Returns empty string if transcription fails.
    """
    try:
        from faster_whisper import WhisperModel
        model_size = self.config.get("whisper_model", "base")
        model = WhisperModel(model_size, compute_type="int8")
        segments, _ = model.transcribe(audio_path)
        return " ".join(seg.text.strip() for seg in segments)
    except Exception as e:
        logger.warning(f"Voice transcription failed: {e}")
        return ""
```

Add `faster-whisper>=0.10.0` to `pyproject.toml`.

The transcribed text is appended to `ChannelMessage.text` as: `"[Voice message transcription]: {transcribed_text}"`

---

## FEATURE 10.2 — ENHANCED TELEGRAM ADAPTER

Modify `~/nanobot/channels/telegram.py` — same pattern as WhatsApp, add:

**Inline keyboard support:**
```python
def send_with_buttons(
    self,
    chat_id: str,
    text: str,
    buttons: list[list[dict]],  # [[{"text": "Yes", "callback_data": "yes"}, ...], ...]
) -> bool:
    """Send message with inline keyboard buttons."""
```

**Command handler registration:**
```python
def register_command(self, command: str, handler):
    """
    Register a /command handler.
    command: "start" | "help" | "status" | any custom command
    handler: fn(ChannelMessage) -> str
    """
```

**Group message filtering:**
```python
MENTION_PATTERNS = ["@nanobot", "/nanobot"]

def _should_respond(self, msg: ChannelMessage) -> bool:
    """
    In group chats: only respond if bot is mentioned or directly replied to.
    In private chats: always respond.
    """
    if not msg.is_group:
        return True
    return (
        any(p in msg.text for p in self.MENTION_PATTERNS)
        or msg.reply_to_id is not None
    )
```

**Message editing:**
```python
def edit_message(self, chat_id: str, message_id: str, new_text: str) -> bool:
    """Edit a previously sent message (for progressive updates)."""
```

**Progress updates:**
```python
def send_progress(self, chat_id: str, task_description: str, step: int, total: int) -> str:
    """
    Send or update a progress message.
    Returns message_id for subsequent edit_message() calls.
    Progress bar: ████████░░ 80%
    """
    pct = int((step / total) * 10)
    bar = "█" * pct + "░" * (10 - pct)
    text = f"{task_description}\n[{bar}] {step}/{total}"
    # If this is step 1, send new message and return its ID
    # If step > 1, edit the existing message
```

---

## FEATURE 10.3 — EMAIL CHANNEL

Create `~/nanobot/channels/email.py`:

```python
class EmailChannel(BaseChannel):
    """
    IMAP/SMTP email channel.
    Polls inbox every N minutes for new messages.
    Sends replies via SMTP.
    """

    def __init__(self, config: dict, memory_router=None):
        super().__init__(config, memory_router)
        email_cfg = config.get("channels", {}).get("email", {})
        self.smtp_host = email_cfg.get("smtp_host", "smtp.gmail.com")
        self.smtp_port = email_cfg.get("smtp_port", 587)
        self.imap_host = email_cfg.get("imap_host", "imap.gmail.com")
        self.imap_port = email_cfg.get("imap_port", 993)
        self.address  = email_cfg.get("address", "")
        self.password = email_cfg.get("password", "")  # from config, never hardcoded
        self.poll_interval = email_cfg.get("poll_interval_minutes", 5)
        self._handler = None
        self._polling = False
```

**Receive (IMAP polling):**
```python
def _poll_inbox(self):
    """Poll IMAP inbox. Runs in background thread."""
    import imaplib, email as emaillib
    while self._polling:
        try:
            conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            conn.login(self.address, self.password)
            conn.select("INBOX")

            # Search for UNSEEN messages
            _, uids = conn.search(None, "UNSEEN")
            for uid in uids[0].split():
                _, data = conn.fetch(uid, "(RFC822)")
                msg = emaillib.message_from_bytes(data[0][1])
                channel_msg = self._parse_email(msg, uid)
                if self._handler and channel_msg:
                    response = self._handler(channel_msg)
                    if response:
                        self.send(channel_msg.contact_id, response,
                                  subject=f"Re: {msg['Subject']}")
            conn.logout()
        except Exception as e:
            logger.warning(f"Email poll failed: {e}")
        time.sleep(self.poll_interval * 60)
```

**Send (SMTP):**
```python
def send(self, contact_id: str, text: str, subject: str = "From Nanobot", **kwargs) -> bool:
    """Send email via SMTP. contact_id is the recipient email address."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    if not self.rate_limiter.consume():
        logger.warning(f"Email rate-limited to {contact_id}")
        return False

    msg = MIMEMultipart()
    msg["From"] = self.address
    msg["To"] = contact_id
    msg["Subject"] = subject
    msg.attach(MIMEText(text, "plain"))

    try:
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.starttls()
            server.login(self.address, self.password)
            server.sendmail(self.address, contact_id, msg.as_string())
        logger.info(f"Email sent to {contact_id}")
        return True
    except Exception as e:
        logger.warning(f"Email send failed: {e}")
        return False
```

---

## FEATURE 10.4 — MESSAGE BUS AND ROUTING

Enhance `~/nanobot/bus/message_bus.py`:

### `MessageQueue` class

Create `~/nanobot/bus/queue.py`:

```python
import queue, threading, time, logging

logger = logging.getLogger("nanobot")

class MessageQueue:
    """
    FIFO queue for messages when agent is busy processing another task.
    Drains automatically when agent becomes free.
    """

    def __init__(self, max_size: int = 100):
        self._q = queue.Queue(maxsize=max_size)
        self._processing = False

    def enqueue(self, msg, channel_adapter) -> bool:
        """Add message to queue. Returns False if queue full."""
        try:
            self._q.put_nowait((msg, channel_adapter))
            logger.info(f"Message queued from {msg.contact_id} (queue size: {self._q.qsize()})")
            return True
        except queue.Full:
            logger.warning("Message queue full — dropping message")
            return False

    def drain(self, agent_loop):
        """
        Process all queued messages in order.
        Called by AgentLoop when it finishes a task.
        """
        while not self._q.empty():
            try:
                msg, channel = self._q.get_nowait()
                response = agent_loop.process(msg.text, context={"channel_msg": msg})
                channel.send(msg.contact_id, response)
                self._q.task_done()
            except Exception as e:
                logger.warning(f"Queue drain error: {e}")

    @property
    def size(self) -> int:
        return self._q.qsize()
```

### `ChannelRouter` class

Create `~/nanobot/bus/router.py`:

```python
class ChannelRouter:
    """
    Routes incoming messages from any channel to AgentLoop.
    Routes AgentLoop responses back to the originating channel.
    """

    def __init__(self, agent_loop, memory_router, config: dict):
        self.loop = agent_loop
        self.memory = memory_router
        self.config = config
        self._channels: dict[str, BaseChannel] = {}
        self.queue = MessageQueue()

    def register(self, name: str, channel: BaseChannel):
        """Register a channel adapter. name: 'whatsapp' | 'telegram' | 'email'"""
        self._channels[name] = name
        channel.on_message(lambda msg: self._handle(msg, channel))
        logger.info(f"Channel registered: {name}")

    def _handle(self, msg: ChannelMessage, channel: BaseChannel) -> str:
        """
        Called by channel adapter when a message arrives.
        Routes to AgentLoop if free, queues if busy.
        """
        # Save incoming message to memory
        if self.memory:
            self.memory.save("message", msg.to_memory_dict())

        # Build context for AgentLoop
        history = []
        if self.memory:
            history = self.memory.search(f"contact_id:{msg.contact_id}", limit=10)

        context = {
            "channel": msg.channel,
            "contact_id": msg.contact_id,
            "contact_name": msg.contact_name,
            "history": history,
            "channel_msg": msg,
        }

        if self.loop.is_busy():
            self.queue.enqueue(msg, channel)
            return ""

        response = self.loop.process(msg.text, context=context)

        # Save response to memory
        if self.memory and response:
            self.memory.save("message", {
                "channel": msg.channel,
                "contact_id": msg.contact_id,
                "direction": "outbound",
                "text": response,
            })

        return response
```

---

## CONFIG KEYS TO ADD

Add to `~/.nanobot/config.json`:

```json
{
  "channels": {
    "whatsapp": {
      "enabled": false,
      "api_key": "",
      "phone_number_id": "",
      "verify_token": "",
      "messages_per_minute": 10,
      "whisper_model": "base",
      "save_media": true,
      "media_dir": "~/.nanobot/downloads"
    },
    "telegram": {
      "enabled": false,
      "bot_token": "",
      "messages_per_minute": 20,
      "respond_in_groups": false,
      "mention_triggers": ["@nanobot"]
    },
    "email": {
      "enabled": false,
      "address": "",
      "password": "",
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "imap_host": "imap.gmail.com",
      "imap_port": 993,
      "poll_interval_minutes": 5,
      "messages_per_minute": 5,
      "allowed_senders": []
    }
  }
}
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_channels.py`

```python
class TestChannelMessage:
    def test_to_memory_dict_format()
    def test_dataclass_defaults()

class TestRateLimiter:
    def test_allows_within_rate()
    def test_blocks_when_exceeded()
    def test_tokens_refill_over_time()

class TestBaseChannel:
    def test_save_to_memory_called_on_receive()
    def test_rate_limiter_consulted_on_send()

class TestWhatsAppChannel:
    def test_contact_history_queries_memory()
    def test_voice_transcription_appends_to_text()
    def test_send_queued_when_rate_limited()

class TestTelegramChannel:
    def test_group_message_ignored_without_mention()
    def test_group_message_handled_with_mention()
    def test_private_message_always_handled()
    def test_progress_bar_format()

class TestEmailChannel:
    def test_smtp_send_uses_config_credentials()
    def test_imap_marks_read_after_processing()
    def test_send_rate_limited()

class TestMessageQueue:
    def test_enqueue_and_drain_in_order()
    def test_full_queue_drops_gracefully()
    def test_drain_calls_agent_loop()

class TestChannelRouter:
    def test_routes_to_agent_loop_when_free()
    def test_queues_when_agent_busy()
    def test_saves_inbound_to_memory()
    def test_saves_outbound_to_memory()
```

---

## CROSS-REFERENCES

- **Phase 1** (MemoryRouter): Each channel calls `memory.save("message", {...})` and `memory.search(f"contact_id:{id}")` for history
- **Phase 2** (AgentLoop): `ChannelRouter._handle()` calls `loop.process(text, context=...)` and `loop.is_busy()` — AgentLoop must expose `is_busy()` method
- **Phase 11** (HeartbeatEngine): HeartbeatEngine triggers proactive messages via `ChannelRouter` — register as `channel_router.send_proactive(contact_id, text, channel)`
- **Phase 14** (Security): `ChannelRouter._handle()` must pass messages through `ActionGate` before routing to agent
- **Phase 15** (Observability): Wrap `_handle()` with trace span

All canonical names are in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
