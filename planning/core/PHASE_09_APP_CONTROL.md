# PHASE 9 — DESKTOP APP CONTROL MCP SERVER
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)
> **Implementation Day:** Day 24
> **Primary File:** `~/.nanobot/mcp-servers/app_control/server.py` (NEW)
> **Test File:** `~/nanobot/tests/test_app_control.py`
> **Depends on:** Phase 4 (ModelRouter — vision model routing), Phase 8 (browser/server.py — screenshot patterns)

---

## BEFORE YOU START — READ THESE FILES

```bash
cat ~/.nanobot/mcp-servers/browser/server.py   # understand screenshot + vision patterns from Phase 8
cat ~/.nanobot/config.json                     # see current mcp_servers registration format
cat ~/nanobot/pyproject.toml                   # know current dependencies
```

This MCP server lives at `~/.nanobot/mcp-servers/app_control/server.py` — NOT inside `~/nanobot/`.
Register it in `~/.nanobot/config.json` under `mcp_servers.app_control`.

---

## WHAT YOU ARE BUILDING

A standalone MCP server giving the AI agent full control over desktop GUI applications — launching apps, clicking UI elements identified by image or accessibility tree, typing, reading screen text via OCR, and managing the clipboard. Operates in two modes:

- **Accessibility mode (default):** Uses OS accessibility APIs (AT-SPI on Linux, Accessibility API on macOS) — zero vision cost
- **Vision mode (fallback):** Takes screenshot → sends to vision model → gets coordinates → acts

---

## ENVIRONMENT SETUP

```bash
mkdir -p ~/.nanobot/mcp-servers/app_control
mkdir -p ~/.nanobot/templates
pip install "pyautogui>=0.9.54" "pillow>=10.0.0" "pytesseract>=0.3.10" \
            "pynput>=1.7.6" "pyperclip>=1.8.2"

# Add to pyproject.toml [project.dependencies]:
# "pyautogui>=0.9.54", "pillow>=10.0.0", "pytesseract>=0.3.10",
# "pynput>=1.7.6", "pyperclip>=1.8.2"

# Verify
python -c "import pyautogui; print('pyautogui OK')"
python -c "import pytesseract; print('pytesseract OK')"
python -c "from PIL import Image; print('pillow OK')"
```

---

## FEATURE 9.1 — APP LAUNCH AND WINDOW MANAGEMENT

### `app_launch` tool

Launches a named application using the app registry at `~/.nanobot/app_registry.json`.

```python
APP_REGISTRY_PATH = os.path.expanduser("~/.nanobot/app_registry.json")

def _load_registry() -> dict:
    """Load app registry, create with defaults if missing."""
    defaults = {
        "chrome":   {"cmd": "google-chrome", "window_title": "Google Chrome"},
        "firefox":  {"cmd": "firefox",        "window_title": "Mozilla Firefox"},
        "vscode":   {"cmd": "code",            "window_title": "Visual Studio Code"},
        "terminal": {"cmd": "gnome-terminal",  "window_title": "Terminal"},
        "files":    {"cmd": "nautilus",        "window_title": "Files"},
    }
    if not os.path.exists(APP_REGISTRY_PATH):
        with open(APP_REGISTRY_PATH, "w") as f:
            json.dump(defaults, f, indent=2)
        return defaults
    with open(APP_REGISTRY_PATH) as f:
        return json.load(f)
```

Tool signature:
```python
@mcp.tool()
def app_launch(
    app_name: str,          # key in app_registry.json, or raw command string
    args: list[str] = [],   # extra CLI arguments
    wait_seconds: float = 2.0,  # wait after launch before returning
) -> dict:
    """Launch a desktop application."""
```

Implementation:
1. Look up `app_name` in registry. If not found, treat as raw command.
2. Use `subprocess.Popen([cmd] + args)` — non-blocking.
3. Sleep `wait_seconds`.
4. Take a screenshot via `_take_screenshot()`.
5. Return `{"launched": True, "app": app_name, "screenshot_b64": "..."}`.
6. Log: `logger.info(f"App launched: {app_name}")`

### `app_register` tool

```python
@mcp.tool()
def app_register(
    name: str,          # registry key
    cmd: str,           # shell command to launch
    window_title: str,  # substring of window title for focus detection
) -> dict:
    """Add or update an app in the registry."""
```

### `app_focus` tool

Bring a window to foreground by title substring.

```python
@mcp.tool()
def app_focus(window_title: str) -> dict:
    """Focus window by title substring. Returns screenshot after focus."""
```

On Linux use `subprocess.run(["wmctrl", "-a", window_title])`.
On macOS use `subprocess.run(["osascript", "-e", f'tell app "{window_title}" to activate'])`.
Always take a screenshot and return it.

### `app_close` tool

```python
@mcp.tool()
def app_close(
    window_title: str,           # close by window title
    force: bool = False,         # if True, SIGKILL instead of graceful close
) -> dict:
    """Close application window gracefully or by force."""
```

