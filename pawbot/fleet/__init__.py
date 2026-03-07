"""Fleet Commander — multi-agent orchestration with DAG planning.

Phase 18: Distributed fleet management comprising:
  - TaskNode, WorkerSpec, FleetConfig    (dataclasses)
  - TaskDAG                              (directed acyclic graph for task planning)
  - CircuitBreaker                       (per-worker health monitoring)
  - FileInbox / FileOutbox               (durable message passing)
  - ErrorEscalation                      (6-level error classification)
  - FleetStatus                          (live status.json tracker)
  - FleetCommander                       (main orchestration engine)
"""

from pawbot.fleet.models import FleetConfig, TaskNode, WorkerSpec
from pawbot.fleet.dag import TaskDAG
from pawbot.fleet.circuit_breaker import CircuitBreaker
from pawbot.fleet.commander import FleetCommander

__all__ = [
    "FleetConfig",
    "TaskNode",
    "WorkerSpec",
    "TaskDAG",
    "CircuitBreaker",
    "FleetCommander",
]
