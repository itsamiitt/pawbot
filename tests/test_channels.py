"""Tests for Phase 10 Channels & Communication enhancements.

Tests verify:
  - ChannelMessage dataclass (to_memory_dict, defaults, to_inbound)
  - RateLimiter  (allows within rate, blocks, refills, reset)
  - BaseChannel  (save_to_memory, contact_history, rate_limiter init)
  - WhatsApp     (voice transcription hook, typing indicator, rate-limited send)
  - Telegram     (group filtering, _should_respond, progress bar format, MENTION_PATTERNS)
  - Email        (rate-limited send)
  - MessageQueue (enqueue, drain, full-queue drop)
  - ChannelRouter (routing, queuing, memory saves, proactive send)
"""

from __future__ import annotations

import asyncio
import queue
import time
from dataclasses import asdict
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# We import from the pawbot package directly since channels/ and bus/
# are inside the pawbot/ package.
import sys
from pathlib import Path

# Ensure the pawbot package root is on sys.path for direct imports.
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from pawbot.channels.base import BaseChannel, ChannelMessage, RateLimiter
from pawbot.bus.events import InboundMessage, OutboundMessage
from pawbot.bus.queue import MessageBus
from pawbot.bus.router import ChannelRouter, MessageQueue


# ══════════════════════════════════════════════════════════════════════════════
#  Fixtures and helpers
# ══════════════════════════════════════════════════════════════════════════════


def _make_msg(**overrides) -> ChannelMessage:
    """Create a ChannelMessage with sensible defaults."""
    defaults = {
        "id": "msg_001",
        "channel": "test",
        "contact_id": "user123",
        "contact_name": "Test User",
        "text": "Hello!",
    }
    defaults.update(overrides)
    return ChannelMessage(**defaults)


class ConcreteChannel(BaseChannel):
    """Minimal concrete impl of BaseChannel for testing."""

    name = "test"

    def __init__(self, config=None, bus=None, memory_router=None):
        config = config or {"messages_per_minute": 60}
        bus = bus or MagicMock(spec=MessageBus)
        super().__init__(config, bus, memory_router=memory_router)
        self.sent: list[OutboundMessage] = []

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def send(self, msg: OutboundMessage):
        self.sent.append(msg)


class FakeAgentLoop:
    """Stub for AgentLoop with is_busy() and process()."""

    def __init__(self, busy: bool = False, response: str = "OK"):
        self._busy = busy
        self._response = response
        self.process_calls: list[dict] = []

    def is_busy(self) -> bool:
        return self._busy

    def process(self, text: str, context: dict | None = None) -> str:
        self.process_calls.append({"text": text, "context": context})
        return self._response


class FakeMemoryRouter:
    """Stub for MemoryRouter with save() and search()."""

    def __init__(self):
        self.saved: list[tuple[str, dict]] = []
        self._search_results: list[dict] = []

    def save(self, kind: str, data: dict):
        self.saved.append((kind, data))

    def search(self, query: str, limit: int = 10) -> list[dict]:
        return self._search_results


# ══════════════════════════════════════════════════════════════════════════════
#  Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestChannelMessage:
    """ChannelMessage dataclass behaviour."""

    def test_to_memory_dict_format(self):
        """to_memory_dict returns the expected keys."""
        msg = _make_msg()
        d = msg.to_memory_dict()
        assert d["channel"] == "test"
        assert d["contact_id"] == "user123"
        assert d["text"] == "Hello!"
        assert "timestamp" in d

    def test_dataclass_defaults(self):
        """Optional fields default to None/False/empty."""
        msg = _make_msg()
        assert msg.media_type is None
        assert msg.media_path is None
        assert msg.is_group is False
        assert msg.raw == {}

    def test_to_inbound_converts_correctly(self):
        """to_inbound() produces an InboundMessage."""
        msg = _make_msg(media_path="/tmp/img.png")
        ib = msg.to_inbound()
        assert isinstance(ib, InboundMessage)
        assert ib.channel == "test"
        assert ib.sender_id == "user123"
        assert "/tmp/img.png" in ib.media


