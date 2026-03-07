# Phase 15 — Chrome Extension: Core Foundation & Native Messaging Bridge

> **Goal:** Build a Manifest V3 Chrome extension that bridges PawBot's Python backend to the user's real Chrome browser via Native Messaging.  
> **Duration:** 14-21 days  
> **Risk Level:** High (new subsystem, security-critical)  
> **Depends On:** Phase 0 (CLI), Phase 6 (Security)  
> **Reference:** Claude Extension v1.0.57 architecture

---

## Why This Phase Exists

PawBot currently uses Playwright to control a **separate, isolated** Chromium instance. This phase adds a Chrome extension that controls the **user's actual Chrome browser** — with their logged-in sessions, tabs, cookies, and extensions. This is how Claude's browser extension works.

---

## Architecture Overview

```
PawBot Agent Loop
       ↓ (tool call)
Native Messaging Host (Python script registered with Chrome)
       ↓ (chrome.runtime.connectNative)
Chrome Extension Service Worker
       ↓ (chrome.scripting / chrome.tabs / CDP)
User's Real Chrome Browser (all tabs, sessions, cookies)
```

---

## 15.1 — Manifest & Extension Shell

**Create:** `pawbot-extension/manifest.json`

```json
{
  "manifest_version": 3,
  "name": "PawBot Browser Control",
  "version": "1.0.0",
  "description": "Let PawBot control your Chrome browser",
  "permissions": [
    "activeTab",
    "tabs",
    "tabGroups",
    "scripting",
    "debugger",
    "nativeMessaging",
    "storage",
    "sidePanel",
    "notifications",
    "webNavigation",
    "offscreen",
    "downloads"
  ],
  "host_permissions": ["<all_urls>"],
  "background": {
    "service_worker": "service-worker.js",
    "type": "module"
  },
  "content_scripts": [
    {
      "js": ["content-scripts/accessibility-tree.js"],
      "matches": ["<all_urls>"],
      "all_frames": true,
      "run_at": "document_start"
    },
    {
      "js": ["content-scripts/agent-indicator.js"],
      "matches": ["<all_urls>"],
      "all_frames": false,
      "run_at": "document_idle"
    }
  ],
  "action": {
    "default_title": "PawBot",
    "default_icon": { "128": "icons/icon-128.png" }
  },
  "side_panel": {
    "default_path": "sidepanel.html"
  },
  "icons": { "128": "icons/icon-128.png" },
  "minimum_chrome_version": "116"
}
```

---

## 15.2 — Native Messaging Host

The bridge between PawBot (Python) and Chrome. Chrome launches this script when the extension calls `chrome.runtime.connectNative()`.

**Create:** `pawbot/browser_bridge/native_host.py`

```python
"""Native Messaging Host — bridges PawBot ↔ Chrome Extension.

Chrome launches this script. Communication is via stdin/stdout
with length-prefixed JSON messages.

Protocol:
  Extension → Host: {type: "ping"} → Host → Extension: {type: "pong"}
  Host → Extension: {type: "tool_request", method, params}
  Extension → Host: {type: "tool_response", result | error}
"""

from __future__ import annotations

import json
import struct
import sys
import asyncio
import threading
from pathlib import Path
from typing import Any
from loguru import logger


class NativeMessagingHost:
    """Handles Chrome Native Messaging protocol (length-prefixed JSON on stdio)."""

    def __init__(self):
        self._pending: dict[str, asyncio.Future] = {}
        self._running = True
        self._tool_id_counter = 0

    def read_message(self) -> dict | None:
        """Read a single message from Chrome (stdin)."""
        raw_length = sys.stdin.buffer.read(4)
        if not raw_length or len(raw_length) < 4:
            return None
        length = struct.unpack("I", raw_length)[0]
        if length > 1024 * 1024:  # 1MB safety limit
            return None
        data = sys.stdin.buffer.read(length)
        return json.loads(data.decode("utf-8"))

    def send_message(self, msg: dict) -> None:
        """Send a message to Chrome (stdout)."""
        encoded = json.dumps(msg).encode("utf-8")
        sys.stdout.buffer.write(struct.pack("I", len(encoded)))
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()

    async def execute_tool(self, tool_name: str, args: dict) -> dict:
        """Send tool request to Chrome extension and wait for response."""
        self._tool_id_counter += 1
        request_id = f"tool_{self._tool_id_counter}"

        self.send_message({
            "type": "tool_request",
            "method": "execute_tool",
            "params": {"tool": tool_name, "args": args},
            "id": request_id,
        })

        future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            return {"error": f"Tool '{tool_name}' timed out after 30s"}

    def _handle_response(self, msg: dict) -> None:
        """Handle tool response from Chrome."""
        request_id = msg.get("id")
        if request_id and request_id in self._pending:
            future = self._pending.pop(request_id)
            if not future.done():
                if "error" in msg:
                    future.set_result({"error": msg["error"]})
                else:
                    future.set_result(msg.get("result", {}))

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
                except Exception as e:
                    logger.error("Native host reader error: {}", e)
                    break
            self._running = False

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()


def register_native_host() -> None:
    """Register native messaging host manifest with Chrome."""
    import platform
    host_name = "com.pawbot.browser_extension"
    python_path = sys.executable
    script_path = str(Path(__file__).resolve())

    manifest = {
        "name": host_name,
        "description": "PawBot Browser Control Bridge",
        "path": script_path if platform.system() != "Windows"
                else str(Path(__file__).parent / "native_host_wrapper.bat"),
        "type": "stdio",
        "allowed_origins": [
            f"chrome-extension://EXTENSION_ID_HERE/"
        ],
    }

    if platform.system() == "Windows":
        import winreg
        key_path = f"SOFTWARE\\Google\\Chrome\\NativeMessagingHosts\\{host_name}"
        manifest_path = Path.home() / ".pawbot" / "native-host-manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2))

        # Create wrapper .bat
        wrapper = Path(__file__).parent / "native_host_wrapper.bat"
        wrapper.write_text(f'@echo off\n"{python_path}" "{script_path}" %*\n')

        manifest["path"] = str(wrapper)
        manifest_path.write_text(json.dumps(manifest, indent=2))

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
        winreg.CloseKey(key)
    else:
        # macOS / Linux
        if platform.system() == "Darwin":
            nm_dir = Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts"
        else:
            nm_dir = Path.home() / ".config/google-chrome/NativeMessagingHosts"
        nm_dir.mkdir(parents=True, exist_ok=True)
        (nm_dir / f"{host_name}.json").write_text(json.dumps(manifest, indent=2))

    logger.info("Native messaging host registered: {}", host_name)
```

