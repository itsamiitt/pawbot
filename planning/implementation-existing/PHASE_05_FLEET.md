# Phase 5 — Fleet System Hardening

> **Goal:** Make multi-agent orchestration tested, reliable, and observable.  
> **Duration:** 7-10 days  
> **Risk Level:** High (behavioral changes to task execution)  
> **Depends On:** Phase 1 (resilient LLM calls), Phase 3 (provider health)

---

## 5.1 — Integration Test Suite

### Problem
`FleetCommander` (736 lines) has zero end-to-end tests with real LLM calls. The DAG decomposition, worker assignment, and result combining are untested in practice.

### Solution
Create a comprehensive test suite:

```python
# Create: tests/test_fleet_integration.py

"""Integration tests for FleetCommander.

These tests use mock LLM calls to verify the full lifecycle:
planning → assignment → execution → result combining.

Mark with @pytest.mark.integration for CI separation.
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from pawbot.fleet.commander import FleetCommander
from pawbot.fleet.models import FleetConfig, TaskNode, TASK_DONE, TASK_FAILED


@pytest.fixture
def fleet_config():
    return FleetConfig(
        max_concurrent_tasks=3,
        task_timeout_seconds=30,
        max_retries=1,
    )


@pytest.fixture
def commander(fleet_config, tmp_path):
    return FleetCommander(
        config=fleet_config,
        workspace=tmp_path,
    )


class TestDAGDecomposition:
    """Test task decomposition into DAG."""

    @pytest.mark.asyncio
    async def test_simple_task_becomes_single_node(self, commander):
        """A simple request should produce a single-task DAG."""
        with patch.object(commander, '_call_llm', new_callable=AsyncMock) as mock_llm:
            # Simulate LLM returning a single task
            mock_llm.return_value = '[{"id": "task-1", "description": "Do the thing", "tags": ["code"]}]'
            
            dag = await commander.decompose_to_dag("Fix the bug in login.py")
            
            assert len(dag.tasks) == 1
            assert dag.tasks[0].description == "Do the thing"

    @pytest.mark.asyncio
    async def test_complex_task_becomes_multi_node(self, commander):
        """A complex request should produce multiple tasks with dependencies."""
        with patch.object(commander, '_call_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = '''[
                {"id": "task-1", "description": "Research API docs", "tags": ["research"], "depends_on": []},
                {"id": "task-2", "description": "Implement integration", "tags": ["code"], "depends_on": ["task-1"]},
                {"id": "task-3", "description": "Write tests", "tags": ["test"], "depends_on": ["task-2"]}
            ]'''
            
            dag = await commander.decompose_to_dag(
                "Research the Stripe API, implement payment processing, and write tests"
            )
            
            assert len(dag.tasks) == 3
            # task-2 depends on task-1
            assert "task-1" in [d for d in dag.get_dependencies("task-2")]

    @pytest.mark.asyncio
    async def test_llm_failure_produces_single_task_fallback(self, commander):
        """If LLM decomposition fails, fall back to single task."""
        with patch.object(commander, '_call_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = RuntimeError("LLM unavailable")
            
            dag = await commander.decompose_to_dag("Do something complex")
            
            # Should fall back to single task
            assert len(dag.tasks) == 1


class TestWorkerAssignment:
    """Test task-to-worker matching."""

    def test_coding_task_goes_to_coder(self, commander):
        """Tasks tagged 'code' should be assigned to the coder worker."""
        task = TaskNode(
            id="test-1",
            description="Implement feature",
            tags=["code", "implement"],
        )
        worker = commander._find_best_worker(task)
        assert worker is not None
        assert worker.role == "coder"

    def test_research_task_goes_to_scout(self, commander):
        """Tasks tagged 'research' should be assigned to the scout worker."""
        task = TaskNode(
            id="test-2",
            description="Research API",
            tags=["research", "docs"],
        )
        worker = commander._find_best_worker(task)
        assert worker is not None
        assert worker.role == "scout"

    def test_review_task_goes_to_guardian(self, commander):
        """Tasks tagged 'review' should be assigned to the guardian worker."""
        task = TaskNode(
            id="test-3",
            description="Review code",
            tags=["review", "qa"],
        )
        worker = commander._find_best_worker(task)
        assert worker is not None
        assert worker.role == "guardian"


class TestErrorHandling:
    """Test error escalation and circuit breaker."""

    @pytest.mark.asyncio
    async def test_task_failure_triggers_retry(self, commander):
        """Failed tasks should be retried up to max_retries."""
        # This tests the retry logic in _execute_dag
        pass  # Implement with mock worker execution

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_threshold(self, commander):
        """Circuit breaker should open after consecutive failures."""
        from pawbot.fleet.circuit_breaker import CircuitBreaker, CB_OPEN
        
        cb = CircuitBreaker(failure_threshold=3)
        
        # Simulate 3 failures
        for _ in range(3):
            cb.record_failure()
        
        assert cb.state == CB_OPEN

    @pytest.mark.asyncio
    async def test_budget_exhaustion_stops_execution(self, commander):
        """Tasks exceeding their token budget should be terminated."""
        pass  # Implement with budget tracking


class TestEndToEnd:
    """Full lifecycle tests."""

    @pytest.mark.asyncio
    async def test_plan_and_execute_simple(self, commander):
        """Test the full plan_and_execute lifecycle with a simple task."""
        with patch.object(commander, '_call_llm', new_callable=AsyncMock) as mock_llm:
            # First call: decomposition
            mock_llm.side_effect = [
                '[{"id": "task-1", "description": "Do the thing", "tags": ["code"]}]',
                'Task completed successfully. The bug was in line 42.',
            ]
            
            result = await commander.plan_and_execute("Fix the bug")
            
            assert result is not None
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_cancel_all_terminates_tasks(self, commander):
        """cancel_all() should stop all running tasks."""
        cancelled = commander.cancel_all()
        # No tasks running = 0 cancelled
        assert cancelled == 0
```

