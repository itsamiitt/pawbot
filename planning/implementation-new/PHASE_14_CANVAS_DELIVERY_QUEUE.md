# Phase 14 — Canvas Web UI & Delivery Queue

> **Goal:** Add a web-based canvas interface for rich agent output (code blocks, diagrams, files) and a delivery queue for reliable message delivery across channels.  
> **Duration:** 7-10 days  
> **Risk Level:** Medium (new subsystem, no impact on existing features)  
> **Depends On:** Phase 7 (observability APIs), Phase 4 (channels)

---

## Why This Phase Exists

OpenClaw has two features PawBot completely lacks:
1. **Canvas System** (`canvas/index.html`) — web UI for rendering rich agent output
2. **Delivery Queue** (`delivery-queue/`) — persistent message queue with failure tracking

---

## 14.1 — Canvas Web Interface

A lightweight web UI that renders agent responses with rich formatting — code highlighting, Mermaid diagrams, file downloads, and interactive elements.

**Create:** `pawbot/canvas/server.py`

```python
"""Canvas web UI — rich output renderer for agent responses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger


CANVAS_DIR = Path(__file__).parent
STATIC_DIR = CANVAS_DIR / "static"


def get_canvas_html() -> str:
    """Return the canvas HTML page."""
    return (CANVAS_DIR / "index.html").read_text()


def register_canvas_routes(app) -> None:
    """Register canvas routes with a FastAPI/Starlette app."""
    from starlette.responses import HTMLResponse, FileResponse

    @app.get("/canvas")
    async def canvas_page():
        """Serve the canvas web UI."""
        return HTMLResponse(content=get_canvas_html())

    @app.get("/canvas/static/{filename}")
    async def canvas_static(filename: str):
        """Serve canvas static files."""
        filepath = STATIC_DIR / filename
        if filepath.exists() and filepath.is_relative_to(STATIC_DIR):
            return FileResponse(str(filepath))
        return HTMLResponse(content="Not Found", status_code=404)

    @app.get("/api/canvas/sessions")
    async def canvas_sessions():
        """Get recent canvas sessions for display."""
        return {"sessions": []}

    @app.get("/api/canvas/render")
    async def canvas_render(session_id: str = "latest"):
        """Get rendered content for a canvas session."""
        return {"content": "", "format": "markdown"}
```

**Create:** `pawbot/canvas/index.html`

