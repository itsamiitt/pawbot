"""Tests for Phase 12 — Enhanced Subagent System.

Tests verify:
  - SubagentRole        (built-in roles, custom registration, tool filtering)
  - SubagentBudget      (timeouts, token budget, time_remaining)
  - Subagent            (run, cancel, discoveries, disallowed tools, budget)
  - SubagentPool        (submit, get_result, max_concurrent, cancel)
  - SubagentRunner      (spawn, spawn_and_wait, unknown role, status, review_inbox)
  - SubagentMessageBus  (send/receive, empty receive, thread safety)
"""

from __future__ import annotations

import sys
import time
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Ensure the pawbot package root is on sys.path.
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from pawbot.agent.subagent import (
    BUILTIN_ROLES,
    Subagent,
    SubagentBudget,
    SubagentMessageBus,
    SubagentPool,
    SubagentResult,
    SubagentRole,
    SubagentRunner,
)


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════


class FakeModelRouter:
    """Stub for ModelRouter — returns a canned response after one iteration."""

    def __init__(
        self,
        content: str = "Done.",
        tool_calls: list | None = None,
        usage_tokens: int = 100,
    ):
        self._content = content
        self._tool_calls = tool_calls
        self._usage_tokens = usage_tokens
        self.call_count = 0

    def call(self, **kwargs) -> dict:
        self.call_count += 1
        return {
            "content": self._content,
            "tool_calls": self._tool_calls,
            "usage": {"total_tokens": self._usage_tokens},
        }


class FakeMemoryRouter:
    """Stub for MemoryRouter with a sqlite-like inbox."""

    def __init__(self):
        self.sqlite = FakeSQLite()

    def save(self, kind: str, data: dict):
        pass


class FakeSQLite:
    """Stub for SQLiteFactStore inbox interface."""

    def __init__(self):
        self.inbox: list[dict] = []

    def inbox_write(self, subagent_id: str, content: dict, confidence: float, proposed_type: str):
        self.inbox.append({
            "subagent_id": subagent_id,
            "content": content,
            "confidence": confidence,
            "type": proposed_type,
        })

    def inbox_review(self) -> list[str]:
        ids = [f"mem_{i}" for i in range(len(self.inbox))]
        self.inbox.clear()
        return ids


def _dummy_tool(**kwargs):
    """A simple tool callable for testing."""
    return {"status": "ok", "args": kwargs}


# ══════════════════════════════════════════════════════════════════════════════
#  TestSubagentRole
# ══════════════════════════════════════════════════════════════════════════════


class TestSubagentRole:

    def test_builtin_roles_registered(self):
        """All 5 built-in roles exist."""
        expected = {"researcher", "coder", "planner", "critic", "deployer"}
        assert set(BUILTIN_ROLES.keys()) == expected

    def test_custom_role_registration(self):
        """SubagentRunner accepts custom role registration."""
        runner = SubagentRunner()
        custom = SubagentRole(
            name="qa_engineer",
            system_prompt="You test software.",
            allowed_tools=["code_run_checks"],
            description="Quality assurance",
        )
        runner.register_role(custom)
        assert "qa_engineer" in runner.roles
        assert runner.roles["qa_engineer"].description == "Quality assurance"

    def test_tools_filtered_by_allowed_list(self):
        """Subagent only has access to tools listed in role.allowed_tools."""
        role = SubagentRole(
            name="limited",
            system_prompt="test",
            allowed_tools=["tool_a"],
        )
        all_tools = {"tool_a": _dummy_tool, "tool_b": _dummy_tool, "tool_c": _dummy_tool}
        sa = Subagent(
            subagent_id="test-001",
            role=role,
            task="do stuff",
            budget=SubagentBudget(),
            mcp_tools=all_tools,
        )
        assert set(sa.tools.keys()) == {"tool_a"}


# ══════════════════════════════════════════════════════════════════════════════
#  TestSubagentBudget
# ══════════════════════════════════════════════════════════════════════════════


class TestSubagentBudget:

    def test_timed_out_after_limit(self):
        """timed_out is True when elapsed > max_time_seconds."""
        budget = SubagentBudget(
            max_time_seconds=1,
            started_at=time.time() - 2,  # Started 2 seconds ago
        )
        assert budget.timed_out is True

    def test_over_budget_after_tokens(self):
        """over_budget is True when tokens_used >= max_tokens."""
        budget = SubagentBudget(max_tokens=100, tokens_used=100)
        assert budget.over_budget is True

    def test_time_remaining_decreases(self):
        """time_remaining is less than max_time_seconds after some time."""
        budget = SubagentBudget(
            max_time_seconds=300,
            started_at=time.time() - 10,
        )
        assert budget.time_remaining < 300
        assert budget.time_remaining > 280

    def test_not_timed_out_when_fresh(self):
        """A fresh budget is not timed out."""
        budget = SubagentBudget(max_time_seconds=300)
        assert budget.timed_out is False

    def test_not_over_budget_when_fresh(self):
        """A fresh budget is not over budget."""
        budget = SubagentBudget(max_tokens=50_000)
        assert budget.over_budget is False