---

## 5.2 — Improved Worker Role Matching

### Problem
Current `ROLE_TAG_MAP` (line 118 in `commander.py`) uses simple keyword matching. "research what code changes are needed" matches both `scout` and `coder`.

### Solution
Scored matching with priority:

```python
# Replace _find_best_worker in fleet/commander.py:

def _find_best_worker(self, task: TaskNode) -> WorkerSpec | None:
    """Find the best worker using scored tag matching.
    
    Scoring:
    - Each matching tag adds 1.0 points
    - Description keyword match adds 0.3 points
    - Worker availability multiplier: busy workers score 0.5x
    - Returns highest scoring available worker
    """
    if not self._workers:
        return None

    best_worker: WorkerSpec | None = None
    best_score = -1.0
    task_tags = set(t.lower() for t in (task.tags or []))
    desc_lower = task.description.lower() if task.description else ""

    for worker_id, spec in self._workers.items():
        # Check circuit breaker
        cb = self._circuit_breakers.get(worker_id)
        if cb and cb.state != CB_CLOSED:
            continue

        # Check capacity
        current_tasks = len([
            t for t in self._dag.tasks
            if t.assigned_worker == worker_id and t.status == TASK_RUNNING
        ]) if self._dag else 0
        
        if current_tasks >= spec.max_concurrent_tasks:
            continue

        # Calculate score
        role_tags = set(ROLE_TAG_MAP.get(spec.role, []))
        tag_overlap = len(task_tags & role_tags)
        score = float(tag_overlap)  # 1 point per matching tag

        # Description keyword bonus
        for tag in role_tags:
            if tag in desc_lower:
                score += 0.3

        # Availability bonus (prefer idle workers)
        if current_tasks == 0:
            score *= 1.2
        elif current_tasks > 0:
            score *= 0.7

        if score > best_score:
            best_score = score
            best_worker = spec

    return best_worker
```

