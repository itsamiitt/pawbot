"""Tests for Phase 14 canvas delivery queue features."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from pawbot.bus.events import OutboundMessage
from pawbot.bus.queue import MessageBus
from pawbot.canvas.server import record_canvas_session
from pawbot.channels.manager import ChannelManager
from pawbot.config.schema import Config
from pawbot.dashboard import auth as dashboard_auth
from pawbot.dashboard.server import app as dashboard_app
from pawbot.delivery.queue import DeliveryMessage, DeliveryQueue
from pawbot.gateway.server import app as gateway_app


@pytest.fixture
def local_tmp_path() -> Path:
    """Workspace-local temp dir to avoid Windows temp permission issues in sandbox."""
    base = Path(__file__).resolve().parents[1] / "pytest_temp_phase14"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class RecordingChannel:
    """Simple channel stub that records successful sends."""

    def __init__(self):
        self.sent = 0
        self.messages: list[OutboundMessage] = []
        self.is_running = True

    async def start(self):
        return None

    async def stop(self):
        return None

    async def send(self, msg: OutboundMessage):
        self.sent += 1
        self.messages.append(msg)


def test_delivery_queue_persists_failures_and_retry(local_tmp_path: Path) -> None:
    """Queued messages should survive reload, fail after max attempts, and be retryable."""
    queue_dir = local_tmp_path / "delivery-queue"
    queue = DeliveryQueue(queue_dir)

    message_id = queue.enqueue(DeliveryMessage(
        channel="telegram",
        recipient="user-1",
        content="hello",
        message_id="msg-1",
        max_attempts=2,
    ))
    assert message_id == "msg-1"

    reloaded = DeliveryQueue(queue_dir)
    assert reloaded.get_stats()["total_queued"] == 1

    first = reloaded.dequeue()
    assert first is not None
    reloaded.mark_failed("msg-1", "temporary failure")
    assert reloaded.get_stats()["pending"] == 1
    retrying = reloaded.get("msg-1")
    assert retrying is not None
    retrying.next_attempt_at = 0.0
    reloaded._persist(retrying)

    second = reloaded.dequeue()
    assert second is not None
    reloaded.mark_failed("msg-1", "permanent failure")
    assert reloaded.get_stats()["failed_total"] == 1
    assert reloaded.get_stats()["total_queued"] == 0

    failures = reloaded.list_failed()
    assert failures[0]["message_id"] == "msg-1"
    assert reloaded.retry_failed("msg-1") is True
    assert reloaded.get_stats()["pending"] == 1


def test_delivery_queue_recovers_sending_messages_after_restart(local_tmp_path: Path) -> None:
    """A persisted in-flight delivery should become pending again after reload."""
    queue_dir = local_tmp_path / "delivery-queue"
    queue = DeliveryQueue(queue_dir)
    queue.enqueue(DeliveryMessage(
        channel="telegram",
        recipient="user-recover",
        content="resume me",
        message_id="recovery-1",
    ))

    inflight = queue.dequeue()
    assert inflight is not None
    assert inflight.status == "sending"

    recovered = DeliveryQueue(queue_dir)
    restored = recovered.get("recovery-1")
    assert restored is not None
    assert restored.status == "pending"
    assert recovered.dequeue() is not None


def test_delivery_queue_expires_old_messages(local_tmp_path: Path) -> None:
    """Expired messages should be moved into failed storage automatically."""
    queue_dir = local_tmp_path / "delivery-queue"
    queue = DeliveryQueue(queue_dir)
    queue.enqueue(DeliveryMessage(
        channel="telegram",
        recipient="user-2",
        content="stale",
        message_id="expired-1",
        ttl_seconds=1,
        created_at=1.0,
    ))

    refreshed = DeliveryQueue(queue_dir)
    assert refreshed.get_stats()["total_queued"] == 0
    assert refreshed.get_stats()["failed_total"] == 1
    assert refreshed.list_failed()[0]["status"] == "expired"


@pytest.mark.asyncio
async def test_channel_manager_dispatches_via_delivery_queue(local_tmp_path: Path) -> None:
    """Outbound bus messages should be persisted then delivered through the queue."""
    cfg = Config()
    bus = MessageBus()
    queue = DeliveryQueue(local_tmp_path / "delivery-queue")
    manager = ChannelManager(cfg, bus, delivery_queue=queue)
    manager._delivery_idle_sleep_s = 0.01
    manager.channels["telegram"] = RecordingChannel()

    await bus.publish_outbound(OutboundMessage(channel="telegram", chat_id="123", content="queued"))

    task = asyncio.create_task(manager._dispatch_outbound())
    await asyncio.sleep(0.15)
    task.cancel()
    await task

    channel = manager.channels["telegram"]
    assert channel.sent == 1
    assert channel.messages[0].content == "queued"
    assert queue.get_stats()["total_queued"] == 0


@pytest.mark.asyncio
async def test_channel_manager_failed_delivery_writes_dead_letter(local_tmp_path: Path) -> None:
    """Permanent delivery failures should still be mirrored to the dead-letter log."""
    class FailingChannel:
        is_running = True

        async def start(self):
            return None

        async def stop(self):
            return None

        async def send(self, _msg):
            raise RuntimeError("channel offline")

    cfg = Config()
    bus = MessageBus()
    queue = DeliveryQueue(local_tmp_path / "delivery-queue")
    manager = ChannelManager(cfg, bus, delivery_queue=queue)
    manager._delivery_idle_sleep_s = 0.01
    manager._max_send_attempts = 1
    manager._dead_letter_path = local_tmp_path / "logs" / "dead_letter.jsonl"
    manager.channels["telegram"] = FailingChannel()

    await bus.publish_outbound(OutboundMessage(channel="telegram", chat_id="123", content="queued"))

    task = asyncio.create_task(manager._dispatch_outbound())
    await asyncio.sleep(0.15)
    task.cancel()
    await task

    assert queue.get_stats()["failed_total"] == 1
    assert manager._dead_letter_path.exists()
    assert "channel offline" in manager._dead_letter_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_gateway_canvas_routes_render_recorded_session(local_tmp_path: Path, monkeypatch) -> None:
    """Gateway should expose the canvas page and recorded session content."""
    monkeypatch.setattr(
        "pawbot.canvas.server.get_canvas_root",
        lambda: local_tmp_path / "canvas",
    )
    record_canvas_session(
        "cli:canvas-test",
        "Summary\n\n```python\nprint('hi')\n```",
    )

    transport = ASGITransport(app=gateway_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/canvas")
        sessions = await client.get("/api/canvas/sessions")
        render = await client.get("/api/canvas/render", params={"session_id": "latest"})

    assert page.status_code == 200
    assert "PawBot Canvas" in page.text
    assert sessions.status_code == 200
    assert sessions.json()["sessions"][0]["session_id"] == "cli:canvas-test"
    assert render.status_code == 200
    assert render.json()["blocks"][1]["type"] == "code"


def test_gateway_canvas_websocket_streams_latest_updates(local_tmp_path: Path, monkeypatch) -> None:
    """Canvas websocket should stream the latest session and later updates."""
    monkeypatch.setattr(
        "pawbot.canvas.server.get_canvas_root",
        lambda: local_tmp_path / "canvas",
    )
    record_canvas_session("cli:first", "First session")

    client = TestClient(gateway_app)
    with client.websocket_connect("/canvas/ws") as websocket:
        first = websocket.receive_json()
        assert first["type"] == "canvas_update"
        assert first["session"]["session_id"] == "cli:first"

        record_canvas_session("cli:second", "```mermaid\nflowchart LR\nA-->B\n```")
        websocket.send_text("ping")
        second = websocket.receive_json()
        assert second["type"] == "canvas_update"
        assert second["session"]["session_id"] == "cli:second"
        assert second["session"]["blocks"][0]["type"] == "mermaid"


def test_dashboard_delivery_api_lists_and_retries_failed(local_tmp_path: Path, monkeypatch) -> None:
    """Dashboard delivery endpoints should expose failed items and support retry."""
    monkeypatch.setattr(
        "pawbot.delivery.queue.get_default_queue_dir",
        lambda: local_tmp_path / "delivery-queue",
    )
    monkeypatch.setattr(dashboard_auth, "AUTH_FILE", local_tmp_path / "dashboard_auth.json")
    monkeypatch.setattr(dashboard_auth, "JWT_SECRET_FILE", local_tmp_path / "dashboard_secret")
    monkeypatch.setattr(dashboard_auth, "AUTH_STORAGE_DIR", local_tmp_path / "dashboard_tokens")
    dashboard_auth.set_password("phase14-secret")

    queue = DeliveryQueue()
    queue.enqueue(DeliveryMessage(
        channel="telegram",
        recipient="user-3",
        content="fail-me",
        message_id="failed-1",
        max_attempts=1,
    ))
    current = queue.dequeue()
    assert current is not None
    queue.mark_failed("failed-1", "channel offline")

    client = TestClient(dashboard_app)
    login = client.post("/api/auth/login", json={"password": "phase14-secret"})
    assert login.status_code == 200

    stats = client.get("/api/delivery/stats")
    assert stats.status_code == 200
    assert stats.json()["failed_total"] == 1

    failed = client.get("/api/delivery/failed")
    assert failed.status_code == 200
    assert failed.json()["failed"][0]["message_id"] == "failed-1"

    retried = client.post("/api/delivery/retry/failed-1")
    assert retried.status_code == 200
    assert retried.json()["success"] is True

    stats_after = client.get("/api/delivery/stats")
    assert stats_after.json()["pending"] == 1
