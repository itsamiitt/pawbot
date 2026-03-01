#!/usr/bin/env python3
"""Desktop App Control MCP Server.

Registered as: mcp_servers.app_control in ~/.pawbot/config.json
App registry: ~/.pawbot/app_registry.json
Templates: ~/.pawbot/templates/
Dependencies: pyautogui>=0.9.54, pillow>=10.0.0, pytesseract>=0.3.10,
              pynput>=1.7.6, pyperclip>=1.8.2
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import platform
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# ── Optional Dependencies ─────────────────────────────────────────────────────

try:
    import pyautogui

    pyautogui.FAILSAFE = True  # Moving mouse to corner (0,0) raises exception
    pyautogui.PAUSE = 0.05     # Small default pause between pyautogui actions
    _PYAUTOGUI_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    pyautogui = None  # type: ignore[assignment]
    _PYAUTOGUI_AVAILABLE = False

try:
    from PIL import Image
    _PILLOW_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    Image = None  # type: ignore[assignment, misc]
    _PILLOW_AVAILABLE = False

try:
    import pytesseract
    _TESSERACT_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    pytesseract = None  # type: ignore[assignment]
    _TESSERACT_AVAILABLE = False

try:
    import pyperclip
    _PYPERCLIP_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    pyperclip = None  # type: ignore[assignment]
    _PYPERCLIP_AVAILABLE = False


# ── Logging ───────────────────────────────────────────────────────────────────


def _configure_logger() -> logging.Logger:
    log_path = Path.home() / ".pawbot" / "logs" / "pawbot.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _logger = logging.getLogger("pawbot.mcp.app_control")
    if not _logger.handlers:
        _logger.setLevel(logging.INFO)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        _logger.addHandler(handler)
        _logger.propagate = False
    return _logger


logger = _configure_logger()
mcp = FastMCP(name="app_control")

# ── Paths ─────────────────────────────────────────────────────────────────────

APP_REGISTRY_PATH = os.path.expanduser("~/.pawbot/app_registry.json")
TEMPLATES_DIR = os.path.expanduser("~/.pawbot/templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(os.path.dirname(APP_REGISTRY_PATH), exist_ok=True)

# ── Tool Registry ─────────────────────────────────────────────────────────────

TOOL_NAMES = [
    "app_launch",
    "app_register",
    "app_focus",
    "app_close",
    "screen_read",
    "screen_find",
    "screen_wait",
    "app_click",
    "app_type",
    "app_key",
    "app_scroll",
    "app_drag",
    "clipboard_read",
    "clipboard_write",
    "clipboard_paste",
    "template_save",
    "template_list",
]

# ── Platform Detection ────────────────────────────────────────────────────────

PLATFORM = platform.system().lower()  # "linux", "darwin", "windows"


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def _truncate(text: str | None, limit: int) -> str:
    return (text or "")[:limit]


def _require_pyautogui() -> dict[str, Any] | None:
    """Return an error dict if pyautogui is not installed, else None."""
    if not _PYAUTOGUI_AVAILABLE:
        return {"error": "pyautogui is not installed — run: pip install pyautogui"}
    return None


def _require_tesseract() -> dict[str, Any] | None:
    """Return an error dict if pytesseract or pillow is missing."""
    if not _TESSERACT_AVAILABLE:
        return {"error": "pytesseract is not installed — run: pip install pytesseract"}
    if not _PILLOW_AVAILABLE:
        return {"error": "pillow is not installed — run: pip install pillow"}
    return None


def _require_pyperclip() -> dict[str, Any] | None:
    """Return an error dict if pyperclip is missing."""
    if not _PYPERCLIP_AVAILABLE:
        return {"error": "pyperclip is not installed — run: pip install pyperclip"}
    return None


# ── App Registry ──────────────────────────────────────────────────────────────


# Default apps, platform-aware
if PLATFORM == "windows":
    _DEFAULT_APPS = {
        "chrome":   {"cmd": "start chrome",   "window_title": "Google Chrome"},
        "firefox":  {"cmd": "start firefox",  "window_title": "Mozilla Firefox"},
        "vscode":   {"cmd": "code",           "window_title": "Visual Studio Code"},
        "terminal": {"cmd": "cmd",            "window_title": "Command Prompt"},
        "explorer": {"cmd": "explorer",       "window_title": "File Explorer"},
        "notepad":  {"cmd": "notepad",        "window_title": "Notepad"},
    }
elif PLATFORM == "darwin":
    _DEFAULT_APPS = {
        "chrome":   {"cmd": "open -a 'Google Chrome'", "window_title": "Google Chrome"},
        "firefox":  {"cmd": "open -a Firefox",         "window_title": "Mozilla Firefox"},
        "vscode":   {"cmd": "code",                    "window_title": "Visual Studio Code"},
        "terminal": {"cmd": "open -a Terminal",        "window_title": "Terminal"},
        "finder":   {"cmd": "open -a Finder",          "window_title": "Finder"},
    }
else:  # Linux
    _DEFAULT_APPS = {
        "chrome":   {"cmd": "google-chrome",  "window_title": "Google Chrome"},
        "firefox":  {"cmd": "firefox",        "window_title": "Mozilla Firefox"},
        "vscode":   {"cmd": "code",           "window_title": "Visual Studio Code"},
        "terminal": {"cmd": "gnome-terminal", "window_title": "Terminal"},
        "files":    {"cmd": "nautilus",        "window_title": "Files"},
    }


def _load_registry() -> dict[str, Any]:
    """Load app registry, create with defaults if missing."""
    if not os.path.exists(APP_REGISTRY_PATH):
        with open(APP_REGISTRY_PATH, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT_APPS, f, indent=2)
        return dict(_DEFAULT_APPS)
    try:
        with open(APP_REGISTRY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else dict(_DEFAULT_APPS)
    except Exception:
        return dict(_DEFAULT_APPS)


def _save_registry(registry: dict[str, Any]) -> None:
    """Persist the app registry to disk."""
    os.makedirs(os.path.dirname(APP_REGISTRY_PATH) or ".", exist_ok=True)
    with open(APP_REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)


# ── Screenshot Helper ─────────────────────────────────────────────────────────


def _take_screenshot(region: tuple | None = None) -> str:
    """Take screenshot. Returns base64-encoded PNG string.

    region: (left, top, width, height) or None for full screen.
    """
    if not _PYAUTOGUI_AVAILABLE:
        return ""
    screenshot = pyautogui.screenshot(region=region)
    buffer = io.BytesIO()
    screenshot.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return b64


# ── Window Management Helpers ─────────────────────────────────────────────────


def _focus_window(window_title: str) -> dict[str, Any]:
    """Bring a window to the foreground by title substring."""
    try:
        if PLATFORM == "linux":
            result = subprocess.run(
                ["wmctrl", "-a", window_title],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return {"error": f"wmctrl failed: {result.stderr.strip()}"}
        elif PLATFORM == "darwin":
            script = f'tell application "{window_title}" to activate'
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return {"error": f"osascript failed: {result.stderr.strip()}"}
        elif PLATFORM == "windows":
            # Use PowerShell to bring window to front
            ps_script = (
                f"$wnd = Get-Process | Where-Object {{$_.MainWindowTitle -like '*{window_title}*'}} "
                f"| Select-Object -First 1; "
                f"if ($wnd) {{ "
                f"  Add-Type -Name Win32 -Namespace Util -MemberDefinition '"
                f"    [DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr hWnd);';"
                f"  [Util.Win32]::SetForegroundWindow($wnd.MainWindowHandle) "
                f"}} else {{ Write-Error 'Window not found' }}"
            )
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return {"error": f"Could not focus window: {result.stderr.strip()}"}
        else:
            return {"error": f"Unsupported platform: {PLATFORM}"}

        return {"focused": True}
    except FileNotFoundError as exc:
        return {"error": f"Required tool not found: {exc}"}
    except subprocess.TimeoutExpired:
        return {"error": "Window focus timed out"}
    except Exception as exc:
        return {"error": str(exc)}


def _close_window(window_title: str, force: bool = False) -> dict[str, Any]:
    """Close a window by title."""
    try:
        if PLATFORM == "linux":
            if force:
                result = subprocess.run(
                    ["pkill", "-f", window_title],
                    capture_output=True, text=True, timeout=5,
                )
            else:
                result = subprocess.run(
                    ["wmctrl", "-c", window_title],
                    capture_output=True, text=True, timeout=5,
                )
            return {"closed": result.returncode == 0, "force": force}
        elif PLATFORM == "darwin":
            verb = "quit" if not force else "quit"
            script = f'tell application "{window_title}" to {verb}'
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=5,
            )
            return {"closed": result.returncode == 0, "force": force}
        elif PLATFORM == "windows":
            if force:
                result = subprocess.run(
                    ["taskkill", "/F", "/FI", f"WINDOWTITLE eq *{window_title}*"],
                    capture_output=True, text=True, timeout=10,
                )
            else:
                result = subprocess.run(
                    ["taskkill", "/FI", f"WINDOWTITLE eq *{window_title}*"],
                    capture_output=True, text=True, timeout=10,
                )
            return {"closed": result.returncode == 0, "force": force}
        else:
            return {"error": f"Unsupported platform: {PLATFORM}"}
    except Exception as exc:
        return {"error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL IMPLEMENTATIONS
# ══════════════════════════════════════════════════════════════════════════════


def list_tools() -> dict[str, Any]:
    """Return explicit tool inventory for tests and diagnostics."""
    return {"tools": TOOL_NAMES.copy(), "count": len(TOOL_NAMES)}


# ── Feature 9.1 — App Launch and Window Management ───────────────────────────


@mcp.tool()
def app_launch(
    app_name: str,
    args: list[str] | None = None,
    wait_seconds: float = 2.0,
) -> dict[str, Any]:
    """Launch a desktop application."""
    missing = _require_pyautogui()
    if missing:
        return missing

    args = args or []
    registry = _load_registry()

    # Look up in registry; if not found, treat as raw command
    if app_name in registry:
        cmd = registry[app_name]["cmd"]
    else:
        cmd = app_name

    try:
        if PLATFORM == "windows" and cmd.startswith("start "):
            # On Windows, 'start' is a shell built-in
            full_cmd = f"{cmd} {' '.join(args)}" if args else cmd
            subprocess.Popen(full_cmd, shell=True)
        else:
            cmd_parts = cmd.split() + args
            subprocess.Popen(
                cmd_parts,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        time.sleep(wait_seconds)
        screenshot_b64 = _take_screenshot()
        logger.info("App launched: %s (cmd=%s)", app_name, cmd)
        return {
            "launched": True,
            "app": app_name,
            "cmd": cmd,
            "screenshot_b64": screenshot_b64,
        }
    except FileNotFoundError:
        return {"error": f"Application not found: {cmd}"}
    except Exception as exc:
        logger.error("App launch failed: %s — %s", app_name, exc)
        return {"error": str(exc), "app": app_name}


@mcp.tool()
def app_register(
    name: str,
    cmd: str,
    window_title: str,
) -> dict[str, Any]:
    """Add or update an app in the registry."""
    registry = _load_registry()
    registry[name] = {"cmd": cmd, "window_title": window_title}
    _save_registry(registry)
    logger.info("App registered: %s → %s", name, cmd)
    return {"registered": True, "name": name, "cmd": cmd, "window_title": window_title}


@mcp.tool()
def app_focus(
    window_title: str,
) -> dict[str, Any]:
    """Focus window by title substring. Returns screenshot after focus."""
    missing = _require_pyautogui()
    if missing:
        return missing

    result = _focus_window(window_title)
    if "error" in result:
        return result

    time.sleep(0.5)
    screenshot_b64 = _take_screenshot()
    logger.info("App focused: %s", window_title)
    return {
        "focused": True,
        "window_title": window_title,
        "screenshot_b64": screenshot_b64,
    }


@mcp.tool()
def app_close(
    window_title: str,
    force: bool = False,
) -> dict[str, Any]:
    """Close application window gracefully or by force."""
    result = _close_window(window_title, force=force)
    if result.get("closed"):
        logger.info("App closed: %s (force=%s)", window_title, force)
    else:
        logger.warning("App close failed: %s — %s", window_title, result.get("error", "unknown"))
    return result


# ── Feature 9.2 — Screen Reading and Vision ──────────────────────────────────


@mcp.tool()
def screen_read(
    region: dict | None = None,
    language: str = "eng",
) -> dict[str, Any]:
    """Read all text on screen using OCR. Returns structured text and screenshot."""
    gui_check = _require_pyautogui()
    if gui_check:
        return gui_check
    ocr_check = _require_tesseract()
    if ocr_check:
        return ocr_check

    try:
        region_tuple = None
        if region:
            region_tuple = (region["left"], region["top"], region["width"], region["height"])

        screenshot = pyautogui.screenshot(region=region_tuple)
        text = pytesseract.image_to_string(screenshot, lang=language)

        # Build word-location list for precise targeting
        words: list[dict[str, Any]] = []
        try:
            data = pytesseract.image_to_data(screenshot, output_type=pytesseract.Output.DICT)
            words = [
                {
                    "text": data["text"][i],
                    "x": data["left"][i],
                    "y": data["top"][i],
                    "w": data["width"][i],
                    "h": data["height"][i],
                    "conf": data["conf"][i],
                }
                for i in range(len(data["text"]))
                if data["conf"][i] > 50 and data["text"][i].strip()
            ]
        except Exception:
            pass  # word positions are best-effort

        screenshot_b64 = _take_screenshot(region_tuple)
        logger.info("Screen read: %d chars, %d words detected", len(text), len(words))
        return {
            "full_text": text.strip(),
            "words": words,
            "screenshot_b64": screenshot_b64,
        }
    except Exception as exc:
        logger.error("Screen read failed: %s", exc)
        return {"error": str(exc)}


@mcp.tool()
def screen_find(
    target: str,
    mode: str = "text",
    confidence: float = 0.8,
) -> dict[str, Any]:
    """Find UI element location on screen.

    mode: "text" (OCR), "template" (image matching), "vision" (LLM)
    Returns {found, x, y, screenshot_b64}.
    """
    gui_check = _require_pyautogui()
    if gui_check:
        return gui_check

    try:
        if mode == "text":
            # Run OCR and find target text
            ocr_check = _require_tesseract()
            if ocr_check:
                return ocr_check

            screenshot = pyautogui.screenshot()
            data = pytesseract.image_to_data(screenshot, output_type=pytesseract.Output.DICT)

            best_match = None
            target_lower = target.lower()
            for i in range(len(data["text"])):
                word = data["text"][i].strip()
                if not word or data["conf"][i] < 50:
                    continue
                if target_lower in word.lower():
                    x_center = data["left"][i] + data["width"][i] // 2
                    y_center = data["top"][i] + data["height"][i] // 2
                    best_match = {
                        "found": True,
                        "x": x_center,
                        "y": y_center,
                        "text": word,
                        "confidence": data["conf"][i],
                    }
                    break

            if best_match:
                best_match["screenshot_b64"] = _take_screenshot()
                return best_match
            return {"found": False, "target": target, "mode": mode}

        elif mode == "template":
            # Use pyautogui.locateOnScreen with template image
            template_path = target
            if not os.path.isabs(target):
                template_path = os.path.join(TEMPLATES_DIR, f"{target}.png")

            if not os.path.exists(template_path):
                return {"error": f"Template image not found: {template_path}"}

            location = pyautogui.locateOnScreen(template_path, confidence=confidence)
            if location:
                center = pyautogui.center(location)
                return {
                    "found": True,
                    "x": center.x,
                    "y": center.y,
                    "width": location.width,
                    "height": location.height,
                    "screenshot_b64": _take_screenshot(),
                }
            return {"found": False, "target": target, "mode": mode}

        elif mode == "vision":
            # Vision mode — take screenshot and return for external LLM analysis
            # (In production, this would call ModelRouter with task_type="vision")
            screenshot_b64 = _take_screenshot()
            return {
                "found": False,
                "requires_vision_model": True,
                "target": target,
                "screenshot_b64": screenshot_b64,
                "hint": "Send this screenshot to a vision model with the prompt: "
                        f"'Find the UI element: {target}' to get coordinates.",
            }

        else:
            return {"error": f"Unknown mode: {mode}. Use 'text', 'template', or 'vision'."}

    except Exception as exc:
        logger.error("Screen find failed: %s — %s", target, exc)
        return {"error": str(exc), "target": target}


@mcp.tool()
def screen_wait(
    target: str,
    mode: str = "text",
    timeout: int = 30,
    poll_interval: float = 1.0,
) -> dict[str, Any]:
    """Wait until target appears (or vanishes) on screen.

    mode: "text" (OCR), "template" (image matching), "vanish" (wait until gone)
    """
    gui_check = _require_pyautogui()
    if gui_check:
        return gui_check

    deadline = time.time() + timeout

    try:
        while time.time() < deadline:
            if mode == "vanish":
                result = screen_find(target, mode="text")
                if not result.get("found", False):
                    logger.info("Screen wait: target vanished '%s'", target)
                    return {
                        "found": False,
                        "vanished": True,
                        "target": target,
                        "screenshot_b64": _take_screenshot(),
                    }
            else:
                result = screen_find(target, mode=mode)
                if result.get("found", False):
                    logger.info("Screen wait: target found '%s'", target)
                    return result

            time.sleep(poll_interval)

        logger.info("Screen wait: timed out for '%s' (%ds)", target, timeout)
        return {"found": False, "timed_out": True, "target": target, "timeout": timeout}

    except Exception as exc:
        return {"error": str(exc), "target": target}


# ── Feature 9.3 — Mouse and Keyboard Control ─────────────────────────────────


@mcp.tool()
def app_click(
    target: str,
    button: str = "left",
    clicks: int = 1,
    pre_delay_ms: int = 300,
) -> dict[str, Any]:
    """Click a UI element. Finds element first, then clicks its center."""
    gui_check = _require_pyautogui()
    if gui_check:
        return gui_check

    try:
        # Parse coordinates directly
        if target.startswith("x:") and "y:" in target:
            parts = target.replace("x:", "").replace("y:", "").split(",")
            x, y = int(parts[0]), int(parts[1])
        else:
            # Try OCR text match first
            result = screen_find(target, mode="text")
            if result.get("found"):
                x, y = result["x"], result["y"]
            else:
                # Try template match
                result = screen_find(target, mode="template")
                if result.get("found"):
                    x, y = result["x"], result["y"]
                else:
                    return {
                        "clicked": False,
                        "error": f"Could not find target: {target}",
                        "target": target,
                    }

        # Human-like delay before clicking
        time.sleep(pre_delay_ms / 1000 + random.uniform(0, 0.1))
        pyautogui.click(x, y, clicks=clicks, button=button)
        time.sleep(random.uniform(0.1, 0.3))  # post-click pause

        screenshot_b64 = _take_screenshot()
        logger.info("App click: target=%s x=%d y=%d button=%s clicks=%d", target, x, y, button, clicks)
        return {
            "clicked": True,
            "x": x,
            "y": y,
            "button": button,
            "clicks": clicks,
            "screenshot_b64": screenshot_b64,
        }

    except Exception as exc:
        logger.error("App click failed: %s — %s", target, exc)
        return {"error": str(exc), "target": target}


@mcp.tool()
def app_type(
    text: str,
    clear_first: bool = False,
    interval_ms: float = 40,
    press_enter: bool = False,
) -> dict[str, Any]:
    """Type text into the currently focused element with human-like timing."""
    gui_check = _require_pyautogui()
    if gui_check:
        return gui_check

    try:
        if clear_first:
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.05)
            pyautogui.press("delete")
            time.sleep(0.05)

        # Type each character with human-like interval
        for char in text:
            delay = (interval_ms / 1000) * random.uniform(0.7, 1.3)
            pyautogui.typewrite(char, interval=delay)

        if press_enter:
            time.sleep(0.1)
            pyautogui.press("enter")

        logger.info("App typed: %d chars (clear=%s, enter=%s)", len(text), clear_first, press_enter)
        return {"typed": True, "chars": len(text), "press_enter": press_enter}

    except Exception as exc:
        logger.error("App type failed: %s", exc)
        return {"error": str(exc)}


@mcp.tool()
def app_key(
    key: str,
) -> dict[str, Any]:
    """Press a single key or hotkey combination (e.g. 'ctrl+c', 'enter')."""
    gui_check = _require_pyautogui()
    if gui_check:
        return gui_check

    try:
        if "+" in key:
            keys = [k.strip() for k in key.lower().split("+")]
            pyautogui.hotkey(*keys)
            logger.info("App hotkey: %s", key)
        else:
            pyautogui.press(key.lower())
            logger.info("App key: %s", key)

        return {"pressed": True, "key": key}

    except Exception as exc:
        logger.error("App key failed: %s — %s", key, exc)
        return {"error": str(exc), "key": key}


@mcp.tool()
def app_scroll(
    direction: str = "down",
    amount: int = 3,
    x: int | None = None,
    y: int | None = None,
) -> dict[str, Any]:
    """Scroll in any direction."""
    gui_check = _require_pyautogui()
    if gui_check:
        return gui_check

    try:
        # Move to target position if specified
        if x is not None and y is not None:
            pyautogui.moveTo(x, y)

        if direction in ("up", "down"):
            scroll_amount = amount if direction == "up" else -amount
            pyautogui.scroll(scroll_amount)
        elif direction in ("left", "right"):
            scroll_amount = -amount if direction == "left" else amount
            pyautogui.hscroll(scroll_amount)
        else:
            return {"error": f"Unknown direction: {direction}. Use 'up', 'down', 'left', 'right'."}

        logger.info("App scroll: %s amount=%d", direction, amount)
        return {"scrolled": True, "direction": direction, "amount": amount}

    except Exception as exc:
        logger.error("App scroll failed: %s", exc)
        return {"error": str(exc)}


@mcp.tool()
def app_drag(
    from_target: str,
    to_target: str,
    duration: float = 0.5,
) -> dict[str, Any]:
    """Drag from one element to another."""
    gui_check = _require_pyautogui()
    if gui_check:
        return gui_check

    try:
        # Resolve source coordinates
        if from_target.startswith("x:") and "y:" in from_target:
            parts = from_target.replace("x:", "").replace("y:", "").split(",")
            from_x, from_y = int(parts[0]), int(parts[1])
        else:
            result = screen_find(from_target, mode="text")
            if not result.get("found"):
                return {"error": f"Could not find source: {from_target}"}
            from_x, from_y = result["x"], result["y"]

        # Resolve destination coordinates
        if to_target.startswith("x:") and "y:" in to_target:
            parts = to_target.replace("x:", "").replace("y:", "").split(",")
            to_x, to_y = int(parts[0]), int(parts[1])
        else:
            result = screen_find(to_target, mode="text")
            if not result.get("found"):
                return {"error": f"Could not find destination: {to_target}"}
            to_x, to_y = result["x"], result["y"]

        pyautogui.moveTo(from_x, from_y)
        time.sleep(0.2)
        pyautogui.drag(to_x - from_x, to_y - from_y, duration=duration)

        screenshot_b64 = _take_screenshot()
        logger.info("App drag: (%d,%d) → (%d,%d)", from_x, from_y, to_x, to_y)
        return {
            "dragged": True,
            "from": {"x": from_x, "y": from_y},
            "to": {"x": to_x, "y": to_y},
            "screenshot_b64": screenshot_b64,
        }

    except Exception as exc:
        logger.error("App drag failed: %s", exc)
        return {"error": str(exc)}


# ── Feature 9.4 — Clipboard Management ───────────────────────────────────────


@mcp.tool()
def clipboard_read() -> dict[str, Any]:
    """Read current clipboard content."""
    check = _require_pyperclip()
    if check:
        return check

    try:
        content = pyperclip.paste()
        return {"content": content, "length": len(content)}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def clipboard_write(
    text: str,
) -> dict[str, Any]:
    """Write text to clipboard."""
    check = _require_pyperclip()
    if check:
        return check

    try:
        pyperclip.copy(text)
        logger.info("Clipboard write: %d chars", len(text))
        return {"written": True, "length": len(text)}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def clipboard_paste(
    text: str,
) -> dict[str, Any]:
    """Write text to clipboard and paste it at current cursor position."""
    clip_check = _require_pyperclip()
    if clip_check:
        return clip_check
    gui_check = _require_pyautogui()
    if gui_check:
        return gui_check

    try:
        pyperclip.copy(text)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        logger.info("Clipboard paste: %d chars", len(text))
        return {"pasted": True, "length": len(text)}
    except Exception as exc:
        return {"error": str(exc)}


# ── Feature 9.5 — Screen Templates ───────────────────────────────────────────


@mcp.tool()
def template_save(
    name: str,
    region: dict,
) -> dict[str, Any]:
    """Save a screen region as a named template for future matching."""
    gui_check = _require_pyautogui()
    if gui_check:
        return gui_check

    try:
        template_path = os.path.join(TEMPLATES_DIR, f"{name}.png")
        region_tuple = (region["left"], region["top"], region["width"], region["height"])
        screenshot = pyautogui.screenshot(region=region_tuple)
        screenshot.save(template_path)
        logger.info("Template saved: %s → %s", name, template_path)
        return {"saved": True, "name": name, "path": template_path}
    except Exception as exc:
        logger.error("Template save failed: %s — %s", name, exc)
        return {"error": str(exc)}


@mcp.tool()
def template_list() -> dict[str, Any]:
    """List all saved screen templates."""
    templates: list[dict[str, str]] = []
    if os.path.exists(TEMPLATES_DIR):
        for f in sorted(os.listdir(TEMPLATES_DIR)):
            if f.endswith(".png"):
                templates.append({
                    "name": f[:-4],
                    "path": os.path.join(TEMPLATES_DIR, f),
                })
    return {"templates": templates, "count": len(templates)}


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════


def main() -> None:
    """Run the MCP server over stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
