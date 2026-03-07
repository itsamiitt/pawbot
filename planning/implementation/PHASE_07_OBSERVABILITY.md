# Phase 7 — Observability & Monitoring

> **Goal:** Production-grade logging, metrics, and alerting.  
> **Duration:** 5-7 days | **Risk:** Low | **Depends On:** Phase 1, Phase 3

## Prerequisites

```bash
pip install "structlog>=24.0.0"
```

---

## 7.1 — Structured Logging with structlog

### Problem
Current logging uses `loguru` with unstructured string messages. Not machine-parseable.

### Solution
Add structured JSON logging alongside loguru:

**Create:** `pawbot/observability/logging.py`

```python
"""Structured logging configuration."""
import structlog
import logging
import sys

def configure_logging(json_output: bool = False, level: str = "INFO"):
    """Configure structured logging for the entire application.
    
    Args:
        json_output: If True, output JSON lines (for production).
                     If False, output human-readable colored format.
        level: Log level string.
    """
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
    ]
    
    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

def get_logger(name: str = "pawbot"):
    """Get a structured logger instance."""
    return structlog.get_logger(name)
```

### Usage pattern (gradual adoption):
```python
# Existing code continues to work with loguru
from loguru import logger
logger.info("Old style log: {}", value)

# New code can use structlog for structured events
from pawbot.observability.logging import get_logger
slog = get_logger("agent.loop")
slog.info("llm_call", model=model, tokens=token_count, latency_ms=elapsed)
```

---

## 7.2 — Prometheus-Compatible Metrics

**Create:** `pawbot/observability/metrics.py`

```python
"""Application metrics — Prometheus-compatible format."""
from __future__ import annotations
import time, threading
from typing import Any

class Counter:
    """Simple thread-safe counter metric."""
    def __init__(self, name: str, help: str):
        self.name = name
        self.help = help
        self._value = 0
        self._lock = threading.Lock()
    def inc(self, amount: int = 1):
        with self._lock:
            self._value += amount
    @property
    def value(self) -> int:
        return self._value

class Histogram:
    """Simple histogram for latency tracking."""
    def __init__(self, name: str, help: str):
        self.name = name
        self.help = help
        self._values: list[float] = []
        self._lock = threading.Lock()
    def observe(self, value: float):
        with self._lock:
            self._values.append(value)
            if len(self._values) > 10000:
                self._values = self._values[-5000:]
    @property
    def summary(self) -> dict[str, float]:
        with self._lock:
            if not self._values:
                return {"count": 0, "sum": 0, "avg": 0, "p50": 0, "p99": 0}
            sorted_v = sorted(self._values)
            return {
                "count": len(sorted_v),
                "sum": sum(sorted_v),
                "avg": sum(sorted_v) / len(sorted_v),
                "p50": sorted_v[len(sorted_v) // 2],
                "p99": sorted_v[int(len(sorted_v) * 0.99)],
            }

class Gauge:
    """Simple thread-safe gauge metric."""
    def __init__(self, name: str, help: str):
        self.name, self.help = name, help
        self._value = 0.0
        self._lock = threading.Lock()
    def set(self, value: float):
        with self._lock: self._value = value
    def inc(self, amount: float = 1.0):
        with self._lock: self._value += amount
    def dec(self, amount: float = 1.0):
        with self._lock: self._value -= amount
    @property
    def value(self) -> float:
        return self._value


# ── Global Metrics Registry ──────────────────────────────────────────────────

class MetricsRegistry:
    """Central metrics registry for the application."""
    def __init__(self):
        # LLM metrics
        self.llm_calls = Counter("pawbot_llm_calls_total", "Total LLM API calls")
        self.llm_errors = Counter("pawbot_llm_errors_total", "Total LLM API errors")
        self.llm_latency = Histogram("pawbot_llm_latency_ms", "LLM call latency")
        self.llm_tokens_in = Counter("pawbot_llm_tokens_input", "Total input tokens")
        self.llm_tokens_out = Counter("pawbot_llm_tokens_output", "Total output tokens")
        # Agent metrics
        self.messages_processed = Counter("pawbot_messages_total", "Total messages processed")
        self.tool_calls = Counter("pawbot_tool_calls_total", "Total tool executions")
        self.tool_errors = Counter("pawbot_tool_errors_total", "Total tool errors")
        self.tool_latency = Histogram("pawbot_tool_latency_ms", "Tool execution latency")
        # Session metrics
        self.active_sessions = Gauge("pawbot_active_sessions", "Current active sessions")
        # Memory metrics
        self.memory_saves = Counter("pawbot_memory_saves_total", "Total memory saves")
        self.memory_searches = Counter("pawbot_memory_searches_total", "Total memory searches")
        # Security metrics
        self.security_blocks = Counter("pawbot_security_blocks_total", "Total security blocks")
        self.injections_detected = Counter("pawbot_injections_total", "Injection attempts detected")

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            if isinstance(attr, Counter):
                lines.append(f"# HELP {attr.name} {attr.help}")
                lines.append(f"# TYPE {attr.name} counter")
                lines.append(f"{attr.name} {attr.value}")
            elif isinstance(attr, Gauge):
                lines.append(f"# HELP {attr.name} {attr.help}")
                lines.append(f"# TYPE {attr.name} gauge")
                lines.append(f"{attr.name} {attr.value}")
            elif isinstance(attr, Histogram):
                s = attr.summary
                lines.append(f"# HELP {attr.name} {attr.help}")
                lines.append(f"# TYPE {attr.name} histogram")
                lines.append(f'{attr.name}_count {s["count"]}')
                lines.append(f'{attr.name}_sum {s["sum"]:.1f}')
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict[str, Any]:
        """Export as JSON-friendly dict for dashboard."""
        return {
            "llm": {
                "calls": self.llm_calls.value,
                "errors": self.llm_errors.value,
                "latency": self.llm_latency.summary,
                "tokens_in": self.llm_tokens_in.value,
                "tokens_out": self.llm_tokens_out.value,
            },
            "agent": {
                "messages": self.messages_processed.value,
                "tool_calls": self.tool_calls.value,
                "tool_errors": self.tool_errors.value,
            },
            "sessions": {"active": self.active_sessions.value},
            "security": {
                "blocks": self.security_blocks.value,
                "injections": self.injections_detected.value,
            },
        }

# Singleton
metrics = MetricsRegistry()
```

