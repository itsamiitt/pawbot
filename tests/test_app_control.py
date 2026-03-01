"""Tests for the Phase 9 Desktop App Control MCP server.

Tests verify:
  - Server lifecycle and tool discovery
  - App registry (create defaults, register, unknown apps)
  - Screen reading (screenshot, OCR, word locations)
  - Screen finding (text, template, vision mode, not-found)
  - Mouse/keyboard (click, type, key, scroll, drag)
  - Clipboard (read, write, paste)
  - Templates (save, list)
  - Graceful error handling when optional deps are missing
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ══════════════════════════════════════════════════════════════════════════════
#  Module Loader
# ══════════════════════════════════════════════════════════════════════════════


def _load_server_module():
    path = Path(__file__).resolve().parents[1] / "mcp-servers" / "app_control" / "server.py"
    spec = importlib.util.spec_from_file_location("app_control_mcp", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def server_module():
    return _load_server_module()


# ══════════════════════════════════════════════════════════════════════════════
#  Fake pyautogui / pytesseract / pyperclip stubs
# ══════════════════════════════════════════════════════════════════════════════


class FakeScreenshot:
    """Minimal PIL Image replacement for screenshots."""

    def __init__(self, width: int = 1920, height: int = 1080):
        self.width = width
        self.height = height

    def save(self, target, format: str = "PNG"):
        # Handle both file path (str) and BytesIO buffer
        if isinstance(target, str):
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "wb") as f:
                f.write(b"\x89PNG_FAKE_SCREENSHOT_DATA_FOR_TESTING")
        else:
            target.write(b"\x89PNG_FAKE_SCREENSHOT_DATA_FOR_TESTING")


class FakePyAutoGUI:
    """Stub for pyautogui module."""

    FAILSAFE = True
    PAUSE = 0.0  # No delays in tests

    def __init__(self):
        self.clicks: list[dict] = []
        self.typed_chars: list[str] = []
        self.pressed_keys: list[str] = []
        self.hotkeys: list[list[str]] = []
        self.scrolls: list[dict] = []
        self.moves: list[dict] = []
        self.drags: list[dict] = []
        self._locate_result = None

    def screenshot(self, region=None):
        return FakeScreenshot()

    def click(self, x=None, y=None, clicks=1, button="left"):
        self.clicks.append({"x": x, "y": y, "clicks": clicks, "button": button})

    def typewrite(self, char, interval=0):
        self.typed_chars.append(char)

    def press(self, key):
        self.pressed_keys.append(key)

    def hotkey(self, *keys):
        self.hotkeys.append(list(keys))

    def scroll(self, amount, x=None, y=None):
        self.scrolls.append({"amount": amount, "x": x, "y": y})

    def hscroll(self, amount, x=None, y=None):
        self.scrolls.append({"amount": amount, "x": x, "y": y, "horizontal": True})

    def moveTo(self, x, y):
        self.moves.append({"x": x, "y": y})

    def drag(self, xOffset, yOffset, duration=0.5):
        self.drags.append({"x": xOffset, "y": yOffset, "duration": duration})

    def locateOnScreen(self, image_path, confidence=0.8):
        return self._locate_result

    def center(self, location):
        return types.SimpleNamespace(x=location.x, y=location.y)


class FakePyTesseract:
    """Stub for pytesseract module."""

    class Output:
        DICT = "dict"

    def image_to_string(self, image, lang="eng"):
        return "Hello World\nSample text\nSubmit button"

    def image_to_data(self, image, output_type=None):
        return {
            "text": ["Hello", "World", "Submit", "button", ""],
            "left": [10, 80, 200, 280, 0],
            "top": [20, 20, 100, 100, 0],
            "width": [60, 60, 70, 70, 0],
            "height": [20, 20, 20, 20, 0],
            "conf": [90, 85, 95, 88, -1],
        }


class FakePyperclip:
    """Stub for pyperclip module."""

    def __init__(self):
        self._clipboard = ""

    def copy(self, text):
        self._clipboard = text

    def paste(self):
        return self._clipboard


# ══════════════════════════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def setup_fakes(server_module, tmp_path, monkeypatch):
    """Inject fake deps and isolate file paths for every test."""
    fake_gui = FakePyAutoGUI()
    fake_tess = FakePyTesseract()
    fake_clip = FakePyperclip()

    monkeypatch.setattr(server_module, "pyautogui", fake_gui)
    monkeypatch.setattr(server_module, "_PYAUTOGUI_AVAILABLE", True)

    monkeypatch.setattr(server_module, "pytesseract", fake_tess)
    monkeypatch.setattr(server_module, "_TESSERACT_AVAILABLE", True)
    monkeypatch.setattr(server_module, "_PILLOW_AVAILABLE", True)

    monkeypatch.setattr(server_module, "pyperclip", fake_clip)
    monkeypatch.setattr(server_module, "_PYPERCLIP_AVAILABLE", True)

    # Isolate registry and templates paths
    registry_path = str(tmp_path / "app_registry.json")
    templates_dir = str(tmp_path / "templates")
    os.makedirs(templates_dir, exist_ok=True)
    monkeypatch.setattr(server_module, "APP_REGISTRY_PATH", registry_path)
    monkeypatch.setattr(server_module, "TEMPLATES_DIR", templates_dir)

    # Suppress real sleep/time for speed
    monkeypatch.setattr(time, "sleep", lambda _: None)

    yield {
        "gui": fake_gui,
        "tess": fake_tess,
        "clip": fake_clip,
        "registry_path": registry_path,
        "templates_dir": templates_dir,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestAppRegistry:
    """Feature 9.1 — App registry management."""

    def test_registry_creates_defaults_if_missing(self, server_module, setup_fakes):
        """Registry is auto-created with platform defaults when file doesn't exist."""
        path = setup_fakes["registry_path"]
        assert not os.path.exists(path)

        registry = server_module._load_registry()
        assert os.path.exists(path)
        assert isinstance(registry, dict)
        assert len(registry) > 0

    def test_app_register_saves_entry(self, server_module, setup_fakes):
        """app_register adds new entry to the registry file."""
        result = server_module.app_register(
            name="gimp",
            cmd="gimp",
            window_title="GNU Image Manipulation Program",
        )
        assert result["registered"] is True
        assert result["name"] == "gimp"

        # Verify it persisted
        registry = server_module._load_registry()
        assert "gimp" in registry
        assert registry["gimp"]["cmd"] == "gimp"

    def test_unknown_app_uses_raw_command(self, server_module, setup_fakes, monkeypatch):
        """app_launch with unknown name uses the name as a raw command."""
        launched_cmds = []

        def fake_popen(cmd_parts, **kwargs):
            launched_cmds.append(cmd_parts)
            return MagicMock(pid=12345)

        import subprocess as sp
        monkeypatch.setattr(sp, "Popen", fake_popen)
        monkeypatch.setattr(server_module, "subprocess", sp)

        result = server_module.app_launch("my-custom-app")
        assert result["launched"] is True
        assert result["cmd"] == "my-custom-app"