---

## 5.3 — Fleet Status API

### New endpoints for dashboard integration:

```python
# Add to dashboard/server.py:

@app.get("/api/fleet/status")
def fleet_status():
    """Fleet commander status."""
    try:
        from pawbot.fleet.commander import FleetCommander
        from pawbot.fleet.models import FleetConfig
        
        commander = FleetCommander(config=FleetConfig())
        workers = []
        for w in commander.workers:
            workers.append({
                "id": w.id,
                "role": w.role,
                "description": w.description,
                "model_preference": w.model_preference,
                "max_concurrent_tasks": w.max_concurrent_tasks,
                "status": "idle",
            })
        
        return {
            "workers": workers,
            "active_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "paused": False,
        }
    except Exception as e:
        return {"error": str(e), "workers": []}


@app.get("/api/fleet/tasks")
def fleet_tasks():
    """Active and recent fleet tasks."""
    return {"active": [], "completed": [], "failed": []}
```

---

## 5.4 — Task Result Validation

### Problem
Currently, task results are accepted as-is. No validation that the worker actually addressed the task.

### Solution

```python
# Create: pawbot/fleet/validator.py

"""Task result validation — verify worker output addresses the task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass
class ValidationResult:
    """Result of validating a task output."""
    is_valid: bool
    score: float           # 0.0-1.0 confidence that task was addressed
    issues: list[str]      # Why it might not be valid
    suggestion: str = ""   # What to do if invalid


class TaskValidator:
    """Validates that worker outputs actually address their assigned tasks."""

    MIN_OUTPUT_LENGTH = 20        # Minimum chars for a valid response
    ERROR_PREFIXES = ("error:", "failed:", "i couldn't", "i'm unable", "cannot")

    def validate(self, task_description: str, output: str) -> ValidationResult:
        """Validate a task result against its description.
        
        Returns:
            ValidationResult with validity assessment
        """
        issues: list[str] = []
        score = 1.0

        # Check 1: Non-empty output
        if not output or not output.strip():
            return ValidationResult(
                is_valid=False, score=0.0,
                issues=["Empty output"],
                suggestion="Re-execute task or escalate to human",
            )

        # Check 2: Minimum length
        if len(output.strip()) < self.MIN_OUTPUT_LENGTH:
            issues.append(f"Output too short ({len(output)} chars)")
            score -= 0.3

        # Check 3: Error indicators
        output_lower = output.lower().strip()
        for prefix in self.ERROR_PREFIXES:
            if output_lower.startswith(prefix):
                issues.append(f"Output starts with error indicator: '{prefix}'")
                score -= 0.5
                break

        # Check 4: Keyword relevance (simple heuristic)
        task_words = set(task_description.lower().split())
        output_words = set(output_lower.split())
        overlap = len(task_words & output_words)
        if overlap < 2 and len(task_words) > 3:
            issues.append("Low keyword overlap between task and output")
            score -= 0.2

        is_valid = score > 0.3
        suggestion = "" if is_valid else "Consider re-executing with more specific instructions"

        return ValidationResult(
            is_valid=is_valid,
            score=max(0.0, min(1.0, score)),
            issues=issues,
            suggestion=suggestion,
        )
```

---

## Verification Checklist — Phase 5 Complete

- [ ] `tests/test_fleet_integration.py` has ≥ 10 test cases
- [ ] All fleet integration tests pass with mock LLM
- [ ] `_find_best_worker` uses scored matching (not just keyword presence)
- [ ] `/api/fleet/status` endpoint returns worker info
- [ ] `/api/fleet/tasks` endpoint returns task history
- [ ] `TaskValidator` checks output quality
- [ ] Circuit breaker tests verify open/close transitions
- [ ] All tests pass: `pytest tests/ -v --tb=short`