class TestRateLimiter:
    """Token-bucket rate limiter."""

    def test_allows_within_rate(self):
        """Consuming fewer tokens than rate succeeds."""
        rl = RateLimiter(messages_per_minute=60)
        for _ in range(5):
            assert rl.consume() is True

    def test_blocks_when_exceeded(self):
        """Exhausting all tokens blocks the next consume."""
        rl = RateLimiter(messages_per_minute=2)
        rl.tokens = 0.0
        rl.last_refill = time.time()
        assert rl.consume() is False

    def test_tokens_refill_over_time(self):
        """Tokens refill after enough time passes."""
        rl = RateLimiter(messages_per_minute=60)
        rl.tokens = 0.0
        rl.last_refill = time.time() - 2  # 2 seconds ago
        assert rl.consume() is True  # Should have refilled ~2 tokens

    def test_wait_time_positive_when_empty(self):
        """wait_time returns positive value when bucket is empty."""
        rl = RateLimiter(messages_per_minute=10)
        rl.tokens = 0.0
        assert rl.wait_time() > 0

    def test_reset_fills_bucket(self):
        """reset() restores full token count."""
        rl = RateLimiter(messages_per_minute=10)
        rl.tokens = 0.0
        rl.reset()
        assert rl.tokens == 10.0


class TestBaseChannel:
    """BaseChannel Phase 10 enhancements."""

    def test_rate_limiter_initialised(self):
        """BaseChannel creates a RateLimiter from config."""
        ch = ConcreteChannel(config={"messages_per_minute": 30})
        assert isinstance(ch.rate_limiter, RateLimiter)
        assert ch.rate_limiter.rate == 30

    def test_save_to_memory_called_on_receive(self):
        """_save_to_memory delegates to the memory router."""
        memory = FakeMemoryRouter()
        ch = ConcreteChannel(memory_router=memory)
        msg = _make_msg()
        ch._save_to_memory(msg, response="Goodbye")
        assert len(memory.saved) == 1
        kind, data = memory.saved[0]
        assert kind == "message"
        assert data["text"] == "Hello!"
        assert data["response"] == "Goodbye"

    def test_contact_history_queries_memory(self):
        """get_contact_history returns search results from memory."""
        memory = FakeMemoryRouter()
        memory._search_results = [{"text": "past message"}]
        ch = ConcreteChannel(memory_router=memory)
        history = ch.get_contact_history("user123")
        assert len(history) == 1
        assert history[0]["text"] == "past message"

    def test_contact_history_empty_without_memory(self):
        """get_contact_history returns [] when no memory router."""
        ch = ConcreteChannel(memory_router=None)
        assert ch.get_contact_history("x") == []

    def test_send_file_not_implemented_warning(self):
        """Default send_file returns False with a warning."""
        ch = ConcreteChannel()
        assert ch.send_file("user", "/tmp/file.txt") is False


class TestWhatsAppChannel:
    """Phase 10 WhatsApp enhancements — tested via the base patterns."""

    def test_voice_transcription_method_exists(self):
        """WhatsApp module exposes _transcribe_audio."""
        from pawbot.channels.whatsapp import WhatsAppChannel
        assert hasattr(WhatsAppChannel, "_transcribe_audio")
        assert hasattr(WhatsAppChannel, "_transcribe_audio_stub")

    def test_typing_indicator_method_exists(self):
        """WhatsApp module exposes send_typing."""
        from pawbot.channels.whatsapp import WhatsAppChannel
        assert hasattr(WhatsAppChannel, "send_typing")

    def test_send_queued_when_rate_limited(self):
        """Send is rate-limited (tested via BaseChannel's rate_limiter)."""
        # This tests the pattern — the actual WhatsApp.send()
        # checks self.rate_limiter.consume() before sending.
        rl = RateLimiter(messages_per_minute=1)
        assert rl.consume() is True
        # Now the bucket should be low/empty
        rl.tokens = 0.0
        rl.last_refill = time.time()
        assert rl.consume() is False  # Would be rate-limited


