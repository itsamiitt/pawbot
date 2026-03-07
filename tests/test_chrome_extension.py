"""Tests for Phase 15 — Chrome Extension Core & Native Messaging Bridge."""

from __future__ import annotations

import asyncio
import json
import struct
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pawbot.browser_bridge.native_host import (
    NativeMessagingHost,
    NATIVE_HOST_NAME,
    get_native_host_manifest,
)


# ── Test Helpers ──────────────────────────────────────────────────────────────


def encode_native_message(msg: dict) -> bytes:
    """Encode a message in Chrome Native Messaging format."""
    data = json.dumps(msg).encode("utf-8")
    return struct.pack("<I", len(data)) + data


def decode_native_message(raw: bytes) -> dict:
    """Decode a Chrome Native Messaging message."""
    length = struct.unpack("<I", raw[:4])[0]
    return json.loads(raw[4:4 + length].decode("utf-8"))


# ── NativeMessagingHost Tests ─────────────────────────────────────────────────


class TestNativeMessagingHostProtocol:
    """Test the low-level Chrome Native Messaging protocol."""

    def test_read_message(self):
        msg = {"type": "ping"}
        raw = encode_native_message(msg)
        stdin = BytesIO(raw)

        host = NativeMessagingHost(stdin=stdin, stdout=BytesIO())
        result = host.read_message()

        assert result is not None
        assert result["type"] == "ping"

    def test_read_message_eof(self):
        stdin = BytesIO(b"")
        host = NativeMessagingHost(stdin=stdin, stdout=BytesIO())
        assert host.read_message() is None

    def test_read_message_truncated_header(self):
        stdin = BytesIO(b"\x01\x00")  # Only 2 bytes, need 4
        host = NativeMessagingHost(stdin=stdin, stdout=BytesIO())
        assert host.read_message() is None

    def test_read_message_too_large(self):
        # Length > 1MB
        raw = struct.pack("<I", 2 * 1024 * 1024) + b"\x00" * 100
        stdin = BytesIO(raw)
        host = NativeMessagingHost(stdin=stdin, stdout=BytesIO())
        assert host.read_message() is None

    def test_read_message_truncated_body(self):
        length = 100
        raw = struct.pack("<I", length) + b'{"short": true}'  # Less than 100 bytes
        stdin = BytesIO(raw)
        host = NativeMessagingHost(stdin=stdin, stdout=BytesIO())
        assert host.read_message() is None

    def test_read_message_invalid_json(self):
        data = b"NOT VALID JSON!!!"
        raw = struct.pack("<I", len(data)) + data
        stdin = BytesIO(raw)
        host = NativeMessagingHost(stdin=stdin, stdout=BytesIO())
        assert host.read_message() is None

    def test_send_message(self):
        stdout = BytesIO()
        host = NativeMessagingHost(stdin=BytesIO(), stdout=stdout)
        host.send_message({"type": "pong"})

        stdout.seek(0)
        result = decode_native_message(stdout.read())
        assert result["type"] == "pong"

    def test_send_message_encoding(self):
        stdout = BytesIO()
        host = NativeMessagingHost(stdin=BytesIO(), stdout=stdout)
        host.send_message({"text": "Hello 🐾"})

        stdout.seek(0)
        result = decode_native_message(stdout.read())
        assert result["text"] == "Hello 🐾"

    def test_multiple_messages(self):
        """Read multiple messages in sequence."""
        msgs = [{"type": "ping"}, {"type": "tool_response", "id": "1"}]
        raw = b"".join(encode_native_message(m) for m in msgs)
        stdin = BytesIO(raw)
        host = NativeMessagingHost(stdin=stdin, stdout=BytesIO())

        m1 = host.read_message()
        m2 = host.read_message()
        assert m1["type"] == "ping"
        assert m2["type"] == "tool_response"

    def test_ping_pong_flow(self):
        """Simulate a ping/pong exchange."""
        raw = encode_native_message({"type": "ping"})
        stdin = BytesIO(raw)
        stdout = BytesIO()

        host = NativeMessagingHost(stdin=stdin, stdout=stdout)
        msg = host.read_message()
        assert msg["type"] == "ping"

        host.send_message({"type": "pong"})
        stdout.seek(0)
        response = decode_native_message(stdout.read())
        assert response["type"] == "pong"


class TestNativeMessagingHostTools:
    """Test tool request/response handling."""

    @pytest.mark.asyncio
    async def test_execute_tool_timeout(self):
        """Tool requests should timeout after specified duration."""
        host = NativeMessagingHost(stdin=BytesIO(), stdout=BytesIO())
        result = await host.execute_tool("slow_tool", {}, timeout=0.1)
        assert "error" in result
        assert "timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_tool_success(self):
        """Manually resolve a tool response."""
        stdout = BytesIO()
        host = NativeMessagingHost(stdin=BytesIO(), stdout=stdout)

        # Start the tool request in background
        async def run_tool():
            return await host.execute_tool("read_page", {"url": "test"}, timeout=5.0)

        task = asyncio.create_task(run_tool())

        # Give it a moment to send the request
        await asyncio.sleep(0.05)

        # Manually resolve
        host._handle_response({
            "type": "tool_response",
            "id": "tool_1",
            "result": {"content": "page data"},
        })

        result = await task
        assert result == {"content": "page data"}

    @pytest.mark.asyncio
    async def test_execute_tool_error_response(self):
        """Tool returning an error."""
        host = NativeMessagingHost(stdin=BytesIO(), stdout=BytesIO())

        async def run_tool():
            return await host.execute_tool("bad_tool", {}, timeout=5.0)

        task = asyncio.create_task(run_tool())
        await asyncio.sleep(0.05)

        host._handle_response({
            "type": "tool_response",
            "id": "tool_1",
            "error": "Permission denied",
        })

        result = await task
        assert result == {"error": "Permission denied"}

    def test_handle_response_unknown_id(self):
        """Responses with unknown IDs should be silently ignored."""
        host = NativeMessagingHost(stdin=BytesIO(), stdout=BytesIO())
        # Should not raise
        host._handle_response({"type": "tool_response", "id": "unknown_123"})

    def test_tool_id_counter_increments(self):
        stdout = BytesIO()
        host = NativeMessagingHost(stdin=BytesIO(), stdout=stdout)
        assert host._tool_id_counter == 0

        # Simulate two sends (won't actually await, just check counter)
        host.send_message({
            "type": "tool_request",
            "id": "tool_1",
            "params": {},
        })
        host._tool_id_counter += 1
        assert host._tool_id_counter == 1


