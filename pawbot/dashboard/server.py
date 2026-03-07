#!/usr/bin/env python3
"""
Pawbot Dashboard Backend
Serves the UI and exposes REST API endpoints for all dashboard sections.
Start with: pawbot dashboard
"""

import json
import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

from pawbot.canvas.server import register_canvas_routes
from pawbot.dashboard.auth import (
    create_token,
    password_configured,
    verify_password,
    verify_token,
)
from pawbot.utils.rate_limit import RateLimitExceeded, RequestRateLimiter

app = FastAPI(title="Pawbot Dashboard", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4000", "http://127.0.0.1:4000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(_: Request, exc: RateLimitExceeded) -> JSONResponse:
    retry_after = max(1, int(exc.retry_after) if exc.retry_after else 1)
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(retry_after)},
        content={"error": "Rate limit exceeded", "limit": exc.limit},
    )

from pawbot.utils.paths import PAWBOT_HOME, CONFIG_PATH
UI_FILE = Path(__file__).parent / "ui.html"
dashboard_limiter = RequestRateLimiter()


class AuthMiddleware(BaseHTTPMiddleware):
    """Protect dashboard API routes with a signed session cookie."""

    PUBLIC_PATHS = {"/", "/favicon.ico", "/api/auth/login", "/api/auth/status"}

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in self.PUBLIC_PATHS:
            return await call_next(request)

        token = request.cookies.get("pawbot_session", "")
        if not token or not verify_token(token):
            if request.url.path.startswith("/api/"):
                return JSONResponse(
                    status_code=401,
                    content={"error": "Auth required"},
                )
            return JSONResponse(status_code=401, content={"error": "Auth required"})

        return await call_next(request)


app.add_middleware(AuthMiddleware)
register_canvas_routes(app)


# ── Static file serving ────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def root():
    if UI_FILE.exists():
        return UI_FILE.read_text(encoding="utf-8")
    return "<h1>ui.html not found</h1>"


@app.get("/api/auth/status")
async def auth_status(request: Request) -> dict:
    token = request.cookies.get("pawbot_session", "")
    return {
        "authenticated": bool(token and verify_token(token)),
        "configured": password_configured(),
    }


@app.post("/api/auth/login")
async def login(request: Request, body: dict) -> JSONResponse:
    dashboard_limiter.check_request(request, "dashboard:login", "10/minute")

    if not password_configured():
        return JSONResponse(
            status_code=503,
            content={"error": "Dashboard auth is not configured"},
        )

    if not verify_password(str(body.get("password", ""))):
        return JSONResponse(status_code=401, content={"error": "Invalid password"})

    response = JSONResponse(content={"success": True, "configured": True})
    response.set_cookie(
        "pawbot_session",
        create_token(),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=24 * 3600,
    )
    return response


@app.post("/api/auth/logout")
async def logout() -> JSONResponse:
    response = JSONResponse(content={"success": True})
    response.delete_cookie("pawbot_session", samesite="strict")
    return response




# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_raw_config() -> dict:
    """Load raw config as dict."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: F841
            return {}
    return {}


def _save_raw_config(data: dict) -> None:
    """Save raw config dict."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    from pawbot.utils.fs import write_json_with_backup
    write_json_with_backup(CONFIG_PATH, data)


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
    except Exception as e:  # noqa: F841
        return "offline"


def _channel_status(cfg: dict) -> dict:
    """Count enabled channels."""
    channels = cfg.get("channels", {})
    # Only count sub-dicts that represent actual channel configs, not top-level
    # scalar fields like sendProgress / sendToolHints.
    channel_dicts = {k: v for k, v in channels.items() if isinstance(v, dict)}
    enabled = sum(1 for c in channel_dicts.values() if c.get("enabled"))
    return {"enabled": enabled, "total": len(channel_dicts)}


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
    except Exception as e:  # noqa: F841
        return 0


def _count_memories() -> int:
    """Count memory items from SQLite (primary) with MEMORY.md fallback."""
    try:
        from pawbot.agent.memory.sqlite_store import SQLiteFactStore
        from pawbot.agent.memory._compat import to_config_dict
        from pawbot.config.loader import load_config

        config = load_config()
        store = SQLiteFactStore(to_config_dict(config))
        rows = store.load(query="", limit=10000)
        return len(rows)
    except Exception:
        pass
    try:
        mem_file = PAWBOT_HOME / "workspace" / "memory" / "MEMORY.md"
        if mem_file.exists():
            content = mem_file.read_text(encoding="utf-8")
            return len([line for line in content.splitlines() if line.strip().startswith("- ")])
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


