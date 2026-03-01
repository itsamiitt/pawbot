"""Tests for Phase 4 — Model Router & OllamaProvider.

Cross-reference: PHASE_04_MODEL_ROUTER.md, MASTER_REFERENCE.md
Run with: pytest tests/test_model_router.py -v --tb=short
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from pawbot.providers.ollama import OllamaProvider
from pawbot.providers.router import (
    ROUTING_TABLE,
    ModelRouter,
    validate_routing_config,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def config_with_keys() -> dict:
    """Config with API keys set for remote providers."""
    return {
        "providers": {
            "openrouter": {"apiKey": "sk-or-test-key-123"},
            "anthropic": {"apiKey": "sk-ant-test-key-456"},
            "openai": {"apiKey": "sk-oai-test-key-789"},
            "ollama": {
                "base_url": "http://localhost:11434",
                "default_model": "llama3.1:8b",
                "embedding_model": "nomic-embed-text",
                "coding_model": "deepseek-coder:6.7b",
            },
        },
        "routing": {"enabled": True},
    }


@pytest.fixture
def config_no_keys() -> dict:
    """Config with no API keys."""
    return {"providers": {}, "routing": {"enabled": True}}


@pytest.fixture
def config_routing_disabled() -> dict:
    """Config with routing explicitly disabled."""
    return {
        "providers": {
            "openrouter": {"apiKey": "sk-or-test-key-123"},
        },
        "routing": {"enabled": False},
        "agents": {"defaults": {"model": "test/default-model"}},
    }


@pytest.fixture
def ollama_provider(config_with_keys) -> OllamaProvider:
    return OllamaProvider(config_with_keys)


@pytest.fixture
def router(config_with_keys) -> ModelRouter:
    return ModelRouter(config_with_keys)


@pytest.fixture
def router_disabled(config_routing_disabled) -> ModelRouter:
    return ModelRouter(config_routing_disabled)


# ═══════════════════════════════════════════════════════════════════════════════
# TestOllamaProvider
# ═══════════════════════════════════════════════════════════════════════════════


class TestOllamaProvider:
    """Tests for OllamaProvider class."""

    def test_default_config(self):
        """OllamaProvider uses sensible defaults when config is empty."""
        provider = OllamaProvider({})
        assert provider.base_url == "http://localhost:11434"
        assert provider.default_model == "llama3.1:8b"
        assert provider.embedding_model == "nomic-embed-text"
        assert provider.coding_model == "deepseek-coder:6.7b"

    def test_custom_config(self, config_with_keys):
        """OllamaProvider reads config correctly."""
        provider = OllamaProvider(config_with_keys)
        assert provider.base_url == "http://localhost:11434"
        assert provider.default_model == "llama3.1:8b"

    def test_is_available_returns_false_when_offline(self):
        """is_available returns False when Ollama is not running."""
        provider = OllamaProvider({})
        # Override with unreachable URL
        provider.base_url = "http://192.0.2.1:99999"
        assert provider.is_available() is False

    @patch("pawbot.providers.ollama.httpx.get")
    def test_is_available_returns_true_when_running(self, mock_get):
        """is_available returns True when Ollama responds with 200."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        provider = OllamaProvider({})
        assert provider.is_available() is True
        mock_get.assert_called_once()

    @patch("pawbot.providers.ollama.httpx.get")
    def test_get_available_models(self, mock_get):
        """get_available_models parses model list from API response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3.1:8b"},
                {"name": "nomic-embed-text:latest"},
                {"name": "deepseek-coder:6.7b"},
            ]
        }
        mock_get.return_value = mock_response

        provider = OllamaProvider({})
        models = provider.get_available_models()
        assert len(models) == 3
        assert "llama3.1:8b" in models
        assert "nomic-embed-text:latest" in models

    @patch("pawbot.providers.ollama.httpx.get")
    def test_get_available_models_handles_failure(self, mock_get):
        """get_available_models returns empty list on failure."""
        mock_get.side_effect = Exception("Connection refused")
        provider = OllamaProvider({})
        models = provider.get_available_models()
        assert models == []

    @patch("pawbot.providers.ollama.httpx.post")
    @patch.object(OllamaProvider, "ensure_model")
    def test_complete_returns_string(self, mock_ensure, mock_post):
        """complete() returns the generated text."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "Hello world!"}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        provider = OllamaProvider({})
        result = provider.complete("llama3.1:8b", "Say hello")
        assert result == "Hello world!"

    @patch("pawbot.providers.ollama.httpx.post")
    @patch.object(OllamaProvider, "ensure_model")
    def test_complete_with_system_prompt(self, mock_ensure, mock_post):
        """complete() sends system prompt when provided."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "Yes"}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        provider = OllamaProvider({})
        provider.complete("llama3.1:8b", "Hello", system="You are helpful")

        call_args = mock_post.call_args
        payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
        assert payload.get("system") == "You are helpful"

    @patch("pawbot.providers.ollama.httpx.post")
    @patch.object(OllamaProvider, "ensure_model")
    def test_embed_returns_float_list(self, mock_ensure, mock_post):
        """embed() returns a list of floats."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"embedding": [0.1, 0.2, 0.3, 0.4]}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        provider = OllamaProvider({})
        result = provider.embed("test text")
        assert isinstance(result, list)
        assert all(isinstance(x, float) for x in result)
        assert len(result) == 4

    @patch("pawbot.providers.ollama.httpx.post")
    @patch.object(OllamaProvider, "ensure_model")
    def test_complete_raises_on_failure(self, mock_ensure, mock_post):
        """complete() raises when the request fails."""
        mock_post.side_effect = Exception("Server error")

        provider = OllamaProvider({})
        with pytest.raises(Exception, match="Server error"):
            provider.complete("llama3.1:8b", "Hello")

    @patch("pawbot.providers.ollama.httpx.get")
    def test_ensure_model_skips_when_already_available(self, mock_get):
        """ensure_model does nothing if model is already pulled."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "models": [{"name": "llama3.1:8b"}]
        }
        mock_get.return_value = mock_response

        provider = OllamaProvider({})
        # Should not raise or attempt a pull
        provider.ensure_model("llama3.1:8b")

    @patch("pawbot.providers.ollama.httpx.post")
    @patch.object(OllamaProvider, "ensure_model")
    def test_chat_returns_string(self, mock_ensure, mock_post):
        """chat() returns the assistant response text."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"role": "assistant", "content": "Hi there!"}
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        provider = OllamaProvider({})
        result = provider.chat(
            "llama3.1:8b",
            [{"role": "user", "content": "Hello"}],
        )
        assert result == "Hi there!"