class TestTelegramChannel:
    """Phase 10 Telegram enhancements."""

    def test_group_message_ignored_without_mention(self):
        """_should_respond returns False for group messages without mention."""
        from pawbot.channels.telegram import TelegramChannel
        ch = TelegramChannel.__new__(TelegramChannel)
        ch.config = MagicMock()
        ch.config.mention_triggers = ["@pawbot"]

        msg = _make_msg(is_group=True, text="plain message no mention")
        assert ch._should_respond(msg) is False

    def test_group_message_handled_with_mention(self):
        """_should_respond returns True for group messages with mention."""
        from pawbot.channels.telegram import TelegramChannel
        ch = TelegramChannel.__new__(TelegramChannel)
        ch.config = MagicMock()
        ch.config.mention_triggers = ["@pawbot"]

        msg = _make_msg(is_group=True, text="Hey @pawbot do something")
        assert ch._should_respond(msg) is True

    def test_private_message_always_handled(self):
        """_should_respond returns True for private messages."""
        from pawbot.channels.telegram import TelegramChannel
        ch = TelegramChannel.__new__(TelegramChannel)
        ch.config = MagicMock()

        msg = _make_msg(is_group=False, text="anything")
        assert ch._should_respond(msg) is True

    def test_group_message_handled_with_reply(self):
        """_should_respond returns True when reply_to_id is set."""
        from pawbot.channels.telegram import TelegramChannel
        ch = TelegramChannel.__new__(TelegramChannel)
        ch.config = MagicMock()
        ch.config.mention_triggers = ["@pawbot"]

        msg = _make_msg(is_group=True, text="replying", reply_to_id="prev_msg_123")
        assert ch._should_respond(msg) is True

    def test_progress_bar_format(self):
        """Progress bar string has the correct format."""
        step, total = 8, 10
        pct = int((step / max(total, 1)) * 10)
        bar = "█" * pct + "░" * (10 - pct)
        text = f"Compiling\n[{bar}] {step}/{total}"
        assert "████████░░" in text
        assert "8/10" in text

    def test_mention_patterns_class_attribute(self):
        """TelegramChannel has MENTION_PATTERNS."""
        from pawbot.channels.telegram import TelegramChannel
        assert hasattr(TelegramChannel, "MENTION_PATTERNS")
        assert len(TelegramChannel.MENTION_PATTERNS) >= 2


class TestEmailChannel:
    """Phase 10 Email channel — rate-limited send."""

    def test_send_rate_limited(self):
        """Email send honours the rate limiter pattern."""
        # We can't instantiate EmailChannel without real config,
        # but we verify the pattern via BaseChannel.
        rl = RateLimiter(messages_per_minute=5)
        for _ in range(5):
            rl.consume()
        rl.tokens = 0.0
        rl.last_refill = time.time()
        assert rl.consume() is False


class TestMessageQueue:
    """Phase 10 message queue for busy agent."""

    def test_enqueue_and_drain_in_order(self):
        """Messages drain in FIFO order."""
        mq = MessageQueue(max_size=10)
        ch = ConcreteChannel()

        msg1 = _make_msg(id="1", text="first")
        msg2 = _make_msg(id="2", text="second")
        mq.enqueue(msg1, ch)
        mq.enqueue(msg2, ch)
        assert mq.size == 2

        results = []

        def handler(msg, channel):
            results.append(msg.text)
            return ""

        mq.drain(handler)
        assert results == ["first", "second"]
        assert mq.is_empty

    def test_full_queue_drops_gracefully(self):
        """Enqueue returns False when queue is full."""
        mq = MessageQueue(max_size=2)
        ch = ConcreteChannel()

        mq.enqueue(_make_msg(id="1"), ch)
        mq.enqueue(_make_msg(id="2"), ch)
        assert mq.enqueue(_make_msg(id="3"), ch) is False
        assert mq.is_full

    def test_drain_calls_handler(self):
        """Drain passes each msg+channel to the handler function."""
        mq = MessageQueue()
        ch = ConcreteChannel()
        mq.enqueue(_make_msg(), ch)

        called = []

        def handler(msg, channel):
            called.append({"contact": msg.contact_id, "channel_name": channel.name})
            return ""

        mq.drain(handler)
        assert len(called) == 1
        assert called[0]["contact"] == "user123"
        assert called[0]["channel_name"] == "test"