---

## 15.3 — Service Worker (Extension Brain)

**Create:** `pawbot-extension/service-worker.js`

```javascript
/**
 * PawBot Chrome Extension — Service Worker
 * Handles: native messaging, tab management, tool execution
 */

let nativePort = null;
let isConnected = false;

// ── Native Messaging ──────────────────────────────────────────────
async function connectToNativeHost() {
  try {
    nativePort = chrome.runtime.connectNative("com.pawbot.browser_extension");

    nativePort.onMessage.addListener(async (msg) => {
      if (msg.type === "tool_request") {
        const result = await executeToolRequest(msg);
        nativePort.postMessage({
          type: "tool_response",
          id: msg.id,
          result: result,
        });
      }
    });

    nativePort.onDisconnect.addListener(() => {
      const err = chrome.runtime.lastError?.message;
      console.warn("[PawBot] Native host disconnected:", err);
      nativePort = null;
      isConnected = false;
    });

    nativePort.postMessage({ type: "ping" });
    isConnected = true;
    console.log("[PawBot] Connected to native host");
  } catch (e) {
    console.error("[PawBot] Failed to connect:", e);
  }
}

// ── Tool Execution Router ─────────────────────────────────────────
async function executeToolRequest(msg) {
  const { tool, args } = msg.params || {};
  try {
    switch (tool) {
      case "read_page":      return await toolReadPage(args);
      case "click":          return await toolClick(args);
      case "type":           return await toolType(args);
      case "navigate":       return await toolNavigate(args);
      case "screenshot":     return await toolScreenshot(args);
      case "get_tabs":       return await toolGetTabs(args);
      case "switch_tab":     return await toolSwitchTab(args);
      case "new_tab":        return await toolNewTab(args);
      case "close_tab":      return await toolCloseTab(args);
      case "scroll":         return await toolScroll(args);
      case "execute_js":     return await toolExecuteJs(args);
      case "download":       return await toolDownload(args);
      default:
        return { content: `Unknown tool: ${tool}`, is_error: true };
    }
  } catch (e) {
    return { content: `Tool error: ${e.message}`, is_error: true };
  }
}

// ── Tab Helpers ───────────────────────────────────────────────────
async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function getTargetTabId(args) {
  if (args.tabId) return args.tabId;
  const tab = await getActiveTab();
  return tab?.id;
}

// ── Tool Implementations ──────────────────────────────────────────

async function toolReadPage(args) {
  const tabId = await getTargetTabId(args);
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: (filter, depth, maxChars, refId) => {
      return window.__generateAccessibilityTree?.(filter, depth, maxChars, refId)
        || { error: "Accessibility tree not available" };
    },
    args: [args.filter || "all", args.depth, args.maxChars, args.refId],
  });
  return { content: JSON.stringify(results[0]?.result || {}) };
}

async function toolClick(args) {
  const tabId = await getTargetTabId(args);
  const refId = args.ref_id || args.refId;
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: (refId, coords) => {
      if (coords) {
        const el = document.elementFromPoint(coords[0], coords[1]);
        if (el) { el.click(); return { success: true, method: "coordinates" }; }
        return { success: false, error: "No element at coordinates" };
      }
      if (refId && window.__claudeElementMap?.[refId]) {
        const el = window.__claudeElementMap[refId].deref();
        if (el) { el.click(); return { success: true, method: "ref_id" }; }
        return { success: false, error: "Element no longer exists" };
      }
      return { success: false, error: "No ref_id or coordinates" };
    },
    args: [refId, args.coordinate],
  });
  return { content: JSON.stringify(results[0]?.result || {}) };
}

async function toolType(args) {
  const tabId = await getTargetTabId(args);
  const refId = args.ref_id || args.refId;
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: (refId, text) => {
      if (!refId || !window.__claudeElementMap?.[refId]) {
        return { success: false, error: "Element not found" };
      }
      const el = window.__claudeElementMap[refId].deref();
      if (!el) return { success: false, error: "Element removed" };
      el.focus();
      el.value = text;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { success: true, chars: text.length };
    },
    args: [refId, args.text],
  });
  return { content: JSON.stringify(results[0]?.result || {}) };
}

async function toolNavigate(args) {
  const tabId = await getTargetTabId(args);
  await chrome.tabs.update(tabId, { url: args.url });
  return { content: JSON.stringify({ success: true, url: args.url }) };
}

async function toolScreenshot(args) {
  const dataUrl = await chrome.tabs.captureVisibleTab(null, {
    format: "png", quality: 90,
  });
  return { content: dataUrl.split(",")[1] };  // base64 only
}

async function toolGetTabs(args) {
  const tabs = await chrome.tabs.query({});
  const simplified = tabs.map(t => ({
    id: t.id, title: t.title, url: t.url,
    active: t.active, groupId: t.groupId,
  }));
  return { content: JSON.stringify(simplified) };
}

async function toolSwitchTab(args) {
  await chrome.tabs.update(args.tabId, { active: true });
  const tab = await chrome.tabs.get(args.tabId);
  if (tab.windowId) await chrome.windows.update(tab.windowId, { focused: true });
  return { content: JSON.stringify({ success: true }) };
}

async function toolNewTab(args) {
  const tab = await chrome.tabs.create({ url: args.url || "about:blank" });
  return { content: JSON.stringify({ tabId: tab.id, url: tab.url }) };
}

async function toolCloseTab(args) {
  await chrome.tabs.remove(args.tabId);
  return { content: JSON.stringify({ success: true }) };
}

async function toolScroll(args) {
  const tabId = await getTargetTabId(args);
  await chrome.scripting.executeScript({
    target: { tabId },
    func: (dir, amount) => {
      const map = {
        up: [0, -amount], down: [0, amount],
        left: [-amount, 0], right: [amount, 0],
        top: null, bottom: null,
      };
      if (dir === "top") window.scrollTo(0, 0);
      else if (dir === "bottom") window.scrollTo(0, document.body.scrollHeight);
      else if (map[dir]) window.scrollBy(...map[dir]);
    },
    args: [args.direction || "down", args.amount || 500],
  });
  return { content: JSON.stringify({ success: true }) };
}

async function toolExecuteJs(args) {
  const tabId = await getTargetTabId(args);
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: new Function("return (" + args.script + ")"),
  });
  return { content: JSON.stringify(results[0]?.result) };
}

async function toolDownload(args) {
  const downloadId = await chrome.downloads.download({ url: args.url });
  return { content: JSON.stringify({ downloadId }) };
}

// ── Lifecycle ──────────────────────────────────────────────────────
chrome.runtime.onInstalled.addListener(() => {
  console.log("[PawBot] Extension installed");
  connectToNativeHost();
});

chrome.runtime.onStartup.addListener(() => {
  connectToNativeHost();
});

chrome.action.onClicked.addListener(async (tab) => {
  if (!isConnected) await connectToNativeHost();
  chrome.sidePanel.open({ tabId: tab.id });
});

// Reconnect on message if disconnected
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "check_status") {
    sendResponse({ connected: isConnected });
  } else if (msg.type === "reconnect") {
    connectToNativeHost().then(() => sendResponse({ success: true }));
    return true;
  }
});
```