def _configured_agents_status() -> list[dict]:
    """Return config-derived agent status when no live pool is attached."""
    from pawbot.agents.pool import effective_agent_settings
    from pawbot.agents.workspace_manager import WorkspaceManager
    from pawbot.config.loader import load_config

    config = load_config()
    agents = []
    for definition in config.agents.agents:
        runtime = effective_agent_settings(definition, config.agents.defaults)
        agents.append({
            "id": definition.id,
            "running": False,
            "default": definition.default,
            "enabled": definition.enabled,
            "model": runtime["model"],
            "workspace": WorkspaceManager(definition.id, runtime["workspace"]).to_dict(),
            "tools_allow": list(definition.tools.allow),
            "tools_deny": list(definition.tools.deny),
            "dispatch_count": 0,
            "last_message_at": None,
            "heartbeat_enabled": definition.heartbeat.enabled,
        })
    return agents


def _configured_heartbeat_status() -> list[dict]:
    """Return config-derived heartbeat state when no live pool is attached."""
    from pawbot.config.loader import load_config

    config = load_config()
    return [
        {
            "agent_id": definition.id,
            "running": False,
            "interval": definition.heartbeat.every,
            "interval_seconds": None,
            "target": definition.heartbeat.target,
            "beat_count": 0,
            "errors": 0,
            "last_beat": 0.0,
            "seconds_since_beat": None,
        }
        for definition in config.agents.agents
    ]


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
async def health(request: Request):
    """Run all doctor checks."""
    dashboard_limiter.check_request(request, "dashboard:health", "60/minute")
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
    except Exception as e:  # noqa: F841
        checks.append({
            "label": "Node.js",
            "status": "warn",
            "fix": "Install Node.js 18+ for WhatsApp bridge"
        })

    return {"checks": checks}


# ── Chat ──────────────────────────────────────────────────────────────────────


@app.post("/api/chat")
async def chat(request: Request, body: dict):
    """Quick chat — sends message to pawbot agent, returns response."""
    dashboard_limiter.check_request(request, "dashboard:chat", "10/minute")
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
        clean = [line for line in lines if not line.startswith("🐾")]
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
    from pawbot.utils.fs import atomic_write_text
    atomic_write_text(soul_path, body["content"])
    return {"ok": True}


@app.get("/api/agents/status")
async def agents_status():
    """Get status of all configured agent instances."""
    pool = getattr(app.state, "agent_pool", None)
    return {"agents": pool.status() if pool else _configured_agents_status()}


@app.get("/api/agents/{agent_id}/workspace")
async def agent_workspace(agent_id: str):
    """Get workspace info for a specific agent."""
    pool = getattr(app.state, "agent_pool", None)
    if pool:
        instance = pool.get_agent(agent_id)
        if instance:
            return {"workspace": instance.workspace_mgr.to_dict()}

    from pawbot.agents.pool import effective_agent_settings, resolve_agent_definition
    from pawbot.agents.workspace_manager import WorkspaceManager
    from pawbot.config.loader import load_config

    config = load_config()
    definition = resolve_agent_definition(config.agents, agent_id, include_disabled=True)
    if definition.id != agent_id:
        return {"error": f"Agent '{agent_id}' not found"}

    runtime = effective_agent_settings(definition, config.agents.defaults)
    return {"workspace": WorkspaceManager(agent_id, runtime["workspace"]).to_dict()}


@app.post("/api/agents/{agent_id}/restart")
async def restart_agent(agent_id: str):
    """Restart a specific live agent."""
    pool = getattr(app.state, "agent_pool", None)
    if not pool:
        return {"error": "Agent pool not initialized"}
    ok = await pool.restart_agent(agent_id)
    if ok:
        return {"success": True, "message": f"Agent '{agent_id}' restarted"}
    return {"error": f"Agent '{agent_id}' not found"}


@app.get("/api/agents/heartbeats")
async def agents_heartbeats():
    """Get heartbeat status for all agents."""
    pool = getattr(app.state, "agent_pool", None)
    return {"heartbeats": pool.heartbeat_status() if pool else _configured_heartbeat_status()}


