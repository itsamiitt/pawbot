"""Channel manager for coordinating chat channels."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from pawbot.bus.events import OutboundMessage
from pawbot.bus.queue import MessageBus
from pawbot.channels.base import BaseChannel
from pawbot.config.schema import Config


class ChannelManager:
    """
    Manages chat channels and coordinates message routing.

    Responsibilities:
    - Initialize enabled channels (Telegram, WhatsApp, etc.)
    - Start/stop channels
    - Route outbound messages with retry, dedupe, and dead-lettering
    """

    def __init__(self, config: Config, bus: MessageBus):
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

        self._init_channels()

    def _init_channels(self) -> None:
        """Initialize channels based on config."""

        # Telegram channel
        if self.config.channels.telegram.enabled:
            try:
                from pawbot.channels.telegram import TelegramChannel
                self.channels["telegram"] = TelegramChannel(
                    self.config.channels.telegram,
                    self.bus,
                    groq_api_key=self.config.providers.groq.api_key,
                )
                logger.info("Telegram channel enabled")
            except ImportError as e:
                logger.warning("Telegram channel not available: {}", e)

        # WhatsApp channel
        if self.config.channels.whatsapp.enabled:
            try:
                from pawbot.channels.whatsapp import WhatsAppChannel
                self.channels["whatsapp"] = WhatsAppChannel(
                    self.config.channels.whatsapp, self.bus
                )
                logger.info("WhatsApp channel enabled")
            except ImportError as e:
                logger.warning("WhatsApp channel not available: {}", e)

        # Discord channel
        if self.config.channels.discord.enabled:
            try:
                from pawbot.channels.discord import DiscordChannel
                self.channels["discord"] = DiscordChannel(
                    self.config.channels.discord, self.bus
                )
                logger.info("Discord channel enabled")
            except ImportError as e:
                logger.warning("Discord channel not available: {}", e)

        # Feishu channel
        if self.config.channels.feishu.enabled:
            try:
                from pawbot.channels.feishu import FeishuChannel
                self.channels["feishu"] = FeishuChannel(
                    self.config.channels.feishu, self.bus
                )
                logger.info("Feishu channel enabled")
            except ImportError as e:
                logger.warning("Feishu channel not available: {}", e)

        # Mochat channel
        if self.config.channels.mochat.enabled:
            try:
                from pawbot.channels.mochat import MochatChannel

                self.channels["mochat"] = MochatChannel(
                    self.config.channels.mochat, self.bus
                )
                logger.info("Mochat channel enabled")
            except ImportError as e:
                logger.warning("Mochat channel not available: {}", e)

        # DingTalk channel
        if self.config.channels.dingtalk.enabled:
            try:
                from pawbot.channels.dingtalk import DingTalkChannel
                self.channels["dingtalk"] = DingTalkChannel(
                    self.config.channels.dingtalk, self.bus
                )
                logger.info("DingTalk channel enabled")
            except ImportError as e:
                logger.warning("DingTalk channel not available: {}", e)

        # Email channel
        if self.config.channels.email.enabled:
            try:
                from pawbot.channels.email import EmailChannel
                self.channels["email"] = EmailChannel(
                    self.config.channels.email, self.bus
                )
                logger.info("Email channel enabled")
            except ImportError as e:
                logger.warning("Email channel not available: {}", e)

        # Slack channel
        if self.config.channels.slack.enabled:
            try:
                from pawbot.channels.slack import SlackChannel
                self.channels["slack"] = SlackChannel(
                    self.config.channels.slack, self.bus
                )
                logger.info("Slack channel enabled")
            except ImportError as e:
                logger.warning("Slack channel not available: {}", e)

        # QQ channel
        if self.config.channels.qq.enabled:
            try:
                from pawbot.channels.qq import QQChannel
                self.channels["qq"] = QQChannel(
                    self.config.channels.qq,
                    self.bus,
                )
                logger.info("QQ channel enabled")
            except ImportError as e:
                logger.warning("QQ channel not available: {}", e)

        # Matrix channel
        if self.config.channels.matrix.enabled:
            try:
                from pawbot.channels.matrix import MatrixChannel
                self.channels["matrix"] = MatrixChannel(
                    self.config.channels.matrix,
                    self.bus,
                )
                logger.info("Matrix channel enabled")
            except ImportError as e:
                logger.warning("Matrix channel not available: {}", e)

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

    async def _dispatch_outbound(self) -> None:
        """Dispatch outbound messages to the appropriate channel."""
        logger.info("Outbound dispatcher started")

        while True:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_outbound(),
                    timeout=1.0
                )

                if msg.metadata.get("_progress"):
                    if msg.metadata.get("_tool_hint") and not self.config.channels.send_tool_hints:
                        continue
                    if not msg.metadata.get("_tool_hint") and not self.config.channels.send_progress:
                        continue

                channel = self.channels.get(msg.channel)
                if not channel:
                    logger.warning("Unknown channel: {}", msg.channel)
                    self._write_dead_letter(msg, "unknown channel")
                    continue

                message_id = self._message_id(msg)
                if self._is_duplicate(message_id):
                    logger.info("Skipping duplicate outbound message {}", message_id)
                    continue

                sent = await self._send_with_retry(channel, msg)
                if sent:
                    self._mark_seen(message_id)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_channel(self, name: str) -> BaseChannel | None:
        """Get a channel by name."""
        return self.channels.get(name)

    def get_status(self) -> dict[str, Any]:
        """Get status of all channels."""
        return {
            name: {
                "enabled": True,
                "running": channel.is_running
            }
            for name, channel in self.channels.items()
        }

    @property
    def enabled_channels(self) -> list[str]:
        """Get list of enabled channel names."""
        return list(self.channels.keys())