---

## 15.4 — CLI Registration Command

**Add to:** `pawbot/cli/commands.py`

```python
@app.command("install-extension")
def install_extension():
    """Register PawBot native messaging host for Chrome."""
    from pawbot.browser_bridge.native_host import register_native_host
    register_native_host()
    console.print("[green]✓[/green] Native messaging host registered")
    console.print("Now load the extension in Chrome:")
    console.print("  1. Open chrome://extensions")
    console.print("  2. Enable Developer Mode")
    console.print("  3. Click 'Load unpacked'")
    console.print(f"  4. Select: pawbot-extension/")
```

---

## Verification Checklist — Phase 15

- [ ] Extension loads in `chrome://extensions` without errors
- [ ] `pawbot install-extension` registers native host on Windows/macOS/Linux
- [ ] Extension connects to native host (service worker log shows "Connected")
- [ ] `ping` / `pong` handshake works
- [ ] `read_page` returns accessibility tree of active tab
- [ ] `click` by ref_id triggers real click on user's page
- [ ] `type` fills text into real form fields
- [ ] `navigate` changes the active tab's URL
- [ ] `screenshot` captures the visible tab as base64 PNG
- [ ] `get_tabs` lists all open tabs with URLs
- [ ] `switch_tab` / `new_tab` / `close_tab` work
- [ ] Extension icon shows in Chrome toolbar
