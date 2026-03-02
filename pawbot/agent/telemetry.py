"""Observability & tracing for pawbot.

Phase 15 — OpenTelemetry-compatible tracing system providing:
  - Span               (single trace span context manager / dataclass)
  - PawbotTracer      (main tracing interface with span() and trace_fn())
  - TraceExporter      (exports spans to JSONL and optional OTLP endpoint)
  - SessionMetrics     (per-session aggregated metrics from spans)
  - MetricsCollector   (Prometheus-compatible HTTP /metrics endpoint)
  - _NoOpSpan          (zero-cost no-op when tracing is disabled)

All canonical names per MASTER_REFERENCE.md.
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("pawbot.telemetry")


# ══════════════════════════════════════════════════════════════════════════════
#  Span — single trace span
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class Span:
    """A single trace span representing one operation."""

    trace_id: str                        # shared across all spans in a request
    span_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    parent_id: Optional[str] = None
    name: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    status: str = "ok"                   # "ok" | "error" | "cancelled"
    attributes: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        """Elapsed time in milliseconds."""
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return (time.time() - self.start_time) * 1000

    def set(self, key: str, value: Any) -> None:
        """Add or update an attribute on this span."""
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict | None = None) -> None:
        """Record a timestamped event within this span."""
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        })

    def finish(self, status: str = "ok", error: str = "") -> None:
        """Mark the span as finished."""
        self.end_time = time.time()
        self.status = status
        if error:
            self.attributes["error"] = error

    def to_dict(self) -> dict:
        """Serialise span to a dict suitable for JSON export."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "attributes": self.attributes,
            "events": self.events,
        }


class _NoOpSpan:
    """No-op span returned when tracing is disabled."""

    trace_id: str = ""
    span_id: str = ""
    parent_id: Optional[str] = None
    name: str = ""
    status: str = "ok"
    attributes: dict = {}
    events: list = []

    @property
    def duration_ms(self) -> float:
        return 0.0

    def set(self, key: str, value: Any) -> None:
        pass

    def add_event(self, name: str, attributes: dict | None = None) -> None:
        pass

    def finish(self, status: str = "ok", error: str = "") -> None:
        pass

    def to_dict(self) -> dict:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
#  TraceExporter — JSONL + optional OTLP
# ══════════════════════════════════════════════════════════════════════════════


