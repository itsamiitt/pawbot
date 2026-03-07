"""Tests for Config provider matching strategy helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from pawbot.config.schema import Config


def _make_config(
    forced_provider: str = "auto",
    openrouter_key: str = "",
    anthropic_key: str = "",
    model: str = "anthropic/claude-sonnet-4-6",
) -> Config:
    cfg = Config()
    cfg.agents.defaults.provider = forced_provider
    cfg.agents.defaults.model = model
    cfg.providers.openrouter.api_key = openrouter_key
    cfg.providers.anthropic.api_key = anthropic_key
    return cfg


def test_forced_provider_wins_over_other_strategies() -> None:
    cfg = _make_config(forced_provider="openrouter", openrouter_key="sk-or-test")
    provider, name = cfg._match_forced_provider()
    assert name == "openrouter"
    assert provider is cfg.providers.openrouter


def test_forced_auto_returns_none() -> None:
    cfg = _make_config(forced_provider="auto")
    provider, name = cfg._match_forced_provider()
    assert provider is None
    assert name is None


def test_prefix_match_wins_for_prefixed_model() -> None:
    cfg = _make_config()
    spec = SimpleNamespace(name="github_copilot", is_oauth=True, keywords=["copilot"])
    with patch("pawbot.providers.registry.PROVIDERS", [spec]):
        provider, name = cfg._match_by_prefix("github-copilot/gpt-4o")
    assert provider is cfg.providers.github_copilot
    assert name == "github_copilot"


def test_prefix_match_skips_non_prefixed_model() -> None:
    cfg = _make_config()
    provider, name = cfg._match_by_prefix("claude-sonnet-4-6")
    assert provider is None
    assert name is None


def test_keyword_match_selects_correct_provider() -> None:
    cfg = _make_config(anthropic_key="sk-ant-test")
    spec = SimpleNamespace(name="anthropic", is_oauth=False, keywords=["claude", "haiku", "sonnet"])
    with patch("pawbot.providers.registry.PROVIDERS", [spec]):
        provider, name = cfg._match_by_keyword("anthropic/claude-sonnet-4-6")
    assert provider is cfg.providers.anthropic
    assert name == "anthropic"


def test_fallback_skips_oauth_providers() -> None:
    cfg = _make_config(openrouter_key="sk-or-test")
    oauth_spec = SimpleNamespace(name="github_copilot", is_oauth=True, keywords=["copilot"])
    real_spec = SimpleNamespace(name="openrouter", is_oauth=False, keywords=["openrouter"])
    with patch("pawbot.providers.registry.PROVIDERS", [oauth_spec, real_spec]):
        provider, name = cfg._match_by_fallback()
    assert provider is cfg.providers.openrouter
    assert name == "openrouter"
