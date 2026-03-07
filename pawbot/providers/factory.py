"""Provider factory — create the right LLMProvider from config.

This was extracted from cli/commands.py to decouple provider creation
from the CLI layer.  Any code needing a provider should call
``create_provider(config)`` instead of the old ``_make_provider()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pawbot.config.schema import Config
    from pawbot.providers.base import LLMProvider


def create_provider(config: Config) -> LLMProvider:
    """Create the appropriate LLM provider from *config*.

    Returns:
        A ready-to-use ``LLMProvider`` instance.

    Raises:
        SystemExit: When no API key is configured (interactive CLI use).
    """
    from pawbot.providers.custom_provider import CustomProvider
    from pawbot.providers.litellm_provider import LiteLLMProvider
    from pawbot.providers.openai_codex_provider import OpenAICodexProvider

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)

    # OpenAI Codex (OAuth)
    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        return OpenAICodexProvider(default_model=model)

    # Custom: direct OpenAI-compatible endpoint, bypasses LiteLLM
    if provider_name == "custom":
        return CustomProvider(
            api_key=p.api_key if p else "no-key",
            api_base=config.get_api_base(model) or "http://localhost:8000/v1",
            default_model=model,
        )

    from pawbot.providers.registry import find_by_name

    spec = find_by_name(provider_name)
    if (
        not model.startswith("bedrock/")
        and not (p and p.api_key)
        and not (spec and spec.is_oauth)
    ):
        raise ValueError(
            f"No API key configured for provider '{provider_name}'. "
            "Set one in ~/.pawbot/config.json under the providers section."
        )

    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(model),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=provider_name,
    )