### Expose via API endpoint:

```python
# In gateway/server.py or dashboard/server.py:
from pawbot.observability.metrics import metrics

@app.get("/metrics")
def prometheus_metrics():
    """Prometheus-compatible metrics endpoint."""
    from starlette.responses import Response
    return Response(content=metrics.to_prometheus(), media_type="text/plain")

@app.get("/api/metrics")
def json_metrics():
    """JSON metrics for dashboard."""
    return metrics.to_dict()
```

---

## 7.3 — Health Check Endpoint Enhancement

```python
# Upgrade existing /health endpoint:

@app.get("/health")
async def health():
    """Comprehensive health check."""
    import time
    checks = {
        "status": "healthy",
        "uptime_s": int(time.time() - _start_time),
        "version": __version__,
        "checks": {},
    }
    # SQLite check
    try:
        from pawbot.agent.memory.sqlite_store import SQLiteFactStore
        store = SQLiteFactStore({})
        checks["checks"]["sqlite"] = {"status": "ok"}
    except Exception as e:
        checks["checks"]["sqlite"] = {"status": "error", "error": str(e)[:100]}
        checks["status"] = "degraded"
    
    # Redis check
    try:
        import redis
        r = redis.Redis(socket_connect_timeout=2)
        r.ping()
        checks["checks"]["redis"] = {"status": "ok"}
    except Exception:
        checks["checks"]["redis"] = {"status": "unavailable"}
    
    return checks
```

---

## 7.4 — Integration Points

Instrument existing code to emit metrics:

```python
# In agent/loop.py _run_agent_loop, after LLM call:
from pawbot.observability.metrics import metrics
metrics.llm_calls.inc()
metrics.llm_latency.observe(elapsed_ms)

# In _process_tool_calls, after each tool:
metrics.tool_calls.inc()
if result and "Error:" in result:
    metrics.tool_errors.inc()

# In _process_message, at start:
metrics.messages_processed.inc()
```

---

## Verification Checklist

- [ ] `structlog>=24.0.0` in `pyproject.toml`
- [ ] `pawbot/observability/logging.py` provides `get_logger()`
- [ ] `pawbot/observability/metrics.py` tracks LLM, tool, session, security metrics
- [ ] `/metrics` endpoint returns Prometheus text format
- [ ] `/api/metrics` endpoint returns JSON for dashboard
- [ ] `/health` checks SQLite and Redis connectivity
- [ ] Metrics increment correctly during agent operation
- [ ] All tests pass: `pytest tests/ -v --tb=short`
