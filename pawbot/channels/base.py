"""Base channel interface for chat platforms.

Phase 10 additions:
  - ChannelMessage  (unified message dataclass)
  - RateLimiter     (token-bucket outbound rate limiter)
  - _save_to_memory (memory persistence helper on BaseChannel)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
import time

from loguru import logger

from pawbot.bus.events import InboundMessage, OutboundMessage
from pawbot.bus.queue import MessageBus


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 10 — ChannelMessage
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class ChannelMessage:
    """Unified message format across all channel adapters.

    Wraps the raw payload from any channel into a single consistent shape
    that the ChannelRouter and agent loop can work with.
    """

    id: str                                      # channel-specific message ID
    channel: str                                  # "whatsapp" | "telegram" | "email"
    contact_id: str                               # sender identifier
    contact_name: str                             # human-readable display name
    text: str                                     # message text content
    timestamp: int = field(default_factory=lambda: int(time.time()))
    media_type: Optional[str] = None              # "image" | "audio" | "file" | None
    media_path: Optional[str] = None              # local path to downloaded media
    reply_to_id: Optional[str] = None
    is_group: bool = False
    group_id: Optional[str] = None
    raw: dict = field(default_factory=dict)        # original payload from channel API

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

    def to_inbound(self) -> InboundMessage:
        """Convert to an InboundMessage for the MessageBus."""
        return InboundMessage(
            channel=self.channel,
            sender_id=self.contact_id,
            chat_id=self.group_id or self.contact_id,
            content=self.text,
            media=[self.media_path] if self.media_path else [],
            metadata=self.raw,
        )


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 10 — RateLimiter
# ══════════════════════════════════════════════════════════════════════════════


class RateLimiter:
    """Token-bucket rate limiter for outbound messages."""

    def __init__(self, messages_per_minute: int = 10):
        self.rate = messages_per_minute
        self.tokens = float(messages_per_minute)
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
        return max(0.0, (1 - self.tokens) * (60 / self.rate))

    def reset(self) -> None:
        """Reset to full bucket."""
        self.tokens = float(self.rate)
        self.last_refill = time.time()


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.

    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the pawbot message bus.

    Phase 10 additions:
      - self.rate_limiter — outbound rate limiter
      - self.memory       — optional MemoryRouter reference
      - _save_to_memory() — persist message/response pairs
      - send_file()       — abstract file/image attachment
    """

    name: str = "base"

    def __init__(self, config: Any, bus: MessageBus, memory_router: Any = None):
        """
        Initialize the channel.

        Args:
            config: Channel-specific configuration.
            bus: The message bus for communication.
            memory_router: Optional MemoryRouter for conversation persistence.
        """
        self.config = config
        self.bus = bus
        self.memory = memory_router
        self._running = False

        # Phase 10 — outbound rate limiting
        rate_cfg = getattr(config, "messages_per_minute", None)
        if rate_cfg is None:
            rate_cfg = config.get("messages_per_minute", 10) if isinstance(config, dict) else 10
        self.rate_limiter = RateLimiter(int(rate_cfg))

    @abstractmethod
    async def start(self) -> None:
        """
        Start the channel and begin listening for messages.

        This should be a long-running async task that:
        1. Connects to the chat platform
        2. Listens for incoming messages
        3. Forwards messages to the bus via _handle_message()
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message through this channel.

        Args:
            msg: The message to send.
        """
        pass

    def send_file(self, contact_id: str, file_path: str, caption: str = "") -> bool:
        """Send a file/image attachment (override in subclass)."""
        logger.warning("send_file not implemented for channel %s", self.name)
        return False

    def is_allowed(self, sender_id: str) -> bool:
        """
        Check if a sender is allowed to use this bot.

        Args:
            sender_id: The sender's identifier.

        Returns:
            True if allowed, False otherwise.
        """
        allow_list = getattr(self.config, "allow_from", [])

        # If no allow list, allow everyone
        if not allow_list:
            return True

        sender_str = str(sender_id)
        if sender_str in allow_list:
            return True
        if "|" in sender_str:
            for part in sender_str.split("|"):
                if part and part in allow_list:
                    return True
        return False

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        """
        Handle an incoming message from the chat platform.

        This method checks permissions and forwards to the bus.

        Args:
            sender_id: The sender's identifier.
            chat_id: The chat/channel identifier.
            content: Message text content.
            media: Optional list of media URLs.
            metadata: Optional channel-specific metadata.
            session_key: Optional session key override (e.g. thread-scoped sessions).
        """
        if not self.is_allowed(sender_id):
            logger.warning(
                "Access denied for sender {} on channel {}. "
                "Add them to allowFrom list in config to grant access.",
                sender_id, self.name,
            )
            return

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {},
            session_key_override=session_key,
        )

        await self.bus.publish_inbound(msg)

    # ── Phase 10: Memory persistence ──────────────────────────────────────

    def _save_to_memory(self, msg: ChannelMessage, response: str = "") -> None:
        """Save message exchange to MemoryRouter as 'message' type."""
        if self.memory:
            try:
                self.memory.save("message", {
                    **msg.to_memory_dict(),
                    "response": response,
                })
            except Exception as exc:
                logger.warning("Failed to save message to memory: {}", exc)

    def get_contact_history(self, contact_id: str, limit: int = 20) -> list[dict]:
        """Return recent message history for a contact from MemoryRouter."""
        if not self.memory:
            return []
        try:
            return self.memory.search(f"contact_id:{contact_id}", limit=limit)
        except Exception as e:  # noqa: F841
            return []

    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return self._running
