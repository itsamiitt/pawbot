"""FleetCommander — main orchestration engine for the worker fleet.

Phase 18: The FleetCommander is the brain of the multi-agent fleet.
It decomposes user requests into a DAG of tasks, assigns them to
specialised workers, monitors execution, handles failures with
escalation, and delivers combined results.

Architecture:
  User Request → FleetCommander.plan_and_execute()
    → decompose_to_dag()    (LLM-powered task decomposition)
    → assign_tasks()         (match tasks to workers by role)
    → monitor_loop()         (poll outboxes, check circuit breakers)
    → handle_failure()       (classify, retry, reassign, or escalate)
    → combine_results()      (merge all task outputs)
    → return final result

The Commander integrates with:
  - TaskDAG           (dependency graph)
  - CircuitBreaker    (worker health)
  - InboxRouter       (durable messaging)
  - FleetStatus       (status.json persistence)
  - ErrorEscalation   (failure handling)
  - SubagentRunner    (existing Phase 12 — workers run as subagents)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from pawbot.fleet.circuit_breaker import CB_CLOSED, CircuitBreaker
from pawbot.fleet.dag import TaskDAG
from pawbot.fleet.escalation import (
    LEVEL_CATASTROPHIC,
    LEVEL_RESOURCE,
    ErrorEscalation,
)
from pawbot.fleet.inbox import FileOutbox, InboxMessage, InboxRouter
from pawbot.fleet.models import (
    TASK_CANCELLED,
    TASK_DONE,
    TASK_FAILED,
    TASK_PENDING,
    TASK_QUEUED,
    TASK_RUNNING,
    FleetConfig,
    TaskNode,
    TaskResult,
    WorkerSpec,
)
from pawbot.fleet.status import FleetStatus

logger = logging.getLogger("pawbot.fleet.commander")


# ══════════════════════════════════════════════════════════════════════════════
#  Default Worker Definitions
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_WORKERS: dict[str, WorkerSpec] = {
    "worker-1": WorkerSpec(
        id="worker-1",
        role="coder",
        workspace=Path("."),
        system_prompt=(
            "You are a focused coding worker agent. Write, modify, and debug code. "
            "Execute tests to verify your work. Do not make architectural decisions — "
            "implement exactly what the task specification describes."
        ),
        model_preference="sonnet",
        max_concurrent_tasks=3,
        allowed_tools=[
            "code_write", "code_edit", "code_run_checks", "code_search",
            "server_read_file", "server_write_file", "exec",
        ],
        description="Code implementation, debugging, and testing",
    ),
    "worker-2": WorkerSpec(
        id="worker-2",
        role="scout",
        workspace=Path("."),
        system_prompt=(
            "You are a focused research worker agent. Search for information, "
            "read documentation, gather data, and explore APIs. Report findings "
            "concisely with sources. Do not take actions — only gather and analyse."
        ),
        model_preference="sonnet",
        max_concurrent_tasks=2,
        allowed_tools=[
            "web_search", "browser_open", "browser_extract",
            "code_search", "server_read_file",
        ],
        description="Research, data gathering, API exploration",
    ),
    "worker-3": WorkerSpec(
        id="worker-3",
        role="guardian",
        workspace=Path("."),
        system_prompt=(
            "You are a code review and QA worker agent. Review code for bugs, "
            "security issues, and edge cases. Run tests and verify outputs. "
            "Be thorough and specific in your feedback."
        ),
        model_preference="sonnet",
        max_concurrent_tasks=2,
        allowed_tools=[
            "code_search", "code_run_checks", "server_read_file", "exec",
        ],
        description="Code review, QA, security checks, verification",
    ),
}

# Worker role → task tag matching
ROLE_TAG_MAP: dict[str, list[str]] = {
    "coder":    ["code", "implement", "fix", "build", "refactor", "test"],
    "scout":    ["research", "search", "docs", "api", "explore", "gather"],
    "guardian": ["review", "test", "verify", "qa", "security", "audit"],
    "analyst":  ["analyze", "report", "summarize", "compare"],
    "planner":  ["plan", "design", "architecture", "spec"],
}


class FleetCommander:
    """Main orchestration engine for the worker fleet.

    Manages the full lifecycle of fleet task execution:
    planning → assignment → monitoring → error handling → result delivery.
    """

    def __init__(
        self,
        config: FleetConfig | None = None,
        workspace: Path | None = None,
        model_router: Any = None,
        memory_router: Any = None,
        on_user_alert: Callable[[str], Any] | None = None,
    ) -> None:
        self.config = config or FleetConfig()
        self.workspace = workspace or Path.home() / ".pawbot"
        self.model_router = model_router
        self.memory_router = memory_router
        self.on_user_alert = on_user_alert

        # Compute paths
        shared_dir = self.workspace / "shared"
        status_path = self.workspace / self.config.status_file

        # Subsystems
        self.dag = TaskDAG()
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=self.config.circuit_breaker_threshold,
            cooldown_seconds=self.config.circuit_breaker_cooldown,
        )
        self.inbox_router = InboxRouter(shared_dir)
        self.status_tracker = FleetStatus(status_path, self.config)
        self.escalation = ErrorEscalation()

        # Worker registry
        self._workers: dict[str, WorkerSpec] = {}
        self._worker_task_count: dict[str, int] = {}
        self._paused = False

        # Dead letter queue
        self._dead_letters: list[TaskNode] = []

        # Initialise default workers
        self._init_default_workers()

    def _init_default_workers(self) -> None:
        """Register default workers from config or built-in definitions."""
        for worker_def in self.config.default_workers:
            wid = worker_def["id"]
            if wid in DEFAULT_WORKERS:
                spec = DEFAULT_WORKERS[wid]
                # Update workspace path
                spec.workspace = self.workspace / "workers" / wid
                spec.workspace.mkdir(parents=True, exist_ok=True)
            else:
                spec = WorkerSpec(
                    id=wid,
                    role=worker_def.get("role", "coder"),
                    workspace=self.workspace / "workers" / wid,
                    description=worker_def.get("description", ""),
                )
                spec.workspace.mkdir(parents=True, exist_ok=True)
            self.add_worker(spec)

    # ── Worker Management ────────────────────────────────────────────────────

    def add_worker(self, spec: WorkerSpec) -> None:
        """Register a worker in the fleet."""
        self._workers[spec.id] = spec
        self._worker_task_count[spec.id] = 0
        self.inbox_router.register_worker(spec.id)
        self.circuit_breaker.force_close(spec.id)
        logger.info("Worker registered: %s (%s)", spec.id, spec.role)

    def remove_worker(self, worker_id: str) -> WorkerSpec | None:
        """Remove a worker from the fleet."""
        spec = self._workers.pop(worker_id, None)
        self._worker_task_count.pop(worker_id, None)
        if spec:
            logger.info("Worker removed: %s", worker_id)
        return spec

    def get_worker(self, worker_id: str) -> WorkerSpec | None:
        return self._workers.get(worker_id)

    @property
    def workers(self) -> dict[str, WorkerSpec]:
        return dict(self._workers)

    @property
    def healthy_workers(self) -> list[str]:
        return [
            wid for wid in self._workers
            if self.circuit_breaker.can_accept_task(wid)
        ]

    # ── Task Planning ────────────────────────────────────────────────────────

    async def plan_and_execute(self, user_request: str) -> str:
        """Full lifecycle: decompose → assign → monitor → combine results.

        This is the main entry point called by AgentLoop for fleet-worthy tasks.
        Returns the combined result as a string.
        """
        request_id = str(uuid.uuid4())[:8]
        logger.info("Fleet request %s: %.100s", request_id, user_request)
        self.status_tracker.log_event("request", user_request[:200], request_id=request_id)

        try:
            # 1. Decompose into DAG
            dag = await self.decompose_to_dag(user_request)
            self.dag = dag
            self._update_status()

            # 2. Execute the DAG
            result = await self._execute_dag()

            # 3. Combine results
            combined = self._combine_results()

            self.status_tracker.log_event(
                "complete", f"Request {request_id} done",
                request_id=request_id,
                task_count=dag.task_count,
                failed_count=dag.failed_count,
            )
            return combined

        except Exception as exc:
            logger.error("Fleet request %s failed: %s", request_id, exc)
            self.status_tracker.log_event(
                "error", str(exc), request_id=request_id,
            )
            return f"Fleet execution failed: {exc}"

    async def decompose_to_dag(self, request: str) -> TaskDAG:
        """Decompose a user request into a TaskDAG using the LLM.

        Falls back to a single-task DAG if decomposition fails.
        """
        dag = TaskDAG()

        if not self.model_router:
            # No model router — create single task
            task = TaskNode(
                title="Execute request",
                description=request,
                priority=5,
                tags=["code"],
            )
            dag.add_task(task)
            return dag

        # Use LLM to decompose the request into subtasks
        decompose_prompt = f"""Break down this request into atomic subtasks for a worker fleet.