---

## FEATURE 9.2 — SCREEN READING AND VISION

### `_take_screenshot()` internal helper

```python
import pyautogui
from PIL import Image
import base64, io

def _take_screenshot(region: tuple = None) -> str:
    """
    Takes screenshot. Returns base64-encoded PNG string.
    region: (left, top, width, height) or None for full screen.
    """
    screenshot = pyautogui.screenshot(region=region)
    buffer = io.BytesIO()
    screenshot.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return b64
```

### `screen_read` tool

Returns the text content of the current screen using OCR.

```python
@mcp.tool()
def screen_read(
    region: dict = None,      # {"left": int, "top": int, "width": int, "height": int}
    language: str = "eng",    # tesseract language code
) -> dict:
    """Read all text on screen using OCR. Returns structured text and screenshot."""
```

Implementation:
```python
import pytesseract

region_tuple = None
if region:
    region_tuple = (region["left"], region["top"], region["width"], region["height"])

screenshot = pyautogui.screenshot(region=region_tuple)
text = pytesseract.image_to_string(screenshot, lang=language)
data = pytesseract.image_to_data(screenshot, output_type=pytesseract.Output.DICT)

# Build word-location list for precise targeting
words = [
    {"text": data["text"][i], "x": data["left"][i], "y": data["top"][i],
     "w": data["width"][i], "h": data["height"][i], "conf": data["conf"][i]}
    for i in range(len(data["text"]))
    if data["conf"][i] > 50 and data["text"][i].strip()
]

return {
    "full_text": text.strip(),
    "words": words,
    "screenshot_b64": _take_screenshot(region_tuple),
}
```

### `screen_find` tool

Locate a UI element on screen by template image or text label.

```python
@mcp.tool()
def screen_find(
    target: str,              # text string or path to template image
    mode: str = "text",       # "text" (OCR) | "template" (image matching) | "vision" (LLM)
    confidence: float = 0.8,  # minimum match confidence for template mode
) -> dict:
    """Find UI element location on screen. Returns {found, x, y, screenshot_b64}."""
```

**text mode:** Run OCR, search word list for `target`, return center coordinates of best match.

**template mode:** Use `pyautogui.locateOnScreen(template_path, confidence=confidence)`. Template images stored in `~/.nanobot/templates/`.

**vision mode:** Take screenshot → call vision model via Phase 4 ModelRouter with prompt: `"In this screenshot, find the UI element described as: '{target}'. Return JSON: {found: bool, x: int, y: int, description: str}"` → parse JSON response.

### `screen_wait` tool

```python
@mcp.tool()
def screen_wait(
    target: str,              # text or element to wait for
    mode: str = "text",       # "text" | "template" | "vanish"
    timeout: int = 30,        # seconds
    poll_interval: float = 1.0,
) -> dict:
    """Wait until target appears (or vanishes) on screen."""
```

Poll every `poll_interval` seconds using `screen_find`. Return as soon as found (or gone for "vanish"). Return `{"found": False, "timed_out": True}` on timeout — never raise.

---

## FEATURE 9.3 — MOUSE AND KEYBOARD CONTROL

### `app_click` tool

```python
@mcp.tool()
def app_click(
    target: str,                     # text label, "x:450,y:300", or template image path
    button: str = "left",            # "left" | "right" | "middle"
    clicks: int = 1,                 # 1 for single, 2 for double-click
    pre_delay_ms: int = 300,         # human-like pre-action delay
) -> dict:
    """Click a UI element. Finds element first, then clicks its center."""
```

Finding order:
1. If `target` starts with `"x:"` → parse coordinates directly
2. Try OCR text match via `screen_find(target, "text")`
3. Try template match via `screen_find(target, "template")`
4. Fall back to vision mode via `screen_find(target, "vision")`

After finding:
```python
import time, random
time.sleep(pre_delay_ms / 1000 + random.uniform(0, 0.1))
pyautogui.click(x, y, clicks=clicks, button=button)
time.sleep(random.uniform(0.1, 0.3))  # post-click pause
```

Return `{"clicked": True, "x": x, "y": y, "screenshot_b64": after_screenshot}`.

### `app_type` tool

```python
@mcp.tool()
def app_type(
    text: str,
    clear_first: bool = False,     # Ctrl+A then Delete before typing
    interval_ms: float = 40,       # base ms between keystrokes
    press_enter: bool = False,     # press Enter after typing
) -> dict:
    """Type text into the currently focused element with human-like timing."""
```

```python
if clear_first:
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.05)
    pyautogui.press("delete")
    time.sleep(0.05)

for char in text:
    pyautogui.typewrite(char, interval=interval_ms / 1000 * random.uniform(0.7, 1.3))

if press_enter:
    time.sleep(0.1)
    pyautogui.press("enter")
```

### `app_key` tool

