"""Tests for Phase 15 — Observability & Tracing.

Tests verify:
  - Span               (duration, attributes, events, finish, to_dict)
  - PawbotTracer      (span context manager, exception handling, nested spans,
                         disabled mode, new_trace, trace_fn decorator)
  - TraceExporter      (JSONL write, non-blocking, OTLP failure non-fatal)
  - SessionMetrics     (tool calls, memory ops, model calls, errors, summary)
  - MetricsCollector   (Prometheus /metrics endpoint, /health, format)
  - _NoOpSpan          (no-op methods don't raise)
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import urlopen

import pytest

# Ensure the pawbot package root is on sys.path.
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from pawbot.agent.telemetry import (
    MetricsCollector,
    PawbotTracer,
    SessionMetrics,
    Span,
    TraceExporter,
    _NoOpSpan,
)


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _free_port() -> int:
    """Find a free TCP port for the Prometheus test server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _make_tracer(tmp_path, enabled: bool = True) -> PawbotTracer:
    """Create a PawbotTracer with temp trace file."""
    trace_file = str(tmp_path / "traces.jsonl")
    return PawbotTracer(config={
        "observability": {
            "enabled": enabled,
            "trace_file": trace_file,
        }
    })


# ══════════════════════════════════════════════════════════════════════════════
#  TestSpan
# ══════════════════════════════════════════════════════════════════════════════


class TestSpan:

    def test_span_duration_calculated(self):
        """duration_ms is positive after span is finished."""
        s = Span(trace_id="t1", name="test")
        time.sleep(0.05)
        s.finish()
        assert s.duration_ms >= 40  # At least ~50ms minus scheduling jitter

    def test_set_attribute(self):
        """set() adds/updates attributes."""
        s = Span(trace_id="t1")
        s.set("key", "value")
        s.set("count", 42)
        assert s.attributes["key"] == "value"
        assert s.attributes["count"] == 42

    def test_add_event(self):
        """add_event() records timestamped events."""
        s = Span(trace_id="t1")
        s.add_event("checkpoint", {"step": 1})
        assert len(s.events) == 1
        assert s.events[0]["name"] == "checkpoint"
        assert "timestamp" in s.events[0]
        assert s.events[0]["attributes"]["step"] == 1

    def test_finish_sets_end_time(self):
        """finish() sets end_time and status."""
        s = Span(trace_id="t1")
        assert s.end_time is None
        s.finish(status="ok")
        assert s.end_time is not None
        assert s.status == "ok"

    def test_finish_error_records_error(self):
        """finish(status='error', error=msg) sets error attribute."""
        s = Span(trace_id="t1")
        s.finish(status="error", error="something broke")
        assert s.status == "error"
        assert s.attributes["error"] == "something broke"

    def test_to_dict_complete(self):
        """to_dict() returns all required fields."""
        s = Span(trace_id="t1", name="test.op")
        s.set("foo", "bar")
        s.add_event("evt")
        s.finish()
        d = s.to_dict()
        assert d["trace_id"] == "t1"
        assert d["name"] == "test.op"
        assert "duration_ms" in d
        assert "attributes" in d
        assert "events" in d
        assert d["status"] == "ok"

    def test_duration_before_finish(self):
        """duration_ms works even before finish() is called."""
        s = Span(trace_id="t1")
        time.sleep(0.01)
        assert s.duration_ms > 0


# ══════════════════════════════════════════════════════════════════════════════
#  TestPawbotTracer
# ══════════════════════════════════════════════════════════════════════════════


