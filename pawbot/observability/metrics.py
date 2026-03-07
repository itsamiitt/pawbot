"""Application metrics — Prometheus-compatible format (Phase 7).

Provides lightweight, thread-safe metric primitives (Counter, Gauge, Histogram)
and a global MetricsRegistry singleton for tracking LLM, tool, session,
memory, and security metrics.

No external dependencies required — uses only stdlib threading.
"""

from __future__ import annotations

import threading
from typing import Any


# ── Metric Primitives ────────────────────────────────────────────────────────


class Counter:
    """Simple thread-safe counter metric."""

    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help = help_text
        self._value = 0
        self._lock = threading.Lock()

    def inc(self, amount: int = 1) -> None:
        with self._lock:
            self._value += amount

    @property
    def value(self) -> int:
        return self._value

    def reset(self) -> None:
        with self._lock:
            self._value = 0


class Histogram:
    """Simple histogram for latency tracking with percentile support."""

    def __init__(self, name: str, help_text: str, max_samples: int = 10000):
        self.name = name
        self.help = help_text
        self._max_samples = max_samples
        self._values: list[float] = []
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._values.append(value)
            if len(self._values) > self._max_samples:
                self._values = self._values[-self._max_samples // 2:]

    @property
    def summary(self) -> dict[str, float]:
        with self._lock:
            if not self._values:
                return {"count": 0, "sum": 0.0, "avg": 0.0, "p50": 0.0, "p99": 0.0}
            sorted_v = sorted(self._values)
            n = len(sorted_v)
            return {
                "count": n,
                "sum": sum(sorted_v),
                "avg": sum(sorted_v) / n,
                "p50": sorted_v[n // 2],
                "p99": sorted_v[min(int(n * 0.99), n - 1)],
            }

    def reset(self) -> None:
        with self._lock:
            self._values.clear()


class Gauge:
    """Simple thread-safe gauge metric."""

    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help = help_text
        self._value = 0.0
        self._lock = threading.Lock()

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value -= amount

    @property
    def value(self) -> float:
        return self._value


# ── Global Metrics Registry ─────────────────────────────────────────────────


class MetricsRegistry:
    """Central metrics registry for the application.

    Contains all application-wide metrics organized by subsystem.
    Thread-safe for concurrent access from multiple async tasks.
    """

    def __init__(self) -> None:
        # LLM metrics
        self.llm_calls = Counter("pawbot_llm_calls_total", "Total LLM API calls")
        self.llm_errors = Counter("pawbot_llm_errors_total", "Total LLM API errors")
        self.llm_latency = Histogram("pawbot_llm_latency_ms", "LLM call latency in ms")
        self.llm_tokens_in = Counter("pawbot_llm_tokens_input", "Total input tokens")
        self.llm_tokens_out = Counter("pawbot_llm_tokens_output", "Total output tokens")

        # Agent metrics
        self.messages_processed = Counter("pawbot_messages_total", "Total messages processed")
        self.tool_calls = Counter("pawbot_tool_calls_total", "Total tool executions")
        self.tool_errors = Counter("pawbot_tool_errors_total", "Total tool errors")
        self.tool_latency = Histogram("pawbot_tool_latency_ms", "Tool execution latency in ms")

        # Session metrics
        self.active_sessions = Gauge("pawbot_active_sessions", "Current active sessions")

        # Memory metrics
        self.memory_saves = Counter("pawbot_memory_saves_total", "Total memory saves")
        self.memory_searches = Counter("pawbot_memory_searches_total", "Total memory searches")

        # Security metrics
        self.security_blocks = Counter("pawbot_security_blocks_total", "Total security blocks")
        self.injections_detected = Counter("pawbot_injections_total", "Injection attempts detected")

        # Fleet metrics
        self.fleet_tasks_completed = Counter("pawbot_fleet_tasks_completed", "Fleet tasks completed")
        self.fleet_tasks_failed = Counter("pawbot_fleet_tasks_failed", "Fleet tasks failed")

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text exposition format.

        Compatible with Prometheus scraping at /metrics endpoint.
        """
        lines: list[str] = []
        for attr_name in sorted(dir(self)):
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
        """Export as JSON-friendly dict for dashboard API."""
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
                "tool_latency": self.tool_latency.summary,
            },
            "sessions": {"active": self.active_sessions.value},
            "memory": {
                "saves": self.memory_saves.value,
                "searches": self.memory_searches.value,
            },
            "security": {
                "blocks": self.security_blocks.value,
                "injections": self.injections_detected.value,
            },
            "fleet": {
                "tasks_completed": self.fleet_tasks_completed.value,
                "tasks_failed": self.fleet_tasks_failed.value,
            },
        }


# ── Singleton ────────────────────────────────────────────────────────────────

metrics = MetricsRegistry()