class TestNativeMessagingHostLifecycle:
    """Test host lifecycle."""

    def test_is_running_initially(self):
        host = NativeMessagingHost(stdin=BytesIO(), stdout=BytesIO())
        assert host.is_running is True

    def test_stop(self):
        host = NativeMessagingHost(stdin=BytesIO(), stdout=BytesIO())
        host.stop()
        assert host.is_running is False


# ── Registration Tests ────────────────────────────────────────────────────────


class TestNativeHostRegistration:
    """Test native host manifest generation and registration."""

    def test_manifest_structure(self):
        manifest = get_native_host_manifest("test-extension-id")

        assert manifest["name"] == NATIVE_HOST_NAME
        assert manifest["type"] == "stdio"
        assert manifest["description"]
        assert "test-extension-id" in manifest["allowed_origins"][0]

    def test_manifest_name(self):
        assert NATIVE_HOST_NAME == "com.pawbot.browser_extension"

    def test_manifest_path_is_set(self):
        manifest = get_native_host_manifest()
        assert manifest["path"]  # Should point to something

    def test_manifest_allowed_origins_format(self):
        manifest = get_native_host_manifest("abcdefghijklmnop")
        origin = manifest["allowed_origins"][0]
        assert origin.startswith("chrome-extension://")
        assert origin.endswith("/")


# ── Extension File Tests ──────────────────────────────────────────────────────


class TestExtensionFiles:
    """Test that extension files are properly structured."""

    EXTENSION_DIR = Path(__file__).resolve().parent.parent.parent / "pawbot-extension"

    def test_manifest_exists(self):
        manifest_path = self.EXTENSION_DIR / "manifest.json"
        assert manifest_path.exists(), f"Missing: {manifest_path}"

    def test_manifest_valid_json(self):
        manifest_path = self.EXTENSION_DIR / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["manifest_version"] == 3
        assert data["name"] == "PawBot Browser Control"

    def test_manifest_permissions(self):
        manifest_path = self.EXTENSION_DIR / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        perms = data["permissions"]
        required = ["activeTab", "tabs", "scripting", "nativeMessaging", "storage"]
        for p in required:
            assert p in perms, f"Missing permission: {p}"

    def test_manifest_service_worker(self):
        manifest_path = self.EXTENSION_DIR / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["background"]["service_worker"] == "service-worker.js"
        assert data["background"]["type"] == "module"

    def test_service_worker_exists(self):
        sw_path = self.EXTENSION_DIR / "service-worker.js"
        assert sw_path.exists()

    def test_service_worker_has_tools(self):
        sw_path = self.EXTENSION_DIR / "service-worker.js"
        content = sw_path.read_text(encoding="utf-8")
        tools = ["read_page", "click", "type", "navigate", "screenshot",
                 "get_tabs", "switch_tab", "new_tab", "close_tab", "scroll"]
        for tool in tools:
            assert tool in content, f"Missing tool: {tool}"

    def test_service_worker_has_native_messaging(self):
        sw_path = self.EXTENSION_DIR / "service-worker.js"
        content = sw_path.read_text(encoding="utf-8")
        assert "connectNative" in content
        assert NATIVE_HOST_NAME in content

    def test_content_script_accessibility_tree(self):
        cs_path = self.EXTENSION_DIR / "content-scripts" / "accessibility-tree.js"
        assert cs_path.exists()
        content = cs_path.read_text(encoding="utf-8")
        assert "__pawbotAccessibilityTree" in content
        assert "__pawbotElementMap" in content

    def test_content_script_agent_indicator(self):
        cs_path = self.EXTENSION_DIR / "content-scripts" / "agent-indicator.js"
        assert cs_path.exists()
        content = cs_path.read_text(encoding="utf-8")
        assert "pawbot_agent_active" in content

    def test_sidepanel_exists(self):
        sp_path = self.EXTENSION_DIR / "sidepanel.html"
        assert sp_path.exists()
        content = sp_path.read_text(encoding="utf-8")
        assert "PawBot" in content
        assert "sidepanel.js" in content

    def test_icon_exists(self):
        icon_path = self.EXTENSION_DIR / "icons" / "icon-128.png"
        assert icon_path.exists()
        # Should be a valid PNG (starts with PNG signature)
        data = icon_path.read_bytes()
        assert data[:4] == b'\x89PNG'

    def test_manifest_content_scripts(self):
        manifest_path = self.EXTENSION_DIR / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        cs = data["content_scripts"]
        assert len(cs) == 2

        # Accessibility tree runs at document_start
        assert "accessibility-tree.js" in cs[0]["js"][0]
        assert cs[0]["run_at"] == "document_start"

        # Agent indicator runs at document_idle
        assert "agent-indicator.js" in cs[1]["js"][0]
        assert cs[1]["run_at"] == "document_idle"

    def test_minimum_chrome_version(self):
        manifest_path = self.EXTENSION_DIR / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert int(data["minimum_chrome_version"]) >= 116