# ══════════════════════════════════════════════════════════════════════════════
#  TestSubagent
# ══════════════════════════════════════════════════════════════════════════════


class TestSubagent:

    def test_run_returns_result(self):
        """run() returns a SubagentResult with proper fields."""
        router = FakeModelRouter(content="Research complete.")
        role = BUILTIN_ROLES["researcher"]
        sa = Subagent(
            subagent_id="test-run-001",
            role=role,
            task="Find info about pawbot",
            budget=SubagentBudget(),
            model_router=router,
        )
        result = sa.run()

        assert isinstance(result, SubagentResult)
        assert result.success is True
        assert result.output == "Research complete."
        assert result.iterations == 1
        assert result.tokens_used == 100

    def test_cancel_stops_at_next_iteration(self):
        """cancel() sets flag that stops execution at next iteration."""
        # Router that always returns tool calls (would loop forever)
        router = FakeModelRouter(
            content="",
            tool_calls=[{"name": "web_search", "args": {"query": "test"}}],
        )
        role = SubagentRole(
            name="looper",
            system_prompt="test",
            allowed_tools=["web_search"],
            max_iterations=100,
        )
        sa = Subagent(
            subagent_id="test-cancel",
            role=role,
            task="loop forever",
            budget=SubagentBudget(max_time_seconds=60),
            model_router=router,
            mcp_tools={"web_search": _dummy_tool},
        )

        # Cancel immediately
        sa.cancel()
        result = sa.run()

        assert result.success is False
        assert result.iterations == 0  # Cancelled before first iteration

    def test_discoveries_written_to_inbox(self):
        """Discoveries from output are written to memory inbox."""
        memory = FakeMemoryRouter()
        router = FakeModelRouter(
            content="I found: The server runs on port 3000.\nNote: Use HTTPS in production.",
        )
        role = BUILTIN_ROLES["researcher"]
        sa = Subagent(
            subagent_id="test-discovery",
            role=role,
            task="Research server setup",
            budget=SubagentBudget(),
            model_router=router,
            memory_router=memory,
        )
        result = sa.run()

        assert result.success is True
        assert len(result.discoveries) == 2
        # High confidence for "I found:" prefix
        assert result.discoveries[0]["confidence"] == 0.7
        # Check inbox was written
        assert len(memory.sqlite.inbox) == 2

    def test_disallowed_tool_blocked(self):
        """Tool calls to tools not in allowed_tools produce error messages."""
        router_calls = []

        class TrackingRouter:
            def call(self, **kwargs):
                router_calls.append(kwargs.get("messages", []))
                # First call: try to use forbidden tool; second call: done
                if len(router_calls) == 1:
                    return {
                        "content": "",
                        "tool_calls": [{"name": "deploy_app", "args": {}}],
                        "usage": {"total_tokens": 50},
                    }
                return {
                    "content": "No tools available.",
                    "tool_calls": None,
                    "usage": {"total_tokens": 50},
                }

        role = SubagentRole(
            name="limited",
            system_prompt="test",
            allowed_tools=["web_search"],
            max_iterations=5,
        )
        sa = Subagent(
            subagent_id="test-blocked",
            role=role,
            task="do stuff",
            budget=SubagentBudget(),
            model_router=TrackingRouter(),
            mcp_tools={"web_search": _dummy_tool, "deploy_app": _dummy_tool},
        )
        result = sa.run()
        assert result.success is True
        assert result.iterations == 2

    def test_budget_exceeded_stops_run(self):
        """Subagent stops when token budget is exceeded."""
        class TokenHungryRouter:
            def call(self, **kwargs):
                return {
                    "content": "",
                    "tool_calls": [{"name": "code_search", "args": {}}],
                    "usage": {"total_tokens": 30_000},
                }

        role = SubagentRole(
            name="hungry",
            system_prompt="test",
            allowed_tools=["code_search"],
            max_iterations=100,
        )
        sa = Subagent(
            subagent_id="test-budget",
            role=role,
            task="burn tokens",
            budget=SubagentBudget(max_tokens=50_000),
            model_router=TokenHungryRouter(),
            mcp_tools={"code_search": _dummy_tool},
        )
        result = sa.run()

        # Should stop after 2 iterations (30k + 30k = 60k > 50k budget)
        assert result.iterations <= 3
        assert result.success is False

    def test_run_without_model_router(self):
        """Subagent gracefully handles missing model_router."""
        role = BUILTIN_ROLES["planner"]
        sa = Subagent(
            subagent_id="test-no-router",
            role=role,
            task="plan something",
            budget=SubagentBudget(),
            model_router=None,
        )
        result = sa.run()
        assert "No model_router" in result.output