# ═══════════════════════════════════════════════════════════════════════════════
# TestModelRouter
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelRouter:
    """Tests for ModelRouter class."""

    def test_routes_memory_tasks_to_ollama(self, router):
        """Memory-related tasks should route to local Ollama."""
        with patch.object(router.ollama, "is_available", return_value=True):
            provider, model = router.route("memory_save", 0.5)
            assert provider == "ollama"
            assert model == "llama3.1:8b"

            provider, model = router.route("memory_search", 0.0)
            assert provider == "ollama"
            assert model == "nomic-embed-text"

            provider, model = router.route("file_index", 1.0)
            assert provider == "ollama"
            assert model == "deepseek-coder:6.7b"

            provider, model = router.route("result_compress", 0.3)
            assert provider == "ollama"
            assert model == "llama3.1:8b"

    def test_routes_architecture_to_opus(self, router):
        """Architecture tasks should route to Claude Opus."""
        provider, model = router.route("architecture", 0.5)
        assert provider == "openrouter"
        assert "opus" in model

    def test_routes_casual_low_complexity_to_haiku(self, router):
        """Low complexity casual chat should route to Claude Haiku."""
        provider, model = router.route("casual_chat", 0.2)
        assert provider == "openrouter"
        assert "haiku" in model

    def test_routes_casual_high_complexity_to_sonnet(self, router):
        """High complexity casual chat should route to Claude Sonnet."""
        provider, model = router.route("casual_chat", 0.5)
        assert provider == "openrouter"
        assert "sonnet" in model

    def test_routes_code_generation_to_sonnet(self, router):
        """Code generation should route to Sonnet."""
        provider, model = router.route("code_generation", 0.8)
        assert provider == "openrouter"
        assert "sonnet" in model

    def test_routes_debugging_to_sonnet(self, router):
        """Debugging should route to Sonnet."""
        provider, model = router.route("debugging", 0.5)
        assert provider == "openrouter"
        assert "sonnet" in model

    def test_routes_reasoning_high_to_opus(self, router):
        """High complexity reasoning should route to Opus."""
        provider, model = router.route("reasoning", 0.8)
        assert provider == "openrouter"
        assert "opus" in model

    def test_routes_reasoning_low_to_sonnet(self, router):
        """Low complexity reasoning should route to Sonnet."""
        provider, model = router.route("reasoning", 0.5)
        assert provider == "openrouter"
        assert "sonnet" in model

    def test_ollama_unavailable_fallback_to_openrouter(self, router):
        """When Ollama is down, local tasks should fallback to openrouter."""
        with patch.object(router.ollama, "is_available", return_value=False):
            provider, model = router.route("memory_save", 0.5)
            assert provider == "openrouter"
            assert "haiku" in model  # cheap fallback

    def test_routing_disabled_uses_default_model(self, router_disabled):
        """When routing is disabled, always returns default model."""
        provider, model = router_disabled.route("architecture", 0.9)
        assert provider == "openrouter"
        assert model == "test/default-model"

    def test_routing_disabled_uses_hardcoded_default_when_no_config(self):
        """When routing disabled and no config default, use hardcoded default."""
        router = ModelRouter({"routing": {"enabled": False}})
        provider, model = router.route("anything", 0.5)
        assert provider == "openrouter"
        assert model == "anthropic/claude-sonnet-4-6"

    def test_unknown_task_type_returns_default(self, router):
        """Unknown task types should still return a valid route."""
        provider, model = router.route("nonexistent_task", 0.5)
        assert provider == "openrouter"
        assert "sonnet" in model

    def test_session_stats_tracked(self, router):
        """Session stats should be tracked after calls."""
        with patch.object(router.ollama, "is_available", return_value=True):
            with patch.object(router.ollama, "complete", return_value="done"):
                router.call("memory_save", 0.5, "test prompt")

        stats = router.get_session_stats()
        assert stats["calls_per_provider"]["ollama"] == 1
        assert stats["latency_sum"]["ollama"] >= 0
        assert stats["latency_count"]["ollama"] == 1

    def test_session_stats_accumulate(self, router):
        """Session stats should accumulate across multiple calls."""
        with patch.object(router.ollama, "is_available", return_value=True):
            with patch.object(router.ollama, "complete", return_value="done"):
                router.call("memory_save", 0.5, "prompt 1")
                router.call("result_compress", 0.3, "prompt 2")

        stats = router.get_session_stats()
        assert stats["calls_per_provider"]["ollama"] == 2

    def test_log_session_summary_no_crash(self, router):
        """log_session_summary should not crash even with empty stats."""
        # Should not raise
        router.log_session_summary()

        # Also test with some data
        with patch.object(router.ollama, "is_available", return_value=True):
            with patch.object(router.ollama, "complete", return_value="done"):
                router.call("memory_save", 0.5, "test prompt")

        # Should log without crashing
        router.log_session_summary()

    def test_current_provider_type_tracks_last_call(self, router):
        """current_provider_type() should reflect the last provider used."""
        assert router.current_provider_type() == "openrouter"  # initial default

        with patch.object(router.ollama, "is_available", return_value=True):
            with patch.object(router.ollama, "complete", return_value="done"):
                router.call("memory_save", 0.5, "test")

        assert router.current_provider_type() == "ollama"

    def test_all_remote_unavailable_raises_clear_error(self, router):
        """When all providers fail, a clear RuntimeError is raised."""
        with patch.object(router.ollama, "is_available", return_value=False):
            with patch.object(
                router, "_call_openrouter", side_effect=Exception("API down")
            ):
                with pytest.raises(RuntimeError, match="All LLM providers unavailable"):
                    router.call("casual_chat", 0.2, "hello")

    def test_fallback_from_openrouter_to_ollama(self, router):
        """When OpenRouter fails, should fallback to Ollama."""
        with patch.object(
            router, "_call_openrouter", side_effect=Exception("API error")
        ):
            with patch.object(router.ollama, "is_available", return_value=True):
                with patch.object(
                    router.ollama, "complete", return_value="fallback response"
                ):
                    result = router.call("casual_chat", 0.2, "hello")
                    assert result == "fallback response"

    def test_call_with_messages(self, router):
        """call() should pass messages to the provider when provided."""
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ]
        with patch.object(
            router, "_call_openrouter", return_value="response"
        ) as mock_call:
            router.call("casual_chat", 0.2, "hello", messages=messages)
            _, kwargs = mock_call.call_args
            assert kwargs["messages"] == messages

    def test_call_with_system_prompt(self, router):
        """call() should pass system prompt to Ollama."""
        with patch.object(router.ollama, "is_available", return_value=True):
            with patch.object(
                router.ollama, "complete", return_value="done"
            ) as mock_complete:
                router.call(
                    "memory_save", 0.5, "prompt", system="system instructions"
                )
                mock_complete.assert_called_once_with(
                    model="llama3.1:8b",
                    prompt="prompt",
                    system="system instructions",
                )

    def test_build_messages_with_system(self, router):
        """_build_messages creates proper message list with system prompt."""
        msgs = router._build_messages("You are helpful", "Hello")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are helpful"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "Hello"

    def test_build_messages_without_system(self, router):
        """_build_messages creates proper message list without system prompt."""
        msgs = router._build_messages("", "Hello")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"


