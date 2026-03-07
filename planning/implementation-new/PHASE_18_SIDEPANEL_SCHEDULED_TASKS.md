# Phase 18 — Chrome Extension: Side Panel UI & Scheduled Tasks

> **Goal:** Build the side panel chat interface, offscreen document for background processing, and scheduled task automation.  
> **Duration:** 10-14 days  
> **Risk Level:** Medium  
> **Depends On:** Phase 15-17  
> **Reference:** Claude's side panel, offscreen.js, alarm-based tasks

---

## 18.1 — Side Panel Chat UI

**Create:** `pawbot-extension/sidepanel.html`

```html
<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PawBot</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="sidepanel.css">
</head>
<body>
  <div id="pawbot-panel">
    <header class="panel-header">
      <div class="logo">🐾 PawBot</div>
      <div class="status" id="status-dot">
        <span class="dot"></span> <span id="status-text">Connecting...</span>
      </div>
    </header>

    <div class="messages" id="messages"></div>

    <div class="input-area">
      <textarea id="user-input" placeholder="Ask PawBot to do something..."
        rows="2" autofocus></textarea>
      <button id="send-btn" title="Send">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
          <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
        </svg>
      </button>
    </div>
  </div>
  <script src="sidepanel.js"></script>
</body>
</html>
```

**Create:** `pawbot-extension/sidepanel.js`

```javascript
const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");

let ws = null;
const tabId = new URLSearchParams(location.search).get("tabId");

function connectWebSocket() {
  ws = new WebSocket("ws://127.0.0.1:8765/ws/panel");

  ws.onopen = () => {
    statusDot.classList.add("connected");
    statusText.textContent = "Connected";
    ws.send(JSON.stringify({ type: "init", tabId }));
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "agent_response") addMessage("assistant", msg.content);
    else if (msg.type === "tool_use") addMessage("tool", `Using: ${msg.tool}`);
    else if (msg.type === "agent_active") showAgentActive(msg.active);
  };

  ws.onclose = () => {
    statusDot.classList.remove("connected");
    statusText.textContent = "Disconnected";
    setTimeout(connectWebSocket, 3000);
  };
}

function addMessage(role, content) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.textContent = content;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function showAgentActive(active) {
  chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
    if (!tab?.id) return;
    chrome.tabs.sendMessage(tab.id, {
      type: active ? "SHOW_AGENT_INDICATORS" : "HIDE_AGENT_INDICATORS"
    });
  });
}

function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  addMessage("user", text);
  ws.send(JSON.stringify({ type: "user_message", content: text, tabId }));
  inputEl.value = "";
}

sendBtn.addEventListener("click", sendMessage);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

connectWebSocket();
```

---

## 18.2 — Offscreen Document

For background audio and GIF generation (same pattern as Claude).

**Create:** `pawbot-extension/offscreen.html`

```html
<!doctype html><html><head><title>PawBot Offscreen</title></head>
<body><script src="offscreen.js"></script></body></html>
```

**Create:** `pawbot-extension/offscreen.js`

```javascript
/** Offscreen document for audio playback and screenshot GIF generation. */

const AudioCtx = window.AudioContext || window.webkitAudioContext;
const audioContext = new AudioCtx();

async function playSound(audioUrl, volume = 0.5) {
  const response = await fetch(audioUrl);
  const buffer = await audioContext.decodeAudioData(await response.arrayBuffer());
  const source = audioContext.createBufferSource();
  const gain = audioContext.createGain();
  source.buffer = buffer;
  gain.gain.value = volume;
  source.connect(gain);
  gain.connect(audioContext.destination);
  if (audioContext.state === "suspended") await audioContext.resume();
  source.start(0);
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "PLAY_NOTIFICATION_SOUND") {
    playSound(msg.audioUrl, msg.volume)
      .then(() => sendResponse({ success: true }))
      .catch(e => sendResponse({ success: false, error: e.message }));
    return true;
  }
});
```

---

## 18.3 — Scheduled Task System

Add to service worker:

```javascript
// ── Scheduled Tasks via Chrome Alarms ─────────────────────────────

async function createScheduledTask(task) {
  // task: { id, name, prompt, url, repeatType, repeatInterval }
  const tasks = (await chrome.storage.local.get(["scheduledTasks"])).scheduledTasks || [];
  tasks.push({ ...task, createdAt: Date.now(), enabled: true });
  await chrome.storage.local.set({ scheduledTasks: tasks });

  if (task.repeatType === "interval") {
    await chrome.alarms.create(`task_${task.id}`, {
      periodInMinutes: task.repeatInterval || 60,
    });
  } else if (task.repeatType === "daily") {
    await chrome.alarms.create(`task_${task.id}`, { periodInMinutes: 1440 });
  }
  return { success: true, taskId: task.id };
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (!alarm.name.startsWith("task_")) return;
  const taskId = alarm.name;
  const { scheduledTasks } = await chrome.storage.local.get(["scheduledTasks"]);
  const task = (scheduledTasks || []).find(t => `task_${t.id}` === taskId);
  if (!task || !task.enabled) return;

  // Create window, navigate, and execute agent prompt
  const tab = await chrome.tabs.create({ url: task.url || "about:blank" });
  // Send task to PawBot via native messaging
  if (nativePort) {
    nativePort.postMessage({
      type: "scheduled_task",
      task: { prompt: task.prompt, tabId: tab.id, name: task.name },
    });
  }
});
```

---

## 18.4 — WebSocket Server in PawBot

**Create:** `pawbot/browser_bridge/ws_server.py`

```python
"""WebSocket server for side panel ↔ PawBot communication."""

import asyncio
import json
from loguru import logger

try:
    import websockets
except ImportError:
    websockets = None


class PanelWebSocketServer:
    """Serves WebSocket connections from the side panel."""

    def __init__(self, agent_callback, host="127.0.0.1", port=8765):
        self.agent_callback = agent_callback
        self.host = host
        self.port = port
        self.clients = set()

    async def handler(self, websocket, path):
        self.clients.add(websocket)
        try:
            async for raw in websocket:
                msg = json.loads(raw)
                if msg["type"] == "user_message":
                    # Run agent and stream responses back
                    response = await self.agent_callback(
                        msg["content"], tab_id=msg.get("tabId")
                    )
                    await websocket.send(json.dumps({
                        "type": "agent_response",
                        "content": response,
                    }))
        except Exception as e:
            logger.debug("Panel WS error: {}", e)
        finally:
            self.clients.discard(websocket)

    async def broadcast(self, msg: dict):
        for ws in self.clients:
            try:
                await ws.send(json.dumps(msg))
            except Exception:
                pass

    async def start(self):
        if not websockets:
            logger.warning("websockets not installed — panel disabled")
            return
        server = await websockets.serve(self.handler, self.host, self.port)
        logger.info("Panel WebSocket server on ws://{}:{}", self.host, self.port)
        await server.wait_closed()
```

---

## Verification Checklist — Phase 18

- [ ] Side panel opens when clicking PawBot extension icon
- [ ] Side panel shows connection status (Connected/Disconnected)
- [ ] User messages sent from panel reach PawBot agent
- [ ] Agent responses stream back to side panel in real time
- [ ] Tool use indicators shown in panel (e.g. "Using: chrome_read_page")
- [ ] Agent indicators toggled on active tab when agent is working
- [ ] Offscreen document plays notification sounds
- [ ] Scheduled tasks created and stored in `chrome.storage.local`
- [ ] Chrome alarms trigger task execution at configured intervals
- [ ] WebSocket server starts on `ws://127.0.0.1:8765/ws/panel`