# ── Memory ────────────────────────────────────────────────────────────────────


@app.get("/api/delivery/stats")
async def delivery_stats():
    """Delivery queue statistics."""
    from pawbot.delivery.queue import DeliveryQueue

    return DeliveryQueue().get_stats()


@app.get("/api/delivery/failed")
async def delivery_failed():
    """List recent failed deliveries."""
    from pawbot.delivery.queue import DeliveryQueue

    return {"failed": DeliveryQueue().list_failed(limit=50)}


@app.post("/api/delivery/retry/{message_id}")
async def retry_delivery(message_id: str):
    """Retry a failed delivery."""
    from pawbot.delivery.queue import DeliveryQueue

    queue = DeliveryQueue()
    if not queue.retry_failed(message_id):
        return JSONResponse(status_code=404, content={"error": "Message not found"})
    return {"success": True, "message_id": message_id}


@app.get("/api/memory")
async def list_memory(page: int = 1, limit: int = 50, search: str = None):
    """List memory items from SQLite store (primary) with MEMORY.md fallback."""
    items = []

    # Try the real SQLite memory system first — this is the system of record.
    try:
        from pawbot.agent.memory.sqlite_store import SQLiteFactStore
        from pawbot.agent.memory._compat import to_config_dict
        from pawbot.config.loader import load_config

        config = load_config()
        store = SQLiteFactStore(to_config_dict(config))
        if search:
            rows = store.search(query=search, limit=500)
        else:
            rows = store.load(query="", limit=500)
        for row in rows:
            content = row.get("content", {})
            if isinstance(content, dict):
                text = content.get("text", json.dumps(content, ensure_ascii=False))
            else:
                text = str(content)
            items.append({
                "id": row.get("id", ""),
                "content": text,
                "type": row.get("type", "fact"),
                "salience": row.get("salience", 1.0),
                "created": row.get("created_at", ""),
                "archived": False,
                "source": "sqlite",
            })
    except Exception:
        # Fallback: read from MEMORY.md if SQLite is unavailable
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
                        "source": "markdown",
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
            except Exception as e:  # noqa: F841
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

    seen: dict[str, dict] = {}  # name → skill dict (workspace overrides builtin)
    for d in [builtin_dir, skills_dir]:
        if not d.exists():
            continue
        for skill_path in sorted(d.iterdir()):
            if skill_path.is_dir():
                meta = skill_path / "SKILL.md"
                name = skill_path.name
                desc = ""
                if meta.exists():
                    try:
                        raw = meta.read_text(encoding="utf-8")
                        # Parse YAML frontmatter between --- markers
                        if raw.startswith("---"):
                            end = raw.find("---", 3)
                            if end != -1:
                                fm = raw[3:end]
                                for line in fm.splitlines():
                                    stripped = line.strip()
                                    if stripped.startswith("name:"):
                                        name = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                                    elif stripped.startswith("description:"):
                                        desc = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    except Exception:
                        pass
                seen[name] = {
                    "name": name,
                    "description": desc[:200] if desc else "",
                    "builtin": str(d) == str(builtin_dir),
                }

    skills = list(seen.values())
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
async def test_api_key(request: Request, body: dict):
    """Test an API key by making a minimal call to the provider."""
    dashboard_limiter.check_request(request, "dashboard:test-key", "10/minute")

    provider = str(body.get("provider", "")).strip().lower()
    api_key = str(body.get("apiKey", body.get("api_key", ""))).strip()

    if not provider:
        return {"valid": False, "latency_ms": 0, "error": "No provider specified"}
    if not api_key:
        return {"valid": False, "latency_ms": 0, "error": "No API key provided"}

    try:
        import httpx
    except ImportError:
        return {"valid": False, "latency_ms": 0, "error": "httpx not installed"}

    start_ts = time.time()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if provider in ("openai",):
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                r.raise_for_status()
            elif provider in ("openrouter",):
                r = await client.get(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                r.raise_for_status()
            elif provider in ("anthropic",):
                if not api_key.startswith("sk-ant-"):
                    latency = round((time.time() - start_ts) * 1000, 1)
                    return {"valid": False, "latency_ms": latency, "error": "Invalid Anthropic key format (expected sk-ant-...)"}
                # Anthropic has no lightweight health endpoint; format check is best-effort
                latency = round((time.time() - start_ts) * 1000, 1)
                return {"valid": True, "latency_ms": latency}
            elif provider in ("ollama",):
                base = body.get("baseUrl", "http://localhost:11434")
                r = await client.get(f"{base}/api/tags")
                r.raise_for_status()
            else:
                return {"valid": False, "latency_ms": 0, "error": f"Unknown provider '{provider}'; cannot validate"}

        latency = round((time.time() - start_ts) * 1000, 1)
        return {"valid": True, "latency_ms": latency}
    except httpx.HTTPStatusError as e:
        latency = round((time.time() - start_ts) * 1000, 1)
        return {"valid": False, "latency_ms": latency, "error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        latency = round((time.time() - start_ts) * 1000, 1)
        return {"valid": False, "latency_ms": latency, "error": str(e)}


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
    except Exception as e:  # noqa: F841
        return {"lines": [], "total": 0}
    if search:
        lines = [line for line in lines if search.lower() in line.lower()]
    if level and level != "ALL":
        lines = [line for line in lines if f"[{level}]" in line]
    return {"lines": lines[-tail:], "total": len(lines)}


@app.get("/api/logs/{filename}/download")
async def download_log(filename: str):
    if filename not in ALLOWED_LOGS:
        raise HTTPException(400, "Invalid log file")
    log_path = PAWBOT_HOME / "logs" / filename
    if not log_path.exists():
        raise HTTPException(404, "Log file not found")
    return FileResponse(str(log_path), filename=filename)




# ── Fleet ─────────────────────────────────────────────────────────────────────────


@app.get("/api/fleet/status")
async def fleet_status():
    status_path = PAWBOT_HOME / "shared" / "status.json"
    if not status_path.exists():
        return {"active": False, "fleet": {}, "config": {}, "execution_log": []}
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
        data["active"] = True
        return data
    except (json.JSONDecodeError, OSError):
        return {"active": False, "fleet": {}, "config": {}, "execution_log": []}


@app.get("/api/fleet/dag")
async def fleet_dag():
    status_path = PAWBOT_HOME / "shared" / "status.json"
    if not status_path.exists():
        return {"mermaid": "", "tasks": []}
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
        fleet = data.get("fleet", {})
        return {
            "mermaid": fleet.get("dag_mermaid", ""),
            "tasks": fleet.get("tasks", []),
        }
    except (json.JSONDecodeError, OSError):
        return {"mermaid": "", "tasks": []}


# ── Phase 7: Metrics Endpoints ───────────────────────────────────────────────


@app.get("/api/metrics")
async def dashboard_json_metrics():
    """JSON metrics for dashboard (Phase 7)."""
    from pawbot.observability.metrics import metrics
    return metrics.to_dict()


@app.get("/api/metrics/prometheus")
async def dashboard_prometheus_metrics():
    """Prometheus text format metrics (Phase 7)."""
    from starlette.responses import Response
    from pawbot.observability.metrics import metrics
    return Response(content=metrics.to_prometheus(), media_type="text/plain")


# ── Observability ─────────────────────────────────────────────────────────────


@app.get("/api/observability")
async def observability_summary(limit: int = 500):
    """Return trace-based SLO and reliability summary."""
    cfg = _load_raw_config()
    obs = cfg.get("observability", {}) if isinstance(cfg, dict) else {}
    trace_file = obs.get("traceFile") or obs.get("trace_file") or "~/.pawbot/logs/traces.jsonl"

    from pawbot.agent.telemetry import summarize_trace_file

    summary = summarize_trace_file(trace_file, limit=limit)
    return {
        "trace_file": str(Path(trace_file).expanduser()),
        "window_span_count": summary.get("window_span_count", 0),
        "error_count": summary.get("error_count", 0),
        "success_rate_pct": summary.get("success_rate_pct", 100.0),
        "latency_p50_ms": summary.get("latency_p50_ms", 0.0),
        "latency_p95_ms": summary.get("latency_p95_ms", 0.0),
        "channel_delivery_success_pct": summary.get("channel_delivery_success_pct", {}),
    }


# ── SPA fallback (MUST be last route — catch-all for frontend routing) ────────


@app.get("/{path:path}", response_class=HTMLResponse)
async def spa_fallback(path: str):
    if UI_FILE.exists():
        return UI_FILE.read_text(encoding="utf-8")
    return "<h1>ui.html not found</h1>"


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
