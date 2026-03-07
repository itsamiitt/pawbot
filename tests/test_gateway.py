"""
tests/test_gateway.py
Run: pytest tests/test_gateway.py -v
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from pawbot.gateway.server import app, gateway_limiter


@pytest.mark.asyncio
async def test_health_returns_200():
    """GET /health must return 200 and status=ok."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "uptime_seconds"  in data
    assert "active_sessions" in data
    assert "memory_db_mb"    in data


@pytest.mark.asyncio
async def test_sessions_endpoint():
    """GET /sessions must return active_sessions count."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/sessions")
    assert r.status_code == 200
    data = r.json()
    assert "active_sessions" in data
    assert "lanes"           in data


@pytest.mark.asyncio
async def test_health_contains_lane_depths():
    """lane_depths in /health must be a dict."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
    assert isinstance(r.json()["lane_depths"], dict)


@pytest.mark.asyncio
async def test_invalid_json_on_ws_returns_error():
    """WebSocket must return error JSON on invalid input — not crash."""
    from starlette.testclient import TestClient

    client = TestClient(app)
    with client.websocket_connect("/ws/test_session_001") as ws:
        ws.send_text("NOT VALID JSON {{{")  # malformed
        response = json.loads(ws.receive_text())
    assert "error" in response


@pytest.mark.asyncio
async def test_health_endpoint_rate_limited():
    """GET /health should return 429 after hitting the same-client limit."""
    gateway_limiter.reset()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(60):
            r = await client.get("/health")
            assert r.status_code == 200
        blocked = await client.get("/health")
    gateway_limiter.reset()
    assert blocked.status_code == 429
    assert blocked.json()["error"] == "Rate limit exceeded"


@pytest.mark.asyncio
async def test_rest_chat_endpoint_returns_response():
    """POST /api/chat returns a direct response payload."""
    gateway_limiter.reset()
    transport = ASGITransport(app=app)
    with patch("pawbot.gateway.server._process_api_message", new=AsyncMock(return_value="pong")):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/chat", json={"message": "ping", "user_id": "u1"})
    gateway_limiter.reset()
    assert r.status_code == 200
    data = r.json()
    assert data["response"] == "pong"
    assert "session_id" in data
