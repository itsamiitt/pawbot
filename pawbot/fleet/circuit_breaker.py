"""CircuitBreaker — per-worker health monitoring.

Phase 18: Three-state circuit breaker pattern:
  - CLOSED   → Normal operation, tasks flow freely
  - OPEN     → Worker failing, no new tasks, begin cooldown
  - HALF_OPEN → Cooldown elapsed, send one probe task to check recovery

Transitions:
  CLOSED  → record_failure() × threshold  → OPEN
  OPEN    → cooldown elapsed              → HALF_OPEN
  HALF_OPEN → probe succeeds              → CLOSED
  HALF_OPEN → probe fails                 → OPEN (reset cooldown)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("pawbot.fleet.circuit_breaker")


# State constants
CB_CLOSED = "closed"
CB_OPEN = "open"
CB_HALF_OPEN = "half-open"


@dataclass
class CircuitBreakerRecord:
    """Tracks the state history of a single circuit breaker."""

    worker_id: str
    state: str = CB_CLOSED
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_failure_at: float = 0.0
    last_success_at: float = 0.0
    opened_at: float = 0.0                # When circuit was opened
    last_probe_at: float = 0.0            # When last probe was sent

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "last_failure_at": self.last_failure_at,
            "last_success_at": self.last_success_at,
        }


class CircuitBreaker:
    """Per-worker circuit breaker with automatic state transitions.

    Usage:
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=300)

        # Before sending a task:
        if cb.can_accept_task("worker-1"):
            send_task(task)
        else:
            reassign_to_another_worker(task)

        # After task completes:
        if success:
            cb.record_success("worker-1")
        else:
            cb.record_failure("worker-1")
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: int = 300,
        success_threshold: int = 2,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.success_threshold = success_threshold    # Successes needed in HALF_OPEN to close
        self._breakers: dict[str, CircuitBreakerRecord] = {}

    def _get_or_create(self, worker_id: str) -> CircuitBreakerRecord:
        if worker_id not in self._breakers:
            self._breakers[worker_id] = CircuitBreakerRecord(worker_id=worker_id)
        return self._breakers[worker_id]

    # ── State Queries ────────────────────────────────────────────────────────

    def state(self, worker_id: str) -> str:
        """Get the current circuit breaker state for a worker."""
        record = self._get_or_create(worker_id)
        self._check_cooldown(record)
        return record.state

    def can_accept_task(self, worker_id: str) -> bool:
        """True if this worker can accept a new task.

        CLOSED:    True
        OPEN:      False (unless cooldown elapsed → transitions to HALF_OPEN)
        HALF_OPEN: True (one probe task allowed)
        """
        record = self._get_or_create(worker_id)
        self._check_cooldown(record)
        return record.state in (CB_CLOSED, CB_HALF_OPEN)

    def is_healthy(self, worker_id: str) -> bool:
        """True if the worker is in the CLOSED (healthy) state."""
        return self.state(worker_id) == CB_CLOSED

    # ── State Transitions ────────────────────────────────────────────────────

    def record_success(self, worker_id: str) -> None:
        """Record a successful task completion for this worker."""
        record = self._get_or_create(worker_id)
        record.consecutive_successes += 1
        record.consecutive_failures = 0
        record.total_successes += 1
        record.last_success_at = time.time()

        if record.state == CB_HALF_OPEN:
            if record.consecutive_successes >= self.success_threshold:
                record.state = CB_CLOSED
                record.opened_at = 0.0
                logger.info(
                    "Circuit breaker CLOSED for %s (recovered after %d successes)",
                    worker_id, record.consecutive_successes,
                )

    def record_failure(self, worker_id: str) -> None:
        """Record a task failure for this worker."""
        record = self._get_or_create(worker_id)
        record.consecutive_failures += 1
        record.consecutive_successes = 0
        record.total_failures += 1
        record.last_failure_at = time.time()

        if record.state == CB_CLOSED:
            if record.consecutive_failures >= self.failure_threshold:
                record.state = CB_OPEN
                record.opened_at = time.time()
                logger.warning(
                    "Circuit breaker OPEN for %s (failed %d times consecutively)",
                    worker_id, record.consecutive_failures,
                )

        elif record.state == CB_HALF_OPEN:
            # Probe failed — back to OPEN
            record.state = CB_OPEN
            record.opened_at = time.time()
            record.consecutive_successes = 0
            logger.warning(
                "Circuit breaker re-OPENED for %s (probe failed)",
                worker_id,
            )

    def _check_cooldown(self, record: CircuitBreakerRecord) -> None:
        """Transition from OPEN → HALF_OPEN if cooldown has elapsed."""
        if record.state == CB_OPEN and record.opened_at > 0:
            elapsed = time.time() - record.opened_at
            if elapsed >= self.cooldown_seconds:
                record.state = CB_HALF_OPEN
                record.consecutive_successes = 0
                record.last_probe_at = time.time()
                logger.info(
                    "Circuit breaker HALF-OPEN for %s (cooldown %.0fs elapsed)",
                    record.worker_id, elapsed,
                )

    # ── Manual Controls ──────────────────────────────────────────────────────

    def force_open(self, worker_id: str) -> None:
        """Manually force the circuit breaker open."""
        record = self._get_or_create(worker_id)
        record.state = CB_OPEN
        record.opened_at = time.time()
        logger.info("Circuit breaker manually OPENED for %s", worker_id)

    def force_close(self, worker_id: str) -> None:
        """Manually close the circuit breaker (reset to healthy)."""
        record = self._get_or_create(worker_id)
        record.state = CB_CLOSED
        record.consecutive_failures = 0
        record.consecutive_successes = 0
        record.opened_at = 0.0
        logger.info("Circuit breaker manually CLOSED for %s", worker_id)

    def reset(self, worker_id: str) -> None:
        """Fully reset a worker's circuit breaker history."""
        self._breakers.pop(worker_id, None)

    # ── Fleet-wide Queries ───────────────────────────────────────────────────

    def all_states(self) -> dict[str, str]:
        """Get circuit breaker states for all known workers."""
        return {wid: self.state(wid) for wid in self._breakers}

    def healthy_workers(self) -> list[str]:
        """Get IDs of workers in CLOSED (healthy) state."""
        return [wid for wid in self._breakers if self.is_healthy(wid)]

    def unhealthy_workers(self) -> list[str]:
        """Get IDs of workers NOT in CLOSED state."""
        return [wid for wid in self._breakers if not self.is_healthy(wid)]

    def fleet_health(self) -> dict[str, Any]:
        """Get a summary of fleet health."""
        states = self.all_states()
        return {
            "total_workers": len(states),
            "healthy": sum(1 for s in states.values() if s == CB_CLOSED),
            "degraded": sum(1 for s in states.values() if s == CB_HALF_OPEN),
            "offline": sum(1 for s in states.values() if s == CB_OPEN),
            "workers": {
                wid: self._breakers[wid].to_dict()
                for wid in self._breakers
            },
        }

    def __repr__(self) -> str:
        states = self.all_states()
        return (
            f"CircuitBreaker("
            f"healthy={sum(1 for s in states.values() if s == CB_CLOSED)}, "
            f"degraded={sum(1 for s in states.values() if s == CB_HALF_OPEN)}, "
            f"offline={sum(1 for s in states.values() if s == CB_OPEN)})"
        )
