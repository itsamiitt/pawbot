"""Structured logging re-exports (Phase 7).

This module provides the import path from the Phase 7 plan:
    from pawbot.observability.logging import get_logger, configure_logging

NOTE: This file shadows stdlib 'logging' within this package, so the
__init__.py uses 'import logging as _logging' before this is imported.
"""

# Import from the non-shadowing module to avoid circular issues
from pawbot.observability.structured_logging import configure_logging, get_logger

__all__ = ["configure_logging", "get_logger"]