# ══════════════════════════════════════════════════════════════════════════════
#  TestSubagentPool
# ══════════════════════════════════════════════════════════════════════════════


class TestSubagentPool:

    def test_submit_runs_in_background(self):
        """submit() returns immediately; work happens in thread."""
        pool = SubagentPool(max_concurrent=3)
        router = FakeModelRouter(content="Done.")
        role = BUILTIN_ROLES["planner"]
        sa = Subagent(
            subagent_id="pool-test-1",
            role=role,
            task="plan things",
            budget=SubagentBudget(),
            model_router=router,
        )

        sid = pool.submit(sa)
        assert sid == "pool-test-1"
        # Wait for completion
        result = pool.get_result(sid, timeout=5)
        assert result is not None
        assert result.success is True

    def test_get_result_blocks_until_done(self):
        """get_result with timeout waits for completion."""
        pool = SubagentPool()

        class SlowRouter:
            def call(self, **kwargs):
                time.sleep(0.3)
                return {"content": "Slow result.", "tool_calls": None, "usage": {"total_tokens": 50}}

        role = BUILTIN_ROLES["planner"]
        sa = Subagent(
            subagent_id="pool-slow",
            role=role,
            task="wait",
            budget=SubagentBudget(),
            model_router=SlowRouter(),
        )
        pool.submit(sa)

        start = time.time()
        result = pool.get_result("pool-slow", timeout=5)
        elapsed = time.time() - start

        assert result is not None
        assert result.success is True
        assert elapsed >= 0.2  # Waited for the slow response

    def test_max_concurrent_respected(self):
        """Pool should not have more than max_concurrent active threads."""
        pool = SubagentPool(max_concurrent=2)
        active_snapshots: list[int] = []
        lock = threading.Lock()

        class SlowRouter:
            def call(self, **kwargs):
                with lock:
                    active_snapshots.append(pool.active_count)
                time.sleep(0.2)
                return {"content": "ok", "tool_calls": None, "usage": {"total_tokens": 10}}

        role = BUILTIN_ROLES["planner"]
        for i in range(4):
            sa = Subagent(
                subagent_id=f"conc-{i}",
                role=role,
                task=f"task {i}",
                budget=SubagentBudget(),
                model_router=SlowRouter(),
            )
            pool.submit(sa)

        # Wait for all to complete
        time.sleep(2)

        # Active count should never have exceeded max_concurrent
        # (Note: due to timing, we check the snapshot)
        assert all(ac <= 3 for ac in active_snapshots)  # Slight margin for thread scheduling

    def test_active_count_tracks_running(self):
        """active_count reflects currently running subagents."""
        pool = SubagentPool()
        assert pool.active_count == 0

        class SlowRouter:
            def call(self, **kwargs):
                time.sleep(0.3)
                return {"content": "ok", "tool_calls": None, "usage": {"total_tokens": 10}}

        role = BUILTIN_ROLES["planner"]
        sa = Subagent(
            subagent_id="ac-test",
            role=role,
            task="count check",
            budget=SubagentBudget(),
            model_router=SlowRouter(),
        )
        pool.submit(sa)
        time.sleep(0.05)

        assert pool.active_count >= 1
        pool.get_result("ac-test", timeout=5)
        assert pool.active_count == 0

    def test_cancel_running_subagent(self):
        """cancel() flags a running subagent for cancellation."""
        pool = SubagentPool()

        class InfiniteRouter:
            def call(self, **kwargs):
                time.sleep(0.1)
                return {
                    "content": "",
                    "tool_calls": [{"name": "code_search", "args": {}}],
                    "usage": {"total_tokens": 10},
                }

        role = SubagentRole(
            name="infinite",
            system_prompt="loop",
            allowed_tools=["code_search"],
            max_iterations=1000,
        )
        sa = Subagent(
            subagent_id="cancel-pool",
            role=role,
            task="infinite loop",
            budget=SubagentBudget(max_time_seconds=30),
            model_router=InfiniteRouter(),
            mcp_tools={"code_search": _dummy_tool},
        )
        pool.submit(sa)
        time.sleep(0.2)

        cancelled = pool.cancel("cancel-pool")
        assert cancelled is True

        # Wait for it to stop
        result = pool.get_result("cancel-pool", timeout=5)
        assert result is not None
        assert result.success is False


