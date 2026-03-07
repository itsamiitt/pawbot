"""Tests for fleet DAG, circuit breaker, escalation, and inbox modules."""

import json
import tempfile
import time
from pathlib import Path

import pytest

from pawbot.fleet.circuit_breaker import (
    CB_CLOSED,
    CB_HALF_OPEN,
    CB_OPEN,
    CircuitBreaker,
)
from pawbot.fleet.dag import CycleDetectedError, TaskDAG
from pawbot.fleet.escalation import (
    LEVEL_CATASTROPHIC,
    LEVEL_LOGIC,
    LEVEL_RESOURCE,
    LEVEL_TRANSIENT,
    LEVEL_VALIDATION,
    ErrorEscalation,
)
from pawbot.fleet.inbox import FileInbox, FileOutbox, InboxMessage, InboxRouter
from pawbot.fleet.models import (
    TASK_DONE,
    TASK_FAILED,
    TASK_PENDING,
    FleetConfig,
    TaskNode,
    TaskResult,
    WorkerSpec,
)
from pawbot.fleet.status import FleetStatus


# ══════════════════════════════════════════════════════════════════════════════
#  TaskDAG Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestTaskDAG:
    """Tests for the directed acyclic graph."""

    def test_add_single_task(self):
        dag = TaskDAG()
        task = TaskNode(id="t1", title="Task 1")
        dag.add_task(task)
        assert dag.task_count == 1
        assert dag.get_task("t1") is task

    def test_add_multiple_tasks(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="Task 1"))
        dag.add_task(TaskNode(id="t2", title="Task 2"))
        dag.add_task(TaskNode(id="t3", title="Task 3"))
        assert dag.task_count == 3

    def test_add_dependency(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="Build"))
        dag.add_task(TaskNode(id="t2", title="Test"))
        dag.add_dependency("t2", "t1")  # t2 depends on t1
        assert "t1" in dag.get_dependencies("t2")
        assert "t2" in dag.get_dependents("t1")

    def test_cycle_detection_self_loop(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="Self"))
        with pytest.raises(CycleDetectedError):
            dag.add_dependency("t1", "t1")

    def test_cycle_detection_simple(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="A"))
        dag.add_task(TaskNode(id="t2", title="B"))
        dag.add_dependency("t2", "t1")
        with pytest.raises(CycleDetectedError):
            dag.add_dependency("t1", "t2")

    def test_cycle_detection_transitive(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="A"))
        dag.add_task(TaskNode(id="t2", title="B"))
        dag.add_task(TaskNode(id="t3", title="C"))
        dag.add_dependency("t2", "t1")
        dag.add_dependency("t3", "t2")
        with pytest.raises(CycleDetectedError):
            dag.add_dependency("t1", "t3")  # Would create t1→t3→t2→t1

    def test_topological_sort_linear(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="Step 1", priority=1))
        dag.add_task(TaskNode(id="t2", title="Step 2", priority=2))
        dag.add_task(TaskNode(id="t3", title="Step 3", priority=3))
        dag.add_dependency("t2", "t1")
        dag.add_dependency("t3", "t2")
        order = [t.id for t in dag.topological_sort()]
        assert order == ["t1", "t2", "t3"]

    def test_topological_sort_parallel(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="root", title="Root", priority=1))
        dag.add_task(TaskNode(id="a", title="A", priority=2))
        dag.add_task(TaskNode(id="b", title="B", priority=3))
        dag.add_dependency("a", "root")
        dag.add_dependency("b", "root")
        order = [t.id for t in dag.topological_sort()]
        assert order[0] == "root"
        assert set(order[1:]) == {"a", "b"}

    def test_get_ready_tasks_no_deps(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="Ready 1"))
        dag.add_task(TaskNode(id="t2", title="Ready 2"))
        ready = dag.get_ready_tasks()
        assert len(ready) == 2

    def test_get_ready_tasks_with_deps(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="First"))
        dag.add_task(TaskNode(id="t2", title="Second", depends_on=["t1"]))
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "t1"

    def test_get_ready_tasks_after_completion(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="First"))
        dag.add_task(TaskNode(id="t2", title="Second", depends_on=["t1"]))
        dag.mark_complete("t1", "done")
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "t2"

    def test_parallel_groups(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="root", title="Root"))
        dag.add_task(TaskNode(id="a", title="A", depends_on=["root"]))
        dag.add_task(TaskNode(id="b", title="B", depends_on=["root"]))
        dag.add_task(TaskNode(id="final", title="Final", depends_on=["a", "b"]))
        groups = dag.parallel_groups()
        assert len(groups) == 3  # [root], [a, b], [final]
        assert len(groups[0]) == 1
        assert len(groups[1]) == 2
        assert len(groups[2]) == 1

    def test_mark_complete(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="Task"))
        dag.mark_complete("t1", "result text")
        task = dag.get_task("t1")
        assert task.status == TASK_DONE
        assert task.output == "result text"
        assert task.finished_at > 0

    def test_mark_failed_and_retry(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="Task", max_retries=2))
        dag.mark_failed("t1", "timeout", "TRANSIENT")
        task = dag.get_task("t1")
        assert task.status == TASK_FAILED
        assert task.retry_count == 1
        assert task.can_retry is True

        # Reset for retry
        assert dag.reset_for_retry("t1") is True
        assert task.status == TASK_PENDING
        assert task.error_message == ""

    def test_mark_failed_max_retries(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="Task", max_retries=1))
        dag.mark_failed("t1", "error1", "TRANSIENT")
        assert dag.get_task("t1").can_retry is False
        assert dag.reset_for_retry("t1") is False

    def test_mermaid_output(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="Build Code"))
        dag.add_task(TaskNode(id="t2", title="Test Code", depends_on=["t1"]))
        mermaid = dag.to_mermaid()
        assert "graph TD" in mermaid
        assert "t1" in mermaid
        assert "t2" in mermaid
        assert "t1 --> t2" in mermaid

    def test_all_complete(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="A"))
        dag.add_task(TaskNode(id="t2", title="B"))
        assert dag.all_complete is False
        dag.mark_complete("t1")
        assert dag.all_complete is False
        dag.mark_complete("t2")
        assert dag.all_complete is True

    def test_remove_task(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="A"))
        dag.add_task(TaskNode(id="t2", title="B", depends_on=["t1"]))
        removed = dag.remove_task("t1")
        assert removed is not None
        assert dag.task_count == 1

    def test_empty_dag(self):
        dag = TaskDAG()
        assert dag.task_count == 0
        assert dag.all_complete is True
        assert dag.get_ready_tasks() == []
        assert dag.to_mermaid().startswith("graph TD")

    def test_validate_missing_dependency(self):
        dag = TaskDAG()
        task = TaskNode(id="t1", title="A")
        dag._tasks[task.id] = task
        dag._edges["t1"].add("nonexistent")
        valid, problems = dag.validate()
        assert valid is False
        assert any("nonexistent" in p for p in problems)

    def test_to_dict(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="A"))
        data = dag.to_dict()
        assert "tasks" in data
        assert "edges" in data
        assert "stats" in data
        assert data["stats"]["total"] == 1

    def test_add_tasks_bulk(self):
        dag = TaskDAG()
        tasks = [
            TaskNode(id="t1", title="A"),
            TaskNode(id="t2", title="B", depends_on=["t1"]),
            TaskNode(id="t3", title="C", depends_on=["t1"]),
        ]
        dag.add_tasks(tasks)
        assert dag.task_count == 3
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "t1"


