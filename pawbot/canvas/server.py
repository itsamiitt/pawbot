"""Canvas web UI and session storage."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger

from pawbot.utils.helpers import safe_filename


CANVAS_DIR = Path(__file__).parent
STATIC_DIR = CANVAS_DIR / "static"


def get_canvas_root() -> Path:
    """Return the persistent canvas data root."""
    return Path.home() / ".pawbot" / "canvas"


def get_canvas_sessions_dir() -> Path:
    """Return the persistent directory for rendered canvas sessions."""
    path = get_canvas_root() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_canvas_html() -> str:
    """Return the bundled canvas HTML page."""
    return (CANVAS_DIR / "index.html").read_text(encoding="utf-8")


def _session_path(session_id: str, sessions_dir: Path | None = None) -> Path:
    directory = sessions_dir or get_canvas_sessions_dir()
    return directory / f"{safe_filename(session_id)}.json"


def parse_canvas_blocks(content: str) -> list[dict[str, Any]]:
    """Split markdown-like content into render blocks."""
    blocks: list[dict[str, Any]] = []
    pattern = re.compile(r"```([\w.+-]*)\n(.*?)```", re.DOTALL)
    cursor = 0

    for match in pattern.finditer(content):
        prefix = content[cursor:match.start()].strip()
        if prefix:
            blocks.append({"type": "markdown", "label": "Notes", "content": prefix})

        language = (match.group(1) or "").strip().lower()
        code = match.group(2).strip("\n")
        block_type = "mermaid" if language == "mermaid" else "code"
        blocks.append({
            "type": block_type,
            "label": language or ("diagram" if block_type == "mermaid" else "code"),
            "content": code,
            "language": language,
        })
        cursor = match.end()

    suffix = content[cursor:].strip()
    if suffix:
        blocks.append({"type": "markdown", "label": "Notes", "content": suffix})

    if not blocks:
        blocks.append({"type": "markdown", "label": "Output", "content": content})
    return blocks


def record_canvas_session(
    session_id: str,
    content: str,
    *,
    format: str = "markdown",
    metadata: dict[str, Any] | None = None,
    sessions_dir: Path | None = None,
) -> dict[str, Any]:
    """Persist or update a canvas session."""
    now = time.time()
    payload = {
        "session_id": session_id,
        "title": session_id,
        "format": format,
        "content": content,
        "blocks": parse_canvas_blocks(content or ""),
        "metadata": dict(metadata or {}),
        "updated_at": now,
    }
    path = _session_path(session_id, sessions_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def get_canvas_session(session_id: str = "latest", *, sessions_dir: Path | None = None) -> dict[str, Any] | None:
    """Load a single canvas session or the latest one."""
    directory = sessions_dir or get_canvas_sessions_dir()
    if session_id == "latest":
        sessions = list_canvas_sessions(limit=1, sessions_dir=directory)
        if not sessions:
            return None
        session_id = sessions[0]["session_id"]

    path = _session_path(session_id, directory)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load canvas session {}: {}", session_id, exc)
        return None


def list_canvas_sessions(limit: int = 50, *, sessions_dir: Path | None = None) -> list[dict[str, Any]]:
    """Return recent canvas sessions sorted by update time."""
    directory = sessions_dir or get_canvas_sessions_dir()
    rows: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append({
            "session_id": payload.get("session_id", path.stem),
            "title": payload.get("title", path.stem),
            "format": payload.get("format", "markdown"),
            "updated_at": payload.get("updated_at", path.stat().st_mtime),
            "preview": str(payload.get("content", ""))[:180],
            "metadata": payload.get("metadata", {}),
        })

    rows.sort(key=lambda row: row.get("updated_at", 0), reverse=True)
    return rows[:limit]


def register_canvas_routes(app) -> None:
    """Register canvas routes on a FastAPI/Starlette app."""
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    @app.get("/canvas")
    async def canvas_page():
        """Serve the canvas web UI."""
        return HTMLResponse(content=get_canvas_html())

    @app.get("/canvas/static/{filename}")
    async def canvas_static(filename: str):
        """Serve bundled canvas assets."""
        filepath = (STATIC_DIR / filename).resolve()
        try:
            filepath.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return HTMLResponse(content="Not Found", status_code=404)
        if filepath.exists() and filepath.is_file():
            return FileResponse(str(filepath))
        return HTMLResponse(content="Not Found", status_code=404)

    @app.get("/api/canvas/sessions")
    async def canvas_sessions():
        """Return recent canvas sessions."""
        return {"sessions": list_canvas_sessions()}

    @app.get("/api/canvas/render")
    async def canvas_render(session_id: str = "latest"):
        """Return a single rendered canvas session."""
        session = get_canvas_session(session_id)
        if session is None:
            return JSONResponse(status_code=404, content={"error": "Canvas session not found"})
        return session

    @app.websocket("/canvas/ws")
    async def canvas_ws(websocket: WebSocket):
        """Push the latest canvas session whenever it changes."""
        # ── Authenticate the WebSocket upgrade ──────────────────────────
        # BaseHTTPMiddleware does NOT intercept WebSocket connections, so
        # auth must be checked explicitly here.
        from http.cookies import SimpleCookie

        from pawbot.dashboard.auth import verify_token as _ws_verify_token

        raw_cookie = ""
        for header_name, header_value in websocket.scope.get("headers", []):
            if header_name == b"cookie":
                raw_cookie = header_value.decode("utf-8", errors="replace")
                break

        token = ""
        if raw_cookie:
            cookie = SimpleCookie()
            cookie.load(raw_cookie)
            if "pawbot_session" in cookie:
                token = cookie["pawbot_session"].value

        if not token or not _ws_verify_token(token):
            await websocket.close(code=4401, reason="Auth required")
            return
        # ────────────────────────────────────────────────────────────────

        await websocket.accept()
        last_seen: tuple[str, float] | None = None
        try:
            while True:
                latest = get_canvas_session("latest")
                if latest:
                    current = (
                        str(latest.get("session_id", "")),
                        float(latest.get("updated_at", 0.0)),
                    )
                    if current != last_seen:
                        await websocket.send_text(json.dumps({
                            "type": "canvas_update",
                            "session": latest,
                        }, ensure_ascii=False))
                        last_seen = current

                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
        except WebSocketDisconnect:
            return

