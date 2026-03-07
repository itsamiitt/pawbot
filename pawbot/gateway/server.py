"""
pawbot/gateway/server.py

WebSocket Gateway Server — the single entry point for all external connections.

Endpoints:
    WS  /ws/{session_id} — bidirectional message stream per session
    GET /health          — liveness probe (used by Docker HEALTHCHECK)
    GET /sessions        — list active sessions and queue depths

IMPORTS FROM: pawbot/contracts.py — InboundMessage, OutboundMessage,
              ChannelType, new_id(), now(), SQLITE_DB, get_logger()
USES:         lane_queue (Phase 1) — all messages enqueued, never direct
RUNS AS:      uvicorn pawbot.gateway.server:app --host 0.0.0.0 --port 8080
"""

import json
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from pawbot.contracts import (
    InboundMessage, ChannelType,
    now, SQLITE_DB, get_logger, PRIORITY_KEYWORDS
)
from pawbot.agent.lane_queue import lane_queue
from pawbot.agent.agent_router import agent_router
from pawbot.canvas.server import register_canvas_routes
from pawbot.utils.rate_limit import RateLimitExceeded, RequestRateLimiter

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern lifespan handler — replaces deprecated on_event."""
    yield
    # Shutdown: gracefully drain lane queues
    await lane_queue.shutdown()
    logger.info("Gateway: shut down cleanly")


app = FastAPI(title="Pawbot Gateway", version="2.0.0", lifespan=lifespan)
register_canvas_routes(app)

_boot_ts = time.time()  # used by /health uptime calculation
gateway_limiter = RequestRateLimiter()


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(_: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a consistent response for rate-limited requests."""
    retry_after = max(1, int(exc.retry_after) if exc.retry_after else 1)
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(retry_after)},
        content={"error": "Rate limit exceeded", "limit": exc.limit},
    )


def _build_api_message(
    *,
    session_id: str,
    user_id: str,
    content: str,
    is_priority: bool,
) -> InboundMessage:
    """Build an API message with agent-aware session routing."""
    agent_config = agent_router.resolve(ChannelType.API, user_id)
    routed_session = agent_router.get_session_id(
        agent_config, user_id, ChannelType.API
    )
    return InboundMessage(
        channel=ChannelType.API.value,
        sender_id=user_id,
        chat_id=session_id,
        content=content,
        metadata={
            "raw_type": "text",
            "has_media": False,
            "is_priority": is_priority,
            "agent_id": agent_config.get("id", "default"),
        },
        session_key_override=routed_session,
    )


# ── WebSocket endpoint ──────────────────────────────────────────────────────────
@app.websocket("/ws/{session_id}")
async def ws_endpoint(websocket: WebSocket, session_id: str) -> None:
    """
    Persistent bidirectional connection for a session.
    Client sends JSON:  {"user_id": "...", "message": "...", "is_priority": false}
    Server sends JSON:  {"response": "...", "session_id": "...", "ts": 1234567890}
    Messages are enqueued via lane_queue — never processed in parallel within the same session_id.
    """
    await websocket.accept()
    logger.info(f"Gateway: WS connection opened — session={session_id}")

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "error": "Invalid JSON", "session_id": session_id
                }))
                continue

            content = str(data.get("message", "")).strip()
            if not content:
                continue

            user_id     = str(data.get("user_id", "api_user"))
            is_priority = bool(data.get("is_priority", False)) or any(
                kw.lower() in content.lower() for kw in PRIORITY_KEYWORDS
            )

            # Phase 4: resolve agent config via multi-agent router
            msg = _build_api_message(
                session_id=session_id,
                user_id=user_id,
                content=content,
                is_priority=is_priority,
            )

            # Enqueue via LaneQueue (Phase 1) — never call handler directly
            # Use routed_session (namespaced by agent) for lane isolation
            await lane_queue.enqueue(
                msg.session_key,
                _handle_message,
                msg,
                websocket
            )

    except WebSocketDisconnect:
        logger.info(f"Gateway: WS disconnected — session={session_id}")
    except Exception as exc:
        logger.error(f"Gateway: WS error — session={session_id}: {exc}", exc_info=True)
        try:
            await websocket.send_text(json.dumps({"error": str(exc)}))
        except Exception:
            pass


async def _handle_message(msg: InboundMessage, websocket: WebSocket) -> None:
    """
    Called by LaneQueue worker — processes one message and sends response.
    Import agent_loop here (lazy import) to avoid circular imports.
    """
    try:
        result = await _process_api_message(msg)
        await websocket.send_text(json.dumps({
            "response":   result or "",
            "session_id": msg.session_key,
            "ts":         now(),
        }))
    except Exception as exc:
        logger.error(f"Gateway: handler error — {exc}", exc_info=True)
        await websocket.send_text(json.dumps({
            "error":      str(exc),
            "session_id": msg.session_key,
        }))


