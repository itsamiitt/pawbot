#!/usr/bin/env python3
"""
Pawbot Dashboard Backend
Serves the UI and exposes REST API endpoints for all dashboard sections.
Start with: pawbot dashboard
"""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

app = FastAPI(title="Pawbot Dashboard", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4000", "http://127.0.0.1:4000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PAWBOT_HOME = Path.home() / ".pawbot"
CONFIG_PATH = PAWBOT_HOME / "config.json"
UI_FILE = Path(__file__).parent / "ui.html"


# ── Static file serving ────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def root():
    if UI_FILE.exists():
        return UI_FILE.read_text(encoding="utf-8")
    return "<h1>ui.html not found</h1>"


@app.get("/{path:path}", response_class=HTMLResponse)
async def spa_fallback(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    if UI_FILE.exists():
        return UI_FILE.read_text(encoding="utf-8")
    return "<h1>ui.html not found</h1>"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_raw_config() -> dict:
    """Load raw config as dict."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_raw_config(data: dict) -> None:
    """Save raw config dict."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _mask_key(key: str) -> str:
    """Mask an API key for display."""
    if not key or len(key) < 8:
        return "••••••••"
    return key[:8] + "••••••••"


def _agent_status() -> str:
    """Check if agent/gateway is running."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pawbot", "status"],
            capture_output=True, text=True, timeout=5
        )
        return "online" if result.returncode == 0 else "offline"
    except Exception:
        return "offline"


def _channel_status(cfg: dict) -> dict:
    """Count enabled channels."""
    channels = cfg.get("channels", {})
    enabled = sum(1 for c in channels.values() if isinstance(c, dict) and c.get("enabled"))
    return {"enabled": enabled, "total": len(channels)}


def _count_cron_jobs() -> int:
    """Count active cron jobs."""
    cron_file = PAWBOT_HOME / "cron" / "jobs.json"
    if not cron_file.exists():
        # Try legacy path
        cron_file = PAWBOT_HOME / "crons.json"
        if not cron_file.exists():
            return 0
    try:
        jobs = json.loads(cron_file.read_text(encoding="utf-8"))
        if isinstance(jobs, list):
            return len([j for j in jobs if j.get("enabled", True)])
        if isinstance(jobs, dict):
            return len(jobs)
        return 0
    except Exception:
        return 0


def _count_memories() -> int:
    """Count memory items."""
    try:
        mem_file = PAWBOT_HOME / "workspace" / "memory" / "MEMORY.md"
        if mem_file.exists():
            content = mem_file.read_text(encoding="utf-8")
            return len([l for l in content.splitlines() if l.strip().startswith("- ")])
        return 0
    except Exception:
        return 0


def _deep_merge(base: dict, override: dict) -> None:
    """Deep merge override into base."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ── Overview ──────────────────────────────────────────────────────────────────


@app.get("/api/overview")
async def overview():
    cfg = _load_raw_config()
    return {
        "agent_status": _agent_status(),
        "memory_count": _count_memories(),
        "active_channels": _channel_status(cfg),
        "cron_jobs": _count_cron_jobs(),
    }


@app.get("/api/health")
async def health():
    """Run all doctor checks."""
    checks = []

    # Python version
    checks.append({
        "label": "Python 3.11+",
        "status": "ok" if sys.version_info >= (3, 11) else "error",
        "fix": "Upgrade Python"
    })

    # Config file
    checks.append({
        "label": "Config file",
        "status": "ok" if CONFIG_PATH.exists() else "error",
        "fix": "Run: pawbot onboard"
    })

    # API key
    cfg = _load_raw_config()
    has_key = any(
        isinstance(v, dict) and v.get("apiKey") and "xxx" not in v.get("apiKey", "")
        for v in cfg.get("providers", {}).values()
    )
    checks.append({
        "label": "API key configured",
        "status": "ok" if has_key else "error",
        "fix": "Add API key in Config section"
    })

    # Workspace
    ws = PAWBOT_HOME / "workspace" / "SOUL.md"
    checks.append({
        "label": "Workspace initialized",
        "status": "ok" if ws.exists() else "warn",
        "fix": "Run: pawbot onboard"
    })

    # Channels
    for ch, ch_cfg in cfg.get("channels", {}).items():
        if isinstance(ch_cfg, dict) and ch_cfg.get("enabled"):
            allow = ch_cfg.get("allowFrom", [])
            if not allow:
                checks.append({
                    "label": f"{ch} allowFrom",
                    "status": "warn",
                    "fix": f"Bot is public — add your ID to channels.{ch}.allowFrom"
                })
            else:
                checks.append({
                    "label": f"{ch} channel",
                    "status": "ok",
                    "fix": ""
                })

    # Node.js (for WhatsApp)
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=3)
        checks.append({
            "label": "Node.js",
            "status": "ok" if result.returncode == 0 else "warn",
            "fix": "Install Node.js 18+ for WhatsApp"
        })
    except Exception:
        checks.append({
            "label": "Node.js",
            "status": "warn",
            "fix": "Install Node.js 18+ for WhatsApp bridge"
        })

    return {"checks": checks}


# ── Chat ──────────────────────────────────────────────────────────────────────


@app.post("/api/chat")
async def chat(body: dict):
    """Quick chat — sends message to pawbot agent, returns response."""
    message = body.get("message", "")
    if not message:
        raise HTTPException(400, "message required")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pawbot", "agent", "-m", message, "--no-markdown"],
            capture_output=True, text=True, timeout=120
        )
        output = result.stdout.strip()
        # Strip Rich formatting artifacts if present
        lines = output.splitlines()
        clean = [l for l in lines if not l.startswith("🐾")]
        return {"response": "\n".join(clean) if clean else output, "error": result.returncode != 0}
    except subprocess.TimeoutExpired:
        return {"response": "", "error": True, "detail": "Agent timed out (120s)"}
    except Exception as e:
        return {"response": "", "error": True, "detail": str(e)}