Request: {request}

Available worker roles:
- coder: Write, modify, debug, test code
- scout: Research, search docs, gather data
- guardian: Review code, run tests, verify quality

Respond with a JSON array of tasks. Each task has:
- "id": short unique ID (e.g., "t1", "t2")
- "title": brief task title
- "description": detailed instructions with ALL context needed
- "role": which worker role should handle this ("coder", "scout", "guardian")
- "depends_on": array of task IDs this depends on (empty if independent)
- "priority": 1 (highest) to 10 (lowest)

Rules:
- Independent tasks should have empty depends_on (they'll run in parallel)
- Guardian should review coder output (add dependency)
- Include ALL context in description — workers have NO shared memory
- Respond ONLY with the JSON array, no other text

JSON:"""

        try:
            response = self.model_router.call(
                task_type="planning",
                messages=[{"role": "user", "content": decompose_prompt}],
                system="You are a task decomposition engine. Output only valid JSON.",
                tools=[],
            )
            content = response.get("content", "")

            # Parse JSON from response
            tasks_data = self._parse_tasks_json(content)
            if not tasks_data:
                raise ValueError("No tasks parsed from LLM response")

            # Build task ID mapping
            id_map: dict[str, str] = {}
            nodes: list[TaskNode] = []

            for td in tasks_data:
                node = TaskNode(
                    title=td.get("title", "Untitled task"),
                    description=td.get("description", ""),
                    priority=td.get("priority", 5),
                    tags=[td.get("role", "coder")],
                )
                id_map[td.get("id", node.id)] = node.id
                nodes.append(node)

            # Resolve dependencies
            for i, td in enumerate(tasks_data):
                raw_deps = td.get("depends_on", [])
                for dep_id in raw_deps:
                    resolved = id_map.get(dep_id)
                    if resolved:
                        nodes[i].depends_on.append(resolved)

            dag.add_tasks(nodes)
            logger.info("Decomposed request into %d tasks", dag.task_count)

        except Exception as exc:
            logger.warning("Decomposition failed (%s), using single task", exc)
            task = TaskNode(
                title="Execute request",
                description=request,
                priority=5,
                tags=["code"],
            )
            dag.add_task(task)

        return dag

    def _parse_tasks_json(self, content: str) -> list[dict[str, Any]]:
        """Extract JSON array from LLM response (handles markdown fences)."""
        text = content.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        # Find JSON array
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            return []

        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []

    # ── Task Assignment ──────────────────────────────────────────────────────

    def _find_best_worker(self, task: TaskNode) -> str | None:
        """Find the best worker using scored tag matching (Phase 5).

        Scoring:
        - Each matching tag adds 1.0 points
        - Description keyword match adds 0.3 points per keyword
        - Worker availability: idle workers get 1.2x, loaded get 0.7x
        - Circuit-breaker-tripped workers are skipped
        """
        task_tags = set(t.lower() for t in (task.tags or []))
        desc_lower = task.description.lower() if task.description else ""

        candidates: list[tuple[str, float]] = []
        for wid, spec in self._workers.items():
            if not self.circuit_breaker.can_accept_task(wid):
                continue
            current_tasks = self._worker_task_count.get(wid, 0)
            if current_tasks >= spec.max_concurrent_tasks:
                continue

            # Tag overlap score (1.0 per matching tag)
            role_tags = set(ROLE_TAG_MAP.get(spec.role, []))
            tag_overlap = len(task_tags & role_tags)
            score = float(tag_overlap)

            # Description keyword bonus (0.3 per keyword found in description)
            for tag in role_tags:
                if tag in desc_lower:
                    score += 0.3

            # Availability multiplier (prefer idle workers)
            if current_tasks == 0:
                score *= 1.2
            elif current_tasks > 0:
                score *= 0.7

            candidates.append((wid, score))

        if not candidates:
            return None

        # Sort by score descending
        candidates.sort(key=lambda c: c[1], reverse=True)
        return candidates[0][0]

    async def _assign_task(self, task: TaskNode) -> bool:
        """Assign a task to the best available worker.

        Returns True if successfully assigned.
        """
        worker_id = self._find_best_worker(task)
        if not worker_id:
            logger.warning("No available worker for task %s", task.id[:8])
            return False

        task.assigned_to = worker_id
        task.status = TASK_QUEUED

        # Write task spec to worker's inbox
        message = InboxMessage(
            task_id=task.id,
            worker_id=worker_id,
            message_type="task_assignment",
            payload={
                "title": task.title,
                "description": task.description,
                "priority": task.priority,
                "timeout_seconds": task.timeout_seconds,
                "tags": task.tags,
                "depends_on": task.depends_on,
            },
            priority=task.priority,
        )
        self.inbox_router.dispatch_task(worker_id, message)
        self._worker_task_count[worker_id] = self._worker_task_count.get(worker_id, 0) + 1

        self.status_tracker.log_event(
            "assigned", f"Task {task.id[:8]} → {worker_id}",
            task_id=task.id, worker_id=worker_id,
        )
        logger.info("Assigned task %s → %s (%s)", task.id[:8], worker_id, task.title[:40])
        return True

    # ── Execution Loop ───────────────────────────────────────────────────────

    async def _execute_dag(self) -> None:
        """Execute the full DAG: assign ready tasks, monitor, handle failures."""
        max_cycles = 200  # Safety limit
        cycle = 0

        while not self.dag.all_complete and cycle < max_cycles:
            cycle += 1

            if self._paused:
                await asyncio.sleep(self.config.poll_interval)
                continue

            # 1. Get ready tasks and assign them
            ready_tasks = self.dag.get_ready_tasks()
            for task in ready_tasks:
                if task.status == TASK_PENDING:
                    assigned = await self._assign_task(task)
                    if not assigned:
                        logger.warning("Could not assign task %s — will retry", task.id[:8])

            # 2. Execute running tasks via SubagentRunner (simulate worker)
            running = [t for t in self.dag.tasks if t.status == TASK_QUEUED]
            for task in running:
                if task.assigned_to:
                    asyncio.create_task(self._execute_task(task))

            # 3. Poll for completed tasks
            await asyncio.sleep(self.config.poll_interval)

            # 4. Check for timed-out tasks
            self._check_timeouts()

            # 5. Update status file
            self._update_status()

        if cycle >= max_cycles:
            logger.error("Fleet execution hit max cycles (%d)", max_cycles)

    async def _execute_task(self, task: TaskNode) -> None:
        """Execute a single task using the model router (worker simulation).

        In a real multi-node fleet, this would be handled by a separate
        worker process polling its inbox. Here we simulate it in-process
        using the existing SubagentRunner or direct LLM call.
        """
        if task.status not in (TASK_QUEUED, TASK_PENDING):
            return

        self.dag.mark_running(task.id)
        worker_id = task.assigned_to or "unassigned"

        try:
            if self.model_router is None:
                self.dag.mark_complete(task.id, "Executed (no model router)")
                return

            worker_spec = self._workers.get(worker_id)
            system_prompt = (
                worker_spec.system_prompt if worker_spec
                else "You are a helpful assistant. Complete the task precisely."
            )

            response = self.model_router.call(
                task_type="fleet_worker",
                messages=[{"role": "user", "content": task.description}],
                system=system_prompt,
                tools=[],
            )

            output = response.get("content", "")
            self.dag.mark_complete(task.id, output)

            # Write result to outbox
            result_msg = InboxMessage(
                task_id=task.id,
                worker_id=worker_id,
                message_type="task_result",
                payload={"output": output, "success": True},
            )
            outbox = self.inbox_router.get_outbox(worker_id)
            if outbox:
                outbox.write(result_msg)

            self.circuit_breaker.record_success(worker_id)
            self.status_tracker.log_event(
                "task_done", f"Task {task.id[:8]} completed by {worker_id}",
                task_id=task.id, worker_id=worker_id,
            )

        except Exception as exc:
            error_str = str(exc)
            error_class = self.escalation.classify(error_str)
            self.dag.mark_failed(task.id, error_str, error_class)
            self.circuit_breaker.record_failure(worker_id)

            await self._handle_failure(task, error_str, error_class)

        finally:
            self._worker_task_count[worker_id] = max(
                0, self._worker_task_count.get(worker_id, 1) - 1
            )

    # ── Failure Handling ─────────────────────────────────────────────────────

    async def _handle_failure(
        self, task: TaskNode, error: str, error_class: str
    ) -> None:
        """Handle a task failure based on escalation level."""
        action = self.escalation.get_action(error_class)

        self.status_tracker.log_event(
            "task_failed",
            f"Task {task.id[:8]} failed ({error_class}): {error[:100]}",
            task_id=task.id,
            error_class=error_class,
            retry_count=task.retry_count,
        )

        if action.action == "auto_retry" and task.can_retry:
            # Auto-retry: reset and re-queue
            logger.info(
                "Auto-retrying task %s (attempt %d/%d)",
                task.id[:8], task.retry_count, task.max_retries,
            )
            self.dag.reset_for_retry(task.id)

        elif action.action == "check_upstream":
            # Check upstream dependencies
            for dep_id in task.depends_on:
                dep = self.dag.get_task(dep_id)
                if dep and dep.status == TASK_FAILED:
                    logger.info(
                        "Upstream task %s also failed — retrying upstream first",
                        dep_id[:8],
                    )
                    if dep.can_retry:
                        self.dag.reset_for_retry(dep_id)

        elif action.action == "fix_spec":
            # Try to fix the task specification
            if task.can_retry:
                logger.info("Validation error on task %s — retrying", task.id[:8])
                self.dag.reset_for_retry(task.id)

        elif action.should_pause_fleet:
            # RESOURCE or CATASTROPHIC — pause fleet
            self._paused = True
            logger.critical(
                "Fleet PAUSED due to %s error on task %s", error_class, task.id[:8]
            )

        if action.should_alert_user and self.on_user_alert:
            alert_msg = (
                f"⚠️ Fleet alert ({error_class}): Task '{task.title}' failed.\n"
                f"Error: {error[:200]}\n"
                f"Action: {action.description}"
            )
            try:
                self.on_user_alert(alert_msg)
            except Exception:
                pass

        # Dead letter queue if max retries exceeded
        if not task.can_retry and task.status == TASK_FAILED:
            self._dead_letters.append(task)
            self.status_tracker.log_event(
                "dead_letter",
                f"Task {task.id[:8]} moved to dead letter queue",
                task_id=task.id,
            )

    def _check_timeouts(self) -> None:
        """Check for tasks that have exceeded their timeout."""
        for task in self.dag.tasks:
            if task.status == TASK_RUNNING and task.started_at > 0:
                elapsed = time.time() - task.started_at
                if elapsed > task.timeout_seconds:
                    logger.warning(
                        "Task %s timed out (%.0fs > %ds)",
                        task.id[:8], elapsed, task.timeout_seconds,
                    )
                    self.dag.mark_failed(task.id, "Timeout exceeded", "TRANSIENT")
                    if task.assigned_to:
                        self.circuit_breaker.record_failure(task.assigned_to)

    # ── Result Combination ───────────────────────────────────────────────────

    def _combine_results(self) -> str:
        """Combine all completed task outputs into a single response."""
        parts: list[str] = []
        failed: list[str] = []

        for task in self.dag.topological_sort():
            if task.status == TASK_DONE and task.output:
                if len(self.dag.tasks) > 1:
                    parts.append(f"### {task.title}\n{task.output}")
                else:
                    parts.append(task.output)
            elif task.status == TASK_FAILED:
                failed.append(f"- {task.title}: {task.error_message[:100]}")

        result = "\n\n".join(parts)

        if failed:
            result += "\n\n---\n⚠️ **Some tasks failed:**\n" + "\n".join(failed)

        if not result:
            result = "Fleet execution completed but produced no output."

        return result

    # ── Status & Monitoring ──────────────────────────────────────────────────

    def _update_status(self) -> None:
        """Persist current fleet state to status.json."""
        self.status_tracker.update(
            workers=self._workers,
            dag=self.dag,
            circuit_breaker=self.circuit_breaker,
        )

    def status(self) -> dict[str, Any]:
        """Return current fleet status as a dictionary."""
        return {
            "workers": {
                wid: {
                    **spec.to_dict(),
                    "circuit": self.circuit_breaker.state(wid),
                    "active_tasks": self._worker_task_count.get(wid, 0),
                }
                for wid, spec in self._workers.items()
            },
            "dag": self.dag.to_dict(),
            "health": self.circuit_breaker.fleet_health(),
            "paused": self._paused,
            "dead_letters": len(self._dead_letters),
        }

    @property
    def is_paused(self) -> bool:
        return self._paused

    def resume(self) -> None:
        """Resume a paused fleet."""
        self._paused = False
        logger.info("Fleet resumed")

    def pause(self) -> None:
        """Pause the fleet (stop assigning new tasks)."""
        self._paused = True
        logger.info("Fleet paused")

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a specific task."""
        task = self.dag.get_task(task_id)
        if not task:
            return False
        self.dag.mark_cancelled(task_id)
        if task.assigned_to:
            self._worker_task_count[task.assigned_to] = max(
                0, self._worker_task_count.get(task.assigned_to, 1) - 1
            )
        self.status_tracker.log_event("cancelled", f"Task {task_id[:8]} cancelled")
        return True

    async def cancel_all(self) -> int:
        """Cancel all non-terminal tasks."""
        count = 0
        for task in self.dag.tasks:
            if not task.is_terminal:
                self.dag.mark_cancelled(task.id)
                count += 1
        self.status_tracker.log_event("cancelled_all", f"Cancelled {count} tasks")
        return count

    def __repr__(self) -> str:
        return (
            f"FleetCommander("
            f"workers={len(self._workers)}, "
            f"{self.dag}, "
            f"paused={self._paused})"
        )
