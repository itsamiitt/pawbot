"""Native Messaging Host — bridges PawBot ↔ Chrome Extension (Phase 15.2).

Chrome launches this script. Communication is via stdin/stdout
with length-prefixed JSON messages (Chrome Native Messaging protocol).

Protocol:
  Extension → Host: {type: "ping"}        → Host → Extension: {type: "pong"}
  Host → Extension: {type: "tool_request", method, params, id}
  Extension → Host: {type: "tool_response", result | error, id}
"""

from __future__ import annotations

import asyncio
import json
import platform
import struct
import sys
import threading
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

from loguru import logger


NATIVE_HOST_NAME = "com.pawbot.browser_extension"


class NativeMessagingHost:
    """Handles Chrome Native Messaging protocol (length-prefixed JSON on stdio).

    Chrome sends messages as: [4-byte little-endian length][JSON bytes]
    Responses use the same format on stdout.
    """

    def __init__(
        self,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
    ):
        self._stdin = stdin or sys.stdin.buffer
        self._stdout = stdout or sys.stdout.buffer
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._running = True
        self._tool_id_counter = 0
        self._lock = threading.Lock()

    # ── Low-level I/O ─────────────────────────────────────────────────

    def read_message(self) -> dict[str, Any] | None:
        """Read a single message from Chrome (stdin).

        Returns None on EOF or malformed data.
        """
        raw_length = self._stdin.read(4)
        if not raw_length or len(raw_length) < 4:
            return None

        length = struct.unpack("<I", raw_length)[0]
        if length > 1024 * 1024:  # 1MB safety limit
            logger.warning("Message too large: {} bytes", length)
            return None

        data = self._stdin.read(length)
        if len(data) < length:
            return None

        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            logger.warning("Malformed JSON from extension")
            return None

    def send_message(self, msg: dict[str, Any]) -> None:
        """Send a message to Chrome (stdout)."""
        with self._lock:
            encoded = json.dumps(msg).encode("utf-8")
            self._stdout.write(struct.pack("<I", len(encoded)))
            self._stdout.write(encoded)
            self._stdout.flush()

    # ── Tool Execution ────────────────────────────────────────────────

    async def execute_tool(
        self, tool_name: str, args: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        """Send tool request to Chrome extension and wait for response."""
        self._tool_id_counter += 1
        request_id = f"tool_{self._tool_id_counter}"

        self.send_message({
            "type": "tool_request",
            "method": "execute_tool",
            "params": {"tool": tool_name, "args": args},
            "id": request_id,
        })

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            return {"error": f"Tool '{tool_name}' timed out after {timeout}s"}

    # ── Response Handler ──────────────────────────────────────────────

    def _handle_response(self, msg: dict[str, Any]) -> None:
        """Handle tool response from Chrome."""
        request_id = msg.get("id")
        if request_id and request_id in self._pending:
            future = self._pending.pop(request_id)
            if not future.done():
                if "error" in msg:
                    future.set_result({"error": msg["error"]})
                else:
                    future.set_result(msg.get("result", {}))

    # ── Reader Thread ─────────────────────────────────────────────────

    def start_reader(self) -> None:
        """Start background thread reading from Chrome."""
        def _reader():
            while self._running:
                try:
                    msg = self.read_message()
                    if msg is None:
                        break
                    if msg.get("type") == "ping":
                        self.send_message({"type": "pong"})
                    elif msg.get("type") == "tool_response":
                        self._handle_response(msg)
                    else:
                        logger.debug("Unknown message type: {}", msg.get("type"))
                except Exception as e:
                    logger.error("Native host reader error: {}", e)
                    break
            self._running = False

        thread = threading.Thread(target=_reader, daemon=True, name="native-host-reader")
        thread.start()

    def stop(self) -> None:
        """Stop the reader thread."""
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running


# ── Registration ──────────────────────────────────────────────────────────────


def get_native_host_manifest(extension_id: str = "EXTENSION_ID_HERE") -> dict[str, Any]:
    """Generate the native messaging host manifest JSON."""
    script_path = str(Path(__file__).resolve())

    manifest: dict[str, Any] = {
        "name": NATIVE_HOST_NAME,
        "description": "PawBot Browser Control Bridge",
        "path": script_path,
        "type": "stdio",
        "allowed_origins": [
            f"chrome-extension://{extension_id}/"
        ],
    }

    if platform.system() == "Windows":
        # On Windows, Chrome needs a .bat wrapper
        wrapper_path = Path(__file__).parent / "native_host_wrapper.bat"
        manifest["path"] = str(wrapper_path)

    return manifest


def register_native_host(extension_id: str = "EXTENSION_ID_HERE") -> str:
    """Register native messaging host manifest with Chrome.

    Returns:
        Path where the manifest was registered.
    """
    manifest = get_native_host_manifest(extension_id)
    python_path = sys.executable
    script_path = str(Path(__file__).resolve())

    system = platform.system()

    if system == "Windows":
        import winreg

        # Create .bat wrapper
        wrapper = Path(__file__).parent / "native_host_wrapper.bat"
        wrapper.write_text(
            f'@echo off\n"{python_path}" "{script_path}" %*\n',
            encoding="utf-8",
        )
        manifest["path"] = str(wrapper)

        # Write manifest to disk
        manifest_path = Path.home() / ".pawbot" / "native-host-manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        # Register in Windows Registry
        key_path = f"SOFTWARE\\Google\\Chrome\\NativeMessagingHosts\\{NATIVE_HOST_NAME}"
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
        winreg.CloseKey(key)

        logger.info("Native messaging host registered (Windows Registry)")
        return str(manifest_path)

    elif system == "Darwin":
        nm_dir = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts"
    else:
        nm_dir = Path.home() / ".config" / "google-chrome" / "NativeMessagingHosts"

    nm_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = nm_dir / f"{NATIVE_HOST_NAME}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info("Native messaging host registered: {}", manifest_path)
    return str(manifest_path)


# ── Entrypoint (when Chrome launches this script) ─────────────────────────────

if __name__ == "__main__":
    host = NativeMessagingHost()
    host.start_reader()
    # Keep running until Chrome disconnects
    try:
        while host.is_running:
            import time
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
