"""Channel manager for coordinating chat channels."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, NamedTuple

from loguru import logger

from pawbot.bus.events import OutboundMessage
from pawbot.bus.queue import MessageBus
from pawbot.channels.base import BaseChannel
from pawbot.config.schema import Config
from pawbot.delivery.queue import DeliveryQueue


# ── Channel registry ─────────────────────────────────────────────────────────

class _ChannelEntry(NamedTuple):
    """Registry entry for a single channel type."""
    config_attr:  str                    # attribute on config.channels, e.g. "telegram"
    import_path:  str                    # dotted module path
    class_name:   str                    # class name in that module
    extra_kwargs: Callable[[Any], dict]  # function(full_config) -> extra kwargs


def _no_extra(cfg: Any) -> dict:
    """Most channels need no extra kwargs beyond (channel_cfg, bus)."""
    return {}


# To add a new channel: append one _ChannelEntry here — no other code changes needed.
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



class ChannelManager:
    """
    Manages chat channels and coordinates message routing.

    Responsibilities:
    - Initialize enabled channels (Telegram, WhatsApp, etc.)
    - Start/stop channels
    - Route outbound messages with retry, dedupe, and dead-lettering
    """

    def __init__(
        self,
        config: Config,
        bus: MessageBus,
        delivery_queue: DeliveryQueue | None = None,
    ):
        self.config = config
        self.bus = bus
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task | None = None

        # Phase 3 reliability controls
        self._max_send_attempts = 3
        self._base_retry_delay_s = 0.5
        self._seen_outbound_ids: dict[str, float] = {}
        self._seen_ttl_seconds = 3600
        self._dead_letter_path = Path.home() / ".pawbot" / "logs" / "dead_letter.jsonl"
        self._delivery_idle_sleep_s = 0.05
        self.delivery_queue = delivery_queue or DeliveryQueue()

        self._init_channels()

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
                module   = __import__(entry.import_path, fromlist=[entry.class_name])
                cls      = getattr(module, entry.class_name)
                extra    = entry.extra_kwargs(self.config)
                instance = cls(channel_cfg, self.bus, **extra)
                self.channels[entry.config_attr] = instance
                logger.info("{} channel enabled", entry.config_attr)
            except ImportError as exc:
                logger.warning("{} channel not available: {}", entry.config_attr, exc)
            except Exception as exc:
                logger.error("{} channel failed to initialise: {}", entry.config_attr, exc)



    async def _start_channel(self, name: str, channel: BaseChannel) -> None:
        """Start a channel and log any exceptions."""
        try:
            await channel.start()
        except Exception as e:
            logger.error("Failed to start channel {}: {}", name, e)

    async def start_all(self) -> None:
        """Start all channels and the outbound dispatcher."""
        if not self.channels:
            logger.warning("No channels enabled")
            return

        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())

        tasks = []
        for name, channel in self.channels.items():
            logger.info("Starting {} channel...", name)
            tasks.append(asyncio.create_task(self._start_channel(name, channel)))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        """Stop all channels and the dispatcher."""
        logger.info("Stopping all channels...")

        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info("Stopped {} channel", name)
            except Exception as e:
                logger.error("Error stopping {}: {}", name, e)

    def _message_id(self, msg: OutboundMessage) -> str:
        explicit = (msg.metadata or {}).get("idempotency_key")
        if explicit:
            return str(explicit)
        payload = f"{msg.channel}|{msg.chat_id}|{msg.reply_to or ''}|{msg.content}|{','.join(msg.media)}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _should_dispatch_progress(self, msg: OutboundMessage) -> bool:
        """Apply progress visibility rules before queueing a message."""
        metadata = msg.metadata or {}
        if not metadata.get("_progress"):
            return True
        if metadata.get("_tool_hint") and not self.config.channels.send_tool_hints:
            return False
        if not metadata.get("_tool_hint") and not self.config.channels.send_progress:
            return False
        return True

    def _prune_seen(self) -> None:
        now = time.time()
        stale = [k for k, ts in self._seen_outbound_ids.items() if now - ts > self._seen_ttl_seconds]
        for k in stale:
            self._seen_outbound_ids.pop(k, None)

    def _is_duplicate(self, message_id: str) -> bool:
        self._prune_seen()
        return message_id in self._seen_outbound_ids

    def _mark_seen(self, message_id: str) -> None:
        self._seen_outbound_ids[message_id] = time.time()

    def _write_dead_letter(self, msg: OutboundMessage, error_text: str) -> None:
        try:
            self._dead_letter_path.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "timestamp": int(time.time()),
                "channel": msg.channel,
                "chat_id": msg.chat_id,
                "content": msg.content,
                "reply_to": msg.reply_to,
                "media": msg.media,
                "metadata": msg.metadata,
                "error": error_text,
            }
            with self._dead_letter_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("Failed writing dead-letter record: {}", exc)

    async def _send_with_retry(self, channel: BaseChannel, msg: OutboundMessage) -> bool:
        last_error: Exception | None = None
        for attempt in range(1, self._max_send_attempts + 1):
            try:
                await channel.send(msg)
                return True
            except Exception as e:
                last_error = e
                if attempt < self._max_send_attempts:
                    delay = self._base_retry_delay_s * (2 ** (attempt - 1))
                    logger.warning(
                        "Send attempt {}/{} failed for {}: {}. Retrying in {:.2f}s",
                        attempt,
                        self._max_send_attempts,
                        msg.channel,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)

        self._write_dead_letter(msg, str(last_error) if last_error else "unknown send failure")
        logger.error("Message moved to dead-letter queue for channel {}", msg.channel)
        return False

    def _enqueue_delivery_message(self, msg: OutboundMessage) -> str | None:
        """Persist an outbound message into the delivery queue."""
        if not self._should_dispatch_progress(msg):
            return None

        message_id = self._message_id(msg)
        if self._is_duplicate(message_id):
            logger.info("Skipping duplicate outbound message {}", message_id)
            return None

        metadata = dict(msg.metadata or {})
        metadata.setdefault("idempotency_key", message_id)
        queued = OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=msg.content,
            reply_to=msg.reply_to,
            media=list(msg.media),
            metadata=metadata,
        )
        ttl_seconds = int(metadata.get("delivery_ttl_seconds", 3600) or 3600)
        self.delivery_queue.enqueue_outbound(
            queued,
            message_id=message_id,
            max_attempts=self._max_send_attempts,
            ttl_seconds=ttl_seconds,
        )
        return message_id

    async def _send_delivery_message(self, channel: BaseChannel, msg) -> bool:
        """Attempt a single delivery queue send."""
        try:
            await channel.send(msg.to_outbound())
            return True
        except Exception as exc:
            self.delivery_queue.mark_failed(msg.message_id, str(exc))
            if self.delivery_queue.get(msg.message_id) is None:
                self._write_dead_letter(msg.to_outbound(), str(exc))
            logger.warning(
                "Delivery {} failed for {}: {}",
                msg.message_id[:8],
                msg.channel,
                exc,
            )
            return False

    async def _dispatch_outbound(self) -> None:
        """Drain bus messages into the delivery queue and dispatch pending work."""
        logger.info("Outbound dispatcher started")

        while True:
            try:
                drained = 0
                while True:
                    try:
                        outbound = self.bus.outbound.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if self._enqueue_delivery_message(outbound):
                        drained += 1

                delivery = self.delivery_queue.dequeue()
                if delivery is None:
                    if drained == 0:
                        await asyncio.sleep(self._delivery_idle_sleep_s)
                    continue

                channel = self.channels.get(delivery.channel)
                if not channel:
                    logger.warning("Unknown channel: {}", delivery.channel)
                    self.delivery_queue.mark_failed(delivery.message_id, "unknown channel")
                    if self.delivery_queue.get(delivery.message_id) is None:
                        self._write_dead_letter(delivery.to_outbound(), "unknown channel")
                    continue

                sent = await self._send_delivery_message(channel, delivery)
                if sent:
                    self.delivery_queue.mark_delivered(delivery.message_id)
                    self._mark_seen(delivery.message_id)
            except asyncio.CancelledError:
                break

    def get_channel(self, name: str) -> BaseChannel | None:
        """Get a channel by name."""
        return self.channels.get(name)

    def get_status(self) -> dict[str, Any]:
        """Get status of all channels with tier info (Phase 4)."""
        return {
            name: {
                "enabled": True,
                "running": channel.is_running,
                "tier": self._get_channel_tier(name),
                "connected": getattr(channel, '_connected', channel.is_running),
                "reconnect_count": getattr(channel, '_reconnect_count', 0),
            }
            for name, channel in self.channels.items()
        }

    @staticmethod
    def _get_channel_tier(name: str) -> str:
        """Get tier classification for a channel (Phase 4)."""
        tiers = {
            "telegram": "production",
            "whatsapp": "production",
            "cli": "production",
            "discord": "supported",
            "slack": "supported",
        }
        return tiers.get(name, "community")

    @property
    def enabled_channels(self) -> list[str]:
        """Get list of enabled channel names."""
        return list(self.channels.keys())