class TraceExporter:
    """Exports spans to local JSONL file and optionally to OTLP endpoint."""

    def __init__(self, trace_file: str, otlp_endpoint: str = ""):
        self.trace_file = trace_file
        self.otlp_endpoint = otlp_endpoint
        self._lock = threading.Lock()

    def export(self, span: Span) -> None:
        """Export a finished span. Non-blocking — writes in background thread."""
        span_dict = span.to_dict()
        thread = threading.Thread(
            target=self._write,
            args=(span_dict,),
            daemon=True,
            name="trace-export",
        )
        thread.start()

    def export_sync(self, span: Span) -> None:
        """Synchronous export (for testing)."""
        self._write(span.to_dict())

    def _write(self, span_dict: dict) -> None:
        """Write span to local file and optional OTLP endpoint."""
        # Write to local JSONL
        try:
            with self._lock:
                with open(self.trace_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(span_dict) + "\n")
        except Exception as exc:
            logger.debug("Trace file write failed: %s", exc)

        # Send to OTLP endpoint if configured
        if self.otlp_endpoint:
            self._send_otlp(span_dict)

    def _send_otlp(self, span_dict: dict) -> None:
        """Send span to OTLP endpoint. Failures are non-fatal."""
        try:
            import httpx
            httpx.post(
                self.otlp_endpoint,
                json={
                    "resourceSpans": [{
                        "scopeSpans": [{
                            "spans": [span_dict],
                        }],
                    }],
                },
                timeout=2.0,
            )
        except ImportError:
            logger.debug("httpx not installed — OTLP export skipped")
        except Exception as exc:
            logger.debug("OTLP export failed (non-fatal): %s", exc)

    def read_recent(self, n: int = 50) -> list[dict]:
        """Read the most recent n spans from the trace file."""
        try:
            with open(self.trace_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return [json.loads(line) for line in lines[-n:] if line.strip()]
        except FileNotFoundError:
            return []
        except Exception as e:  # noqa: F841
            return []


# ══════════════════════════════════════════════════════════════════════════════
#  SessionMetrics — per-session aggregated metrics
# ══════════════════════════════════════════════════════════════════════════════


class SessionMetrics:
    """Aggregates per-session metrics from completed spans."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {
            "total_spans": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "tool_calls": {},            # {tool_name: {"count": N, "total_ms": N}}
            "memory_ops": {"save": 0, "search": 0, "cache_hits": 0},
            "model_calls": {},           # {model_name: {"count": N, "tokens": N}}
            "errors": 0,
            "session_start": time.time(),
        }

    def record(self, span: Span) -> None:
        """Update metrics based on a completed span."""
        with self._lock:
            self._data["total_spans"] += 1

            if span.status == "error":
                self._data["errors"] += 1

            tokens = span.attributes.get("tokens_used", 0)
            self._data["total_tokens"] += tokens

            # Tool call tracking
            if span.name.startswith("tool."):
                tool = span.name[5:]
                if tool not in self._data["tool_calls"]:
                    self._data["tool_calls"][tool] = {"count": 0, "total_ms": 0}
                self._data["tool_calls"][tool]["count"] += 1
                self._data["tool_calls"][tool]["total_ms"] += span.duration_ms

            # Memory operation tracking
            if span.name.startswith("memory."):
                op = span.name[7:]  # "save" | "search" | etc.
                if op in self._data["memory_ops"]:
                    self._data["memory_ops"][op] += 1
                if span.attributes.get("cache_hit"):
                    self._data["memory_ops"]["cache_hits"] += 1

            # Model call tracking
            model = span.attributes.get("model")
            if model:
                if model not in self._data["model_calls"]:
                    self._data["model_calls"][model] = {"count": 0, "tokens": 0}
                self._data["model_calls"][model]["count"] += 1
                self._data["model_calls"][model]["tokens"] += tokens

    def summary(self) -> dict:
        """Return aggregated session metrics."""
        with self._lock:
            elapsed = time.time() - self._data["session_start"]
            return {
                **self._data,
                "session_elapsed_seconds": round(elapsed, 1),
                "avg_tool_latency_ms": {
                    tool: round(v["total_ms"] / v["count"], 1) if v["count"] else 0
                    for tool, v in self._data["tool_calls"].items()
                },
            }

    def reset(self) -> None:
        """Reset all accumulated metrics."""
        with self._lock:
            self._data = {
                "total_spans": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "tool_calls": {},
                "memory_ops": {"save": 0, "search": 0, "cache_hits": 0},
                "model_calls": {},
                "errors": 0,
                "session_start": time.time(),
            }


# ══════════════════════════════════════════════════════════════════════════════
#  PawbotTracer — main tracing interface
# ══════════════════════════════════════════════════════════════════════════════


class PawbotTracer:
    """Main tracing interface for Pawbot.

    Usage:
        tracer = PawbotTracer(config)

        # As context manager:
        with tracer.span("memory.search", {"query": query}) as span:
            results = memory.search(query)
            span.set("result_count", len(results))

        # As decorator:
        @tracer.trace_fn("tool.server_run")
        def server_run(command: str):
            ...
    """

    _local = threading.local()   # thread-local storage for active span stack

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        obs_cfg = self.config.get("observability", {})
        self.enabled = obs_cfg.get("enabled", True)
        self.trace_file = os.path.expanduser(
            obs_cfg.get("trace_file", "~/.pawbot/logs/traces.jsonl")
        )
        self.otlp_endpoint = obs_cfg.get("otlp_endpoint", "")
        os.makedirs(os.path.dirname(self.trace_file), exist_ok=True)
        self._exporter = TraceExporter(self.trace_file, self.otlp_endpoint)
        self._session_metrics = SessionMetrics()

    @contextmanager
    def span(self, name: str, attributes: dict | None = None):
        """Context manager creating a span, yielding it, then finishing/exporting.

        If tracing is disabled, yields a no-op span (zero cost).
        """
        if not self.enabled:
            yield _NoOpSpan()
            return

        trace_id = self._current_trace_id()
        parent_id = self._current_span_id()

        s = Span(
            trace_id=trace_id,
            parent_id=parent_id,
            name=name,
            attributes=dict(attributes or {}),
        )
        self._push_span(s)
        try:
            yield s
        except Exception as e:
            s.finish(status="error", error=str(e))
            self._exporter.export(s)
            self._pop_span()
            raise
        else:
            # Only finish as "ok" if the span hasn't already been
            # explicitly finished with a different status (e.g. by trace_fn)
            if s.end_time is None:
                s.finish(status="ok")
            self._exporter.export(s)
            self._session_metrics.record(s)
        finally:
            self._pop_span()

    def trace_fn(self, span_name: str, extract_attrs: Callable | None = None):
        """Decorator factory. Wraps a function with a trace span.

        extract_attrs: optional fn(args, kwargs) -> dict of span attributes.
        """
        def decorator(fn: Callable) -> Callable:
            def wrapper(*args, **kwargs):
                attrs = extract_attrs(args, kwargs) if extract_attrs else {}
                with self.span(span_name, attrs) as s:
                    result = fn(*args, **kwargs)
                    if isinstance(result, dict) and "error" in result:
                        s.finish(status="error", error=result["error"])
                    return result
            wrapper.__name__ = fn.__name__
            wrapper.__doc__ = fn.__doc__
            return wrapper
        return decorator

    def new_trace(self) -> str:
        """Start a new trace (new request). Returns trace_id."""
        trace_id = str(uuid.uuid4())
        if not hasattr(self._local, "span_stack"):
            self._local.span_stack = []
        self._local.trace_id = trace_id
        return trace_id

    @property
    def exporter(self) -> TraceExporter:
        """Access the trace exporter."""
        return self._exporter

    def session_summary(self) -> dict:
        """Return aggregated session metrics."""
        return self._session_metrics.summary()

    def reset_metrics(self) -> None:
        """Reset session metrics."""
        self._session_metrics.reset()

    # ── Thread-local span stack management ────────────────────────────────

    def _current_trace_id(self) -> str:
        if not hasattr(self._local, "trace_id"):
            self._local.trace_id = str(uuid.uuid4())
        return self._local.trace_id

    def _current_span_id(self) -> Optional[str]:
        stack = getattr(self._local, "span_stack", [])
        return stack[-1].span_id if stack else None

    def _push_span(self, span: Span) -> None:
        if not hasattr(self._local, "span_stack"):
            self._local.span_stack = []
        self._local.span_stack.append(span)

    def _pop_span(self) -> None:
        stack = getattr(self._local, "span_stack", [])
        if stack:
            stack.pop()


# ══════════════════════════════════════════════════════════════════════════════
#  MetricsCollector — Prometheus-compatible /metrics endpoint
# ══════════════════════════════════════════════════════════════════════════════


class MetricsCollector:
    """Exposes Prometheus-compatible metrics at HTTP /metrics endpoint.

    Optional — only starts if observability.prometheus_port is set in config.
    """

    def __init__(self, tracer: PawbotTracer, port: int = 9090):
        self.tracer = tracer
        self.port = port
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start HTTP server in background thread."""
        tracer = self.tracer

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/metrics":
                    metrics = tracer.session_summary()
                    output = self._format_prometheus(metrics)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(output.encode())
                elif self.path == "/health":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                else:
                    self.send_response(404)
                    self.end_headers()

            def _format_prometheus(self, metrics: dict) -> str:
                lines = []
                lines.append(
                    f'pawbot_total_spans {metrics["total_spans"]}'
                )
                lines.append(
                    f'pawbot_total_tokens {metrics["total_tokens"]}'
                )
                lines.append(
                    f'pawbot_total_errors {metrics["errors"]}'
                )
                lines.append(
                    f'pawbot_session_elapsed_seconds '
                    f'{metrics.get("session_elapsed_seconds", 0)}'
                )
                for tool, stats in metrics.get("tool_calls", {}).items():
                    lines.append(
                        f'pawbot_tool_calls_total{{tool="{tool}"}} {stats["count"]}'
                    )
                for tool, avg in metrics.get("avg_tool_latency_ms", {}).items():
                    lines.append(
                        f'pawbot_tool_avg_latency_ms{{tool="{tool}"}} {avg}'
                    )
                for model, stats in metrics.get("model_calls", {}).items():
                    lines.append(
                        f'pawbot_model_calls_total{{model="{model}"}} {stats["count"]}'
                    )
                    lines.append(
                        f'pawbot_model_tokens_total{{model="{model}"}} {stats["tokens"]}'
                    )
                mem = metrics.get("memory_ops", {})
                lines.append(f'pawbot_memory_saves {mem.get("save", 0)}')
                lines.append(f'pawbot_memory_searches {mem.get("search", 0)}')
                lines.append(f'pawbot_memory_cache_hits {mem.get("cache_hits", 0)}')
                return "\n".join(lines) + "\n"

            def log_message(self, *args):
                pass  # suppress access logs

        self._server = http.server.HTTPServer(("0.0.0.0", self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="prometheus-metrics",
        )
        self._thread.start()
        logger.info("Prometheus metrics at http://0.0.0.0:%d/metrics", self.port)

    def stop(self) -> None:
        """Stop the HTTP server."""
        if self._server:
            self._server.shutdown()
            self._server = None
