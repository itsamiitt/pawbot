# PHASE 15 — OBSERVABILITY & TRACING
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)
> **Implementation Days:** Weeks 5–8
> **Primary Files:** `~/nanobot/agent/telemetry.py` (NEW), decorators added across all phases
> **Test File:** `~/nanobot/tests/test_observability.py`
> **Depends on:** All phases — telemetry is a cross-cutting concern added last

---

## BEFORE YOU START — READ THESE FILES

```bash
cat ~/nanobot/agent/loop.py           # main orchestration — add trace spans here
cat ~/nanobot/agent/memory.py         # memory operations — trace every save/search
cat ~/nanobot/agent/context.py        # context building — trace budget enforcement
cat ~/.nanobot/config.json            # current observability config if any
```

**Integration strategy:** Phase 15 adds a `@trace_tool` decorator and `with tracer.span(name)` context manager that all other phases use. If observability is disabled in config, these are no-ops — zero performance cost when off.

---

## WHAT YOU ARE BUILDING

An OpenTelemetry-compatible tracing system that gives full visibility into:
- Every agent decision (complexity score, model chosen, tokens used)
- Every memory operation (type, latency, cache hit/miss)
- Every tool call (name, args, result, duration)
- Every subagent lifecycle (spawn, progress, completion)
- Session-level metrics (total tokens, cost estimate, task success rate)

Traces are exported to:
1. Local JSONL file at `~/.nanobot/logs/traces.jsonl` (always)
2. OpenTelemetry collector endpoint (optional, configurable)
3. Prometheus metrics endpoint (optional, for dashboards)

---

## CANONICAL NAMES — ALL NEW CLASSES IN THIS PHASE

| Class Name | File | Purpose |
|---|---|---|
| `NanobotTracer` | `agent/telemetry.py` | Main tracing interface |
| `Span` | `agent/telemetry.py` | Single trace span context manager |
| `SessionMetrics` | `agent/telemetry.py` | Per-session aggregated metrics |
| `MetricsCollector` | `agent/telemetry.py` | Collects and exports Prometheus metrics |
| `TraceExporter` | `agent/telemetry.py` | Exports traces to OTLP or file |

---

## FEATURE 15.1 — CORE TRACING INFRASTRUCTURE

### `Span` dataclass and context manager

```python
import time, uuid, json, os, threading, logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional, Any

logger = logging.getLogger("nanobot")

@dataclass
class Span:
    """A single trace span representing one operation."""
    trace_id: str                   # shared across all spans in a request
    span_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    parent_id: Optional[str] = None
    name: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    status: str = "ok"             # "ok" | "error" | "cancelled"
    attributes: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return (time.time() - self.start_time) * 1000

    def set(self, key: str, value: Any):
        """Add or update an attribute on this span."""
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict = {}):
        """Record a timestamped event within this span."""
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes,
        })

    def finish(self, status: str = "ok", error: str = ""):
        self.end_time = time.time()
        self.status = status
        if error:
            self.attributes["error"] = error

    def to_dict(self) -> dict:
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
```

### `NanobotTracer` class