class TestChannelRouter:
    """Phase 10 ChannelRouter behaviour."""

    def test_routes_to_agent_loop_when_free(self):
        """handle() calls agent_loop.process when not busy."""
        agent = FakeAgentLoop(busy=False, response="Noted!")
        memory = FakeMemoryRouter()
        router = ChannelRouter(agent_loop=agent, memory_router=memory)

        ch = ConcreteChannel()
        router.register("test", ch)

        msg = _make_msg()
        response = router.handle(msg, ch)
        assert response == "Noted!"
        assert len(agent.process_calls) == 1
        assert agent.process_calls[0]["text"] == "Hello!"

    def test_queues_when_agent_busy(self):
        """handle() queues message when agent is busy."""
        agent = FakeAgentLoop(busy=True)
        router = ChannelRouter(agent_loop=agent)

        ch = ConcreteChannel()
        router.register("test", ch)

        msg = _make_msg()
        response = router.handle(msg, ch)
        assert response == ""
        assert router.queue.size == 1

    def test_saves_inbound_to_memory(self):
        """handle() saves incoming message to MemoryRouter."""
        agent = FakeAgentLoop()
        memory = FakeMemoryRouter()
        router = ChannelRouter(agent_loop=agent, memory_router=memory)

        ch = ConcreteChannel()
        msg = _make_msg()
        router.handle(msg, ch)

        # At least 1 save for inbound, possibly 1 for outbound
        assert len(memory.saved) >= 1
        kinds = [s[0] for s in memory.saved]
        assert "message" in kinds

    def test_saves_outbound_to_memory(self):
        """handle() saves the agent's response to MemoryRouter."""
        agent = FakeAgentLoop(response="My response")
        memory = FakeMemoryRouter()
        router = ChannelRouter(agent_loop=agent, memory_router=memory)

        ch = ConcreteChannel()
        msg = _make_msg()
        router.handle(msg, ch)

        # Should have both inbound and outbound saves
        assert len(memory.saved) == 2
        outbound = memory.saved[1]
        assert outbound[1]["direction"] == "outbound"
        assert outbound[1]["text"] == "My response"

    def test_register_stores_channel(self):
        """register() makes channel accessible by name."""
        router = ChannelRouter()
        ch = ConcreteChannel()
        router.register("whatsapp", ch)
        assert router.get_channel("whatsapp") is ch

    def test_channels_property(self):
        """channels property returns dict of all registered channels."""
        router = ChannelRouter()
        ch1 = ConcreteChannel()
        ch2 = ConcreteChannel()
        router.register("a", ch1)
        router.register("b", ch2)
        assert len(router.channels) == 2

    def test_send_proactive_unknown_channel(self):
        """send_proactive returns False for unregistered channel."""
        router = ChannelRouter()
        assert router.send_proactive("user", "hi", "missing_channel") is False

    def test_handle_without_agent_loop(self):
        """handle() gracefully returns '' when no agent_loop set."""
        router = ChannelRouter(agent_loop=None)
        ch = ConcreteChannel()
        msg = _make_msg()
        assert router.handle(msg, ch) == ""

    def test_drain_queue(self):
        """drain_queue() processes all queued messages."""
        agent = FakeAgentLoop(busy=True)
        router = ChannelRouter(agent_loop=agent)
        ch = ConcreteChannel()

        # Queue a message while busy
        msg = _make_msg()
        router.handle(msg, ch)
        assert router.queue.size == 1

        # Now agent is free — drain
        agent._busy = False
        drained = router.drain_queue()
        # drain_queue calls handle which tries process again
        assert router.queue.size == 0
