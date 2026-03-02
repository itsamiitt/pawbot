from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pawbot.bus.events import OutboundMessage
from pawbot.bus.queue import MessageBus
from pawbot.channels.manager import ChannelManager
from pawbot.config.schema import Config


class FlakyChannel:
    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.sent = 0
        self.is_running = True

    async def start(self):
        return None

    async def stop(self):
        return None

    async def send(self, msg: OutboundMessage):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("temporary failure")
        self.sent += 1


@pytest.mark.asyncio
async def test_send_with_retry_succeeds_after_transient_failure(tmp_path: Path, monkeypatch):
    cfg = Config()
    bus = MessageBus()

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    manager = ChannelManager(cfg, bus)
    manager._base_retry_delay_s = 0.01

    ch = FlakyChannel(fail_times=1)
    msg = OutboundMessage(channel="telegram", chat_id="1", content="hello")

    ok = await manager._send_with_retry(ch, msg)
    assert ok is True
    assert ch.sent == 1


@pytest.mark.asyncio
async def test_dispatch_skips_duplicate_messages(tmp_path: Path, monkeypatch):
    cfg = Config()
    bus = MessageBus()

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    manager = ChannelManager(cfg, bus)
    manager._base_retry_delay_s = 0.01

    ch = FlakyChannel(fail_times=0)
    manager.channels["telegram"] = ch

    msg = OutboundMessage(channel="telegram", chat_id="1", content="same")
    key = manager._message_id(msg)
    manager._mark_seen(key)

    await bus.publish_outbound(msg)
    task = asyncio.create_task(manager._dispatch_outbound())
    await asyncio.sleep(0.1)
    task.cancel()
    await task

    assert ch.sent == 0


@pytest.mark.asyncio
async def test_dead_letter_written_after_retry_exhausted(tmp_path: Path, monkeypatch):
    cfg = Config()
    bus = MessageBus()

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    manager = ChannelManager(cfg, bus)
    manager._base_retry_delay_s = 0.01
    manager._max_send_attempts = 2

    ch = FlakyChannel(fail_times=10)
    msg = OutboundMessage(channel="telegram", chat_id="1", content="will-fail")

    ok = await manager._send_with_retry(ch, msg)
    assert ok is False

    dlq = tmp_path / ".pawbot" / "logs" / "dead_letter.jsonl"
    assert dlq.exists()
    rows = dlq.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1
    payload = json.loads(rows[0])
    assert payload["channel"] == "telegram"
    assert payload["chat_id"] == "1"