# ── Agent ─────────────────────────────────────────────────────────────────────


@app.get("/api/agent")
async def agent_info():
    cfg = _load_raw_config()
    soul_path = PAWBOT_HOME / "workspace" / "SOUL.md"
    defaults = cfg.get("agents", {}).get("defaults", {})
    return {
        "model": defaults.get("model", "unknown"),
        "provider": defaults.get("provider", "auto"),
        "maxTokens": defaults.get("maxTokens", 8192),
        "temperature": defaults.get("temperature", 0.1),
        "maxToolIterations": defaults.get("maxToolIterations", 40),
        "memoryWindow": defaults.get("memoryWindow", 100),
        "reasoningEffort": defaults.get("reasoningEffort", ""),
        "soul": soul_path.read_text(encoding="utf-8") if soul_path.exists() else "",
        "status": _agent_status(),
    }


@app.post("/api/agent/model")
async def set_model(body: dict):
    cfg = _load_raw_config()
    cfg.setdefault("agents", {}).setdefault("defaults", {})["model"] = body["model"]
    _save_raw_config(cfg)
    return {"ok": True}


@app.post("/api/agent/soul")
async def save_soul(body: dict):
    soul_path = PAWBOT_HOME / "workspace" / "SOUL.md"
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(body["content"], encoding="utf-8")
    return {"ok": True}


# ── Memory ────────────────────────────────────────────────────────────────────


@app.get("/api/memory")
async def list_memory(page: int = 1, limit: int = 50, search: str = None):
    """List memory items from MEMORY.md and SQLite if available."""
    items = []
    mem_file = PAWBOT_HOME / "workspace" / "memory" / "MEMORY.md"
    if mem_file.exists():
        content = mem_file.read_text(encoding="utf-8")
        for i, line in enumerate(content.splitlines()):
            line = line.strip()
            if line.startswith("- "):
                text = line[2:].strip()
                if search and search.lower() not in text.lower():
                    continue
                items.append({
                    "id": str(i),
                    "content": text,
                    "type": "fact",
                    "salience": 0.8,
                    "created": "",
                    "archived": False,
                })

    total = len(items)
    start = (page - 1) * limit
    by_type = {}
    for item in items:
        t = item["type"]
        by_type[t] = by_type.get(t, 0) + 1

    return {
        "items": items[start:start + limit],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
        "stats": {"by_type": by_type, "total": total, "archived": 0}
    }


@app.get("/api/memory/export")
async def export_memory():
    mem_file = PAWBOT_HOME / "workspace" / "memory" / "MEMORY.md"
    if not mem_file.exists():
        return StreamingResponse(
            iter(["[]"]),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=pawbot-memory.jsonl"}
        )
    content = mem_file.read_text(encoding="utf-8")
    lines = []
    for i, line in enumerate(content.splitlines()):
        line = line.strip()
        if line.startswith("- "):
            lines.append(json.dumps({"id": str(i), "content": line[2:]}))
    return StreamingResponse(
        iter(["\n".join(lines)]),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=pawbot-memory.jsonl"}
    )


# ── Channels ──────────────────────────────────────────────────────────────────


@app.get("/api/channels")
async def channels():
    cfg = _load_raw_config()
    return {"channels": cfg.get("channels", {}), "status": _channel_status(cfg)}


# ── Cron ──────────────────────────────────────────────────────────────────────


@app.get("/api/cron")
async def list_cron():
    for path in [PAWBOT_HOME / "cron" / "jobs.json", PAWBOT_HOME / "crons.json"]:
        if path.exists():
            try:
                return {"jobs": json.loads(path.read_text(encoding="utf-8"))}
            except Exception:
                pass
    return {"jobs": []}


