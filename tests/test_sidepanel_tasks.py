"""Tests for Phase 18 — Side Panel UI & Scheduled Tasks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pawbot.browser_bridge.ws_server import PanelWebSocketServer


EXTENSION_DIR = Path(__file__).resolve().parent.parent.parent / "pawbot-extension"


# ── Side Panel Files Tests ────────────────────────────────────────────────────


class TestSidePanelFiles:
    """Test side panel HTML/CSS/JS files."""

    def test_html_exists(self):
        path = EXTENSION_DIR / "sidepanel.html"
        assert path.exists()

    def test_html_structure(self):
        content = (EXTENSION_DIR / "sidepanel.html").read_text(encoding="utf-8")
        assert "pawbot-panel" in content
        assert "panel-header" in content
        assert "messages" in content
        assert "user-input" in content
        assert "send-btn" in content

    def test_html_links_css(self):
        content = (EXTENSION_DIR / "sidepanel.html").read_text(encoding="utf-8")
        assert "sidepanel.css" in content

    def test_html_links_js(self):
        content = (EXTENSION_DIR / "sidepanel.html").read_text(encoding="utf-8")
        assert "sidepanel.js" in content

    def test_html_has_status(self):
        content = (EXTENSION_DIR / "sidepanel.html").read_text(encoding="utf-8")
        assert "status-dot" in content
        assert "status-text" in content

    def test_html_has_input_area(self):
        content = (EXTENSION_DIR / "sidepanel.html").read_text(encoding="utf-8")
        assert "textarea" in content
        assert "placeholder" in content

    def test_css_exists(self):
        path = EXTENSION_DIR / "sidepanel.css"
        assert path.exists()

    def test_css_has_dark_theme(self):
        content = (EXTENSION_DIR / "sidepanel.css").read_text(encoding="utf-8")
        assert "--bg-primary" in content
        assert "#0f0f14" in content or "0f0f14" in content

    def test_css_has_message_styles(self):
        content = (EXTENSION_DIR / "sidepanel.css").read_text(encoding="utf-8")
        assert ".message.user" in content
        assert ".message.assistant" in content
        assert ".message.tool" in content

    def test_css_has_animation(self):
        content = (EXTENSION_DIR / "sidepanel.css").read_text(encoding="utf-8")
        assert "@keyframes" in content
        assert "msg-in" in content

    def test_css_has_scrollbar_styling(self):
        content = (EXTENSION_DIR / "sidepanel.css").read_text(encoding="utf-8")
        assert "scrollbar" in content

    def test_js_exists(self):
        path = EXTENSION_DIR / "sidepanel.js"
        assert path.exists()

    def test_js_has_websocket(self):
        content = (EXTENSION_DIR / "sidepanel.js").read_text(encoding="utf-8")
        assert "WebSocket" in content
        assert "ws://127.0.0.1:8765" in content

    def test_js_has_reconnect(self):
        content = (EXTENSION_DIR / "sidepanel.js").read_text(encoding="utf-8")
        assert "reconnect" in content.lower()
        assert "3000" in content  # 3s reconnect timer

    def test_js_handles_messages(self):
        content = (EXTENSION_DIR / "sidepanel.js").read_text(encoding="utf-8")
        assert "agent_response" in content
        assert "tool_use" in content
        assert "agent_active" in content

    def test_js_add_message_function(self):
        content = (EXTENSION_DIR / "sidepanel.js").read_text(encoding="utf-8")
        assert "addMessage" in content

    def test_js_send_message(self):
        content = (EXTENSION_DIR / "sidepanel.js").read_text(encoding="utf-8")
        assert "sendMessage" in content
        assert "user_message" in content

    def test_js_enter_to_send(self):
        content = (EXTENSION_DIR / "sidepanel.js").read_text(encoding="utf-8")
        assert "Enter" in content
        assert "shiftKey" in content

    def test_js_toggles_indicators(self):
        content = (EXTENSION_DIR / "sidepanel.js").read_text(encoding="utf-8")
        assert "SHOW_AGENT_INDICATORS" in content
        assert "HIDE_AGENT_INDICATORS" in content

    def test_js_dom_message_limit(self):
        content = (EXTENSION_DIR / "sidepanel.js").read_text(encoding="utf-8")
        assert "200" in content  # Max 200 messages


# ── Offscreen Document Tests ─────────────────────────────────────────────────


class TestOffscreenDocument:
    """Test offscreen HTML and JS files."""

    def test_html_exists(self):
        path = EXTENSION_DIR / "offscreen.html"
        assert path.exists()

    def test_html_links_js(self):
        content = (EXTENSION_DIR / "offscreen.html").read_text(encoding="utf-8")
        assert "offscreen.js" in content

    def test_js_exists(self):
        path = EXTENSION_DIR / "offscreen.js"
        assert path.exists()

    def test_js_has_audio_context(self):
        content = (EXTENSION_DIR / "offscreen.js").read_text(encoding="utf-8")
        assert "AudioContext" in content

    def test_js_has_play_sound(self):
        content = (EXTENSION_DIR / "offscreen.js").read_text(encoding="utf-8")
        assert "playSound" in content
        assert "decodeAudioData" in content

    def test_js_has_play_beep(self):
        content = (EXTENSION_DIR / "offscreen.js").read_text(encoding="utf-8")
        assert "playBeep" in content
        assert "oscillator" in content.lower() or "createOscillator" in content

    def test_js_handles_notification_sound(self):
        content = (EXTENSION_DIR / "offscreen.js").read_text(encoding="utf-8")
        assert "PLAY_NOTIFICATION_SOUND" in content

    def test_js_handles_beep(self):
        content = (EXTENSION_DIR / "offscreen.js").read_text(encoding="utf-8")
        assert "PLAY_BEEP" in content

    def test_js_volume_control(self):
        content = (EXTENSION_DIR / "offscreen.js").read_text(encoding="utf-8")
        assert "volume" in content
        assert "gain" in content.lower()

    def test_js_ping_handler(self):
        content = (EXTENSION_DIR / "offscreen.js").read_text(encoding="utf-8")
        assert "PING_OFFSCREEN" in content


# ── Scheduled Tasks Tests ────────────────────────────────────────────────────


class TestScheduledTasks:
    """Test scheduled task system in the service worker."""

    def _read_sw(self):
        return (EXTENSION_DIR / "service-worker.js").read_text(encoding="utf-8")

    def test_has_create_scheduled_task(self):
        content = self._read_sw()
        assert "createScheduledTask" in content

    def test_has_list_scheduled_tasks(self):
        content = self._read_sw()
        assert "listScheduledTasks" in content

    def test_has_delete_scheduled_task(self):
        content = self._read_sw()
        assert "deleteScheduledTask" in content

    def test_has_toggle_scheduled_task(self):
        content = self._read_sw()
        assert "toggleScheduledTask" in content

    def test_uses_chrome_alarms(self):
        content = self._read_sw()
        assert "chrome.alarms.create" in content
        assert "chrome.alarms.onAlarm" in content
        assert "chrome.alarms.clear" in content

    def test_uses_chrome_storage(self):
        content = self._read_sw()
        assert "chrome.storage.local" in content
        assert "scheduledTasks" in content

    def test_supports_interval_repeat(self):
        content = self._read_sw()
        assert "interval" in content
        assert "periodInMinutes" in content

    def test_supports_daily_repeat(self):
        content = self._read_sw()
        assert "daily" in content
        assert "1440" in content  # 24h in minutes

    def test_supports_once_task(self):
        content = self._read_sw()
        assert "once" in content
        assert "delayInMinutes" in content

    def test_alarm_sends_to_native(self):
        content = self._read_sw()
        assert "scheduled_task" in content
        assert "nativePort" in content

    def test_enabled_check(self):
        content = self._read_sw()
        assert "task.enabled" in content or "!task.enabled" in content


# ── WebSocket Server Tests ────────────────────────────────────────────────────


class TestPanelWebSocketServer:
    """Test the PawBot panel WebSocket server."""

    def test_default_config(self):
        server = PanelWebSocketServer()
        assert server.host == "127.0.0.1"
        assert server.port == 8765

    def test_custom_config(self):
        server = PanelWebSocketServer(host="0.0.0.0", port=9999)
        assert server.host == "0.0.0.0"
        assert server.port == 9999

    def test_initial_state(self):
        server = PanelWebSocketServer()
        assert server.client_count == 0
        assert server.is_running is False

    def test_agent_callback_stored(self):
        async def callback(content, tab_id):
            return f"reply: {content}"

        server = PanelWebSocketServer(agent_callback=callback)
        assert server.agent_callback is callback

    def test_no_callback_echo(self):
        """Without callback, server should echo."""
        server = PanelWebSocketServer()
        assert server.agent_callback is None

    @pytest.mark.asyncio
    async def test_handle_user_message(self):
        """Test message handling with echo (no callback)."""
        responses = []

        class FakeWS:
            async def send(self, data):
                responses.append(json.loads(data))

        server = PanelWebSocketServer()
        ws = FakeWS()

        await server._handle_message(ws, {
            "type": "user_message",
            "content": "hello",
        })

        # Should get: agent_active(true), agent_response, agent_active(false)
        assert len(responses) == 3
        assert responses[0]["type"] == "agent_active"
        assert responses[0]["active"] is True
        assert responses[1]["type"] == "agent_response"
        assert "hello" in responses[1]["content"]
        assert responses[2]["type"] == "agent_active"
        assert responses[2]["active"] is False

    @pytest.mark.asyncio
    async def test_handle_empty_message(self):
        responses = []

        class FakeWS:
            async def send(self, data):
                responses.append(json.loads(data))

        server = PanelWebSocketServer()
        await server._handle_message(FakeWS(), {
            "type": "user_message",
            "content": "",
        })

        assert len(responses) == 1
        assert responses[0]["type"] == "error"

    @pytest.mark.asyncio
    async def test_handle_ping(self):
        responses = []

        class FakeWS:
            async def send(self, data):
                responses.append(json.loads(data))

        server = PanelWebSocketServer()
        await server._handle_message(FakeWS(), {"type": "ping"})

        assert len(responses) == 1
        assert responses[0]["type"] == "pong"

    @pytest.mark.asyncio
    async def test_broadcast(self):
        sent = []

        class FakeWS:
            async def send(self, data):
                sent.append(json.loads(data))

        server = PanelWebSocketServer()
        server.clients = {FakeWS(), FakeWS()}

        await server.broadcast({"type": "test", "data": "hello"})
        assert len(sent) == 2

    @pytest.mark.asyncio
    async def test_broadcast_no_clients(self):
        """Broadcast with no clients should be a no-op."""
        server = PanelWebSocketServer()
        await server.broadcast({"type": "test"})  # Should not raise

    @pytest.mark.asyncio
    async def test_notify_tool_use(self):
        sent = []

        class FakeWS:
            async def send(self, data):
                sent.append(json.loads(data))

        server = PanelWebSocketServer()
        server.clients = {FakeWS()}

        await server.notify_tool_use("chrome_read_page")
        assert len(sent) == 1
        assert sent[0]["type"] == "tool_use"
        assert sent[0]["tool"] == "chrome_read_page"

    @pytest.mark.asyncio
    async def test_agent_callback_error(self):
        """Agent callback errors should be sent as error messages."""
        async def bad_callback(content, tab_id):
            raise ValueError("Agent crashed")

        responses = []

        class FakeWS:
            async def send(self, data):
                responses.append(json.loads(data))

        server = PanelWebSocketServer(agent_callback=bad_callback)
        await server._handle_message(FakeWS(), {
            "type": "user_message",
            "content": "test",
        })

        # agent_active(true), error, agent_active(false)
        assert len(responses) == 3
        assert responses[1]["type"] == "error"
        assert "crashed" in responses[1]["content"]