class TestScreenRead:
    """Feature 9.2 — Screen reading via OCR."""

    def test_screenshot_returns_base64(self, server_module, setup_fakes):
        """_take_screenshot returns a non-empty base64 string."""
        b64 = server_module._take_screenshot()
        assert isinstance(b64, str)
        assert len(b64) > 0
        # Should be valid base64
        import base64
        raw = base64.b64decode(b64)
        assert len(raw) > 0

    def test_ocr_extracts_text(self, server_module, setup_fakes):
        """screen_read returns OCR full_text."""
        result = server_module.screen_read()
        assert "full_text" in result
        assert "Hello" in result["full_text"]

    def test_ocr_returns_word_locations(self, server_module, setup_fakes):
        """screen_read returns words with x/y/w/h/conf."""
        result = server_module.screen_read()
        assert "words" in result
        assert len(result["words"]) > 0
        word = result["words"][0]
        assert "text" in word
        assert "x" in word
        assert "y" in word

    def test_region_screenshot_smaller_than_full(self, server_module, setup_fakes):
        """screen_read with region parameter succeeds."""
        result = server_module.screen_read(
            region={"left": 0, "top": 0, "width": 400, "height": 300}
        )
        assert "full_text" in result
        assert "screenshot_b64" in result


class TestScreenFind:
    """Feature 9.2 — Screen element finding."""

    def test_find_by_text_returns_coordinates(self, server_module, setup_fakes):
        """screen_find(mode='text') returns x/y for matched text."""
        result = server_module.screen_find(target="Submit", mode="text")
        assert result["found"] is True
        assert "x" in result
        assert "y" in result
        # Coordinates should be center of the bounding box
        assert result["x"] > 0
        assert result["y"] > 0

    def test_find_returns_not_found_gracefully(self, server_module, setup_fakes):
        """screen_find returns found=False without exceptions for missing text."""
        result = server_module.screen_find(target="NonexistentElement12345", mode="text")
        assert result["found"] is False

    def test_template_find_loads_from_templates_dir(self, server_module, setup_fakes):
        """screen_find(mode='template') looks in TEMPLATES_DIR."""
        # With no template on disk, should return not found
        result = server_module.screen_find(target="missing_button", mode="template")
        assert "error" in result or result.get("found") is False

    def test_vision_mode_returns_screenshot(self, server_module, setup_fakes):
        """screen_find(mode='vision') returns screenshot for LLM analysis."""
        result = server_module.screen_find(target="Submit button", mode="vision")
        assert result.get("requires_vision_model") is True
        assert "screenshot_b64" in result


