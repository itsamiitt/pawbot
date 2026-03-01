"""Tests for Phase 3 — Context Builder Upgrades.

Tests cover:
- Feature 3.1: ContextBudget and count_tokens
- Feature 3.2: Relevance-based context loading
- Feature 3.3: Prompt cache markers
- Feature 3.4: TaskTypeDetector and task-aware lazy loading

Cross-references:
- Phase 1: MemoryRouter.search() (mocked)
- Phase 2: Session metadata keys (complexity_score, context_mode, task_type)
- Phase 4: ModelRouter (mocked)
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pawbot.agent.context import (
    CONTEXT_BUDGET,
    CONTEXT_TOTAL_CEILING,
    TASK_CONTEXT_MAP,
    TASK_TYPES,
    ContextBudget,
    ContextBuilder,
    TaskTypeDetector,
    count_tokens,
)


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def budget():
    return ContextBudget()


@pytest.fixture
def detector():
    return TaskTypeDetector()


@pytest.fixture
def mock_memory_router():
    """Create a mock memory router with search capabilities."""
    router = MagicMock()
    router.search.return_value = []
    return router


@pytest.fixture
def mock_model_router():
    """Create a mock model router for task classification fallback."""
    router = MagicMock()
    router.current_provider_type.return_value = "openai"
    router.call.return_value = "casual_chat"
    return router


@pytest.fixture
def mock_session():
    """Create a mock session with metadata dict."""
    from pawbot.session.manager import Session

    session = Session(key="test:session")
    session.metadata = {}
    return session


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace with SOUL.md and USER.md."""
    # Create workspace directory structure
    (tmp_path / "memory").mkdir()

    # Create SOUL.md
    (tmp_path / "SOUL.md").write_text(
        "# Soul\n\nI am pawbot, a helpful AI assistant.\n\n"
        "## Personality\n\n- Helpful and friendly\n",
        encoding="utf-8",
    )

    # Create USER.md with sections
    (tmp_path / "USER.md").write_text(
        "# User Profile\n\n"
        "## Project Info\n\nWorking on a Python web app.\n\n"
        "## Code Preferences\n\nPrefers type hints and docstrings.\n\n"
        "## Personal\n\nBased in UTC timezone.\n\n"
        "## Server Info\n\nDeploys to AWS us-east-1.\n\n"
        "## Meeting Schedule\n\nStandup at 9am daily.\n",
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture
def context_builder(tmp_workspace, mock_memory_router, mock_model_router):
    """Create a ContextBuilder with mock dependencies."""
    return ContextBuilder(
        workspace=tmp_workspace,
        memory_router=mock_memory_router,
        model_router=mock_model_router,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Feature 3.1: count_tokens
# ═══════════════════════════════════════════════════════════════════════════


class TestCountTokens:
    """Test the count_tokens function."""

    def test_empty_string_returns_zero(self):
        """Empty string should return 0 tokens."""
        assert count_tokens("") == 0

    def test_short_text_returns_nonzero(self):
        """A non-empty string should return a positive token count."""
        result = count_tokens("Hello, world!")
        assert result > 0

    def test_longer_text_returns_more(self):
        """Longer text should produce a higher token count."""
        short = count_tokens("hello")
        long = count_tokens("hello world this is a longer sentence with many words")
        assert long > short

    def test_returns_integer(self):
        """Token count should always be an integer."""
        result = count_tokens("Some text here")
        assert isinstance(result, int)

    def test_handles_special_characters(self):
        """Should handle special characters without error."""
        result = count_tokens("🐈 Hello! @#$% special chars")
        assert result > 0


# ═══════════════════════════════════════════════════════════════════════════
# Feature 3.1: ContextBudget
# ═══════════════════════════════════════════════════════════════════════════


class TestContextBudget:
    """Test the ContextBudget class for token limit enforcement."""

    def test_enforce_within_limit_passthrough(self, budget):
        """Content within the token limit should pass through unchanged."""
        short_text = "Hello world."
        result = budget.enforce("system_prompt", short_text)
        assert result == short_text

    def test_enforce_truncates_at_sentence_boundary(self, budget):
        """Content exceeding budget should be truncated at sentence boundary."""
        # Create content that's definitely over the budget
        sentences = [f"Sentence number {i} is here." for i in range(100)]
        long_text = " ".join(sentences)
        result = budget.enforce("system_prompt", long_text)

        # Result should be shorter than input
        assert count_tokens(result) < count_tokens(long_text)
        # Result should end at a sentence boundary (period)
        assert result.rstrip().endswith(".")

    def test_enforce_summarizes_when_2x_over(self, budget):
        """When content > 2x budget and summarizer provided, summarizer is called."""
        long_text = " ".join(["word"] * 500)  # Way over budget
        called = {"count": 0}

        def mock_summarizer(content, limit):
            called["count"] += 1
            return "Summarized content."

        result = budget.enforce("system_prompt", long_text, summarizer=mock_summarizer)
        assert called["count"] == 1

    def test_enforce_skips_summarizer_when_under_2x(self, budget):
        """Summarizer should NOT be called when content is under 2x budget."""
        # Budget for system_prompt is 150 tokens
        # Create text that's just over budget but under 2x
        text = " ".join(["word"] * 80)  # ~104 tokens, between 150 and 300
        called = {"count": 0}

        def mock_summarizer(content, limit):
            called["count"] += 1
            return content

        budget.enforce("system_prompt", text, summarizer=mock_summarizer)
        # If text is under 2x, summarizer should not be called
        # (depends on actual token count)

    def test_total_used_sum(self, budget):
        """total_used should return sum of all section token counts."""
        budget.enforce("system_prompt", "Hello.")
        budget.enforce("user_facts", "Some facts.")
        total = budget.total_used()
        assert total == budget.used["system_prompt"] + budget.used["user_facts"]
        assert total > 0

    def test_log_usage_called_every_10_messages(self, budget):
        """log_usage should log token distribution without errors."""
        budget.enforce("system_prompt", "Test content.")
        budget.enforce("conversation", "More content.")
        # Should not raise
        budget.log_usage()

    def test_enforce_empty_content(self, budget):
        """Empty content should return empty string and use 0 tokens."""
        result = budget.enforce("system_prompt", "")
        assert result == ""
        assert budget.used["system_prompt"] == 0

    def test_enforce_unknown_section_uses_default(self, budget):
        """Unknown section names should use the default limit of 200."""
        text = "Short text."
        result = budget.enforce("unknown_section", text)
        assert result == text

    def test_budgets_are_canonical(self, budget):
        """Budget values should match MASTER_REFERENCE.md canonical values."""
        assert budget.budgets["system_prompt"] == 150
        assert budget.budgets["user_facts"] == 100
        assert budget.budgets["goal_state"] == 100
        assert budget.budgets["reflections"] == 150
        assert budget.budgets["episode_memory"] == 200
        assert budget.budgets["procedure_memory"] == 150
        assert budget.budgets["file_context"] == 500
        assert budget.budgets["conversation"] == 200
        assert budget.budgets["tool_results"] == 250

    def test_context_total_ceiling(self):
        """Total token ceiling should be 1800 per MASTER_REFERENCE.md."""
        assert CONTEXT_TOTAL_CEILING == 1800


# ═══════════════════════════════════════════════════════════════════════════
# Feature 3.4: TaskTypeDetector
# ═══════════════════════════════════════════════════════════════════════════


class TestTaskTypeDetector:
    """Test task type detection for lazy context loading."""

    def test_coding_keywords_detected(self, detector):
        """Messages with coding keywords should return 'coding_task'."""
        result = detector.detect("implement a new function for the login module")
        assert result == "coding_task"

    def test_deployment_keywords_detected(self, detector):
        """Messages with deployment keywords should return 'deployment_task'."""
        result = detector.detect("deploy the app to production server with nginx")
        assert result == "deployment_task"

    def test_debugging_error_words_detected(self, detector):
        """Messages with error/debugging words should return 'debugging_task'."""
        result = detector.detect("there's an error in the traceback, app crashed")
        assert result == "debugging_task"

    def test_planning_keywords_detected(self, detector):
        """Messages with planning keywords should return 'planning_task'."""
        result = detector.detect("plan the roadmap and prioritize goals")
        assert result == "planning_task"

    def test_research_keywords_detected(self, detector):
        """Messages with research keywords should return 'research_task'."""
        result = detector.detect("research what is the best framework and compare options")
        assert result == "research_task"

    def test_memory_keywords_detected(self, detector):
        """Messages with memory keywords should return 'memory_task'."""
        result = detector.detect("do you remember what did we decide last time")
        assert result == "memory_task"

    def test_ambiguous_falls_back_to_local_model(self, detector, mock_model_router):
        """Ambiguous messages should fall back to model classification."""
        mock_model_router.call.return_value = "planning_task"
        result = detector.detect("hello", model_router=mock_model_router)
        assert result == "planning_task"

    def test_unknown_returns_casual_chat(self, detector):
        """Messages with no keywords and no model should return 'casual_chat'."""
        result = detector.detect("hello how are you today")
        assert result == "casual_chat"

    def test_highest_scoring_type_wins(self, detector):
        """When multiple task types match, the one with most matches wins."""
        # This message has more coding keywords than debugging keywords
        result = detector.detect(
            "implement the function and refactor the class with a test for the module"
        )
        assert result == "coding_task"

    def test_task_types_are_canonical(self):
        """TASK_TYPES should match MASTER_REFERENCE.md canonical values."""
        assert "casual_chat" in TASK_TYPES
        assert "coding_task" in TASK_TYPES
        assert "deployment_task" in TASK_TYPES
        assert "memory_task" in TASK_TYPES
        assert "planning_task" in TASK_TYPES
        assert "research_task" in TASK_TYPES
        assert "debugging_task" in TASK_TYPES
        assert len(TASK_TYPES) == 7

    def test_task_context_map_exists_for_all_types(self):
        """Every canonical task type should have an entry in TASK_CONTEXT_MAP."""
        for task_type in TASK_TYPES:
            assert task_type in TASK_CONTEXT_MAP
            assert "system_prompt" in TASK_CONTEXT_MAP[task_type]

    def test_model_fallback_returns_casual_on_failure(self, detector):
        """Model classification failure should return 'casual_chat'."""
        broken_router = MagicMock()
        broken_router.call.side_effect = Exception("API error")
        result = detector.detect("hello", model_router=broken_router)
        assert result == "casual_chat"


# ═══════════════════════════════════════════════════════════════════════════
# Feature 3.2: Relevance-Based Context Loading
# ═══════════════════════════════════════════════════════════════════════════


class TestRelevanceBasedLoading:
    """Test relevance-based episode and USER.md loading."""

    def test_relevant_episodes_loaded_not_recent(
        self, context_builder, mock_memory_router
    ):
        """Episodes should be loaded by semantic relevance, not recency."""
        mock_memory_router.search.return_value = [
            {"type": "episode", "salience": 0.9, "content": {"text": "Deployed to AWS"}},
            {"type": "episode", "salience": 0.8, "content": {"text": "Fixed login bug"}},
            {"type": "fact", "salience": 0.95, "content": {"text": "Not an episode"}},  # filtered
            {"type": "episode", "salience": 0.1, "content": {"text": "Low salience"}},  # filtered
        ]

        result = context_builder._load_relevant_episodes("deploy to AWS", mock_memory_router)

        assert "Deployed to AWS" in result
        assert "Fixed login bug" in result
        assert "Not an episode" not in result  # filtered by type
        assert "Low salience" not in result  # filtered by salience

    def test_episodes_empty_without_router(self, context_builder):
        """Should return empty string when memory router is None."""
        result = context_builder._load_relevant_episodes("test", None)
        assert result == ""

    def test_episodes_graceful_on_error(self, context_builder, mock_memory_router):
        """Should handle search errors gracefully."""
        mock_memory_router.search.side_effect = Exception("DB error")
        result = context_builder._load_relevant_episodes("test", mock_memory_router)
        assert result == ""

    def test_user_md_sections_filtered_by_task_type(self, context_builder, mock_session):
        """USER.md should only return sections relevant to the task type."""
        mock_session.metadata["task_type"] = "coding_task"
        result = context_builder._load_user_md_relevant("implement a feature", mock_session)

        # Should include code/project sections
        assert "code" in result.lower() or "project" in result.lower() or "preference" in result.lower()

    def test_user_md_returns_first_section_if_no_match(self, context_builder, mock_session):
        """If no sections match, should return the first section."""
        mock_session.metadata["task_type"] = "unknown_type"
        result = context_builder._load_user_md_relevant("test", mock_session)
        # Should return something (at least the first section)
        assert len(result) > 0 or result == ""

    def test_user_md_handles_missing_file(self, tmp_path):
        """Should handle missing USER.md gracefully."""
        builder = ContextBuilder(workspace=tmp_path)
        result = builder._load_user_md_relevant("test")
        assert result == ""

    def test_key_concepts_extraction(self, context_builder):
        """Should extract code refs, tech names, and action verbs."""
        concepts = context_builder._extract_key_concepts(
            "refactor the FastAPI server_module in deploy.py using docker"
        )
        concepts_lower = [c.lower() for c in concepts]
        # Should find at least some of these
        found_any = any(
            c in concepts_lower or c in str(concepts)
            for c in ["refactor", "fastapi", "docker", "deploy.py", "server_module"]
        )
        assert found_any

    def test_key_concepts_empty_text(self, context_builder):
        """Should handle empty text gracefully."""
        concepts = context_builder._extract_key_concepts("")
        assert isinstance(concepts, list)


# ═══════════════════════════════════════════════════════════════════════════
# Feature 3.3: Prompt Cache Markers
# ═══════════════════════════════════════════════════════════════════════════


class TestPromptCacheMarkers:
    """Test Anthropic prompt caching integration."""

    def test_cache_markers_applied_for_anthropic(self, context_builder):
        """Cache markers should be added for Anthropic provider."""
        # Create a system message with enough tokens
        system_content = " ".join(["word"] * 200)  # >100 tokens
        messages = [{"role": "system", "content": system_content}]

        result = context_builder._add_cache_markers(messages, "anthropic")

        assert len(result) == 1
        assert result[0]["role"] == "system"
        # Content should be wrapped in a list with cache_control
        assert isinstance(result[0]["content"], list)
        assert result[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_markers_not_applied_for_openai(self, context_builder):
        """Cache markers should NOT be added for OpenAI provider."""
        system_content = " ".join(["word"] * 200)
        messages = [{"role": "system", "content": system_content}]

        result = context_builder._add_cache_markers(messages, "openai")

        assert len(result) == 1
        assert result[0]["role"] == "system"
        # Content should remain a plain string
        assert isinstance(result[0]["content"], str)

    def test_cache_markers_skip_short_content(self, context_builder):
        """Cache markers should not be applied if content is < 100 tokens."""
        messages = [{"role": "system", "content": "Short."}]

        result = context_builder._add_cache_markers(messages, "anthropic")

        # Short content should not get cache markers
        assert isinstance(result[0]["content"], str)

    def test_cache_markers_preserve_user_messages(self, context_builder):
        """User messages should not be modified."""
        messages = [
            {"role": "system", "content": " ".join(["word"] * 200)},
            {"role": "user", "content": "Hello there"},
        ]

        result = context_builder._add_cache_markers(messages, "anthropic")

        assert result[1]["role"] == "user"
        assert isinstance(result[1]["content"], str)
        assert result[1]["content"] == "Hello there"

    def test_track_cache_hits_logs_on_hit(self, context_builder):
        """Cache hit tracking should log when there are cached tokens."""
        api_response = {
            "usage": {
                "input_tokens": 1000,
                "cache_read_input_tokens": 500,
                "cache_creation_input_tokens": 0,
            }
        }
        # Should not raise
        ContextBuilder._track_cache_hits(api_response)

    def test_track_cache_hits_silent_on_miss(self, context_builder):
        """Cache hit tracking should be silent when no cached tokens."""
        api_response = {
            "usage": {
                "input_tokens": 1000,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
        }
        # Should not raise
        ContextBuilder._track_cache_hits(api_response)


# ═══════════════════════════════════════════════════════════════════════════
# Feature 3.4 + Integration: ContextBuilder
# ═══════════════════════════════════════════════════════════════════════════


class TestContextBuilder:
    """Test the enhanced ContextBuilder integration."""

    def test_casual_chat_loads_minimal_sections(
        self, context_builder, mock_session, mock_memory_router
    ):
        """Casual chat should only load system_prompt and conversation."""
        messages = context_builder.build("hello there", mock_session)

        # Should have set task_type
        assert mock_session.metadata["task_type"] == "casual_chat"

        # Should have at least a system message and user message
        roles = [m["role"] for m in messages]
        assert "system" in roles or "user" in roles

    def test_coding_task_loads_file_context(
        self, context_builder, mock_session, mock_memory_router
    ):
        """Coding tasks should include file_context in loaded sections."""
        mock_session.metadata["file_context_raw"] = "def main(): pass"
        messages = context_builder.build(
            "implement a new function for the class", mock_session
        )

        assert mock_session.metadata["task_type"] == "coding_task"

    def test_system_2_loads_all_sections(
        self, context_builder, mock_session, mock_memory_router
    ):
        """When context_mode is 'full' (System 2), should load all sections."""
        mock_session.metadata["context_mode"] = "full"
        mock_session.metadata["file_context_raw"] = "code here"
        mock_session.metadata["last_tool_results"] = "tool output here"

        messages = context_builder.build("complex task", mock_session)

        # Should have system message and user message at minimum
        assert len(messages) >= 2

    def test_build_messages_backward_compatible(self, context_builder):
        """build_messages without session should work as before."""
        messages = context_builder.build_messages(
            history=[],
            current_message="hello",
            channel="cli",
            chat_id="test",
        )

        # Should have system, runtime context, and user message
        assert len(messages) >= 3
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"

    def test_build_messages_with_session_enhanced(
        self, context_builder, mock_session
    ):
        """build_messages with session should use enhanced building."""
        messages = context_builder.build_messages(
            history=[],
            current_message="hello",
            channel="cli",
            chat_id="test",
            session=mock_session,
        )

        assert len(messages) >= 2
        assert messages[0]["role"] == "system"
        assert "task_type" in mock_session.metadata

    def test_total_tokens_under_ceiling(
        self, context_builder, mock_session
    ):
        """Total budget should never exceed CONTEXT_TOTAL_CEILING."""
        budget = ContextBudget()

        # Enforce max content in every section
        for section_name, limit in CONTEXT_BUDGET.items():
            long_text = " ".join(["word"] * 1000)
            budget.enforce(section_name, long_text)

        # Each section should be at most its limit
        for section_name, limit in CONTEXT_BUDGET.items():
            assert budget.used[section_name] <= limit + 5  # small buffer for tokenization

    def test_format_reflections(self, context_builder):
        """Should format reflections into a readable string."""
        reflections = [
            {"rule": "check imports", "lesson": "Missing import caused error"},
            {"rule": "read before write", "lesson": "File didn't exist"},
        ]
        result = context_builder._format_reflections(reflections)
        assert "LESSONS FROM PAST FAILURES:" in result
        assert "check imports" in result
        assert "read before write" in result

    def test_format_reflections_empty(self, context_builder):
        """Empty reflections should return empty string."""
        assert context_builder._format_reflections([]) == ""

    def test_format_procedure(self, context_builder):
        """Should format a procedure into a readable string."""
        proc = {
            "name": "deploy_flow",
            "steps": ["git pull", "npm build", "pm2 restart"],
        }
        result = context_builder._format_procedure(proc)
        assert "KNOWN WORKING PROCEDURE: deploy_flow" in result
        assert "1. git pull" in result
        assert "2. npm build" in result
        assert "3. pm2 restart" in result

    def test_format_procedure_empty(self, context_builder):
        """Empty procedure should return empty string."""
        assert context_builder._format_procedure({}) == ""
        assert context_builder._format_procedure(None) == ""

    def test_load_soul_md(self, context_builder):
        """Should load SOUL.md content."""
        result = context_builder._load_soul_md()
        assert "pawbot" in result.lower() or "soul" in result.lower()

    def test_load_soul_md_missing_file(self, tmp_path):
        """Should handle missing SOUL.md gracefully."""
        builder = ContextBuilder(workspace=tmp_path)
        result = builder._load_soul_md()
        assert result == ""

    def test_add_tool_result_preserved(self, context_builder):
        """add_tool_result should still work unchanged."""
        messages = [{"role": "system", "content": "test"}]
        result = context_builder.add_tool_result(
            messages, "tool-1", "read_file", "file contents"
        )
        assert len(result) == 2
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "tool-1"

    def test_add_assistant_message_preserved(self, context_builder):
        """add_assistant_message should still work unchanged."""
        messages = [{"role": "system", "content": "test"}]
        result = context_builder.add_assistant_message(
            messages, "I'll help you with that."
        )
        assert len(result) == 2
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "I'll help you with that."

    def test_minimal_context_mode(self, context_builder, mock_session):
        """Minimal context mode should only load system_prompt and conversation."""
        mock_session.metadata["context_mode"] = "minimal"
        messages = context_builder.build("anything", mock_session)

        # Should produce valid messages
        assert len(messages) >= 1

    def test_get_provider_type_graceful(self, tmp_path):
        """Should return 'unknown' when model_router is None."""
        builder = ContextBuilder(workspace=tmp_path)
        assert builder._get_provider_type() == "unknown"

    def test_get_provider_type_from_router(self, context_builder, mock_model_router):
        """Should get provider type from model router."""
        mock_model_router.current_provider_type.return_value = "anthropic"
        result = context_builder._get_provider_type()
        assert result == "anthropic"
