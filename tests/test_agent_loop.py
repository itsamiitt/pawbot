"""Tests for Phase 2 — Agent Loop Intelligence.

Tests cover:
- Feature 2.1: ComplexityClassifier and system path routing
- Feature 2.2: Pre-task reflection check
- Feature 2.3: Post-task learning
- Feature 2.4: ThoughtTreePlanner
- Feature 2.5: Self-correction protocol
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pawbot.agent.loop import (
    SYSTEM_1_MAX,
    SYSTEM_1_5_MAX,
    SYSTEM_PATHS,
    AgentLoop,
    ComplexityClassifier,
    ThoughtTreePlanner,
    get_system_path,
)


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def classifier():
    return ComplexityClassifier()


@pytest.fixture
def mock_provider():
    """Create a mock LLM provider."""
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock()
    return provider


@pytest.fixture
def mock_memory():
    """Create a mock memory router."""
    memory = MagicMock()
    memory.search.return_value = []
    memory.save.return_value = "test-id"
    memory.update.return_value = True
    return memory


@pytest.fixture
def mock_bus():
    """Create a mock message bus."""
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    return bus


@pytest.fixture
def agent_loop(mock_bus, mock_provider, tmp_path):
    """Create an AgentLoop instance for testing."""
    loop = AgentLoop(
        bus=mock_bus,
        provider=mock_provider,
        workspace=tmp_path,
    )
    loop._session_meta = {}
    return loop


# ═══════════════════════════════════════════════════════════════════════════
# Feature 2.1: ComplexityClassifier
# ═══════════════════════════════════════════════════════════════════════════


class TestComplexityClassifier:
    """Test the ComplexityClassifier scoring system."""

    def test_short_message_scores_low(self, classifier):
        """Short, simple messages should score near 0."""
        score = classifier.score("hello")
        assert score <= 0.1
        assert score >= 0.0

    def test_hi_message_scores_zero(self, classifier):
        """A simple 'hi' message should score 0."""
        score = classifier.score("hi")
        assert score == 0.0

    def test_complex_keywords_raise_score(self, classifier):
        """Messages with complexity keywords should score higher."""
        score = classifier.score("Please refactor the authentication module")
        assert score >= 0.2  # At least the keyword signal

    def test_multiple_keywords_accumulate(self, classifier):
        """Multiple signals should accumulate."""
        score = classifier.score(
            "debug and refactor the deploy.py and config.js files. "
            "The error traceback shows a crash."
        )
        # keyword (0.2) + files (0.15) + failure words (0.15) + sentences (0.1) = 0.6+
        assert score >= 0.5

    def test_urgency_adds_score(self, classifier):
        """Urgency signals should increase the score."""
        score_normal = classifier.score("fix the login page")
        score_urgent = classifier.score("urgent fix the login page asap")
        assert score_urgent > score_normal

    def test_score_capped_at_1_0(self, classifier):
        """Score should never exceed 1.0."""
        # Craft a message with ALL signals maxed
        long_msg = " ".join(["word"] * 150)  # > 100 words
        complex_msg = (
            f"why refactor deploy and debug the broken error traceback crash "
            f"in file.py and config.js and util.ts urgent asap. "
            f"First sentence. Second sentence. Third sentence. Fourth sentence. "
            f"{long_msg}"
        )
        score = classifier.score(complex_msg)
        assert score <= 1.0

    def test_system_path_selection(self):
        """get_system_path should route correctly based on score."""
        assert get_system_path(0.0) == "system_1"
        assert get_system_path(0.1) == "system_1"
        assert get_system_path(0.3) == "system_1"
        assert get_system_path(0.31) == "system_1_5"
        assert get_system_path(0.5) == "system_1_5"
        assert get_system_path(0.7) == "system_1_5"
        assert get_system_path(0.71) == "system_2"
        assert get_system_path(1.0) == "system_2"

    def test_system_path_boundaries(self):
        """Test exact boundary values."""
        assert get_system_path(SYSTEM_1_MAX) == "system_1"
        assert get_system_path(SYSTEM_1_5_MAX) == "system_1_5"
        assert get_system_path(SYSTEM_1_5_MAX + 0.01) == "system_2"

    def test_file_references_detected(self, classifier):
        """Messages referencing files should get a higher score."""
        score = classifier.score("look at utils.py and config.json")
        assert score >= 0.15

    def test_why_questions_add_score(self, classifier):
        """Questions starting with 'why' should increase score."""
        score = classifier.score("why does the application crash on startup")
        assert score > classifier.score("the application crashes on startup")

    def test_how_does_questions_add_score(self, classifier):
        """Questions starting with 'how does' should increase score."""
        score = classifier.score("how does the authentication system work")
        assert score > 0.0

    def test_failure_words_increase_score(self, classifier):
        """Messages mentioning errors/failures should score higher."""
        score = classifier.score("the build failed with a traceback exception")
        assert score >= 0.15

    def test_long_messages_score_higher(self, classifier):
        """Messages > 100 words should get a length bonus."""
        short = classifier.score("hello world")
        long_msg = " ".join(["word"] * 120)
        long_score = classifier.score(long_msg)
        assert long_score > short

    def test_system_paths_config(self):
        """Verify SYSTEM_PATHS config has the expected structure."""
        assert "system_1" in SYSTEM_PATHS
        assert "system_1_5" in SYSTEM_PATHS
        assert "system_2" in SYSTEM_PATHS
        assert SYSTEM_PATHS["system_1"]["max_iterations"] == 5
        assert SYSTEM_PATHS["system_1_5"]["max_iterations"] == 15
        assert SYSTEM_PATHS["system_2"]["max_iterations"] == 50
        assert SYSTEM_PATHS["system_2"]["use_tree_of_thoughts"] is True
        assert SYSTEM_PATHS["system_2"]["pre_task_reflection"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Feature 2.2: Pre-Task Reflection Check
# ═══════════════════════════════════════════════════════════════════════════


class TestPreTaskReflection:

    def test_loads_reflections_for_system_2(self, agent_loop, mock_memory):
        """System 2 tasks should load relevant reflections from memory."""
        mock_memory.search.return_value = [
            {"type": "reflection", "confidence": 0.9, "rule": "verify imports first"},
            {"type": "reflection", "confidence": 0.8, "rule": "check logs"},
            {"type": "episode", "confidence": 0.9},  # should be filtered out
        ]
        agent_loop._memory_router = mock_memory

        from pawbot.session.manager import Session
        session = Session(key="test:session")

        agent_loop._run_pre_task_reflection("debug the login module", session)

        assert "pre_task_reflections" in session.metadata
        assert len(session.metadata["pre_task_reflections"]) == 2
        assert all(r["type"] == "reflection" for r in session.metadata["pre_task_reflections"])

    def test_skips_low_confidence_reflections(self, agent_loop, mock_memory):
        """Reflections with confidence <= 0.7 should be filtered out."""
        mock_memory.search.return_value = [
            {"type": "reflection", "confidence": 0.4, "rule": "low confidence"},
            {"type": "reflection", "confidence": 0.5, "rule": "also low"},
        ]
        agent_loop._memory_router = mock_memory

        from pawbot.session.manager import Session
        session = Session(key="test:session")

        agent_loop._run_pre_task_reflection("some task", session)

        assert session.metadata.get("pre_task_reflections") is None

    def test_no_op_for_system_1(self, agent_loop, mock_memory):
        """Pre-task reflection should not be called for System 1 tasks."""
        agent_loop._memory_router = mock_memory
        # Just verify that _run_pre_task_reflection doesn't fail when called
        # In actual flow, it's only called for system_2 path
        from pawbot.session.manager import Session
        session = Session(key="test:session")

        # Simulate system_1 path — no reflection is triggered
        complexity = agent_loop._classifier.score("hello")
        path = get_system_path(complexity)
        path_config = SYSTEM_PATHS[path]

        # Should not call reflection
        assert path == "system_1"
        assert not path_config.get("pre_task_reflection")

    def test_loads_procedures(self, agent_loop, mock_memory):
        """System 2 tasks should load proven procedures from memory."""
        mock_memory.search.return_value = [
            {"type": "procedure", "success_count": 5, "name": "deploy_flow"},
        ]
        agent_loop._memory_router = mock_memory

        from pawbot.session.manager import Session
        session = Session(key="test:session")

        agent_loop._run_pre_task_reflection("deploy the application", session)

        assert "pre_task_procedure" in session.metadata
        assert session.metadata["pre_task_procedure"]["name"] == "deploy_flow"

    def test_graceful_without_memory(self, agent_loop):
        """Should handle missing memory router gracefully."""
        agent_loop._memory_router = None

        from pawbot.session.manager import Session
        session = Session(key="test:session")

        # Should not raise
        agent_loop._run_pre_task_reflection("some task", session)
        assert "pre_task_reflections" not in session.metadata


# ═══════════════════════════════════════════════════════════════════════════
# Feature 2.3: Post-Task Learning
# ═══════════════════════════════════════════════════════════════════════════


class TestPostTaskLearning:

    def test_saves_episode_on_success(self, agent_loop, mock_memory):
        """Successful tasks should save an episode to memory."""
        agent_loop._memory_router = mock_memory
        agent_loop._session_meta = {"system_path": "system_1"}

        agent_loop.__post_task_learning_sync = agent_loop._AgentLoop__post_task_learning_sync
        agent_loop.__post_task_learning_sync(
            task="test task",
            success=True,
            execution_trace=[{"action": "read_file", "result": "ok"}],
            failure_reason=None,
            session_key="test:session",
        )

        # Should have called save with "episode" type
        save_calls = [c for c in mock_memory.save.call_args_list if c[0][0] == "episode"]
        assert len(save_calls) > 0
        episode = save_calls[0][0][1]
        assert episode["success"] is True
        assert "Completed" in episode["text"]

    def test_saves_procedure_for_novel_system2_task(self, agent_loop, mock_memory):
        """Novel System 2 tasks should create a new procedure."""
        mock_memory.search.return_value = []  # No existing procedure
        agent_loop._memory_router = mock_memory
        agent_loop._session_meta = {"system_path": "system_2"}

        agent_loop._AgentLoop__post_task_learning_sync(
            task="complex architecture task",
            success=True,
            execution_trace=[
                {"action": "read_file(main.py)", "result": "ok"},
                {"action": "write_file(main.py)", "result": "ok"},
            ],
            failure_reason=None,
            session_key="test:session",
        )

        # Should save both procedure and episode
        save_types = [c[0][0] for c in mock_memory.save.call_args_list]
        assert "procedure" in save_types
        assert "episode" in save_types

    def test_increments_existing_procedure_count(self, agent_loop, mock_memory):
        """Existing similar procedures should get their success count incremented."""
        mock_memory.search.return_value = [
            {
                "id": "proc-1",
                "type": "procedure",
                "similarity": 0.9,
                "success_count": 3,
                "name": "existing procedure",
            }
        ]
        agent_loop._memory_router = mock_memory
        agent_loop._session_meta = {"system_path": "system_2"}

        agent_loop._AgentLoop__post_task_learning_sync(
            task="matching task",
            success=True,
            execution_trace=[],
            failure_reason=None,
            session_key="test:session",
        )

        # Should have called update on the existing procedure
        assert mock_memory.update.called
        update_args = mock_memory.update.call_args
        assert update_args[0][0] == "proc-1"
        assert update_args[0][1]["success_count"] == 4

    def test_saves_reflection_on_failure(self, agent_loop, mock_memory):
        """Failed tasks should save a reflection to memory."""
        agent_loop._memory_router = mock_memory
        agent_loop._session_meta = {"system_path": "system_1"}

        agent_loop._AgentLoop__post_task_learning_sync(
            task="failing task",
            success=False,
            execution_trace=[
                {"action": "exec(npm test)", "result": "Error: test failed"},
            ],
            failure_reason="test suite failed with 3 errors",
            session_key="test:session",
        )

        save_types = [c[0][0] for c in mock_memory.save.call_args_list]
        assert "reflection" in save_types
        assert "episode" in save_types

        # Get the episode call
        episode_calls = [c for c in mock_memory.save.call_args_list if c[0][0] == "episode"]
        assert episode_calls[0][0][1]["success"] is False

    def test_runs_async_no_blocking(self, agent_loop, mock_memory):
        """Post-task learning should run in a background thread without blocking."""
        agent_loop._memory_router = mock_memory
        agent_loop._session_meta = {"system_path": "system_1"}

        start = time.time()
        agent_loop._run_post_task_learning(
            task="test",
            success=True,
            execution_trace=[],
            failure_reason=None,
            session_key="test",
        )
        elapsed = time.time() - start

        # Should return almost immediately (thread spawn is fast)
        assert elapsed < 1.0

    def test_extract_steps_from_trace(self, agent_loop):
        """Should extract readable step summaries from trace."""
        trace = [
            {"action": "read_file(main.py)", "result": "file contents here"},
            {"action": "write_file(main.py)", "result": "written successfully"},
            {},  # should be skipped (no action)
        ]
        steps = agent_loop._extract_steps_from_trace(trace)
        assert len(steps) == 2
        assert "1. read_file(main.py)" in steps[0]
        assert "2. write_file(main.py)" in steps[1]

    def test_generate_reflection_classifies_timeout(self, agent_loop):
        """Timeout errors should be classified correctly."""
        reflection = agent_loop._generate_reflection_sync(
            trace=[], failure_reason="Connection timeout after 30s"
        )
        assert reflection is not None
        assert reflection["failure_type"] == "timeout"

    def test_generate_reflection_classifies_missing(self, agent_loop):
        """Missing resource errors should be classified correctly."""
        reflection = agent_loop._generate_reflection_sync(
            trace=[], failure_reason="File not found: config.json"
        )
        assert reflection is not None
        assert reflection["failure_type"] == "missing_check"

    def test_generate_reflection_none_without_reason(self, agent_loop):
        """Should return None if no failure reason is given."""
        reflection = agent_loop._generate_reflection_sync(trace=[], failure_reason=None)
        assert reflection is None


# ═══════════════════════════════════════════════════════════════════════════
# Feature 2.4: ThoughtTreePlanner
# ═══════════════════════════════════════════════════════════════════════════


class TestThoughtTreePlanner:

    @pytest.fixture
    def planner(self, mock_provider, mock_memory):
        return ThoughtTreePlanner(
            provider=mock_provider,
            model="test-model",
            memory=mock_memory,
        )

    @pytest.mark.asyncio
    async def test_generates_three_approaches(self, planner, mock_provider):
        """Plan should generate and score multiple approaches."""
        # Mock LLM response with 3 approaches
        approaches_json = json.dumps([
            {"name": "A", "core_idea": "idea a", "trade_offs": "none",
             "estimated_complexity": "low", "risk_level": "low"},
            {"name": "B", "core_idea": "idea b", "trade_offs": "some",
             "estimated_complexity": "medium", "risk_level": "medium"},
            {"name": "C", "core_idea": "idea c", "trade_offs": "many",
             "estimated_complexity": "high", "risk_level": "high"},
        ])
        mock_response = MagicMock()
        mock_response.content = approaches_json
        mock_provider.chat.return_value = mock_response

        result = await planner.plan("refactor authentication")

        assert "primary" in result
        assert "fallback" in result
        assert "all" in result
        assert len(result["all"]) == 3

    @pytest.mark.asyncio
    async def test_selects_highest_score(self, planner, mock_provider):
        """The primary approach should have the highest score."""
        approaches_json = json.dumps([
            {"name": "Safe", "core_idea": "safe", "trade_offs": "",
             "estimated_complexity": "low", "risk_level": "low"},
            {"name": "Risky", "core_idea": "risky", "trade_offs": "",
             "estimated_complexity": "high", "risk_level": "high"},
        ])
        mock_response = MagicMock()
        mock_response.content = approaches_json
        mock_provider.chat.return_value = mock_response

        result = await planner.plan("some task")

        # "Safe" should score higher (low risk = no penalty, low complexity = bonus)
        assert result["primary"]["name"] == "Safe"
        assert result["primary"]["score"] >= result["fallback"]["score"]

    @pytest.mark.asyncio
    async def test_provides_fallback(self, planner, mock_provider):
        """Plan should provide a fallback in case primary fails."""
        approaches_json = json.dumps([
            {"name": "A", "core_idea": "a", "trade_offs": "",
             "estimated_complexity": "medium", "risk_level": "low"},
            {"name": "B", "core_idea": "b", "trade_offs": "",
             "estimated_complexity": "medium", "risk_level": "medium"},
        ])
        mock_response = MagicMock()
        mock_response.content = approaches_json
        mock_provider.chat.return_value = mock_response

        result = await planner.plan("task")
        assert result["fallback"] is not None
        assert result["fallback"]["name"] != result["primary"]["name"]

    @pytest.mark.asyncio
    async def test_handles_llm_failure(self, planner, mock_provider):
        """Should return a default approach if LLM fails."""
        mock_provider.chat.side_effect = Exception("API error")

        result = await planner.plan("failing task")

        assert result["primary"]["name"] == "default"

    @pytest.mark.asyncio
    async def test_logs_rejected_approaches(self, planner, mock_provider):
        """Should log which approaches were rejected (tested via result structure)."""
        approaches_json = json.dumps([
            {"name": "A", "core_idea": "a", "trade_offs": "",
             "estimated_complexity": "low", "risk_level": "low"},
            {"name": "B", "core_idea": "b", "trade_offs": "",
             "estimated_complexity": "medium", "risk_level": "medium"},
            {"name": "C", "core_idea": "c", "trade_offs": "",
             "estimated_complexity": "high", "risk_level": "high"},
        ])
        mock_response = MagicMock()
        mock_response.content = approaches_json
        mock_provider.chat.return_value = mock_response

        result = await planner.plan("complex task")

        # 'all' should contain all 3, 'primary' should be the best
        assert len(result["all"]) == 3
        primary_name = result["primary"]["name"]
        all_names = [a["name"] for a in result["all"]]
        rejected = [n for n in all_names if n != primary_name]
        assert len(rejected) == 2

    def test_risk_penalty(self, planner):
        """High-risk approaches should receive a score penalty."""
        low_risk = {"name": "safe", "risk_level": "low", "estimated_complexity": "medium"}
        high_risk = {"name": "risky", "risk_level": "high", "estimated_complexity": "medium"}

        scored_low = planner._score_approach(low_risk, "task")
        scored_high = planner._score_approach(high_risk, "task")

        assert scored_low["score"] > scored_high["score"]

    def test_memory_precedent_bonus(self, planner, mock_memory):
        """Approaches with past decision precedent should get a bonus."""
        mock_memory.search.return_value = [
            {"type": "decision", "name": "past decision"},
        ]
        approach = {"name": "A", "risk_level": "low", "estimated_complexity": "medium"}

        scored = planner._score_approach(approach, "task")
        assert scored["score"] >= 0.65  # baseline 0.5 + precedent 0.15

    def test_graceful_without_memory(self, mock_provider):
        """Planner should work even without memory."""
        planner = ThoughtTreePlanner(
            provider=mock_provider,
            model="test-model",
            memory=None,
        )
        approach = {"name": "A", "risk_level": "low", "estimated_complexity": "low"}
        scored = planner._score_approach(approach, "task")
        assert scored["score"] >= 0.5


# ═══════════════════════════════════════════════════════════════════════════
# Feature 2.5: Self-Correction Protocol
# ═══════════════════════════════════════════════════════════════════════════


class TestSelfCorrection:

    def test_level_1_at_failure_1_2(self, agent_loop):
        """Failures 1-2 should trigger Level 1 (retry with variation)."""
        agent_loop._record_failure("some error", "test_action")
        assert agent_loop.failure_count == 1
        assert "retry_hint" in agent_loop._session_meta

        agent_loop._record_failure("another error", "test_action")
        assert agent_loop.failure_count == 2
        assert "retry_hint" in agent_loop._session_meta

    def test_level_2_at_failure_3_4_loads_reflections(self, agent_loop, mock_memory):
        """Failures 3-4 should trigger Level 2 (replan with reflections)."""
        agent_loop._memory_router = mock_memory
        mock_memory.search.return_value = [
            {"type": "reflection", "rule": "check deps first"},
        ]

        for i in range(3):
            agent_loop._record_failure(f"error {i}", "action")

        assert agent_loop.failure_count == 3
        assert agent_loop._session_meta.get("replan_signal") is True

    def test_level_3_switches_to_fallback_approach(self, agent_loop):
        """Failures 5-6 should trigger Level 3 (strategy change)."""
        agent_loop._session_meta["selected_approach"] = {
            "primary": {"name": "A"},
            "fallback": {"name": "B"},
        }

        for i in range(5):
            agent_loop._record_failure(f"error {i}", "action")

        assert agent_loop._session_meta.get("using_fallback_approach") is True
        assert agent_loop._session_meta["active_approach"]["name"] == "B"
        # Failure count should be reset after switching
        assert agent_loop.failure_count == 0

    def test_level_3_without_fallback(self, agent_loop):
        """Level 3 without a ToT fallback should set strategy_change_signal."""
        # No selected_approach set
        for i in range(5):
            agent_loop._record_failure(f"error {i}", "action")

        assert agent_loop._session_meta.get("strategy_change_signal") is True

    def test_level_4_escalates_to_user_and_pauses(self, agent_loop):
        """7+ failures should trigger Level 4 (escalation to user)."""
        for i in range(7):
            agent_loop._record_failure(f"error {i}", "action")

        assert agent_loop._session_meta.get("paused_for_escalation") is True
        assert "escalation_message" in agent_loop._session_meta
        assert "I've tried" in agent_loop._session_meta["escalation_message"]

    def test_never_loops_forever(self, agent_loop):
        """Self-correction should not cause infinite loops."""
        # Record many failures — should always terminate with escalation
        for i in range(20):
            agent_loop._record_failure(f"error {i}", "action")

        # Should be paused for escalation, not stuck in a loop
        assert agent_loop._session_meta.get("paused_for_escalation") is True

    def test_failure_log_maintained(self, agent_loop):
        """Failure log should track all failures with metadata."""
        agent_loop._record_failure("error 1", "read_file")
        agent_loop._record_failure("error 2", "exec")

        assert len(agent_loop.failure_log) == 2
        assert agent_loop.failure_log[0]["error"] == "error 1"
        assert agent_loop.failure_log[0]["action"] == "read_file"
        assert "timestamp" in agent_loop.failure_log[0]
        assert agent_loop.failure_log[1]["error"] == "error 2"
        assert agent_loop.failure_log[1]["action"] == "exec"

    def test_escalation_message_format(self, agent_loop):
        """Escalation message should include options and error summary."""
        for i in range(7):
            agent_loop._record_failure(f"some error {i}", "action")

        msg = agent_loop._session_meta["escalation_message"]
        assert "Options:" in msg
        assert "A)" in msg
        assert "B)" in msg
        assert "C)" in msg

    def test_error_truncation(self, agent_loop):
        """Errors should be truncated to 200 chars."""
        long_error = "x" * 500
        agent_loop._record_failure(long_error, "action")
        assert len(agent_loop.failure_log[0]["error"]) <= 200