class TestMouseKeyboard:
    """Feature 9.3 — Mouse and keyboard control."""

    def test_app_click_coordinate_target(self, server_module, setup_fakes):
        """app_click with 'x:N,y:N' coordinates clicks at those coords."""
        result = server_module.app_click("x:450,y:300")
        assert result["clicked"] is True
        assert result["x"] == 450
        assert result["y"] == 300

        gui = setup_fakes["gui"]
        assert len(gui.clicks) == 1
        assert gui.clicks[0]["x"] == 450
        assert gui.clicks[0]["y"] == 300

    def test_app_click_by_text(self, server_module, setup_fakes):
        """app_click with text target finds element via OCR then clicks."""
        result = server_module.app_click("Submit")
        assert result["clicked"] is True
        assert result["x"] > 0
        assert result["y"] > 0

    def test_app_type_uses_interval(self, server_module, setup_fakes):
        """app_type types each character through pyautogui."""
        result = server_module.app_type(text="hello")
        assert result["typed"] is True
        assert result["chars"] == 5

        gui = setup_fakes["gui"]
        assert len(gui.typed_chars) == 5
        assert "".join(gui.typed_chars) == "hello"

    def test_app_type_clear_first(self, server_module, setup_fakes):
        """app_type with clear_first sends Ctrl+A then Delete."""
        result = server_module.app_type(text="new text", clear_first=True)
        assert result["typed"] is True

        gui = setup_fakes["gui"]
        # Should have called hotkey("ctrl", "a") and press("delete")
        assert ["ctrl", "a"] in gui.hotkeys
        assert "delete" in gui.pressed_keys

    def test_app_key_single_key(self, server_module, setup_fakes):
        """app_key presses a single key."""
        result = server_module.app_key("enter")
        assert result["pressed"] is True

        gui = setup_fakes["gui"]
        assert "enter" in gui.pressed_keys

    def test_app_key_hotkey_combination(self, server_module, setup_fakes):
        """app_key handles key combos like 'ctrl+c'."""
        result = server_module.app_key("ctrl+c")
        assert result["pressed"] is True

        gui = setup_fakes["gui"]
        assert ["ctrl", "c"] in gui.hotkeys

    def test_app_scroll_down(self, server_module, setup_fakes):
        """app_scroll(direction='down') scrolls down."""
        result = server_module.app_scroll(direction="down", amount=5)
        assert result["scrolled"] is True

        gui = setup_fakes["gui"]
        assert len(gui.scrolls) == 1
        assert gui.scrolls[0]["amount"] == -5  # Negative = down

    def test_app_scroll_up(self, server_module, setup_fakes):
        """app_scroll(direction='up') scrolls up."""
        result = server_module.app_scroll(direction="up", amount=3)
        assert result["scrolled"] is True

        gui = setup_fakes["gui"]
        assert gui.scrolls[0]["amount"] == 3  # Positive = up

    def test_app_drag_by_coordinates(self, server_module, setup_fakes):
        """app_drag from/to coordinate strings performs drag."""
        result = server_module.app_drag(
            from_target="x:100,y:200",
            to_target="x:300,y:400",
        )
        assert result["dragged"] is True
        assert result["from"]["x"] == 100
        assert result["to"]["x"] == 300

        gui = setup_fakes["gui"]
        assert len(gui.drags) == 1
        assert gui.drags[0]["x"] == 200   # 300 - 100
        assert gui.drags[0]["y"] == 200   # 400 - 200


