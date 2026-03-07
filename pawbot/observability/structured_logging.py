"""Structured logging configuration (Phase 7).

Re-exports from the observability package for convenient imports:
    from pawbot.observability.structured_logging import get_logger, configure_logging
"""

from pawbot.observability import configure_logging, get_logger

__all__ = ["configure_logging", "get_logger"]