class TestPawbotTracer:

    def test_span_context_manager_exports_on_exit(self, tmp_path):
        """Span is exported to JSONL on context manager exit."""
        tracer = _make_tracer(tmp_path)
        with tracer.span("test.op", {"key": "val"}) as s:
            s.set("extra", True)

        # Wait for background export thread
        time.sleep(0.3)
        trace_file = tmp_path / "traces.jsonl"
        assert trace_file.exists()
        lines = trace_file.read_text().strip().split("\n")
        assert len(lines) >= 1
        span_data = json.loads(lines[0])
        assert span_data["name"] == "test.op"
        assert span_data["status"] == "ok"

    def test_span_on_exception_sets_error_status(self, tmp_path):
        """Span gets error status when exception is raised inside."""
        tracer = _make_tracer(tmp_path)
        with pytest.raises(ValueError):
            with tracer.span("test.fail") as s:
                raise ValueError("boom")

        time.sleep(0.3)
        trace_file = tmp_path / "traces.jsonl"
        lines = trace_file.read_text().strip().split("\n")
        span_data = json.loads(lines[0])
        assert span_data["status"] == "error"
        assert "boom" in span_data["attributes"].get("error", "")

    def test_nested_spans_set_parent_id(self, tmp_path):
        """Nested spans have their parent_id set to the outer span's span_id."""
        tracer = _make_tracer(tmp_path)
        with tracer.span("outer") as outer:
            outer_id = outer.span_id
            with tracer.span("inner") as inner:
                inner_parent = inner.parent_id

        assert inner_parent == outer_id

    def test_disabled_tracer_returns_noop(self, tmp_path):
        """Disabled tracer returns _NoOpSpan."""
        tracer = _make_tracer(tmp_path, enabled=False)
        with tracer.span("test.disabled") as s:
            assert isinstance(s, _NoOpSpan)
            # No-op methods should not raise
            s.set("key", "value")
            s.add_event("evt")
            s.finish()

    def test_new_trace_generates_unique_id(self, tmp_path):
        """new_trace() returns a unique UUID each time."""
        tracer = _make_tracer(tmp_path)
        id1 = tracer.new_trace()
        id2 = tracer.new_trace()
        assert id1 != id2
        assert len(id1) == 36  # UUID format

    def test_trace_fn_decorator_wraps_correctly(self, tmp_path):
        """trace_fn() decorator wraps a function with tracing."""
        tracer = _make_tracer(tmp_path)

        @tracer.trace_fn("tool.my_tool")
        def my_tool(x: int) -> dict:
            return {"result": x * 2}

        result = my_tool(5)
        assert result["result"] == 10
        assert my_tool.__name__ == "my_tool"

        # Verify span was exported
        time.sleep(0.3)
        trace_file = tmp_path / "traces.jsonl"
        lines = trace_file.read_text().strip().split("\n")
        span_data = json.loads(lines[-1])
        assert span_data["name"] == "tool.my_tool"

    def test_trace_fn_captures_error_results(self, tmp_path):
        """trace_fn() sets error status when function returns error dict."""
        tracer = _make_tracer(tmp_path)

        @tracer.trace_fn("tool.failing")
        def failing_tool() -> dict:
            return {"error": "something went wrong"}

        result = failing_tool()
        assert "error" in result

        time.sleep(0.3)
        trace_file = tmp_path / "traces.jsonl"
        lines = trace_file.read_text().strip().split("\n")
        span_data = json.loads(lines[-1])
        assert span_data["status"] == "error"

    def test_session_summary_returns_dict(self, tmp_path):
        """session_summary() returns a complete dict."""
        tracer = _make_tracer(tmp_path)
        summary = tracer.session_summary()
        assert "total_spans" in summary
        assert "total_tokens" in summary
        assert "session_elapsed_seconds" in summary


# ══════════════════════════════════════════════════════════════════════════════
#  TestTraceExporter
# ══════════════════════════════════════════════════════════════════════════════