```html
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🐾 PawBot Canvas</title>
    <style>
        :root {
            --bg-primary: #0d1117;
            --bg-secondary: #161b22;
            --bg-tertiary: #21262d;
            --text-primary: #e6edf3;
            --text-secondary: #8b949e;
            --accent: #58a6ff;
            --accent-hover: #79c0ff;
            --border: #30363d;
            --success: #3fb950;
            --warning: #d29922;
            --error: #f85149;
            --code-bg: #0d1117;
            --font-mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
            --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: var(--font-sans);
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            min-height: 100vh;
        }

        .canvas-header {
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            padding: 12px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            z-index: 100;
            backdrop-filter: blur(12px);
        }

        .canvas-header h1 {
            font-size: 16px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .canvas-header .status {
            font-size: 12px;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--success);
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .canvas-body {
            max-width: 900px;
            margin: 0 auto;
            padding: 32px 24px;
        }

        .output-block {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 8px;
            margin-bottom: 16px;
            overflow: hidden;
        }

        .output-block .block-header {
            background: var(--bg-tertiary);
            padding: 8px 16px;
            font-size: 12px;
            color: var(--text-secondary);
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
        }

        .output-block .block-content {
            padding: 16px;
            font-family: var(--font-mono);
            font-size: 13px;
            overflow-x: auto;
            white-space: pre-wrap;
        }

        .output-block.markdown .block-content {
            font-family: var(--font-sans);
            font-size: 14px;
        }

        .copy-btn {
            background: var(--bg-primary);
            border: 1px solid var(--border);
            color: var(--text-secondary);
            padding: 4px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 11px;
            transition: all 0.2s;
        }

        .copy-btn:hover {
            color: var(--accent);
            border-color: var(--accent);
        }

        .empty-state {
            text-align: center;
            padding: 80px 24px;
            color: var(--text-secondary);
        }

        .empty-state .icon {
            font-size: 48px;
            margin-bottom: 16px;
        }

        .toolbar {
            display: flex;
            gap: 8px;
            margin-bottom: 24px;
        }

        .toolbar button {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            color: var(--text-primary);
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            transition: all 0.2s;
        }

        .toolbar button:hover {
            background: var(--bg-tertiary);
            border-color: var(--accent);
        }

        .toolbar button.active {
            background: var(--accent);
            border-color: var(--accent);
            color: #000;
        }
    </style>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
</head>
<body>
    <div class="canvas-header">
        <h1>🐾 PawBot Canvas</h1>
        <div class="status">
            <span class="status-dot"></span>
            Connected
        </div>
    </div>

    <div class="canvas-body">
        <div class="toolbar">
            <button class="active" onclick="setView('latest')">Latest</button>
            <button onclick="setView('history')">History</button>
            <button onclick="clearCanvas()">Clear</button>
        </div>

        <div id="output-container">
            <div class="empty-state">
                <div class="icon">🐾</div>
                <h2>Canvas Ready</h2>
                <p>Agent output will appear here in real time.</p>
                <p style="font-size: 12px; margin-top: 8px;">
                    Connect via: <code>pawbot gateway start --with-canvas</code>
                </p>
            </div>
        </div>
    </div>

    <script>
        const container = document.getElementById('output-container');
        let ws = null;

        function connectWebSocket() {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${location.host}/ws/canvas`);
            
            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                renderBlock(data);
            };

            ws.onclose = () => {
                setTimeout(connectWebSocket, 3000);
            };
        }

        function renderBlock(data) {
            container.innerHTML = ''; // Clear empty state
            
            const block = document.createElement('div');
            block.className = `output-block ${data.format || 'text'}`;
            
            const header = document.createElement('div');
            header.className = 'block-header';
            header.innerHTML = `
                <span>${data.agent || 'main'} • ${new Date().toLocaleTimeString()}</span>
                <button class="copy-btn" onclick="copyContent(this)">Copy</button>
            `;
            
            const content = document.createElement('div');
            content.className = 'block-content';
            content.textContent = data.content || '';
            
            block.appendChild(header);
            block.appendChild(content);
            container.appendChild(block);
            
            block.scrollIntoView({ behavior: 'smooth' });
        }

        function copyContent(btn) {
            const content = btn.closest('.output-block').querySelector('.block-content');
            navigator.clipboard.writeText(content.textContent);
            btn.textContent = 'Copied!';
            setTimeout(() => btn.textContent = 'Copy', 2000);
        }

        function setView(view) {
            document.querySelectorAll('.toolbar button').forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');
        }

        function clearCanvas() {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="icon">🐾</div>
                    <h2>Canvas Cleared</h2>
                </div>
            `;
        }

        // Connect on load
        connectWebSocket();
    </script>
</body>
</html>
```

---

## 14.2 — Delivery Queue

A persistent message delivery queue that ensures messages reach their destination even if channels are temporarily down.

**Create:** `pawbot/delivery/queue.py`

```python
"""Delivery queue — persistent message delivery with retry and failure tracking."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger


QUEUE_DIR = Path.home() / ".pawbot" / "delivery-queue"
FAILED_DIR = QUEUE_DIR / "failed"


class DeliveryStatus:
    PENDING = "pending"
    SENDING = "sending"
    DELIVERED = "delivered"
    FAILED = "failed"
    EXPIRED = "expired"


class DeliveryMessage:
    """A single message in the delivery queue."""

    def __init__(
        self,
        channel: str,
        recipient: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
    ):
        self.message_id = message_id or str(uuid.uuid4())
        self.channel = channel
        self.recipient = recipient
        self.content = content
        self.metadata = metadata or {}
        self.status = DeliveryStatus.PENDING
        self.attempts = 0
        self.max_attempts = 3
        self.created_at = time.time()
        self.last_attempt_at: float = 0
        self.delivered_at: float = 0
        self.error: str = ""
        self.ttl_seconds: int = 3600  # Message expires after 1 hour

    def is_expired(self) -> bool:
        return time.time() > self.created_at + self.ttl_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "channel": self.channel,
            "recipient": self.recipient,
            "content": self.content,
            "metadata": self.metadata,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "created_at": self.created_at,
            "last_attempt_at": self.last_attempt_at,
            "delivered_at": self.delivered_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeliveryMessage:
        msg = cls(
            channel=data["channel"],
            recipient=data["recipient"],
            content=data["content"],
            metadata=data.get("metadata", {}),
            message_id=data.get("message_id"),
        )
        msg.status = data.get("status", DeliveryStatus.PENDING)
        msg.attempts = data.get("attempts", 0)
        msg.created_at = data.get("created_at", time.time())
        msg.last_attempt_at = data.get("last_attempt_at", 0)
        msg.error = data.get("error", "")
        return msg


class DeliveryQueue:
    """Persistent message delivery queue with retry logic."""

    def __init__(self):
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        FAILED_DIR.mkdir(parents=True, exist_ok=True)
        self._queue: list[DeliveryMessage] = []
        self._load_pending()

    def _load_pending(self) -> None:
        """Load pending messages from disk."""
        for f in sorted(QUEUE_DIR.glob("msg_*.json")):
            try:
                data = json.loads(f.read_text())
                msg = DeliveryMessage.from_dict(data)
                if not msg.is_expired():
                    self._queue.append(msg)
                else:
                    self._move_to_failed(msg, "expired")
                    f.unlink()
            except Exception as e:
                logger.warning("Could not load queued message {}: {}", f.name, e)

        if self._queue:
            logger.info("Loaded {} pending delivery messages", len(self._queue))

    def enqueue(self, message: DeliveryMessage) -> str:
        """Add a message to the delivery queue."""
        self._queue.append(message)
        self._persist(message)
        logger.debug(
            "Message queued: {} -> {} via {}",
            message.message_id[:8], message.recipient, message.channel,
        )
        return message.message_id

    def dequeue(self) -> DeliveryMessage | None:
        """Get the next pending message for delivery."""
        for msg in self._queue:
            if msg.status == DeliveryStatus.PENDING:
                msg.status = DeliveryStatus.SENDING
                msg.attempts += 1
                msg.last_attempt_at = time.time()
                self._persist(msg)
                return msg
        return None

    def mark_delivered(self, message_id: str) -> None:
        """Mark a message as successfully delivered."""
        for msg in self._queue:
            if msg.message_id == message_id:
                msg.status = DeliveryStatus.DELIVERED
                msg.delivered_at = time.time()
                self._remove_file(msg)
                self._queue.remove(msg)
                return

    def mark_failed(self, message_id: str, error: str) -> None:
        """Mark a delivery attempt as failed."""
        for msg in self._queue:
            if msg.message_id == message_id:
                msg.error = error
                if msg.attempts >= msg.max_attempts:
                    msg.status = DeliveryStatus.FAILED
                    self._move_to_failed(msg, error)
                    self._remove_file(msg)
                    self._queue.remove(msg)
                    logger.warning(
                        "Message {} permanently failed after {} attempts: {}",
                        message_id[:8], msg.attempts, error,
                    )
                else:
                    msg.status = DeliveryStatus.PENDING  # Retry
                    self._persist(msg)
                    logger.debug(
                        "Message {} attempt {}/{} failed: {}",
                        message_id[:8], msg.attempts, msg.max_attempts, error,
                    )
                return

    def get_stats(self) -> dict[str, int]:
        """Get queue statistics."""
        pending = sum(1 for m in self._queue if m.status == DeliveryStatus.PENDING)
        sending = sum(1 for m in self._queue if m.status == DeliveryStatus.SENDING)
        failed_count = len(list(FAILED_DIR.glob("msg_*.json")))
        return {
            "pending": pending,
            "sending": sending,
            "total_queued": len(self._queue),
            "failed_total": failed_count,
        }

    def _persist(self, msg: DeliveryMessage) -> None:
        """Save a message to disk."""
        filepath = QUEUE_DIR / f"msg_{msg.message_id}.json"
        filepath.write_text(json.dumps(msg.to_dict(), indent=2))

    def _remove_file(self, msg: DeliveryMessage) -> None:
        """Remove a message file from disk."""
        filepath = QUEUE_DIR / f"msg_{msg.message_id}.json"
        if filepath.exists():
            filepath.unlink()

    def _move_to_failed(self, msg: DeliveryMessage, reason: str) -> None:
        """Move a message to the failed directory."""
        msg.error = reason
        filepath = FAILED_DIR / f"msg_{msg.message_id}.json"
        filepath.write_text(json.dumps(msg.to_dict(), indent=2))
```

---

## 14.3 — Delivery Queue API

```python
# Add to dashboard/server.py:

@app.get("/api/delivery/stats")
def delivery_stats():
    """Delivery queue statistics."""
    from pawbot.delivery.queue import DeliveryQueue
    return DeliveryQueue().get_stats()


@app.get("/api/delivery/failed")
def delivery_failed():
    """List failed deliveries."""
    import json
    from pathlib import Path
    failed_dir = Path.home() / ".pawbot" / "delivery-queue" / "failed"
    failures = []
    if failed_dir.exists():
        for f in sorted(failed_dir.glob("msg_*.json"))[-50:]:
            try:
                failures.append(json.loads(f.read_text()))
            except Exception:
                pass
    return {"failed": failures}


@app.post("/api/delivery/retry/{message_id}")
def retry_delivery(message_id: str):
    """Retry a failed delivery."""
    import json
    from pathlib import Path
    from pawbot.delivery.queue import DeliveryQueue, DeliveryMessage
    
    failed_file = Path.home() / ".pawbot" / "delivery-queue" / "failed" / f"msg_{message_id}.json"
    if not failed_file.exists():
        return JSONResponse(status_code=404, content={"error": "Message not found"})
    
    data = json.loads(failed_file.read_text())
    msg = DeliveryMessage.from_dict(data)
    msg.status = "pending"
    msg.attempts = 0
    msg.error = ""
    
    queue = DeliveryQueue()
    queue.enqueue(msg)
    failed_file.unlink()
    
    return {"success": True, "message_id": message_id}
```

---

## Verification Checklist — Phase 14 Complete

- [ ] `pawbot/canvas/index.html` renders with dark theme and proper styling
- [ ] Canvas WebSocket connects and receives live agent output
- [ ] Canvas copy button works for code blocks
- [ ] `DeliveryQueue` persists messages to `~/.pawbot/delivery-queue/`
- [ ] Failed messages moved to `delivery-queue/failed/` after max attempts
- [ ] Expired messages (TTL) auto-cleaned
- [ ] Retry logic re-enqueues failed messages as pending
- [ ] `/api/delivery/stats` shows queue depth and failure counts
- [ ] `/api/delivery/retry/{id}` moves failed message back to pending
- [ ] Canvas accessible at `/canvas` via gateway
- [ ] All tests pass: `pytest tests/ -v --tb=short`