async def _process_api_message(msg: InboundMessage) -> str:
    """Process an API message through the routed agent workspace and return its response."""
    from pawbot.agents.pool import AgentInstance, resolve_agent_definition
    from pawbot.bus.queue import MessageBus
    from pawbot.config.loader import load_config
    from pawbot.providers.litellm_provider import LiteLLMProvider

    config = load_config()
    bus = MessageBus()
    agent_id = str((msg.metadata or {}).get("agent_id", "")).strip() or None
    definition = resolve_agent_definition(config.agents, agent_id)
    provider_cfg = config.get_provider()
    provider = LiteLLMProvider(
        api_key=provider_cfg.api_key if provider_cfg else None,
        api_base=config.get_api_base(),
        default_model=definition.model or config.agents.defaults.model,
    )
    agent = AgentInstance(
        definition=definition,
        defaults=config.agents.defaults,
        bus=bus,
        provider=provider,
        global_tools=config.agents.tools,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        enable_heartbeat=False,
    )
    try:
        await agent.start()
        return await agent.process_direct(
            content=msg.content,
            session_key=msg.session_key,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
    finally:
        await agent.stop()


# ── REST endpoints ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health(request: Request) -> JSONResponse:
    """
    Liveness probe. Used by:
    - Docker HEALTHCHECK
    - Kubernetes readiness probe
    - Dashboard ClawHub skill
    Returns 200 OK when healthy, 503 when degraded.
    """
    gateway_limiter.check_request(request, "gateway:health", "60/minute")

    db_path = os.path.expanduser(SQLITE_DB)
    db_mb   = os.path.getsize(db_path) / 1e6 if os.path.exists(db_path) else 0.0

    # Phase 5: include startup validation status
    try:
        from pawbot.config.validator import startup_validator
        validation = startup_validator.validate()
        validation_status = "ok" if validation.ok else "degraded"
        validation_errors = len(validation.errors)
        validation_warnings = len(validation.warnings)
    except Exception:
        validation_status = "unknown"
        validation_errors = 0
        validation_warnings = 0

    payload = {
        "status":              "ok",  # liveness: always ok if server is running
        "version":             "2.0.0",
        "uptime_seconds":      int(time.time() - _boot_ts),
        "active_sessions":     lane_queue.active_sessions(),
        "lane_depths":         lane_queue.stats(),
        "memory_db_mb":        round(db_mb, 2),
        "validation_status":   validation_status,
        "validation_errors":   validation_errors,
        "validation_warnings": validation_warnings,
    }

    # Optional: add system metrics if psutil is available
    try:
        import psutil
        payload["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        payload["ram_percent"] = psutil.virtual_memory().percent
    except ImportError:
        pass

    return JSONResponse(content=payload, status_code=200)


@app.post("/api/chat")
async def api_chat(request: Request, body: dict) -> JSONResponse:
    """One-shot REST chat endpoint mirroring the WebSocket flow."""
    gateway_limiter.check_request(request, "gateway:api_chat", "10/minute")

    content = str(body.get("message", "")).strip()
    if not content:
        return JSONResponse(status_code=400, content={"error": "message required"})

    session_id = str(
        body.get("session_id") or f"rest:{RequestRateLimiter.client_key(request)}"
    )
    user_id = str(body.get("user_id", "api_user"))
    is_priority = bool(body.get("is_priority", False)) or any(
        kw.lower() in content.lower() for kw in PRIORITY_KEYWORDS
    )
    msg = _build_api_message(
        session_id=session_id,
        user_id=user_id,
        content=content,
        is_priority=is_priority,
    )

    try:
        result = await _process_api_message(msg)
    except Exception as exc:
        logger.error("Gateway: REST chat error — %s", exc, exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(exc)})

    return JSONResponse(content={
        "response": result or "",
        "session_id": msg.session_key,
        "ts": now(),
    })


@app.get("/sessions")
async def sessions() -> JSONResponse:
    """List active session lanes and their queue depths."""
    return JSONResponse(content={
        "active_sessions": lane_queue.active_sessions(),
        "lanes":           lane_queue.stats(),
    })


# ── Phase 7: Observability Endpoints ────────────────────────────────────────


@app.get("/metrics")
async def prometheus_metrics() -> "Response":
    """Prometheus-compatible metrics endpoint (Phase 7)."""
    from starlette.responses import Response
    from pawbot.observability.metrics import metrics
    return Response(content=metrics.to_prometheus(), media_type="text/plain")


@app.get("/api/metrics")
async def json_metrics() -> JSONResponse:
    """JSON metrics for dashboard (Phase 7)."""
    from pawbot.observability.metrics import metrics
    return JSONResponse(content=metrics.to_dict())