class TestTraceExporter:

    def test_export_writes_to_jsonl(self, tmp_path):
        """export() writes span to JSONL file."""
        trace_file = str(tmp_path / "traces.jsonl")
        exporter = TraceExporter(trace_file)
        span = Span(trace_id="t1", name="test.export")
        span.finish()
        exporter.export_sync(span)

        with open(trace_file) as f:
            data = json.loads(f.readline())
        assert data["name"] == "test.export"
        assert data["trace_id"] == "t1"

    def test_export_non_blocking(self, tmp_path):
        """export() returns immediately (runs in background thread)."""
        trace_file = str(tmp_path / "traces.jsonl")
        exporter = TraceExporter(trace_file)
        span = Span(trace_id="t1", name="async.test")
        span.finish()

        start = time.time()
        exporter.export(span)
        elapsed = time.time() - start
        assert elapsed < 1.0  # Should return almost instantly

        # Wait for background thread
        time.sleep(0.5)
        assert (tmp_path / "traces.jsonl").exists()

    def test_otlp_failure_non_fatal(self, tmp_path):
        """OTLP export failure doesn't crash the exporter."""
        trace_file = str(tmp_path / "traces.jsonl")
        exporter = TraceExporter(trace_file, otlp_endpoint="http://localhost:99999/invalid")
        span = Span(trace_id="t1", name="otlp.fail")
        span.finish()

        # Should not raise
        exporter._write(span.to_dict())

        # Local file should still be written
        assert (tmp_path / "traces.jsonl").exists()

    def test_read_recent(self, tmp_path):
        """read_recent returns the most recent spans."""
        trace_file = str(tmp_path / "traces.jsonl")
        exporter = TraceExporter(trace_file)
        for i in range(5):
            span = Span(trace_id=f"t{i}", name=f"span.{i}")
            span.finish()
            exporter.export_sync(span)

        recent = exporter.read_recent(3)
        assert len(recent) == 3
        assert recent[-1]["name"] == "span.4"


# ══════════════════════════════════════════════════════════════════════════════
#  TestSessionMetrics
# ══════════════════════════════════════════════════════════════════════════════


class TestSessionMetrics:

    def test_record_tool_call(self):
        """Tool call spans are tracked by name."""
        metrics = SessionMetrics()
        span = Span(trace_id="t1", name="tool.server_run")
        span.finish()
        metrics.record(span)

        summary = metrics.summary()
        assert "server_run" in summary["tool_calls"]
        assert summary["tool_calls"]["server_run"]["count"] == 1

    def test_record_memory_op(self):
        """Memory operations are tracked."""
        metrics = SessionMetrics()

        save_span = Span(trace_id="t1", name="memory.save")
        save_span.finish()
        metrics.record(save_span)

        search_span = Span(trace_id="t1", name="memory.search")
        search_span.set("cache_hit", True)
        search_span.finish()
        metrics.record(search_span)

        summary = metrics.summary()
        assert summary["memory_ops"]["save"] == 1
        assert summary["memory_ops"]["search"] == 1
        assert summary["memory_ops"]["cache_hits"] == 1

    def test_record_model_call(self):
        """Model calls are tracked by model name."""
        metrics = SessionMetrics()
        span = Span(trace_id="t1", name="model.call")
        span.set("model", "claude-3-sonnet")
        span.set("tokens_used", 500)
        span.finish()
        metrics.record(span)

        summary = metrics.summary()
        assert "claude-3-sonnet" in summary["model_calls"]
        assert summary["model_calls"]["claude-3-sonnet"]["count"] == 1
        assert summary["model_calls"]["claude-3-sonnet"]["tokens"] == 500

    def test_error_count_increments(self):
        """Error spans increment the error counter."""
        metrics = SessionMetrics()
        span = Span(trace_id="t1", name="test.error")
        span.finish(status="error", error="fail")
        metrics.record(span)

        assert metrics.summary()["errors"] == 1

    def test_summary_includes_all_fields(self):
        """summary() includes all required fields."""
        metrics = SessionMetrics()
        s = metrics.summary()
        assert "total_spans" in s
        assert "total_tokens" in s
        assert "tool_calls" in s
        assert "memory_ops" in s
        assert "model_calls" in s
        assert "errors" in s
        assert "session_elapsed_seconds" in s
        assert "avg_tool_latency_ms" in s

    def test_avg_tool_latency(self):
        """Average tool latency is computed in summary."""
        metrics = SessionMetrics()
        for i in range(3):
            span = Span(trace_id="t1", name="tool.run")
            time.sleep(0.01)
            span.finish()
            metrics.record(span)

        summary = metrics.summary()
        assert "run" in summary["avg_tool_latency_ms"]
        assert summary["avg_tool_latency_ms"]["run"] > 0

    def test_reset_clears_metrics(self):
        """reset() brings metrics back to zero."""
        metrics = SessionMetrics()
        span = Span(trace_id="t1", name="tool.x")
        span.finish()
        metrics.record(span)
        assert metrics.summary()["total_spans"] == 1

        metrics.reset()
        assert metrics.summary()["total_spans"] == 0