# ══════════════════════════════════════════════════════════════════════════════
#  CircuitBreaker Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    """Tests for the per-worker circuit breaker."""

    def test_initial_state_closed(self):
        cb = CircuitBreaker()
        assert cb.state("worker-1") == CB_CLOSED
        assert cb.can_accept_task("worker-1") is True

    def test_stays_closed_under_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure("w1")
        cb.record_failure("w1")
        assert cb.state("w1") == CB_CLOSED
        assert cb.can_accept_task("w1") is True

    def test_opens_at_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure("w1")
        cb.record_failure("w1")
        cb.record_failure("w1")
        assert cb.state("w1") == CB_OPEN
        assert cb.can_accept_task("w1") is False

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure("w1")
        cb.record_failure("w1")
        cb.record_success("w1")
        cb.record_failure("w1")
        # Only 1 consecutive failure after success
        assert cb.state("w1") == CB_CLOSED

    def test_half_open_after_cooldown(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0)
        cb.record_failure("w1")
        # record_failure sets state to OPEN on the record directly
        assert cb._breakers["w1"].state == CB_OPEN
        # Cooldown is 0 seconds, so calling state() triggers transition to HALF_OPEN
        time.sleep(0.01)
        assert cb.state("w1") == CB_HALF_OPEN
        assert cb.can_accept_task("w1") is True

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0, success_threshold=1)
        cb.record_failure("w1")
        time.sleep(0.01)
        assert cb.state("w1") == CB_HALF_OPEN
        cb.record_success("w1")
        assert cb.state("w1") == CB_CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0)
        cb.record_failure("w1")
        time.sleep(0.01)
        _ = cb.state("w1")  # Trigger transition to HALF_OPEN
        cb.record_failure("w1")
        # record_failure in HALF_OPEN sets state back to OPEN on the record
        assert cb._breakers["w1"].state == CB_OPEN

    def test_force_open(self):
        cb = CircuitBreaker()
        cb.force_open("w1")
        assert cb.state("w1") == CB_OPEN
        assert cb.can_accept_task("w1") is False

    def test_force_close(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure("w1")
        assert cb.state("w1") == CB_OPEN
        cb.force_close("w1")
        assert cb.state("w1") == CB_CLOSED

    def test_reset(self):
        cb = CircuitBreaker()
        cb.record_failure("w1")
        cb.reset("w1")
        assert cb.state("w1") == CB_CLOSED

    def test_fleet_health(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_success("w1")
        cb.record_failure("w2")
        health = cb.fleet_health()
        assert health["total_workers"] == 2
        assert health["healthy"] == 1
        assert health["offline"] == 1

    def test_healthy_workers(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_success("w1")
        cb.record_success("w2")
        cb.record_failure("w3")
        assert set(cb.healthy_workers()) == {"w1", "w2"}

    def test_multiple_workers_independent(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure("w1")
        cb.record_failure("w1")
        cb.record_success("w2")
        assert cb.state("w1") == CB_OPEN
        assert cb.state("w2") == CB_CLOSED


# ══════════════════════════════════════════════════════════════════════════════
#  ErrorEscalation Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestErrorEscalation:
    """Tests for error classification."""

    def setup_method(self):
        self.esc = ErrorEscalation()

    def test_transient_timeout(self):
        assert self.esc.classify("Connection timed out") == LEVEL_TRANSIENT

    def test_transient_rate_limit(self):
        assert self.esc.classify("Rate limit exceeded (429)") == LEVEL_TRANSIENT

    def test_transient_connection_refused(self):
        assert self.esc.classify("Connection refused by server") == LEVEL_TRANSIENT

    def test_resource_out_of_memory(self):
        assert self.esc.classify("Out of memory error") == LEVEL_RESOURCE

    def test_resource_disk_full(self):
        assert self.esc.classify("No space left on device") == LEVEL_RESOURCE

    def test_validation_invalid_argument(self):
        assert self.esc.classify("Invalid argument: name is required") == LEVEL_VALIDATION

    def test_validation_parse_error(self):
        assert self.esc.classify("JSON decode error at line 5") == LEVEL_VALIDATION

    def test_catastrophic_database_corrupt(self):
        assert self.esc.classify("Database corrupt, unrecoverable") == LEVEL_CATASTROPHIC

    def test_catastrophic_fatal(self):
        assert self.esc.classify("Fatal error in core module") == LEVEL_CATASTROPHIC

    def test_default_logic(self):
        assert self.esc.classify("Some unknown error happened") == LEVEL_LOGIC

    def test_context_upstream_failed(self):
        assert self.esc.classify("error", {"upstream_failed": True}) == "DEPENDENCY"

    def test_should_retry_transient(self):
        assert self.esc.should_retry(LEVEL_TRANSIENT, 0) is True
        assert self.esc.should_retry(LEVEL_TRANSIENT, 2) is True
        assert self.esc.should_retry(LEVEL_TRANSIENT, 3) is False

    def test_should_not_retry_catastrophic(self):
        assert self.esc.should_retry(LEVEL_CATASTROPHIC, 0) is False

    def test_action_properties(self):
        action = self.esc.get_action(LEVEL_CATASTROPHIC)
        assert action.should_alert_user is True
        assert action.should_pause_fleet is True
        assert action.action == "halt_all"

    def test_severity_rank(self):
        assert self.esc.severity_rank(LEVEL_TRANSIENT) < self.esc.severity_rank(LEVEL_CATASTROPHIC)

    def test_exception_input(self):
        assert self.esc.classify(TimeoutError("Connection timed out")) == LEVEL_TRANSIENT


# ══════════════════════════════════════════════════════════════════════════════
#  FileInbox / FileOutbox Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestFileInbox:
    """Tests for file-based inbox."""

    def test_write_and_read(self, tmp_path):
        inbox = FileInbox(tmp_path, "worker-1")
        msg = InboxMessage(
            task_id="abc123",
            worker_id="worker-1",
            message_type="task_assignment",
            payload={"title": "Test task"},
        )
        path = inbox.write(msg)
        assert path.exists()
        messages = inbox.read_all()
        assert len(messages) == 1
        assert messages[0].task_id == "abc123"

    def test_acknowledge(self, tmp_path):
        inbox = FileInbox(tmp_path, "worker-1")
        msg = InboxMessage(
            task_id="abc123",
            worker_id="worker-1",
            message_type="task_assignment",
        )
        inbox.write(msg)
        assert inbox.pending_count == 1
        assert inbox.acknowledge("abc123") is True
        assert inbox.pending_count == 0

    def test_delete(self, tmp_path):
        inbox = FileInbox(tmp_path, "worker-1")
        msg = InboxMessage(
            task_id="abc123",
            worker_id="worker-1",
            message_type="task_assignment",
        )
        inbox.write(msg)
        assert inbox.delete("abc123") is True
        assert inbox.pending_count == 0

    def test_read_one(self, tmp_path):
        inbox = FileInbox(tmp_path, "worker-1")
        msg = InboxMessage(
            task_id="abc123",
            worker_id="worker-1",
            message_type="task_assignment",
            payload={"key": "value"},
        )
        inbox.write(msg)
        result = inbox.read_one("abc123")
        assert result is not None
        assert result.payload["key"] == "value"

    def test_clear(self, tmp_path):
        inbox = FileInbox(tmp_path, "worker-1")
        for i in range(3):
            inbox.write(InboxMessage(
                task_id=f"task-{i}",
                worker_id="worker-1",
                message_type="task_assignment",
            ))
        assert inbox.pending_count == 3
        cleared = inbox.clear()
        assert cleared == 3
        assert inbox.pending_count == 0

    def test_priority_ordering(self, tmp_path):
        inbox = FileInbox(tmp_path, "worker-1")
        inbox.write(InboxMessage(
            task_id="low", worker_id="worker-1",
            message_type="task_assignment", priority=10,
        ))
        inbox.write(InboxMessage(
            task_id="high", worker_id="worker-1",
            message_type="task_assignment", priority=1,
        ))
        messages = inbox.read_all()
        assert messages[0].task_id == "high"


class TestFileOutbox:
    """Tests for file-based outbox."""

    def test_write_and_collect(self, tmp_path):
        outbox = FileOutbox(tmp_path, "worker-1")
        msg = InboxMessage(
            task_id="abc123",
            worker_id="worker-1",
            message_type="task_result",
            payload={"output": "done"},
        )
        outbox.write(msg)
        assert outbox.pending_count == 1
        result = outbox.collect("abc123")
        assert result is not None
        assert result.payload["output"] == "done"
        assert outbox.pending_count == 0


class TestInboxRouter:
    """Tests for the fleet-wide inbox router."""

    def test_register_and_dispatch(self, tmp_path):
        router = InboxRouter(tmp_path)
        router.register_worker("worker-1")
        msg = InboxMessage(
            task_id="abc123",
            worker_id="worker-1",
            message_type="task_assignment",
            payload={"task": "do something"},
        )
        path = router.dispatch_task("worker-1", msg)
        assert path is not None
        assert path.exists()

    def test_collect_results(self, tmp_path):
        router = InboxRouter(tmp_path)
        router.register_worker("worker-1")
        # Simulate worker writing to outbox
        outbox = router.get_outbox("worker-1")
        outbox.write(InboxMessage(
            task_id="abc123",
            worker_id="worker-1",
            message_type="task_result",
            payload={"output": "result"},
        ))
        results = router.collect_results("worker-1")
        assert len(results) == 1

    def test_fleet_pending(self, tmp_path):
        router = InboxRouter(tmp_path)
        router.register_worker("w1")
        router.register_worker("w2")
        router.dispatch_task("w1", InboxMessage(
            task_id="t1", worker_id="w1", message_type="task_assignment",
        ))
        router.dispatch_task("w1", InboxMessage(
            task_id="t2", worker_id="w1", message_type="task_assignment",
        ))
        pending = router.fleet_pending()
        assert pending["w1"] == 2
        assert pending["w2"] == 0


# ══════════════════════════════════════════════════════════════════════════════
#  FleetStatus Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestFleetStatus:
    """Tests for fleet status tracking."""

    def test_write_and_read(self, tmp_path):
        status_path = tmp_path / "status.json"
        status = FleetStatus(status_path)
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", title="Test"))
        cb = CircuitBreaker()
        cb.record_success("w1")
        workers = {"w1": WorkerSpec(id="w1", role="coder", workspace=tmp_path)}
        status.update(workers, dag, cb)

        data = status.read()
        assert data is not None
        assert data["version"] == "2.0"
        assert "fleet" in data

    def test_log_event(self, tmp_path):
        status = FleetStatus(tmp_path / "status.json")
        status.log_event("test", "hello", key="value")
        assert len(status._execution_log) == 1
        assert status._execution_log[0]["event"] == "test"

    def test_clear(self, tmp_path):
        status_path = tmp_path / "status.json"
        status = FleetStatus(status_path)
        status_path.write_text("{}")
        status.clear()
        assert not status_path.exists()


# ══════════════════════════════════════════════════════════════════════════════
#  Models Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestModels:
    """Tests for fleet data models."""

    def test_task_node_defaults(self):
        task = TaskNode()
        assert task.status == TASK_PENDING
        assert task.priority == 5
        assert task.is_terminal is False
        assert task.can_retry is True

    def test_task_node_elapsed(self):
        task = TaskNode()
        task.started_at = time.time() - 10
        assert task.elapsed_seconds >= 9.5

    def test_task_node_to_dict(self):
        task = TaskNode(id="t1", title="Test", tags=["code"])
        data = task.to_dict()
        assert data["id"] == "t1"
        assert data["tags"] == ["code"]

    def test_worker_spec_to_dict(self):
        spec = WorkerSpec(id="w1", role="coder", workspace=Path("/tmp"))
        data = spec.to_dict()
        assert data["id"] == "w1"
        assert data["role"] == "coder"

    def test_fleet_config_defaults(self):
        config = FleetConfig()
        assert config.max_workers == 4
        assert config.max_concurrent_tasks == 8
        assert len(config.default_workers) == 3

    def test_task_result_to_dict(self):
        result = TaskResult(
            task_id="t1", worker_id="w1", success=True, output="done"
        )
        data = result.to_dict()
        assert data["success"] is True