```python
class NanobotTracer:
    """
    Main tracing interface for Nanobot.
    
    Usage:
        tracer = NanobotTracer(config)
        
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

    def __init__(self, config: dict):
        self.config = config
        obs_cfg = config.get("observability", {})
        self.enabled = obs_cfg.get("enabled", True)
        self.trace_file = os.path.expanduser(
            obs_cfg.get("trace_file", "~/.nanobot/logs/traces.jsonl")
        )
        self.otlp_endpoint = obs_cfg.get("otlp_endpoint", "")
        os.makedirs(os.path.dirname(self.trace_file), exist_ok=True)
        self._exporter = TraceExporter(self.trace_file, self.otlp_endpoint)
        self._session_metrics = SessionMetrics()

    @contextmanager
    def span(self, name: str, attributes: dict = {}):
        """
        Context manager that creates a span, yields it, then finishes and exports it.
        If tracing is disabled, yields a no-op span.
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
            attributes=dict(attributes),
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
            s.finish(status="ok")
            self._exporter.export(s)
            self._session_metrics.record(s)
        finally:
            self._pop_span()

    def trace_fn(self, span_name: str, extract_attrs: Callable = None):
        """
        Decorator factory. Wraps a function with a trace span.
        
        extract_attrs: optional fn(args, kwargs) -> dict of attributes to set on span
        """
        def decorator(fn):
            def wrapper(*args, **kwargs):
                attrs = extract_attrs(args, kwargs) if extract_attrs else {}
                with self.span(span_name, attrs) as s:
                    result = fn(*args, **kwargs)
                    if isinstance(result, dict) and "error" in result:
                        s.finish(status="error", error=result["error"])
                    return result
            wrapper.__name__ = fn.__name__
            return wrapper
        return decorator

    def new_trace(self) -> str:
        """Start a new trace (new request). Returns trace_id."""
        trace_id = str(uuid.uuid4())
        if not hasattr(self._local, "trace_stack"):
            self._local.trace_stack = []
        self._local.trace_id = trace_id
        return trace_id

    def _current_trace_id(self) -> str:
        if not hasattr(self._local, "trace_id"):
            self._local.trace_id = str(uuid.uuid4())
        return self._local.trace_id

    def _current_span_id(self) -> Optional[str]:
        stack = getattr(self._local, "span_stack", [])
        return stack[-1].span_id if stack else None

    def _push_span(self, span: Span):
        if not hasattr(self._local, "span_stack"):
            self._local.span_stack = []
        self._local.span_stack.append(span)

    def _pop_span(self):
        stack = getattr(self._local, "span_stack", [])
        if stack:
            stack.pop()

    def session_summary(self) -> dict:
        """Return aggregated session metrics."""
        return self._session_metrics.summary()


class _NoOpSpan:
    """No-op span returned when tracing is disabled."""
    def set(self, key, value): pass
    def add_event(self, name, attributes={}): pass
    def finish(self, status="ok", error=""): pass
```

### `TraceExporter` class

```python
class TraceExporter:
    """Exports spans to local JSONL file and optionally to OTLP endpoint."""

    def __init__(self, trace_file: str, otlp_endpoint: str = ""):
        self.trace_file = trace_file
        self.otlp_endpoint = otlp_endpoint
        self._lock = threading.Lock()

    def export(self, span: Span):
        """Export a finished span. Non-blocking — writes in background thread."""
        thread = threading.Thread(
            target=self._write,
            args=(span.to_dict(),),
            daemon=True
        )
        thread.start()

    def _write(self, span_dict: dict):
        # Write to local JSONL
        with self._lock:
            with open(self.trace_file, "a") as f:
                f.write(json.dumps(span_dict) + "\n")

        # Send to OTLP endpoint if configured
        if self.otlp_endpoint:
            try:
                import httpx
                httpx.post(
                    self.otlp_endpoint,
                    json={"resourceSpans": [{"scopeSpans": [{"spans": [span_dict]}]}]},
                    timeout=2.0
                )
            except Exception as e:
                logger.debug(f"OTLP export failed (non-fatal): {e}")
```

### `SessionMetrics` class

```python
class SessionMetrics:
    """Aggregates per-session metrics from completed spans."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "total_spans": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "tool_calls": {},           # {tool_name: {"count": N, "avg_ms": N}}
            "memory_ops": {"save": 0, "search": 0, "cache_hits": 0},
            "model_calls": {},          # {model_name: {"count": N, "tokens": N}}
            "errors": 0,
            "session_start": time.time(),
        }

    def record(self, span: Span):
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
```

---

## FEATURE 15.2 — INTEGRATION ACROSS ALL PHASES

Add trace spans to each existing phase. This is a surgical addition — add spans without changing any existing logic.

### Phase 1 — Memory spans

```python
# In MemoryRouter.save():
with tracer.span("memory.save", {"type": type}) as span:
    result = self.sqlite.save(type, content)
    span.set("memory_id", result[:8])

# In MemoryRouter.search():
with tracer.span("memory.search", {"query": query[:50]}) as span:
    results = ...
    span.set("result_count", len(results))
```

### Phase 2 — AgentLoop spans

```python
# In AgentLoop.process():
tracer.new_trace()
with tracer.span("agent.process", {"complexity": score}) as span:
    span.set("system_path", path)    # "system1" | "system1.5" | "system2"
    span.set("task_type", task_type)
    ...
    span.set("tokens_used", total_tokens)
```

### Phase 4 — ModelRouter spans

```python
# In ModelRouter.call():
with tracer.span("model.call", {"task_type": task_type}) as span:
    span.set("model", chosen_model)
    span.set("provider", provider)
    result = provider.complete(...)
    span.set("tokens_used", result.usage.total_tokens)
    span.set("latency_ms", ...)
```

