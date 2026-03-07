"""Persistent delivery queue for outbound channel messages."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from pawbot.bus.events import OutboundMessage


def get_default_queue_dir() -> Path:
    """Return the on-disk delivery queue directory."""
    return Path.home() / ".pawbot" / "delivery-queue"


class DeliveryStatus:
    """Known message delivery states."""

    PENDING = "pending"
    SENDING = "sending"
    DELIVERED = "delivered"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class DeliveryMessage:
    """Persistent representation of a queued outbound message."""

    channel: str
    recipient: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    status: str = DeliveryStatus.PENDING
    attempts: int = 0
    max_attempts: int = 3
    created_at: float = field(default_factory=time.time)
    last_attempt_at: float = 0.0
    delivered_at: float = 0.0
    error: str = ""
    ttl_seconds: int = 3600
    next_attempt_at: float = 0.0

    def is_expired(self) -> bool:
        """Return True when the message has exceeded its TTL."""
        return time.time() > self.created_at + self.ttl_seconds

    def to_outbound(self) -> OutboundMessage:
        """Convert back into an outbound bus message."""
        return OutboundMessage(
            channel=self.channel,
            chat_id=self.recipient,
            content=self.content,
            reply_to=self.reply_to,
            media=list(self.media),
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this message for persistence."""
        return {
            "message_id": self.message_id,
            "channel": self.channel,
            "recipient": self.recipient,
            "content": self.content,
            "metadata": self.metadata,
            "reply_to": self.reply_to,
            "media": self.media,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "created_at": self.created_at,
            "last_attempt_at": self.last_attempt_at,
            "delivered_at": self.delivered_at,
            "error": self.error,
            "ttl_seconds": self.ttl_seconds,
            "next_attempt_at": self.next_attempt_at,
        }

    @classmethod
    def from_outbound(
        cls,
        msg: OutboundMessage,
        *,
        message_id: str | None = None,
        max_attempts: int = 3,
        ttl_seconds: int = 3600,
    ) -> DeliveryMessage:
        """Create a queued delivery entry from an outbound message."""
        return cls(
            channel=msg.channel,
            recipient=msg.chat_id,
            content=msg.content,
            metadata=dict(msg.metadata or {}),
            message_id=message_id or str(uuid.uuid4()),
            reply_to=msg.reply_to,
            media=list(msg.media),
            max_attempts=max(1, int(max_attempts)),
            ttl_seconds=max(1, int(ttl_seconds)),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeliveryMessage:
        """Hydrate a queued message from its JSON form."""
        return cls(
            channel=str(data["channel"]),
            recipient=str(data["recipient"]),
            content=str(data["content"]),
            metadata=dict(data.get("metadata", {})),
            message_id=str(data.get("message_id") or uuid.uuid4()),
            reply_to=data.get("reply_to"),
            media=list(data.get("media", [])),
            status=str(data.get("status", DeliveryStatus.PENDING)),
            attempts=int(data.get("attempts", 0)),
            max_attempts=max(1, int(data.get("max_attempts", 3))),
            created_at=float(data.get("created_at", time.time())),
            last_attempt_at=float(data.get("last_attempt_at", 0.0)),
            delivered_at=float(data.get("delivered_at", 0.0)),
            error=str(data.get("error", "")),
            ttl_seconds=max(1, int(data.get("ttl_seconds", 3600))),
            next_attempt_at=float(data.get("next_attempt_at", 0.0)),
        )


class DeliveryQueue:
    """Persistent disk-backed queue with retry and failure tracking."""

    def __init__(self, base_dir: str | Path | None = None):
        self.queue_dir = Path(base_dir).expanduser() if base_dir else get_default_queue_dir()
        self.failed_dir = self.queue_dir / "failed"
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)
        self._queue: list[DeliveryMessage] = []
        self._load_pending()

    def _pending_path(self, message_id: str) -> Path:
        return self.queue_dir / f"msg_{message_id}.json"

    def _failed_path(self, message_id: str) -> Path:
        return self.failed_dir / f"msg_{message_id}.json"

    def _load_pending(self) -> None:
        """Load queued messages from disk on startup."""
        self._queue.clear()
        for path in sorted(self.queue_dir.glob("msg_*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                msg = DeliveryMessage.from_dict(data)
            except Exception as exc:
                logger.warning("Could not load queued delivery message {}: {}", path.name, exc)
                continue

            if msg.is_expired():
                self._move_to_failed(msg, DeliveryStatus.EXPIRED)
                self._remove_file(msg)
                continue

            if msg.status == DeliveryStatus.SENDING:
                # A previous process died mid-send; make the message deliverable again.
                msg.status = DeliveryStatus.PENDING
                msg.next_attempt_at = 0.0
                self._persist(msg)
            elif msg.status == DeliveryStatus.DELIVERED:
                self._remove_file(msg)
                continue
            elif msg.status in {DeliveryStatus.FAILED, DeliveryStatus.EXPIRED}:
                self._move_to_failed(msg, msg.error or msg.status)
                self._remove_file(msg)
                continue

            self._queue.append(msg)

        if self._queue:
            logger.info("Loaded {} pending delivery message(s)", len(self._queue))

    def _persist(self, msg: DeliveryMessage) -> None:
        self._pending_path(msg.message_id).write_text(
            json.dumps(msg.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _remove_file(self, msg: DeliveryMessage) -> None:
        path = self._pending_path(msg.message_id)
        if path.exists():
            path.unlink()

    def _expire_pending(self) -> None:
        expired: list[DeliveryMessage] = []
        for msg in self._queue:
            if msg.is_expired():
                expired.append(msg)
        for msg in expired:
            msg.status = DeliveryStatus.EXPIRED
            self._move_to_failed(msg, DeliveryStatus.EXPIRED)
            self._remove_file(msg)
            self._queue.remove(msg)

    def _move_to_failed(self, msg: DeliveryMessage, reason: str) -> None:
        msg.error = reason
        if reason == DeliveryStatus.EXPIRED:
            msg.status = DeliveryStatus.EXPIRED
        else:
            msg.status = DeliveryStatus.FAILED
        self._failed_path(msg.message_id).write_text(
            json.dumps(msg.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, message_id: str) -> DeliveryMessage | None:
        """Find a pending in-memory message by id."""
        for msg in self._queue:
            if msg.message_id == message_id:
                return msg
        return None

    def enqueue(self, message: DeliveryMessage) -> str:
        """Add a message to the queue, ignoring duplicate ids."""
        self._expire_pending()
        if self.get(message.message_id) is not None:
            return message.message_id

        self._queue.append(message)
        self._persist(message)
        logger.debug(
            "Queued delivery {} -> {} via {}",
            message.message_id[:8],
            message.recipient,
            message.channel,
        )
        return message.message_id

    def enqueue_outbound(
        self,
        msg: OutboundMessage,
        *,
        message_id: str | None = None,
        max_attempts: int = 3,
        ttl_seconds: int = 3600,
    ) -> str:
        """Queue an outbound bus message."""
        delivery = DeliveryMessage.from_outbound(
            msg,
            message_id=message_id,
            max_attempts=max_attempts,
            ttl_seconds=ttl_seconds,
        )
        return self.enqueue(delivery)

    def dequeue(self) -> DeliveryMessage | None:
        """Return the next ready message and mark it sending."""
        self._expire_pending()
        now = time.time()
        for msg in sorted(self._queue, key=lambda item: item.created_at):
            if msg.status != DeliveryStatus.PENDING:
                continue
            if msg.next_attempt_at and msg.next_attempt_at > now:
                continue
            msg.status = DeliveryStatus.SENDING
            msg.attempts += 1
            msg.last_attempt_at = now
            self._persist(msg)
            return msg
        return None

    def mark_delivered(self, message_id: str) -> None:
        """Mark a queued message as delivered and remove it from disk."""
        msg = self.get(message_id)
        if msg is None:
            return
        msg.status = DeliveryStatus.DELIVERED
        msg.delivered_at = time.time()
        self._remove_file(msg)
        self._queue.remove(msg)

    def mark_failed(self, message_id: str, error: str) -> None:
        """Mark an in-flight message as failed, retrying or moving to failed."""
        msg = self.get(message_id)
        if msg is None:
            return

        msg.error = error
        if msg.is_expired():
            msg.status = DeliveryStatus.EXPIRED
            self._move_to_failed(msg, DeliveryStatus.EXPIRED)
            self._remove_file(msg)
            self._queue.remove(msg)
            return

        if msg.attempts >= msg.max_attempts:
            self._move_to_failed(msg, error)
            self._remove_file(msg)
            self._queue.remove(msg)
            logger.warning(
                "Delivery {} failed after {} attempt(s): {}",
                message_id[:8],
                msg.attempts,
                error,
            )
            return

        msg.status = DeliveryStatus.PENDING
        msg.next_attempt_at = time.time() + min(30.0, 0.5 * (2 ** max(0, msg.attempts - 1)))
        self._persist(msg)
        logger.debug(
            "Delivery {} attempt {}/{} failed: {}",
            message_id[:8],
            msg.attempts,
            msg.max_attempts,
            error,
        )

    def list_failed(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent failed deliveries."""
        rows: list[dict[str, Any]] = []
        paths = sorted(
            self.failed_dir.glob("msg_*.json"),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        )
        for path in paths[:limit]:
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return rows

    def retry_failed(self, message_id: str) -> bool:
        """Move a failed delivery back into the pending queue."""
        path = self._failed_path(message_id)
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            msg = DeliveryMessage.from_dict(payload)
        except Exception as exc:
            logger.warning("Could not retry failed delivery {}: {}", message_id[:8], exc)
            return False

        msg.status = DeliveryStatus.PENDING
        msg.attempts = 0
        msg.error = ""
        msg.created_at = time.time()
        msg.last_attempt_at = 0.0
        msg.delivered_at = 0.0
        msg.next_attempt_at = 0.0
        self.enqueue(msg)
        path.unlink(missing_ok=True)
        return True

    def get_stats(self) -> dict[str, int]:
        """Return queue and failure counts."""
        self._expire_pending()
        pending = sum(1 for msg in self._queue if msg.status == DeliveryStatus.PENDING)
        sending = sum(1 for msg in self._queue if msg.status == DeliveryStatus.SENDING)
        failed_total = len(list(self.failed_dir.glob("msg_*.json")))
        return {
            "pending": pending,
            "sending": sending,
            "total_queued": len(self._queue),
            "failed_total": failed_total,
        }
