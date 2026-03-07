"""FleetStatus — live status.json tracker.

Phase 18: Maintains a real-time status file at shared/status.json
that records the fleet state: workers, tasks, DAG, circuit breakers,
and execution log.

The file is updated on every state change and can be read by the
dashboard, CLI, and external monitoring tools.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from pawbot.fleet.circuit_breaker import CircuitBreaker
from pawbot.fleet.dag import TaskDAG
from pawbot.fleet.models import FleetConfig, FleetSnapshot, WorkerSpec

logger = logging.getLogger("pawbot.fleet.status")


class FleetStatus:
    """Tracks and persists fleet state to status.json.

    Thread-safe: all writes go through _write() which uses atomic
    file operations.
    """

    def __init__(
        self,
        status_path: Path,
        config: FleetConfig | None = None,
    ) -> None:
        self.status_path = status_path
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.config = config or FleetConfig()
        self._execution_log: list[dict[str, Any]] = []
        self._max_log_entries = 100

    def update(
        self,
        workers: dict[str, WorkerSpec],
        dag: TaskDAG,
        circuit_breaker: CircuitBreaker,
        extra: dict[str, Any] | None = None,
    ) -> FleetSnapshot:
        """Generate and persist a full fleet snapshot."""
        snapshot = FleetSnapshot(
            timestamp=time.time(),
            workers={
                wid: {
                    **spec.to_dict(),
                    "circuit": circuit_breaker.state(wid),
                    "last_seen": time.time(),
                }
                for wid, spec in workers.items()
            },
            tasks=[t.to_dict() for t in dag.tasks],
            dag_mermaid=dag.to_mermaid(),
            active_task_count=dag.running_count,
            completed_task_count=dag.done_count,
            failed_task_count=dag.failed_count,
        )

        self._write(snapshot, extra)
        return snapshot

    def log_event(self, event_type: str, detail: str, **kwargs: Any) -> None:
        """Add an entry to the execution log."""
        entry = {
            "timestamp": time.time(),
            "event": event_type,
            "detail": detail,
            **kwargs,
        }
        self._execution_log.append(entry)
        if len(self._execution_log) > self._max_log_entries:
            self._execution_log = self._execution_log[-self._max_log_entries:]

    def read(self) -> dict[str, Any] | None:
        """Read the current status from disk."""
        if not self.status_path.exists():
            return None
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read status.json: %s", exc)
            return None

    def _write(
        self, snapshot: FleetSnapshot, extra: dict[str, Any] | None = None
    ) -> None:
        """Atomically write status to disk."""
        data = {
            "version": "2.0",
            "fleet": snapshot.to_dict(),
            "config": self.config.to_dict(),
            "execution_log": self._execution_log[-20:],  # Last 20 entries
        }
        if extra:
            data["extra"] = extra

        content = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        tmp_path = self.status_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(self.status_path)
        except OSError as exc:
            logger.error("Failed to write status.json: %s", exc)

    def clear(self) -> None:
        """Remove the status file."""
        self.status_path.unlink(missing_ok=True)
        self._execution_log.clear()
