"""Channel Router for routing messages between channels and agent loop.

Phase 10 — routes incoming messages to AgentLoop, routes responses back,
queues messages when agent is busy, and persists conversation history.
"""

from __future__ import annotations

import queue
import time
from typing import Any

from loguru import logger

from pawbot.channels.base import BaseChannel, ChannelMessage


# ══════════════════════════════════════════════════════════════════════════════
#  MessageQueue — overflow buffer when agent is busy
# ══════════════════════════════════════════════════════════════════════════════


class MessageQueue:
    """FIFO queue for messages when agent is busy processing another task.

    Drains automatically when agent becomes free.
    """

    def __init__(self, max_size: int = 100):
        self._q: queue.Queue = queue.Queue(maxsize=max_size)
        self._processing = False

    def enqueue(self, msg: ChannelMessage, channel_adapter: BaseChannel) -> bool:
        """Add message to queue. Returns False if queue full."""
        try:
            self._q.put_nowait((msg, channel_adapter))
            logger.info(
                "Message queued from {} (queue size: {})",
                msg.contact_id, self._q.qsize(),
            )
            return True
        except queue.Full:
            logger.warning("Message queue full — dropping message from {}", msg.contact_id)
            return False

    def drain(self, handler) -> int:
        """Process all queued messages in order.

        handler: callable(ChannelMessage, BaseChannel) -> str
        Returns how many messages were drained.
        """
        drained = 0
        while not self._q.empty():
            try:
                msg, channel = self._q.get_nowait()
                response = handler(msg, channel)
                if response:
                    from pawbot.bus.events import OutboundMessage
                    out = OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.group_id or msg.contact_id,
                        content=response,
                    )
                    # Use asyncio if send is async
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(channel.send(out))
                    except RuntimeError:
                        asyncio.run(channel.send(out))
                self._q.task_done()
                drained += 1
            except Exception as e:
                logger.warning("Queue drain error: {}", e)
                break
        return drained

    @property
    def size(self) -> int:
        """Number of queued messages."""
        return self._q.qsize()

    @property
    def is_empty(self) -> bool:
        return self._q.empty()

    @property
    def is_full(self) -> bool:
        return self._q.full()


# ══════════════════════════════════════════════════════════════════════════════
#  ChannelRouter — central message routing hub
# ══════════════════════════════════════════════════════════════════════════════


class ChannelRouter:
    """Routes incoming messages from any channel to AgentLoop.

    Routes AgentLoop responses back to the originating channel.
    Queues messages when the agent is busy.
    Persists all message traffic to MemoryRouter.
    """

    def __init__(self, agent_loop: Any = None, memory_router: Any = None, config: dict | None = None):
        """Initialise the router.

        Args:
            agent_loop: AgentLoop instance (must expose .process() and .is_busy()).
            memory_router: MemoryRouter for persistence (optional).
            config: Global configuration dict.
        """
        self.loop = agent_loop
        self.memory = memory_router
        self.config = config or {}
        self._channels: dict[str, BaseChannel] = {}
        self.queue = MessageQueue(max_size=config.get("message_queue_size", 100) if config else 100)

    def register(self, name: str, channel: BaseChannel) -> None:
        """Register a channel adapter.

        name: 'whatsapp' | 'telegram' | 'email' | ...
        """
        self._channels[name] = channel
        logger.info("ChannelRouter: registered channel '{}'", name)

    def get_channel(self, name: str) -> BaseChannel | None:
        """Get a registered channel by name."""
        return self._channels.get(name)

    @property
    def channels(self) -> dict[str, BaseChannel]:
        """All registered channels."""
        return dict(self._channels)

    def handle(self, msg: ChannelMessage, channel: BaseChannel) -> str:
        """Called by channel adapter when a message arrives.

        Routes to AgentLoop if free, queues if busy.
        Returns the response text (or empty string if queued).
        """
        # Save incoming message to memory
        if self.memory:
            try:
                self.memory.save("message", msg.to_memory_dict())
            except Exception as exc:
                logger.warning("ChannelRouter: failed to save inbound: {}", exc)

        # Build context for AgentLoop
        history: list[dict] = []
        if self.memory:
            try:
                history = self.memory.search(f"contact_id:{msg.contact_id}", limit=10)
            except Exception as e:  # noqa: F841
                pass

        context = {
            "channel": msg.channel,
            "contact_id": msg.contact_id,
            "contact_name": msg.contact_name,
            "history": history,
            "channel_msg": msg,
        }

        # If agent is busy, queue the message
        if self.loop and hasattr(self.loop, "is_busy") and self.loop.is_busy():
            self.queue.enqueue(msg, channel)
            return ""

        # Process the message
        response = ""
        if self.loop:
            try:
                response = self.loop.process(msg.text, context=context)
            except Exception as exc:
                logger.error("ChannelRouter: agent error processing '{}': {}", msg.text[:50], exc)
                response = ""

        # Save response to memory
        if self.memory and response:
            try:
                self.memory.save("message", {
                    "channel": msg.channel,
                    "contact_id": msg.contact_id,
                    "direction": "outbound",
                    "text": response,
                    "timestamp": int(time.time()),
                })
            except Exception as exc:
                logger.warning("ChannelRouter: failed to save outbound: {}", exc)

        return response

    def send_proactive(self, contact_id: str, text: str, channel_name: str) -> bool:
        """Send a proactive message via a specific channel.

        Used by HeartbeatEngine (Phase 11) for scheduled/triggered messages.
        """
        channel = self._channels.get(channel_name)
        if not channel:
            logger.warning("ChannelRouter: no channel '{}' registered", channel_name)
            return False

        from pawbot.bus.events import OutboundMessage
        out = OutboundMessage(
            channel=channel_name,
            chat_id=contact_id,
            content=text,
        )

        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(channel.send(out))
        except RuntimeError:
            asyncio.run(channel.send(out))

        logger.info("ChannelRouter: proactive msg to {} via {}", contact_id, channel_name)
        return True

    def drain_queue(self) -> int:
        """Drain queued messages through the agent. Call when agent becomes free."""
        return self.queue.drain(lambda msg, ch: self.handle(msg, ch))
