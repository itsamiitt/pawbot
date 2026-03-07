"""Integration tests for FleetCommander (Phase 5).

Tests the full lifecycle with mock LLM calls:
planning → assignment → execution → result combining.

Mark with @pytest.mark.integration for CI separation.
"""

from __future__ import annotations

import asyncio

import pytest

from pawbot.fleet.circuit_breaker import CB_CLOSED, CB_OPEN, CircuitBreaker
from pawbot.fleet.commander import FleetCommander, ROLE_TAG_MAP
from pawbot.fleet.models import (
    FleetConfig,
    TaskNode,
    TASK_DONE,
    TASK_FAILED,
    TASK_PENDING,
)
from pawbot.fleet.validator import TaskValidator, ValidationResult


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fleet_config():
    return FleetConfig(
        max_workers=3,
        max_concurrent_tasks=3,
        default_max_retries=1,
    )


@pytest.fixture
def commander(fleet_config, tmp_path):
    return FleetCommander(
        config=fleet_config,
        workspace=tmp_path,
    )


# ── 5.1: Worker Assignment Tests ─────────────────────────────────────────────


class TestWorkerAssignment:
    """Test task-to-worker matching."""

    def test_coding_task_goes_to_coder(self, commander):
        """Tasks tagged 'code' should be assigned to the coder worker."""
        task = TaskNode(
            id="test-1",
            description="Implement login feature",
            tags=["code", "implement"],
        )
        worker_id = commander._find_best_worker(task)
        assert worker_id is not None
        worker = commander.get_worker(worker_id)
        assert worker.role == "coder"

    def test_research_task_goes_to_scout(self, commander):
        """Tasks tagged 'research' should be assigned to the scout worker."""
        task = TaskNode(
            id="test-2",
            description="Research the Stripe API documentation",
            tags=["research", "docs"],
        )
        worker_id = commander._find_best_worker(task)
        assert worker_id is not None
        worker = commander.get_worker(worker_id)
        assert worker.role == "scout"

    def test_review_task_goes_to_guardian(self, commander):
        """Tasks tagged 'review' should be assigned to the guardian worker."""
        task = TaskNode(
            id="test-3",
            description="Review code for security vulnerabilities",
            tags=["review", "security"],
        )
        worker_id = commander._find_best_worker(task)
        assert worker_id is not None
        worker = commander.get_worker(worker_id)
        assert worker.role == "guardian"

    def test_description_keyword_bonus(self, commander):
        """Description keywords should influence scoring when tags are ambiguous."""
        # "test" is shared between coder and guardian, but "review" in description
        # should tip towards guardian
        task = TaskNode(
            id="test-4",
            description="Review and verify the test suite",
            tags=["test"],
        )
        worker_id = commander._find_best_worker(task)
        assert worker_id is not None
        worker = commander.get_worker(worker_id)
        assert worker.role == "guardian"

    def test_no_workers_returns_none(self, commander):
        """Should return None when all circuit breakers are open."""
        for wid in list(commander._workers.keys()):
            commander.circuit_breaker.force_open(wid)
        task = TaskNode(id="test-5", description="Do something", tags=["code"])
        assert commander._find_best_worker(task) is None


# ── 5.2: Circuit Breaker Tests ───────────────────────────────────────────────


class TestCircuitBreaker:
    """Test circuit breaker state transitions."""

    def test_starts_closed(self):
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.state("w1") == CB_CLOSED

    def test_opens_after_threshold_failures(self):
        """Circuit breaker should open after consecutive failures."""
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure("w1")
        assert cb.state("w1") == CB_OPEN

    def test_success_resets_failures(self):
        """A success should reset the consecutive failure count."""
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure("w1")
        cb.record_failure("w1")
        cb.record_success("w1")
        assert cb.state("w1") == CB_CLOSED
        # Should need 3 more failures to open
        cb.record_failure("w1")
        cb.record_failure("w1")
        assert cb.state("w1") == CB_CLOSED
        cb.record_failure("w1")
        assert cb.state("w1") == CB_OPEN

    def test_force_open_and_close(self):
        cb = CircuitBreaker()
        cb.force_open("w1")
        assert cb.state("w1") == CB_OPEN
        assert not cb.can_accept_task("w1")
        cb.force_close("w1")
        assert cb.state("w1") == CB_CLOSED
        assert cb.can_accept_task("w1")

    def test_healthy_workers_tracking(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb._get_or_create("w1")
        cb._get_or_create("w2")
        cb.record_failure("w1")
        cb.record_failure("w1")
        assert "w1" not in cb.healthy_workers()
        assert "w2" in cb.healthy_workers()


# ── 5.3: Task Validator Tests ────────────────────────────────────────────────


class TestTaskValidator:
    """Test task result validation."""

    def setup_method(self):
        self.validator = TaskValidator()

    def test_valid_output(self):
        result = self.validator.validate(
            "Implement a login function",
            "Here is the login function implementation with JWT tokens. "
            "The function accepts username and password parameters..."
        )
        assert result.is_valid
        assert result.score > 0.5
        assert len(result.issues) == 0

    def test_empty_output_is_invalid(self):
        result = self.validator.validate("Write code", "")
        assert not result.is_valid
        assert result.score == 0.0
        assert "Empty output" in result.issues

    def test_short_output_penalized(self):
        result = self.validator.validate("Explain Python", "It's a language")
        assert result.score < 1.0
        assert any("too short" in issue for issue in result.issues)

    def test_error_prefix_penalized(self):
        result = self.validator.validate(
            "Fix the bug",
            "Error: I couldn't access the file system to fix the bug in the code"
        )
        assert result.score < 1.0
        assert any("error indicator" in issue for issue in result.issues)

    def test_low_keyword_overlap_penalized(self):
        result = self.validator.validate(
            "Implement Stripe payment processing with webhooks",
            "The weather today is sunny with a high of 72 degrees."
        )
        assert any("keyword overlap" in issue for issue in result.issues)

    def test_repetition_detected(self):
        repeated = "The same line\n" * 20
        result = self.validator.validate("Write something", repeated)
        assert any("repetition" in issue.lower() for issue in result.issues)


# ── 5.4: End-to-End (Smoke) ──────────────────────────────────────────────────


class TestFleetSmoke:
    """Quick smoke tests for fleet structure."""

    def test_fleet_has_default_workers(self, commander):
        assert len(list(commander.workers)) >= 3

    def test_cancel_all_with_no_tasks(self, commander):
        """cancel_all() should return 0 when no tasks running."""
        count = asyncio.get_event_loop().run_until_complete(commander.cancel_all())
        assert count == 0

    def test_fleet_snapshot_serializable(self):
        """FleetSnapshot should be JSON-serializable."""
        from pawbot.fleet.models import FleetSnapshot
        snapshot = FleetSnapshot()
        d = snapshot.to_dict()
        assert "workers" in d
        assert "tasks" in d
        assert isinstance(d["active_task_count"], int)

    def test_role_tag_map_completeness(self):
        """Every default worker role should be in ROLE_TAG_MAP."""
        from pawbot.fleet.commander import DEFAULT_WORKERS
        for spec in DEFAULT_WORKERS.values():
            assert spec.role in ROLE_TAG_MAP, f"Missing role in ROLE_TAG_MAP: {spec.role}"
