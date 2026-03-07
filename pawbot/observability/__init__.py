"""Structured logging configuration (Phase 7).

Provides structured JSON logging alongside the existing loguru logger.
Supports both human-readable console output and machine-parseable JSON lines.
"""

from __future__ import annotations

# IMPORTANT: import stdlib logging before our logging.py can shadow it
import importlib
_logging = importlib.import_module("logging")
import sys
from typing import Any


# ── Lightweight structured logger (no external dependency required) ──────────


class StructuredLogger:
    """Structured event logger that outputs JSON lines.

    Usage:
        slog = get_logger("agent.loop")
        slog.info("llm_call", model="claude-sonnet", tokens=1500, latency_ms=234)
    """

    def __init__(self, name: str = "pawbot", json_output: bool = False):
        self.name = name
        self.json_output = json_output
        self._stdlib_logger = _logging.getLogger(name)

    def _emit(self, level: str, event: str, **kwargs: Any) -> None:
        """Emit a structured log event."""
        import json
        import time

        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": level,
            "logger": self.name,
            "event": event,
            **kwargs,
        }

        if self.json_output:
            line = json.dumps(record, default=str)
        else:
            extras = " ".join(f"{k}={v}" for k, v in kwargs.items())
            line = f"[{record['ts']}] {level.upper():7s} {self.name} | {event} {extras}"

        self._stdlib_logger.log(
            getattr(_logging, level.upper(), _logging.INFO), line
        )

    def debug(self, event: str, **kwargs: Any) -> None:
        self._emit("debug", event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._emit("info", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._emit("warning", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._emit("error", event, **kwargs)


def configure_logging(json_output: bool = False, level: str = "INFO") -> None:
    """Configure structured logging for the application.

    Args:
        json_output: If True, output JSON lines (for production).
                     If False, output human-readable format.
        level: Log level string.
    """
    # Try structlog if available (recommended for production)
    try:
        import structlog

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
                getattr(_logging, level.upper(), _logging.INFO)
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
        return
    except ImportError:
        pass

    # Fallback: configure stdlib logging
    _logging.basicConfig(
        level=getattr(_logging, level.upper(), _logging.INFO),
        format="%(message)s",
        stream=sys.stderr,
    )


def get_logger(name: str = "pawbot") -> StructuredLogger:
    """Get a structured logger instance.

    If structlog is installed, returns a structlog logger.
    Otherwise, returns our built-in StructuredLogger.
    """
    try:
        import structlog
        return structlog.get_logger(name)
    except ImportError:
        return StructuredLogger(name)