@app.post("/api/cron")
async def create_cron(body: dict):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pawbot", "cron", "add",
             "--name", body.get("name", ""),
             "--message", body.get("message", ""),
             "--cron", body.get("schedule", "")],
            capture_output=True, text=True, timeout=10
        )
        return {"ok": result.returncode == 0, "output": result.stdout}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.delete("/api/cron/{job_id}")
async def delete_cron(job_id: str):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pawbot", "cron", "remove", job_id],
            capture_output=True, text=True, timeout=10
        )
        return {"ok": result.returncode == 0}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Subagents ─────────────────────────────────────────────────────────────────


@app.get("/api/subagents")
async def subagents():
    return {"active": [], "inbox": [], "completed": []}


# ── Skills ────────────────────────────────────────────────────────────────────


@app.get("/api/skills")
async def list_skills():
    skills = []
    skills_dir = PAWBOT_HOME / "workspace" / "skills"
    builtin_dir = Path(__file__).parent.parent / "skills"

    for d in [builtin_dir, skills_dir]:
        if not d.exists():
            continue
        for skill_path in sorted(d.iterdir()):
            if skill_path.is_dir():
                meta = skill_path / "SKILL.md"
                desc = ""
                if meta.exists():
                    lines = meta.read_text(encoding="utf-8").splitlines()
                    for line in lines:
                        if line.strip().startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip('"').strip("'")
                            break
                skills.append({
                    "name": skill_path.name,
                    "description": desc,
                    "builtin": str(d) == str(builtin_dir),
                })

    return {"skills": skills, "total": len(skills)}


# ── Config ────────────────────────────────────────────────────────────────────


@app.get("/api/config")
async def get_config():
    cfg = _load_raw_config()
    # Mask sensitive values
    masked = json.loads(json.dumps(cfg))  # deep copy
    for prov_name, prov in masked.get("providers", {}).items():
        if isinstance(prov, dict) and "apiKey" in prov:
            prov["apiKey"] = _mask_key(prov["apiKey"])
            prov["_hasKey"] = True
    for ch_name, ch in masked.get("channels", {}).items():
        if isinstance(ch, dict):
            for k in ["token", "appSecret", "encryptKey", "verificationToken",
                       "bridgeToken", "clawToken", "clientSecret", "botToken",
                       "appToken", "imapPassword", "smtpPassword", "accessToken", "secret"]:
                if k in ch:
                    ch[k] = _mask_key(ch[k])
    return masked


@app.put("/api/config")
async def update_config(body: dict):
    cfg = _load_raw_config()
    _deep_merge(cfg, body)
    _save_raw_config(cfg)
    return {"ok": True}


@app.get("/api/config/raw")
async def get_raw_config():
    return _load_raw_config()


@app.put("/api/config/raw")
async def put_raw_config(body: dict):
    _save_raw_config(body)
    return {"ok": True}


@app.post("/api/config/test-key")
async def test_api_key(body: dict):
    """Test an API key by making a minimal call."""
    # Placeholder — real implementation would call the provider
    return {"valid": True, "latency_ms": 0}


@app.post("/api/config/reset")
async def reset_config(body: dict):
    confirm = body.get("confirm", "")
    if confirm != "CONFIRM":
        raise HTTPException(400, "Type CONFIRM to proceed")
    _save_raw_config({})
    return {"ok": True}


# ── Logs ──────────────────────────────────────────────────────────────────────


ALLOWED_LOGS = {"agent.log", "gateway.log", "security_audit.jsonl", "traces.jsonl"}


@app.get("/api/logs/{filename}")
async def get_log(filename: str, tail: int = 200, search: str = None, level: str = None):
    if filename not in ALLOWED_LOGS:
        raise HTTPException(400, "Invalid log file")
    log_path = PAWBOT_HOME / "logs" / filename
    if not log_path.exists():
        return {"lines": [], "total": 0}
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {"lines": [], "total": 0}
    if search:
        lines = [l for l in lines if search.lower() in l.lower()]
    if level and level != "ALL":
        lines = [l for l in lines if f"[{level}]" in l]
    return {"lines": lines[-tail:], "total": len(lines)}


@app.get("/api/logs/{filename}/download")
async def download_log(filename: str):
    if filename not in ALLOWED_LOGS:
        raise HTTPException(400, "Invalid log file")
    log_path = PAWBOT_HOME / "logs" / filename
    if not log_path.exists():
        raise HTTPException(404, "Log file not found")
    return FileResponse(str(log_path), filename=filename)


# ── Entry point ───────────────────────────────────────────────────────────────


def start(host: str = "127.0.0.1", port: int = 4000, open_browser: bool = True):
    """Start the dashboard server."""
    import uvicorn

    if open_browser:
        import threading
        import webbrowser

        def _open():
            time.sleep(1.2)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    start()