```python
@mcp.tool()
def app_key(
    key: str,      # single key: "enter", "tab", "escape", "f5" etc.
                   # hotkey: "ctrl+c", "ctrl+z", "alt+f4", "ctrl+shift+p" etc.
) -> dict:
    """Press a single key or hotkey combination."""
```

```python
if "+" in key:
    keys = key.lower().split("+")
    pyautogui.hotkey(*keys)
else:
    pyautogui.press(key.lower())
```

### `app_scroll` tool

```python
@mcp.tool()
def app_scroll(
    direction: str = "down",  # "up" | "down" | "left" | "right"
    amount: int = 3,           # scroll units (clicks of scroll wheel)
    x: int = None,             # scroll at specific coordinates, or None for current position
    y: int = None,
) -> dict:
    """Scroll in any direction."""
```

### `app_drag` tool

```python
@mcp.tool()
def app_drag(
    from_target: str,   # source element (text, coords, or template)
    to_target: str,     # destination element
    duration: float = 0.5,
) -> dict:
    """Drag from one element to another."""
```

---

## FEATURE 9.4 — CLIPBOARD MANAGEMENT

### `clipboard_read` tool

```python
@mcp.tool()
def clipboard_read() -> dict:
    """Read current clipboard content."""
    import pyperclip
    content = pyperclip.paste()
    return {"content": content, "length": len(content)}
```

### `clipboard_write` tool

```python
@mcp.tool()
def clipboard_write(text: str) -> dict:
    """Write text to clipboard."""
    import pyperclip
    pyperclip.copy(text)
    return {"written": True, "length": len(text)}
```

### `clipboard_paste` tool

Writes to clipboard then pastes at current cursor location:

```python
@mcp.tool()
def clipboard_paste(text: str) -> dict:
    """Write text to clipboard and paste it at current cursor position."""
    import pyperclip
    pyperclip.copy(text)
    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "v")
    return {"pasted": True}
```

---

## FEATURE 9.5 — SCREEN RECORDING AND TEMPLATES

### `template_save` tool

Save current screen region as a named template for future `screen_find` use:

```python
@mcp.tool()
def template_save(
    name: str,        # template name (becomes ~/.nanobot/templates/{name}.png)
    region: dict,     # {"left": int, "top": int, "width": int, "height": int}
) -> dict:
    """Save a screen region as a named template for future matching."""
```

```python
template_path = os.path.expanduser(f"~/.nanobot/templates/{name}.png")
os.makedirs(os.path.dirname(template_path), exist_ok=True)
region_tuple = (region["left"], region["top"], region["width"], region["height"])
screenshot = pyautogui.screenshot(region=region_tuple)
screenshot.save(template_path)
return {"saved": True, "path": template_path}
```

### `template_list` tool

```python
@mcp.tool()
def template_list() -> dict:
    """List all saved screen templates."""
    template_dir = os.path.expanduser("~/.nanobot/templates")
    templates = []
    if os.path.exists(template_dir):
        for f in os.listdir(template_dir):
            if f.endswith(".png"):
                templates.append({"name": f[:-4], "path": os.path.join(template_dir, f)})
    return {"templates": templates, "count": len(templates)}
```

---

## MCP SERVER REGISTRATION

After implementing, add to `~/.nanobot/config.json`:

```json
{
  "mcp_servers": {
    "app_control": {
      "path": "~/.nanobot/mcp-servers/app_control/server.py",
      "requires_confirmation": true,
      "description": "Desktop app control — launch, click, type, read screen"
    }
  }
}
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_app_control.py`

```python
class TestAppRegistry:
    def test_registry_creates_defaults_if_missing()
    def test_app_register_saves_entry()
    def test_unknown_app_uses_raw_command()

class TestScreenRead:
    def test_screenshot_returns_base64()
    def test_ocr_extracts_text()
    def test_ocr_returns_word_locations()
    def test_region_screenshot_smaller_than_full()

class TestScreenFind:
    def test_find_by_text_returns_coordinates()
    def test_find_returns_not_found_gracefully()
    def test_template_find_loads_from_templates_dir()

class TestMouseKeyboard:
    def test_app_type_uses_interval()
    def test_app_key_single_key()
    def test_app_key_hotkey_combination()
    def test_app_click_coordinate_target()

class TestClipboard:
    def test_clipboard_write_and_read()
    def test_clipboard_paste_invokes_ctrl_v()

class TestTemplates:
    def test_template_save_creates_png()
    def test_template_list_returns_saved()
```

---

## CROSS-REFERENCES

- **Phase 4** (ModelRouter): `app_control` uses vision model for `screen_find(mode="vision")` — call `model_router.call(task_type="vision", ...)` 
- **Phase 8** (Browser): App control shares the `_take_screenshot()` pattern — keep implementations consistent
- **Phase 14** (Security): `app_launch` and `app_click` must pass through `ActionGate.check()` before executing
- **Phase 15** (Observability): Wrap every tool call with `@trace_tool` decorator once Phase 15 is complete

All canonical names are in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
