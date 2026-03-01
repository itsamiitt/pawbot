"""WhatsApp channel implementation using Node.js bridge.

Phase 10 enhancements:
  - Voice message transcription (faster-whisper)
  - Typing indicator before responses
  - Rate-limited outbound send
  - Contact history from MemoryRouter
"""

import asyncio
import json
import os
import time
from collections import OrderedDict
from typing import Any

from loguru import logger

from pawbot.bus.events import OutboundMessage
from pawbot.bus.queue import MessageBus
from pawbot.channels.base import BaseChannel, ChannelMessage
from pawbot.config.schema import WhatsAppConfig


class WhatsAppChannel(BaseChannel):
    """
    WhatsApp channel that connects to a Node.js bridge.

    The bridge uses @whiskeysockets/baileys to handle the WhatsApp Web protocol.
    Communication between Python and Node.js is via WebSocket.
    """

    name = "whatsapp"

    def __init__(self, config: WhatsAppConfig, bus: MessageBus, memory_router: Any = None):
        super().__init__(config, bus, memory_router=memory_router)
        self.config: WhatsAppConfig = config
        self._ws = None
        self._connected = False
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()

        # Phase 10 — media download dir
        self._media_dir = os.path.expanduser(
            getattr(config, "media_dir", "~/.pawbot/downloads")
        )
        os.makedirs(self._media_dir, exist_ok=True)

    async def start(self) -> None:
        """Start the WhatsApp channel by connecting to the bridge."""
        import websockets

        bridge_url = self.config.bridge_url

        logger.info("Connecting to WhatsApp bridge at {}...", bridge_url)

        self._running = True

        while self._running:
            try:
                async with websockets.connect(bridge_url) as ws:
                    self._ws = ws
                    # Send auth token if configured
                    if self.config.bridge_token:
                        await ws.send(json.dumps({"type": "auth", "token": self.config.bridge_token}))
                    self._connected = True
                    logger.info("Connected to WhatsApp bridge")

                    # Listen for messages
                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception as e:
                            logger.error("Error handling bridge message: {}", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                self._ws = None
                logger.warning("WhatsApp bridge connection error: {}", e)

                if self._running:
                    logger.info("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the WhatsApp channel."""
        self._running = False
        self._connected = False

        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through WhatsApp (rate-limited)."""
        # Phase 10 — rate limiter check
        if not self.rate_limiter.consume():
            wait = self.rate_limiter.wait_time()
            logger.warning(
                "WhatsApp rate-limited to {} — waiting {:.1f}s",
                msg.chat_id, wait,
            )
            await asyncio.sleep(wait)
            self.rate_limiter.consume()  # consume after waiting

        if not self._ws or not self._connected:
            logger.warning("WhatsApp bridge not connected")
            return

        try:
            payload = {
                "type": "send",
                "to": msg.chat_id,
                "text": msg.content
            }
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.error("Error sending WhatsApp message: {}", e)

    async def _handle_bridge_message(self, raw: str) -> None:
        """Handle a message from the bridge."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from bridge: {}", raw[:100])
            return

        msg_type = data.get("type")

        if msg_type == "message":
            # Incoming message from WhatsApp
            # Deprecated by whatsapp: old phone number style typically: <phone>@s.whatspp.net
            pn = data.get("pn", "")
            # New LID sytle typically:
            sender = data.get("sender", "")
            content = data.get("content", "")
            message_id = data.get("id", "")

            if message_id:
                if message_id in self._processed_message_ids:
                    return
                self._processed_message_ids[message_id] = None
                while len(self._processed_message_ids) > 1000:
                    self._processed_message_ids.popitem(last=False)

            # Extract just the phone number or lid as chat_id
            user_id = pn if pn else sender
            sender_id = user_id.split("@")[0] if "@" in user_id else user_id
            logger.info("Sender {}", sender)

            # Handle voice transcription if it's a voice message
            if content == "[Voice Message]":
                transcribed = self._transcribe_audio_stub(sender_id)
                if transcribed:
                    content = f"[Voice message transcription]: {transcribed}"
                else:
                    content = "[Voice Message: Transcription not available]"

            await self._handle_message(
                sender_id=sender_id,
                chat_id=sender,  # Use full LID for replies
                content=content,
                metadata={
                    "message_id": message_id,
                    "timestamp": data.get("timestamp"),
                    "is_group": data.get("isGroup", False)
                }
            )

        elif msg_type == "status":
            # Connection status update
            status = data.get("status")
            logger.info("WhatsApp status: {}", status)

            if status == "connected":
                self._connected = True
            elif status == "disconnected":
                self._connected = False

        elif msg_type == "qr":
            # QR code for authentication
            logger.info("Scan QR code in the bridge terminal to connect WhatsApp")

        elif msg_type == "error":
            logger.error("WhatsApp bridge error: {}", data.get('error'))

    # ── Phase 10: Voice Transcription ─────────────────────────────────────

    def _transcribe_audio(self, audio_path: str) -> str:
        """Transcribe voice message to text using faster-whisper.

        Returns empty string if transcription fails.
        """
        try:
            from faster_whisper import WhisperModel

            model_size = getattr(self.config, "whisper_model", "base")
            model = WhisperModel(model_size, compute_type="int8")
            segments, _ = model.transcribe(audio_path)
            return " ".join(seg.text.strip() for seg in segments)
        except ImportError:
            logger.warning("faster-whisper not installed — voice transcription unavailable")
            return ""
        except Exception as e:
            logger.warning("Voice transcription failed: {}", e)
            return ""

    def _transcribe_audio_stub(self, sender_id: str) -> str:
        """Attempt transcription when bridge provides audio data.

        Currently a stub — returns empty string until bridge supports
        downloading voice message audio files directly.
        """
        logger.info(
            "Voice message received from {}, awaiting bridge audio download support.",
            sender_id,
        )
        return ""

    # ── Phase 10: Typing Indicator ────────────────────────────────────────

    async def send_typing(self, contact_id: str, duration_seconds: float = 2.0) -> None:
        """Send typing indicator before the actual response."""
        if not self._ws or not self._connected:
            return
        try:
            payload = {"type": "typing", "to": contact_id}
            await self._ws.send(json.dumps(payload))
            await asyncio.sleep(duration_seconds)
        except Exception as e:
            logger.warning("WhatsApp typing indicator failed: {}", e)