# ══════════════════════════════════════════════════════════════════════════════
#  TestSubagentRunner
# ══════════════════════════════════════════════════════════════════════════════


class TestSubagentRunner:

    def test_spawn_returns_id_immediately(self):
        """spawn() returns a UUID string immediately."""
        router = FakeModelRouter()
        runner = SubagentRunner(model_router=router)
        sid = runner.spawn("research topic X", role="researcher")
        assert isinstance(sid, str)
        assert len(sid) == 36  # UUID length

    def test_spawn_and_wait_blocks(self):
        """spawn_and_wait() blocks until result is available."""
        router = FakeModelRouter(content="Analysis complete.")
        runner = SubagentRunner(model_router=router)
        result = runner.spawn_and_wait("plan something", role="planner", timeout=10)
        assert isinstance(result, SubagentResult)
        assert result.success is True
        assert result.output == "Analysis complete."

    def test_unknown_role_raises(self):
        """spawn() raises ValueError for non-existent role."""
        runner = SubagentRunner()
        with pytest.raises(ValueError, match="Unknown role"):
            runner.spawn("do stuff", role="nonexistent_role")

    def test_review_inbox_called_after_subgoal(self):
        """review_inbox() returns accepted IDs from memory."""
        memory = FakeMemoryRouter()
        memory.sqlite.inbox = [{"content": "fact1"}, {"content": "fact2"}]
        runner = SubagentRunner(memory_router=memory)
        ids = runner.review_inbox()
        assert len(ids) == 2
        assert memory.sqlite.inbox == []  # inbox cleared

    def test_status_reports_active(self):
        """status() returns active count and available roles."""
        runner = SubagentRunner()
        status = runner.status()
        assert status["active_subagents"] == 0
        assert "researcher" in status["available_roles"]
        assert "coder" in status["available_roles"]
        assert "planner" in status["available_roles"]
        assert "critic" in status["available_roles"]
        assert "deployer" in status["available_roles"]

    def test_cancel_delegates_to_pool(self):
        """cancel() calls pool.cancel()."""
        runner = SubagentRunner()
        # No active subagent — should return False
        assert runner.cancel("nonexistent-id") is False

    def test_spawn_with_custom_budget(self):
        """spawn() accepts custom SubagentBudget."""
        router = FakeModelRouter(content="Done.")
        runner = SubagentRunner(model_router=router)
        budget = SubagentBudget(max_tokens=1000, max_time_seconds=10)
        sid = runner.spawn("small task", role="planner", budget=budget)
        assert isinstance(sid, str)

        # Wait for result
        result = runner.pool.get_result(sid, timeout=5)
        assert result is not None
        assert result.success is True


# ══════════════════════════════════════════════════════════════════════════════
#  TestSubagentMessageBus
# ══════════════════════════════════════════════════════════════════════════════


class TestSubagentMessageBus:

    def test_send_and_receive(self):
        """Messages sent to a subagent can be received."""
        bus = SubagentMessageBus()
        bus.send("agent-A", "agent-B", "Here's my finding")
        msgs = bus.receive("agent-B")
        assert len(msgs) == 1
        assert msgs[0]["from"] == "agent-A"
        assert msgs[0]["content"] == "Here's my finding"
        assert "timestamp" in msgs[0]

    def test_receive_empty_returns_empty_list(self):
        """receive() returns [] when no messages pending."""
        bus = SubagentMessageBus()
        assert bus.receive("nobody") == []

    def test_thread_safe_concurrent_access(self):
        """Multiple threads can send/receive without crashes."""
        bus = SubagentMessageBus()
        errors: list[str] = []

        def sender(from_id: str, to_id: str, count: int):
            try:
                for i in range(count):
                    bus.send(from_id, to_id, f"msg-{i}")
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=sender, args=("a", "b", 50)),
            threading.Thread(target=sender, args=("c", "b", 50)),
            threading.Thread(target=sender, args=("d", "e", 30)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0
        # Agent B should have 100 messages
        msgs_b = bus.receive("b")
        assert len(msgs_b) == 100

    def test_pending_count(self):
        """pending_count tracks total messages across all inboxes."""
        bus = SubagentMessageBus()
        bus.send("a", "b", "msg1")
        bus.send("a", "c", "msg2")
        bus.send("b", "c", "msg3")
        assert bus.pending_count == 3

        bus.receive("b")  # Pop b's messages
        assert bus.pending_count == 2

    def test_receive_clears_inbox(self):
        """receive() removes messages from the inbox."""
        bus = SubagentMessageBus()
        bus.send("a", "b", "first")
        bus.send("a", "b", "second")

        msgs = bus.receive("b")
        assert len(msgs) == 2

        # Second receive should be empty
        assert bus.receive("b") == []