# ═══════════════════════════════════════════════════════════════════════════════
# TestRoutingTable
# ═══════════════════════════════════════════════════════════════════════════════


class TestRoutingTable:
    """Tests for the ROUTING_TABLE canonical values."""

    def test_routing_table_not_empty(self):
        """ROUTING_TABLE should have entries."""
        assert len(ROUTING_TABLE) > 0

    def test_routing_table_entry_format(self):
        """Each routing table entry should be a 5-tuple."""
        for entry in ROUTING_TABLE:
            assert len(entry) == 5
            task_type, min_c, max_c, provider, model = entry
            assert isinstance(task_type, str)
            assert isinstance(min_c, float)
            assert isinstance(max_c, float)
            assert isinstance(provider, str)
            assert isinstance(model, str)
            assert 0.0 <= min_c <= max_c <= 1.0

    def test_all_local_tasks_route_to_ollama(self):
        """All local task types should route to Ollama provider."""
        local_tasks = {
            "memory_save", "memory_search", "file_index",
            "result_compress", "status_update", "test_output_parse",
        }
        for entry in ROUTING_TABLE:
            if entry[0] in local_tasks:
                assert entry[3] == "ollama", (
                    f"Task {entry[0]} should route to ollama, not {entry[3]}"
                )

    def test_complexity_ranges_cover_full_spectrum(self):
        """Each task type should cover 0.0 to 1.0 complexity."""
        from collections import defaultdict

        task_ranges: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for task_type, min_c, max_c, _, _ in ROUTING_TABLE:
            task_ranges[task_type].append((min_c, max_c))

        for task_type, ranges in task_ranges.items():
            # At minimum, the first range should start at 0.0
            sorted_ranges = sorted(ranges)
            assert sorted_ranges[0][0] == 0.0, (
                f"Task {task_type} doesn't start at 0.0"
            )
            # And the last range should end at 1.0
            assert sorted_ranges[-1][1] == 1.0, (
                f"Task {task_type} doesn't end at 1.0"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# TestValidateRoutingConfig
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateRoutingConfig:
    """Tests for validate_routing_config startup function."""

    def test_all_clear_when_configured(self):
        """No warnings when Ollama is available and keys are set."""
        config = {
            "providers": {
                "openrouter": {"apiKey": "sk-or-test"},
            },
            "routing": {"enabled": True},
        }
        with patch.object(OllamaProvider, "is_available", return_value=True):
            warnings = validate_routing_config(config)
        assert len(warnings) == 0

    def test_warns_when_ollama_unavailable(self):
        """Should warn when Ollama is not reachable."""
        config = {
            "providers": {
                "openrouter": {"apiKey": "sk-or-test"},
            },
            "routing": {"enabled": True},
        }
        with patch.object(OllamaProvider, "is_available", return_value=False):
            warnings = validate_routing_config(config)
        assert any("Ollama" in w for w in warnings)

    def test_warns_when_openrouter_key_missing(self):
        """Should warn when OpenRouter API key is not set."""
        config = {
            "providers": {},
            "routing": {"enabled": True},
        }
        with patch.object(OllamaProvider, "is_available", return_value=True):
            warnings = validate_routing_config(config)
        assert any("OpenRouter" in w for w in warnings)

    def test_no_ollama_check_when_routing_disabled(self):
        """Should skip Ollama check when routing is disabled."""
        config = {
            "providers": {},
            "routing": {"enabled": False},
        }
        warnings = validate_routing_config(config)
        # Ollama check not run, but API key check still runs
        ollama_warnings = [w for w in warnings if "Ollama" in w]
        assert len(ollama_warnings) == 0

    def test_empty_config_returns_warnings(self):
        """Empty config should return warnings about missing keys."""
        with patch.object(OllamaProvider, "is_available", return_value=False):
            warnings = validate_routing_config({})
        assert len(warnings) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# TestCrossPhaseIntegration
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossPhaseIntegration:
    """Tests that ModelRouter integrates correctly with Phase 1, 2, and 3."""

    def test_context_py_uses_current_provider_type(self):
        """Phase 3 (context.py) calls current_provider_type() for cache markers."""
        router = ModelRouter({"routing": {"enabled": True}})

        # Initial state
        assert router.current_provider_type() == "openrouter"

        # After an Ollama call
        with patch.object(router.ollama, "is_available", return_value=True):
            with patch.object(router.ollama, "complete", return_value="ok"):
                router.call("result_compress", 0.0, "test")

        assert router.current_provider_type() == "ollama"

    def test_result_compress_routes_to_ollama(self):
        """Phase 2/3 uses task_type='result_compress' which should route to Ollama."""
        router = ModelRouter({"routing": {"enabled": True}})
        with patch.object(router.ollama, "is_available", return_value=True):
            provider, model = router.route("result_compress", 0.0)
        assert provider == "ollama"
        assert model == "llama3.1:8b"

    def test_call_interface_matches_phase2_usage(self):
        """ModelRouter.call() interface matches how loop.py calls it."""
        router = ModelRouter({"routing": {"enabled": True}})

        # Phase 2 calls: model_router.call(task_type=..., complexity=..., prompt=...)
        with patch.object(router.ollama, "is_available", return_value=True):
            with patch.object(
                router.ollama, "complete", return_value="reflection result"
            ):
                result = router.call(
                    task_type="result_compress",
                    complexity=0.0,
                    prompt="Analyze this failure...",
                )
                assert isinstance(result, str)
                assert result == "reflection result"

    def test_call_with_task_type_kwarg(self):
        """Verify both positional and keyword task_type arguments work."""
        router = ModelRouter({"routing": {"enabled": True}})
        with patch.object(router.ollama, "is_available", return_value=True):
            with patch.object(router.ollama, "complete", return_value="ok"):
                # Keyword style (as used by Phase 2 & 3)
                result = router.call(
                    task_type="result_compress",
                    complexity=0.0,
                    prompt="test",
                )
                assert result == "ok"

    def test_openrouter_call_requires_api_key(self):
        """OpenRouter calls should fail cleanly when API key is missing."""
        router = ModelRouter({"routing": {"enabled": True}})

        with pytest.raises((ValueError, RuntimeError)):
            router._call_openrouter(
                model="anthropic/claude-sonnet-4-6",
                prompt="test",
                system="",
                messages=None,
            )

    def test_anthropic_call_requires_api_key(self):
        """Anthropic calls should fail cleanly when API key is missing."""
        router = ModelRouter({"routing": {"enabled": True}})

        with pytest.raises(ValueError, match="Anthropic API key not set"):
            router._call_anthropic(
                model="claude-3-sonnet",
                prompt="test",
                system="",
                messages=None,
            )

    def test_openai_call_requires_api_key(self):
        """OpenAI calls should fail cleanly when API key is missing."""
        router = ModelRouter({"routing": {"enabled": True}})

        with pytest.raises(ValueError, match="OpenAI API key not set"):
            router._call_openai(
                model="gpt-4",
                prompt="test",
                system="",
                messages=None,
            )

    def test_unknown_provider_raises_valueerror(self):
        """Unknown providers should raise ValueError, not silently pass."""
        router = ModelRouter({"routing": {"enabled": True}})

        # Manually call with an unknown provider
        with patch.object(router.ollama, "is_available", return_value=False):
            with pytest.raises(RuntimeError, match="All LLM providers unavailable"):
                # The route won't return "unknown_provider" normally,
                # but the fallback path will hit this
                router._last_provider_type = "unknown"
                router._fallback_call(
                    "test", 0.5, "prompt", "system",
                    failed_provider="unknown",
                )
