"""TaskDAG — Directed Acyclic Graph for fleet task planning.

Phase 18: DAG-based task decomposition with:
  - Cycle detection (validates no circular dependencies)
  - Topological sort (correct execution order)
  - Parallel group extraction (fan-out opportunities)
  - Ready-task identification (all deps satisfied)
  - Mermaid diagram export (for dashboard visualisation)
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Any

from pawbot.fleet.models import (
    TASK_BLOCKED,
    TASK_CANCELLED,
    TASK_DONE,
    TASK_FAILED,
    TASK_PENDING,
    TASK_QUEUED,
    TASK_RUNNING,
    TaskNode,
)

logger = logging.getLogger("pawbot.fleet.dag")


class CycleDetectedError(Exception):
    """Raised when a dependency cycle is found in the DAG."""

    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__(f"Dependency cycle detected: {' → '.join(cycle)}")


class TaskDAG:
    """Directed Acyclic Graph for task planning and execution ordering.

    Manages task dependencies, validates acyclicity, and provides
    execution ordering APIs for the FleetCommander.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskNode] = {}
        self._edges: dict[str, set[str]] = defaultdict(set)     # task_id → set of dependency task_ids
        self._reverse: dict[str, set[str]] = defaultdict(set)   # task_id → set of dependent task_ids

    # ── Task Management ─────────────────────────────────────────────────────

    def add_task(self, task: TaskNode) -> None:
        """Add a task to the DAG.

        If the task has depends_on entries, edges are created automatically.
        Raises CycleDetectedError if adding this task creates a cycle.
        """
        self._tasks[task.id] = task
        for dep_id in task.depends_on:
            self.add_dependency(task.id, dep_id)

    def add_tasks(self, tasks: list[TaskNode]) -> None:
        """Add multiple tasks. Validates the full DAG after all are added."""
        for task in tasks:
            self._tasks[task.id] = task
            for dep_id in task.depends_on:
                self._edges[task.id].add(dep_id)
                self._reverse[dep_id].add(task.id)
        # Validate after all tasks are added
        valid, problems = self.validate()
        if not valid:
            raise CycleDetectedError(problems)

    def remove_task(self, task_id: str) -> TaskNode | None:
        """Remove a task and all its edges."""
        task = self._tasks.pop(task_id, None)
        if task is None:
            return None
        # Remove outgoing edges
        for dep_id in self._edges.pop(task_id, set()):
            self._reverse[dep_id].discard(task_id)
        # Remove incoming edges (other tasks depending on this one)
        for dependent_id in self._reverse.pop(task_id, set()):
            self._edges[dependent_id].discard(task_id)
        return task

    def get_task(self, task_id: str) -> TaskNode | None:
        return self._tasks.get(task_id)

    @property
    def tasks(self) -> list[TaskNode]:
        return list(self._tasks.values())

    @property
    def task_count(self) -> int:
        return len(self._tasks)

    # ── Dependency Management ────────────────────────────────────────────────

    def add_dependency(self, task_id: str, depends_on_id: str) -> None:
        """Add a dependency: task_id depends on depends_on_id.

        Raises CycleDetectedError if this would create a cycle.
        """
        # Check for self-dependency
        if task_id == depends_on_id:
            raise CycleDetectedError([task_id, task_id])

        # Temporarily add edge and check for cycles
        self._edges[task_id].add(depends_on_id)
        self._reverse[depends_on_id].add(task_id)

        if self._has_cycle():
            # Rollback
            self._edges[task_id].discard(depends_on_id)
            self._reverse[depends_on_id].discard(task_id)
            raise CycleDetectedError([depends_on_id, task_id, depends_on_id])

    def get_dependencies(self, task_id: str) -> list[str]:
        """Get IDs of tasks that task_id depends on."""
        return list(self._edges.get(task_id, set()))

    def get_dependents(self, task_id: str) -> list[str]:
        """Get IDs of tasks that depend on task_id."""
        return list(self._reverse.get(task_id, set()))

    # ── Validation ───────────────────────────────────────────────────────────

    def validate(self) -> tuple[bool, list[str]]:
        """Validate the DAG is acyclic and all dependencies reference existing tasks.

        Returns (is_valid, list_of_problems).
        """
        problems: list[str] = []

        # Check for missing dependency targets
        for task_id, deps in self._edges.items():
            for dep_id in deps:
                if dep_id not in self._tasks:
                    problems.append(
                        f"Task '{task_id}' depends on '{dep_id}' which does not exist"
                    )

        # Check for cycles using Kahn's algorithm
        if self._has_cycle():
            problems.append("Dependency cycle detected in task graph")

        return len(problems) == 0, problems

    def _has_cycle(self) -> bool:
        """Detect cycles using Kahn's algorithm (topological sort attempt)."""
        if not self._tasks:
            return False

        in_degree: dict[str, int] = {tid: 0 for tid in self._tasks}
        for task_id, deps in self._edges.items():
            if task_id in in_degree:
                in_degree[task_id] = len(deps & set(self._tasks.keys()))

        queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
        visited = 0

        while queue:
            node = queue.popleft()
            visited += 1
            for dependent in self._reverse.get(node, set()):
                if dependent in in_degree:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        return visited != len(self._tasks)

    # ── Execution Ordering ───────────────────────────────────────────────────

    def topological_sort(self) -> list[TaskNode]:
        """Return tasks in topological order (dependencies first).

        Raises CycleDetectedError if the graph has cycles.
        """
        if self._has_cycle():
            raise CycleDetectedError(["cycle detected during topological sort"])

        in_degree: dict[str, int] = {tid: 0 for tid in self._tasks}
        for task_id, deps in self._edges.items():
            if task_id in in_degree:
                in_degree[task_id] = len(deps & set(self._tasks.keys()))

        queue = deque(
            sorted(
                (tid for tid, deg in in_degree.items() if deg == 0),
                key=lambda tid: self._tasks[tid].priority,
            )
        )
        result: list[TaskNode] = []

        while queue:
            node = queue.popleft()
            result.append(self._tasks[node])
            dependents = sorted(
                self._reverse.get(node, set()),
                key=lambda tid: self._tasks[tid].priority if tid in self._tasks else 10,
            )
            for dependent in dependents:
                if dependent in in_degree:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        return result

    def get_ready_tasks(self) -> list[TaskNode]:
        """Get tasks whose dependencies are all in a terminal-success state.

        These tasks can be dispatched to workers immediately.
        """
        ready: list[TaskNode] = []
        for task in self._tasks.values():
            if task.status not in (TASK_PENDING, TASK_QUEUED):
                continue

            deps = self._edges.get(task.id, set())
            if not deps:
                ready.append(task)
                continue

            all_deps_done = all(
                self._tasks.get(dep_id) is not None
                and self._tasks[dep_id].status == TASK_DONE
                for dep_id in deps
            )
            any_dep_failed = any(
                self._tasks.get(dep_id) is not None
                and self._tasks[dep_id].status in (TASK_FAILED, TASK_CANCELLED)
                for dep_id in deps
            )

            if any_dep_failed:
                task.status = TASK_BLOCKED
                logger.warning(
                    "Task %s blocked: dependency failed or cancelled", task.id
                )
            elif all_deps_done:
                ready.append(task)

        # Sort by priority (lower = higher priority)
        return sorted(ready, key=lambda t: t.priority)

    def parallel_groups(self) -> list[list[TaskNode]]:
        """Extract groups of tasks that can run in parallel.

        Each group contains tasks at the same "depth" in the DAG.
        Tasks within a group have no dependencies on each other.
        """
        if not self._tasks:
            return []

        sorted_tasks = self.topological_sort()
        # Calculate depth of each task
        depths: dict[str, int] = {}
        for task in sorted_tasks:
            deps = self._edges.get(task.id, set())
            if not deps:
                depths[task.id] = 0
            else:
                depths[task.id] = max(
                    depths.get(dep_id, 0) for dep_id in deps
                    if dep_id in depths
                ) + 1

        # Group by depth
        groups: dict[int, list[TaskNode]] = defaultdict(list)
        for task in sorted_tasks:
            groups[depths[task.id]].append(task)

        return [groups[d] for d in sorted(groups.keys())]

    # ── State Updates ────────────────────────────────────────────────────────

    def mark_running(self, task_id: str) -> None:
        """Mark a task as currently executing."""
        task = self._tasks.get(task_id)
        if task:
            task.status = TASK_RUNNING
            import time
            task.started_at = time.time()

    def mark_complete(self, task_id: str, output: str = "") -> None:
        """Mark a task as successfully completed."""
        task = self._tasks.get(task_id)
        if task:
            task.status = TASK_DONE
            task.output = output
            import time
            task.finished_at = time.time()
            logger.info("Task %s completed (%.1fs)", task_id, task.elapsed_seconds)

    def mark_failed(
        self, task_id: str, error: str = "", error_class: str = ""
    ) -> None:
        """Mark a task as failed with error details."""
        task = self._tasks.get(task_id)
        if task:
            task.status = TASK_FAILED
            task.error_message = error
            task.error_class = error_class
            import time
            task.finished_at = time.time()
            task.retry_count += 1
            logger.warning(
                "Task %s failed (%s): %s", task_id, error_class, error[:100]
            )

    def mark_cancelled(self, task_id: str) -> None:
        """Mark a task as cancelled."""
        task = self._tasks.get(task_id)
        if task:
            task.status = TASK_CANCELLED
            import time
            task.finished_at = time.time()

    def reset_for_retry(self, task_id: str) -> bool:
        """Reset a failed task for retry. Returns False if max retries exceeded."""
        task = self._tasks.get(task_id)
        if not task or not task.can_retry:
            return False
        task.status = TASK_PENDING
        task.error_message = ""
        task.error_class = ""
        task.started_at = 0.0
        task.finished_at = 0.0
        task.output = ""
        return True

    # ── Statistics ───────────────────────────────────────────────────────────

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TASK_PENDING)

    @property
    def running_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TASK_RUNNING)

    @property
    def done_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TASK_DONE)

    @property
    def failed_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TASK_FAILED)

    @property
    def all_complete(self) -> bool:
        """True if every task is in a terminal state."""
        return all(t.is_terminal for t in self._tasks.values())

    @property
    def all_successful(self) -> bool:
        """True if every task completed successfully."""
        return all(t.status == TASK_DONE for t in self._tasks.values())

    # ── Visualisation ────────────────────────────────────────────────────────

    def to_mermaid(self) -> str:
        """Export the DAG as a Mermaid flowchart diagram.

        Status is encoded in node shapes:
          - pending:   [task]
          - running:   ([task])
          - done:      [[task]]
          - failed:    {{task}}
          - cancelled: >task]
        """
        if not self._tasks:
            return "graph TD\n    empty[No tasks]"

        lines = ["graph TD"]
        status_shapes = {
            TASK_PENDING:   ("[{label}]", ""),
            TASK_QUEUED:    ("[{label}]", ""),
            TASK_RUNNING:   ("([{label}])", "fill:#f6d55c"),
            TASK_DONE:      ("[[{label}]]", "fill:#3caea3"),
            TASK_FAILED:    ("{{{{{label}}}}}", "fill:#ed553b"),
            TASK_CANCELLED: (">{label}]", "fill:#999"),
            TASK_BLOCKED:   ("[{label}]", "fill:#666"),
        }

        for task in self._tasks.values():
            shape, style = status_shapes.get(
                task.status, ("[{label}]", "")
            )
            safe_title = task.title.replace('"', "'")[:40]
            label = f"{task.id[:8]}: {safe_title}"
            node_def = f"    {task.id}{shape.format(label=label)}"
            lines.append(node_def)
            if style:
                lines.append(f"    style {task.id} {style}")

        for task_id, deps in self._edges.items():
            for dep_id in deps:
                if dep_id in self._tasks:
                    lines.append(f"    {dep_id} --> {task_id}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the entire DAG state."""
        return {
            "tasks": [t.to_dict() for t in self._tasks.values()],
            "edges": {
                tid: list(deps) for tid, deps in self._edges.items()
            },
            "stats": {
                "total": self.task_count,
                "pending": self.pending_count,
                "running": self.running_count,
                "done": self.done_count,
                "failed": self.failed_count,
            },
        }

    def __repr__(self) -> str:
        return (
            f"TaskDAG(tasks={self.task_count}, "
            f"pending={self.pending_count}, "
            f"running={self.running_count}, "
            f"done={self.done_count}, "
            f"failed={self.failed_count})"
        )