### Phase 5–9 — Tool call spans (via ActionGate.wrap)

The `ActionGate.wrap()` already wraps every tool. Add tracer to the wrapper:

```python
# In ActionGate.wrap():
def _gated(**kwargs):
    with tracer.span(f"tool.{tool_name}", {"args_preview": str(kwargs)[:100]}) as span:
        allowed, reason = self.check(tool_name, kwargs)
        if not allowed:
            span.set("blocked", True)
            span.set("reason", reason)
            return {"error": reason, "blocked_by": "ActionGate"}
        result = tool_fn(**kwargs)
        span.set("success", "error" not in result)
        return result
```

### Phase 12 — Subagent spans

```python
# In Subagent.run():
with tracer.span("subagent.run", {"role": self.role.name, "task": self.task[:50]}) as span:
    ...
    span.set("iterations", self.iterations)
    span.set("tokens_used", self.budget.tokens_used)
    span.set("discoveries", len(discoveries))
    span.set("success", success)
```

---

## FEATURE 15.3 — PROMETHEUS METRICS ENDPOINT

```python
class MetricsCollector:
    """
    Exposes Prometheus-compatible metrics at HTTP /metrics endpoint.
    Optional — only starts if observability.prometheus_port is set in config.
    """

    def __init__(self, tracer: NanobotTracer, port: int = 9090):
        self.tracer = tracer
        self.port = port

    def start(self):
        """Start HTTP server in background thread."""
        import http.server, threading

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

            def _format_prometheus(self, metrics: dict) -> str:
                lines = []
                lines.append(f'nanobot_total_tokens {metrics["total_tokens"]}')
                lines.append(f'nanobot_total_errors {metrics["errors"]}')
                lines.append(f'nanobot_session_elapsed_seconds {metrics.get("session_elapsed_seconds", 0)}')
                for tool, stats in metrics.get("tool_calls", {}).items():
                    lines.append(f'nanobot_tool_calls_total{{tool="{tool}"}} {stats["count"]}')
                return "\n".join(lines) + "\n"

            def log_message(self, *args): pass  # suppress access logs

        server = http.server.HTTPServer(("0.0.0.0", self.port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"Prometheus metrics at http://0.0.0.0:{self.port}/metrics")
```

---

## CONFIG KEYS TO ADD

```json
{
  "observability": {
    "enabled": true,
    "trace_file": "~/.nanobot/logs/traces.jsonl",
    "otlp_endpoint": "",
    "prometheus_port": 0,
    "sample_rate": 1.0,
    "include_tool_args": false,
    "include_memory_content": false
  }
}
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_observability.py`

```python
class TestSpan:
    def test_span_duration_calculated()
    def test_set_attribute()
    def test_add_event()
    def test_finish_sets_end_time()
    def test_to_dict_complete()

class TestNanobotTracer:
    def test_span_context_manager_exports_on_exit()
    def test_span_on_exception_sets_error_status()
    def test_nested_spans_set_parent_id()
    def test_disabled_tracer_returns_noop()
    def test_new_trace_generates_unique_id()
    def test_trace_fn_decorator_wraps_correctly()

class TestTraceExporter:
    def test_export_writes_to_jsonl()
    def test_export_non_blocking()
    def test_otlp_failure_non_fatal()

class TestSessionMetrics:
    def test_record_tool_call()
    def test_record_memory_op()
    def test_record_model_call()
    def test_error_count_increments()
    def test_summary_includes_all_fields()

class TestMetricsCollector:
    def test_prometheus_endpoint_responds()
    def test_health_endpoint_returns_ok()
    def test_metrics_format_valid_prometheus()

class TestNoOpSpan:
    def test_noop_methods_dont_raise()
```

---

## CROSS-REFERENCES

- **All Phases (1–14)**: Every phase adds `with tracer.span(...)` to its key operations. The `tracer` instance is created once at agent startup and passed down via dependency injection.
- **Phase 2** (AgentLoop): `tracer.new_trace()` is called at the start of every `process()` call to group all spans under one trace ID
- **Phase 4** (ModelRouter): Model call spans carry `model`, `provider`, `tokens_used`, `latency_ms` attributes
- **Phase 14** (Security): `ActionGate.wrap()` adds trace spans automatically to every tool call
- **Phase 16** (CLI): `nanobot metrics` and `nanobot traces` commands use `NanobotTracer.session_summary()` and read `traces.jsonl`

All canonical names are in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