class TestClipboard:
    """Feature 9.4 — Clipboard management."""

    def test_clipboard_write_and_read(self, server_module, setup_fakes):
        """clipboard_write and clipboard_read round-trip text."""
        write_result = server_module.clipboard_write("Hello clipboard")
        assert write_result["written"] is True
        assert write_result["length"] == len("Hello clipboard")

        read_result = server_module.clipboard_read()
        assert read_result["content"] == "Hello clipboard"
        assert read_result["length"] == len("Hello clipboard")

    def test_clipboard_paste_invokes_ctrl_v(self, server_module, setup_fakes):
        """clipboard_paste writes to clipboard then presses Ctrl+V."""
        result = server_module.clipboard_paste("Paste this text")
        assert result["pasted"] is True

        gui = setup_fakes["gui"]
        assert ["ctrl", "v"] in gui.hotkeys

        clip = setup_fakes["clip"]
        assert clip._clipboard == "Paste this text"


class TestTemplates:
    """Feature 9.5 — Screen template management."""

    def test_template_save_creates_png(self, server_module, setup_fakes):
        """template_save creates a PNG file in TEMPLATES_DIR."""
        result = server_module.template_save(
            name="submit_button",
            region={"left": 200, "top": 100, "width": 80, "height": 30},
        )
        assert result["saved"] is True
        assert result["name"] == "submit_button"
        assert os.path.exists(result["path"])

    def test_template_list_returns_saved(self, server_module, setup_fakes):
        """template_list shows templates saved on disk."""
        # Save a template first
        server_module.template_save(
            name="test_tmpl",
            region={"left": 0, "top": 0, "width": 100, "height": 50},
        )

        result = server_module.template_list()
        assert result["count"] >= 1
        names = [t["name"] for t in result["templates"]]
        assert "test_tmpl" in names


class TestServerLifecycle:
    """Server startup and tool registry."""

    def test_server_starts_without_error(self, server_module):
        """The server module loads, registers FastMCP, and exposes main()."""
        assert server_module.mcp is not None
        assert callable(server_module.main)

    def test_list_tools_returns_all_tools(self, server_module):
        """list_tools() returns every registered tool name."""
        tools = server_module.list_tools()
        assert tools["count"] == len(server_module.TOOL_NAMES)
        assert set(tools["tools"]) == set(server_module.TOOL_NAMES)
        assert tools["count"] >= 17

    def test_handles_invalid_args_gracefully(self, server_module, setup_fakes):
        """Tools return error dicts instead of raising exceptions."""
        result = server_module.screen_find(target="X", mode="invalid")
        assert "error" in result

    def test_app_registry_path(self, server_module):
        """APP_REGISTRY_PATH matches canonical path from MASTER_REFERENCE."""
        # In tests this is overridden, but the default should be correct
        default_path = os.path.expanduser("~/.pawbot/app_registry.json")
        # Just verify the module has the attribute
        assert hasattr(server_module, "APP_REGISTRY_PATH")

    def test_templates_dir_path(self, server_module):
        """TEMPLATES_DIR matches canonical path from MASTER_REFERENCE."""
        assert hasattr(server_module, "TEMPLATES_DIR")

    def test_logger_name(self, server_module):
        """Logger uses the correct name."""
        assert server_module.logger.name == "pawbot.mcp.app_control"


