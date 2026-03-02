---
name: screen-control
description: "Control the desktop GUI: read screen text (OCR), find UI elements, click, type, scroll, drag, manage clipboard, launch/focus/close apps. Use when the user asks to interact with desktop applications, automate GUI tasks, take screenshots, read on-screen text, or control mouse/keyboard."
metadata: {"pawbot":{"emoji":"🖥️","requires":{"bins":[]}}}
---

# Screen Control

Use the `app_control` MCP server tools to interact with the desktop GUI. These tools require `pyautogui`, `pytesseract`, and `pillow` to be installed (`pip install pawbot-ai[desktop]`).

## Quick Reference

| Tool | Purpose |
|------|---------|
| `screen_read` | OCR — read all text on screen (with word positions) |
| `screen_find` | Find a UI element by text, template image, or vision |
| `screen_wait` | Poll until an element appears or vanishes |
| `app_click` | Click on a UI element (by text or coordinates) |
| `app_type` | Type text into the focused element |
| `app_key` | Press a key or hotkey (e.g. `ctrl+c`, `enter`) |
| `app_scroll` | Scroll up/down/left/right |
| `app_drag` | Drag from one element to another |
| `app_launch` | Launch a desktop application |
| `app_focus` | Bring a window to the foreground |
| `app_close` | Close an application window |
| `clipboard_read` | Read clipboard contents |
| `clipboard_write` | Write text to clipboard |
| `clipboard_paste` | Write to clipboard then Ctrl+V |
| `template_save` | Save a screen region as a reusable template |
| `template_list` | List saved templates |

## Workflow: Read and Interact

1. **Read the screen** to understand what's visible:
```
mcp_app_control_screen_read()
```

2. **Find a specific element** by its text:
```
mcp_app_control_screen_find(target="Submit", mode="text")
```

3. **Click it**:
```
mcp_app_control_app_click(target="Submit")
```

4. **Type into a field** (click field first, then type):
```
mcp_app_control_app_click(target="Username")
mcp_app_control_app_type(text="admin", press_enter=false)
```

## Click Targets

- **By text**: `app_click(target="Save")` — finds via OCR then clicks center
- **By coordinates**: `app_click(target="x:450,y:300")` — clicks exact position
- **By template**: `screen_find(target="save_button", mode="template")` — matches saved image

## Hotkeys

```
mcp_app_control_app_key(key="ctrl+s")      # Save
mcp_app_control_app_key(key="ctrl+a")      # Select all
mcp_app_control_app_key(key="alt+tab")     # Switch window
mcp_app_control_app_key(key="enter")       # Press Enter
```

## App Management

```
mcp_app_control_app_launch(app_name="chrome")
mcp_app_control_app_focus(window_title="Visual Studio Code")
mcp_app_control_app_close(window_title="Notepad", force=false)
```

## Wait for UI Changes

```
mcp_app_control_screen_wait(target="Loading", mode="vanish", timeout=30)
mcp_app_control_screen_wait(target="Success", mode="text", timeout=10)
```

## Tips

- Always `screen_read()` first to understand the current screen state
- Use `screen_find(mode="vision")` when OCR fails — returns screenshot for vision model analysis
- Pre-registered apps: chrome, firefox, vscode, terminal, notepad (Windows), explorer
- Register custom apps: `app_register(name="gimp", cmd="gimp", window_title="GIMP")`
- Use `clipboard_paste` for long text instead of `app_type` (faster, more reliable)