# ══════════════════════════════════════════════════════════════════════════════
#  TestMetricsCollector
# ══════════════════════════════════════════════════════════════════════════════


class TestMetricsCollector:

    def test_prometheus_endpoint_responds(self, tmp_path):
        """Prometheus /metrics endpoint returns 200."""
        tracer = _make_tracer(tmp_path)
        port = _free_port()
        collector = MetricsCollector(tracer, port=port)
        collector.start()

        try:
            time.sleep(0.3)
            resp = urlopen(f"http://localhost:{port}/metrics", timeout=2)
            assert resp.status == 200
            body = resp.read().decode()
            assert "pawbot_total_spans" in body
        finally:
            collector.stop()

    def test_health_endpoint_returns_ok(self, tmp_path):
        """Health check endpoint returns 200 'ok'."""
        tracer = _make_tracer(tmp_path)
        port = _free_port()
        collector = MetricsCollector(tracer, port=port)
        collector.start()

        try:
            time.sleep(0.3)
            resp = urlopen(f"http://localhost:{port}/health", timeout=2)
            assert resp.status == 200
            assert resp.read() == b"ok"
        finally:
            collector.stop()

    def test_metrics_format_valid_prometheus(self, tmp_path):
        """Metrics output follows Prometheus exposition format."""
        tracer = _make_tracer(tmp_path)

        # Record some spans first
        with tracer.span("tool.my_tool", {"tokens_used": 100}) as s:
            pass
        time.sleep(0.1)

        port = _free_port()
        collector = MetricsCollector(tracer, port=port)
        collector.start()

        try:
            time.sleep(0.3)
            resp = urlopen(f"http://localhost:{port}/metrics", timeout=2)
            body = resp.read().decode()
            # Prometheus format: metric_name value
            lines = [l for l in body.strip().split("\n") if l]
            for line in lines:
                parts = line.split(" ", 1)
                assert len(parts) == 2, f"Invalid Prometheus line: {line}"
                # Value should be a number
                metric_name = parts[0].split("{")[0]  # Strip labels
                assert metric_name.startswith("pawbot_")
        finally:
            collector.stop()


# ══════════════════════════════════════════════════════════════════════════════
#  TestNoOpSpan
# ══════════════════════════════════════════════════════════════════════════════


class TestNoOpSpan:

    def test_noop_methods_dont_raise(self):
        """All _NoOpSpan methods are callable and don't raise."""
        noop = _NoOpSpan()
        noop.set("key", "value")
        noop.add_event("event_name")
        noop.finish()
        noop.finish(status="error", error="msg")
        assert noop.to_dict() == {}
        assert noop.duration_ms == 0.0


def test_summarize_spans_computes_slo_metrics():
    from pawbot.agent.telemetry import summarize_spans

    spans = [
        {"status": "ok", "duration_ms": 100, "attributes": {"channel": "telegram"}},
        {"status": "error", "duration_ms": 500, "attributes": {"channel": "telegram"}},
        {"status": "ok", "duration_ms": 200, "attributes": {"channel": "whatsapp"}},
    ]
    s = summarize_spans(spans)
    assert s["window_span_count"] == 3
    assert s["error_count"] == 1
    assert s["success_rate_pct"] == 66.67
    assert s["latency_p50_ms"] >= 100
    assert s["latency_p95_ms"] >= 200
    assert "telegram" in s["channel_delivery_success_pct"]


def test_summarize_trace_file_handles_missing_file(tmp_path):
    from pawbot.agent.telemetry import summarize_trace_file

    missing = str(tmp_path / "nope.jsonl")
    s = summarize_trace_file(missing)
    assert s["window_span_count"] == 0
    assert s["success_rate_pct"] == 100.0