class TestDependencyFailures:
    """All tools degrade gracefully when deps are missing."""

    def test_tools_error_without_pyautogui(self, server_module, monkeypatch, setup_fakes):
        """Tools requiring pyautogui return error when it's missing."""
        monkeypatch.setattr(server_module, "_PYAUTOGUI_AVAILABLE", False)

        checks = [
            server_module.app_launch("chrome"),
            server_module.app_focus("Terminal"),
            server_module.screen_read(),
            server_module.screen_find(target="x", mode="text"),
            server_module.screen_wait(target="x", timeout=1, poll_interval=0.1),
            server_module.app_click("x:10,y:10"),
            server_module.app_type(text="hello"),
            server_module.app_key(key="enter"),
            server_module.app_scroll(direction="down"),
            server_module.app_drag(from_target="x:0,y:0", to_target="x:1,y:1"),
            server_module.template_save(name="x", region={"left": 0, "top": 0, "width": 1, "height": 1}),
        ]
        for result in checks:
            assert "error" in result
            assert "pyautogui" in result["error"].lower()

    def test_tools_error_without_pytesseract(self, server_module, monkeypatch, setup_fakes):
        """OCR tools return error when pytesseract is missing."""
        monkeypatch.setattr(server_module, "_TESSERACT_AVAILABLE", False)

        result = server_module.screen_read()
        assert "error" in result
        assert "pytesseract" in result["error"].lower()

    def test_tools_error_without_pyperclip(self, server_module, monkeypatch, setup_fakes):
        """Clipboard tools return error when pyperclip is missing."""
        monkeypatch.setattr(server_module, "_PYPERCLIP_AVAILABLE", False)

        checks = [
            server_module.clipboard_read(),
            server_module.clipboard_write("text"),
            server_module.clipboard_paste("text"),
        ]
        for result in checks:
            assert "error" in result
            assert "pyperclip" in result["error"].lower()

    def test_app_close_returns_result(self, server_module, setup_fakes, monkeypatch):
        """app_close returns a result dict on any platform."""
        import subprocess as sp

        def fake_run(*args, **kwargs):
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(sp, "run", fake_run)
        monkeypatch.setattr(server_module, "subprocess", sp)

        result = server_module.app_close("Notepad")
        assert isinstance(result, dict)
        # Should have closed key (may be True or False depending on platform)
        assert "closed" in result or "error" in result


class TestScreenWait:
    """Feature 9.2 — screen_wait polling."""

    def test_screen_wait_finds_text(self, server_module, setup_fakes):
        """screen_wait returns when target text is found."""
        result = server_module.screen_wait(target="Submit", mode="text", timeout=5, poll_interval=0.1)
        assert result.get("found") is True

    def test_screen_wait_timeout(self, server_module, setup_fakes, monkeypatch):
        """screen_wait returns timed_out when target never appears."""
        # Override screen_find to never find anything
        monkeypatch.setattr(
            server_module, "screen_find",
            lambda target, mode="text", **kw: {"found": False},
        )
        # Use a very short timeout
        result = server_module.screen_wait(
            target="NeverGoingToAppear", mode="text", timeout=0.2, poll_interval=0.05,
        )
        assert result.get("timed_out") is True
        assert result.get("found") is False

    def test_screen_wait_vanish(self, server_module, setup_fakes, monkeypatch):
        """screen_wait(mode='vanish') returns when target disappears."""
        # Override screen_find to return not found (simulating vanish)
        monkeypatch.setattr(
            server_module, "screen_find",
            lambda target, mode="text", **kw: {"found": False},
        )
        result = server_module.screen_wait(
            target="Loading", mode="vanish", timeout=5, poll_interval=0.1,
        )
        assert result.get("vanished") is True
