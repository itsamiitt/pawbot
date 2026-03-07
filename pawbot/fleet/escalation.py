"""ErrorEscalation — 6-level error classification and handling.

Phase 18: Classifies task failures into escalation levels and provides
appropriate handling actions for the FleetCommander.

Levels (lowest → highest severity):
  1. TRANSIENT    — Temporary failure (network, timeout) → auto-retry
  2. DEPENDENCY   — Upstream task failed → check and fix upstream
  3. VALIDATION   — Bad task spec → fix spec and re-assign
  4. RESOURCE     — Resource exhaustion → pause queue, investigate
  5. LOGIC        — Fundamental logic error → escalate to user
  6. CATASTROPHIC — Critical failure → halt all tasks, alert user
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("pawbot.fleet.escalation")


# Escalation level constants
LEVEL_TRANSIENT = "TRANSIENT"
LEVEL_DEPENDENCY = "DEPENDENCY"
LEVEL_VALIDATION = "VALIDATION"
LEVEL_RESOURCE = "RESOURCE"
LEVEL_LOGIC = "LOGIC"
LEVEL_CATASTROPHIC = "CATASTROPHIC"

# Ordered by severity
LEVELS = [
    LEVEL_TRANSIENT,
    LEVEL_DEPENDENCY,
    LEVEL_VALIDATION,
    LEVEL_RESOURCE,
    LEVEL_LOGIC,
    LEVEL_CATASTROPHIC,
]


@dataclass
class EscalationAction:
    """Describes what to do when an error is classified."""

    level: str
    action: str               # "auto_retry" | "check_upstream" | "fix_spec" | "pause_queue" | "escalate_user" | "halt_all"
    max_retries: int
    description: str
    should_alert_user: bool
    should_pause_fleet: bool


# Action definitions per level
LEVEL_ACTIONS: dict[str, EscalationAction] = {
    LEVEL_TRANSIENT: EscalationAction(
        level=LEVEL_TRANSIENT,
        action="auto_retry",
        max_retries=3,
        description="Temporary failure — automatic retry with backoff",
        should_alert_user=False,
        should_pause_fleet=False,
    ),
    LEVEL_DEPENDENCY: EscalationAction(
        level=LEVEL_DEPENDENCY,
        action="check_upstream",
        max_retries=2,
        description="Upstream dependency failed — check and fix upstream task",
        should_alert_user=False,
        should_pause_fleet=False,
    ),
    LEVEL_VALIDATION: EscalationAction(
        level=LEVEL_VALIDATION,
        action="fix_spec",
        max_retries=1,
        description="Invalid task specification — fix input and re-assign",
        should_alert_user=False,
        should_pause_fleet=False,
    ),
    LEVEL_RESOURCE: EscalationAction(
        level=LEVEL_RESOURCE,
        action="pause_queue",
        max_retries=0,
        description="Resource exhaustion — pause queue and investigate",
        should_alert_user=True,
        should_pause_fleet=True,
    ),
    LEVEL_LOGIC: EscalationAction(
        level=LEVEL_LOGIC,
        action="escalate_user",
        max_retries=0,
        description="Logic error — needs human guidance",
        should_alert_user=True,
        should_pause_fleet=False,
    ),
    LEVEL_CATASTROPHIC: EscalationAction(
        level=LEVEL_CATASTROPHIC,
        action="halt_all",
        max_retries=0,
        description="Critical failure — halt all tasks and alert user immediately",
        should_alert_user=True,
        should_pause_fleet=True,
    ),
}

# Pattern-based error classification heuristics
_TRANSIENT_PATTERNS = [
    r"timeout",
    r"timed?\s*out",
    r"connection\s*(refused|reset|closed)",
    r"temporary\s*(failure|error|unavailable)",
    r"rate\s*limit",
    r"429",
    r"503",
    r"502",
    r"500",
    r"ECONNREFUSED",
    r"ETIMEDOUT",
    r"network\s*(error|unreachable)",
    r"socket\s*hang\s*up",
    r"retry",
]

_RESOURCE_PATTERNS = [
    r"out\s*of\s*memory",
    r"oom",
    r"disk\s*(full|space)",
    r"no\s*space\s*left",
    r"memory\s*(error|exceeded|limit)",
    r"quota\s*(exceeded|limit)",
    r"ENOMEM",
    r"ENOSPC",
    r"resource\s*(exhausted|limit)",
]

_VALIDATION_PATTERNS = [
    r"invalid\s*(argument|parameter|input|format)",
    r"missing\s*(required|field|argument|parameter)",
    r"type\s*error",
    r"validation\s*(error|failed)",
    r"malformed",
    r"parse\s*error",
    r"schema\s*(violation|error)",
    r"json\s*(decode|parse)\s*error",
]

_CATASTROPHIC_PATTERNS = [
    r"database\s*(corrupt|locked|unrecoverable)",
    r"fatal\s*(error|exception)",
    r"unrecoverable",
    r"data\s*loss",
    r"segmentation\s*fault",
    r"kernel\s*panic",
    r"critical\s*system\s*error",
]


class ErrorEscalation:
    """Classifies errors and determines handling actions.

    Usage:
        escalation = ErrorEscalation()
        level = escalation.classify(error_message)
        action = escalation.get_action(level)
        if action.should_alert_user:
            notify_user(task, error_message)
    """

    def __init__(self) -> None:
        self._transient_re = re.compile(
            "|".join(_TRANSIENT_PATTERNS), re.IGNORECASE
        )
        self._resource_re = re.compile(
            "|".join(_RESOURCE_PATTERNS), re.IGNORECASE
        )
        self._validation_re = re.compile(
            "|".join(_VALIDATION_PATTERNS), re.IGNORECASE
        )
        self._catastrophic_re = re.compile(
            "|".join(_CATASTROPHIC_PATTERNS), re.IGNORECASE
        )

    def classify(
        self,
        error: str | Exception,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Classify an error into an escalation level.

        Uses pattern matching on the error message, with optional context
        hints (e.g., context={"upstream_failed": True}).

        Returns one of the LEVEL_* constants.
        """
        error_str = str(error).lower() if isinstance(error, Exception) else error.lower()
        ctx = context or {}

        # Check for catastrophic first (most severe)
        if self._catastrophic_re.search(error_str):
            return LEVEL_CATASTROPHIC

        # Check context hints
        if ctx.get("upstream_failed"):
            return LEVEL_DEPENDENCY

        # Check for resource exhaustion
        if self._resource_re.search(error_str):
            return LEVEL_RESOURCE

        # Check for validation errors
        if self._validation_re.search(error_str):
            return LEVEL_VALIDATION

        # Check for transient errors
        if self._transient_re.search(error_str):
            return LEVEL_TRANSIENT

        # Default: LOGIC (needs human review)
        return LEVEL_LOGIC

    def get_action(self, level: str) -> EscalationAction:
        """Get the handling action for an escalation level."""
        return LEVEL_ACTIONS.get(level, LEVEL_ACTIONS[LEVEL_LOGIC])

    def should_retry(self, level: str, retry_count: int) -> bool:
        """Check if a task should be retried based on level and current retry count."""
        action = self.get_action(level)
        return retry_count < action.max_retries

    def severity_rank(self, level: str) -> int:
        """Get numeric severity rank (0 = lowest, 5 = highest)."""
        try:
            return LEVELS.index(level)
        except ValueError:
            return len(LEVELS)  # Unknown levels are maximum severity

    def __repr__(self) -> str:
        return "ErrorEscalation(levels=6)"
