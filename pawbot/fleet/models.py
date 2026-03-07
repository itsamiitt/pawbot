"""Fleet data models — dataclasses for fleet orchestration.

Phase 18 models:
  - WorkerSpec     (worker definition)
  - TaskNode       (single task in a DAG)
  - TaskResult     (completed task output)
  - FleetConfig    (global fleet configuration)
  - FleetSnapshot  (serialisable fleet state for status.json)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ══════════════════════════════════════════════════════════════════════════════
#  Worker Specification
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class WorkerSpec:
    """Defines a worker agent in the fleet."""

    id: str                                  # "worker-1", "worker-2", etc.
    role: str                                # "coder", "scout", "guardian", "analyst"
    workspace: Path                          # Isolated workspace directory
    system_prompt: str = ""                  # Role-specific system prompt
    model_preference: str = "sonnet"         # Preferred model tier
    max_concurrent_tasks: int = 3            # Max tasks this worker handles
    heartbeat_interval: int = 30             # Seconds between health checks
    allowed_tools: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "workspace": str(self.workspace),
            "model_preference": self.model_preference,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "heartbeat_interval": self.heartbeat_interval,
            "description": self.description,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Task Node
# ══════════════════════════════════════════════════════════════════════════════


# Task status constants
TASK_PENDING = "pending"
TASK_QUEUED = "queued"
TASK_RUNNING = "running"
TASK_DONE = "done"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"
TASK_BLOCKED = "blocked"

# Error class constants
ERROR_TRANSIENT = "TRANSIENT"
ERROR_DEPENDENCY = "DEPENDENCY"
ERROR_VALIDATION = "VALIDATION"
ERROR_RESOURCE = "RESOURCE"
ERROR_LOGIC = "LOGIC"
ERROR_CATASTROPHIC = "CATASTROPHIC"


@dataclass
class TaskNode:
    """Single task in the execution DAG."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    title: str = ""
    description: str = ""
    assigned_to: str | None = None           # worker_id
    depends_on: list[str] = field(default_factory=list)   # task_ids
    status: str = TASK_PENDING
    priority: int = 5                        # 1 (highest) to 10 (lowest)
    retry_count: int = 0
    max_retries: int = 3
    timeout_seconds: int = 300
    error_class: str = ""
    error_message: str = ""
    output: str = ""                         # Result text
    output_path: str = ""                    # File path where result is written
    started_at: float = 0.0
    finished_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_seconds(self) -> float:
        if self.started_at == 0:
            return 0.0
        end = self.finished_at if self.finished_at > 0 else time.time()
        return end - self.started_at

    @property
    def is_terminal(self) -> bool:
        """True if this task is in a final state."""
        return self.status in (TASK_DONE, TASK_FAILED, TASK_CANCELLED)

    @property
    def can_retry(self) -> bool:
        return self.retry_count < self.max_retries

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description[:200],
            "assigned_to": self.assigned_to,
            "depends_on": self.depends_on,
            "status": self.status,
            "priority": self.priority,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "error_class": self.error_class,
            "error_message": self.error_message[:200] if self.error_message else "",
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "tags": self.tags,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Task Result
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class TaskResult:
    """Completed task output from a worker."""

    task_id: str
    worker_id: str
    success: bool
    output: str = ""
    error: str = ""
    error_class: str = ""
    elapsed_seconds: float = 0.0
    discoveries: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)    # file paths produced

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "success": self.success,
            "output": self.output[:500],
            "error": self.error[:200] if self.error else "",
            "error_class": self.error_class,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "artifacts": self.artifacts,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Fleet Configuration
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class FleetConfig:
    """Global fleet configuration."""

    max_workers: int = 4                     # main + 3 workers (matches OpenClaw)
    max_concurrent_tasks: int = 8            # Total across fleet
    subagent_max_concurrent: int = 12        # Subagents spawned by workers
    default_timeout: int = 300               # Seconds
    default_max_retries: int = 3
    inbox_dir: str = "shared/inbox"
    outbox_dir: str = "shared/outbox"
    status_file: str = "shared/status.json"
    poll_interval: float = 2.0              # Seconds between outbox polls
    circuit_breaker_threshold: int = 3       # Failures before opening circuit
    circuit_breaker_cooldown: int = 300      # Seconds before half-open probe

    # Worker defaults
    default_workers: list[dict[str, Any]] = field(default_factory=lambda: [
        {
            "id": "worker-1",
            "role": "coder",
            "description": "Code implementation, debugging, testing",
        },
        {
            "id": "worker-2",
            "role": "scout",
            "description": "Research, data gathering, API exploration",
        },
        {
            "id": "worker-3",
            "role": "guardian",
            "description": "Code review, QA, security checks, verification",
        },
    ])

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_workers": self.max_workers,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "subagent_max_concurrent": self.subagent_max_concurrent,
            "default_timeout": self.default_timeout,
            "poll_interval": self.poll_interval,
            "circuit_breaker_threshold": self.circuit_breaker_threshold,
            "circuit_breaker_cooldown": self.circuit_breaker_cooldown,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Fleet Snapshot (for status.json)
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class FleetSnapshot:
    """Serialisable snapshot of fleet state for status.json."""

    timestamp: float = field(default_factory=time.time)
    workers: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    dag_mermaid: str = ""
    active_task_count: int = 0
    completed_task_count: int = 0
    failed_task_count: int = 0
    total_elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "workers": self.workers,
            "tasks": self.tasks,
            "dag_mermaid": self.dag_mermaid,
            "active_task_count": self.active_task_count,
            "completed_task_count": self.completed_task_count,
            "failed_task_count": self.failed_task_count,
            "total_elapsed_seconds": round(self.total_elapsed_seconds, 2),
        }
