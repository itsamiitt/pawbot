"""Model Router — multi-provider task-based model routing.

Phase 4 — Model Router: ModelRouter
Routes tasks to the correct LLM provider and model based on task type
and complexity score. Provides fallback chains and session statistics.

Cross-reference: MASTER_REFERENCE.md, PHASE_04_MODEL_ROUTER.md

Provider chain:
  1. Primary match from ROUTING_TABLE
  2. If provider unavailable → fallback to next
  3. If all remote fail → Ollama (local)
  4. If all fail → raise RuntimeError with clear guidance
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from pawbot.providers.ollama import OllamaProvider

logger = logging.getLogger("pawbot")


# ── Routing Table ────────────────────────────────────────────────────────────
# Canonical — matches MASTER_REFERENCE.md
# (task_type, complexity_min, complexity_max, provider, model)
ROUTING_TABLE: list[tuple[str, float, float, str, str]] = [
    # Local tasks (always Ollama — cost optimization)
    ("memory_save",       0.0, 1.0, "ollama", "llama3.1:8b"),
    ("memory_search",     0.0, 1.0, "ollama", "nomic-embed-text"),
    ("file_index",        0.0, 1.0, "ollama", "deepseek-coder:6.7b"),
    ("result_compress",   0.0, 1.0, "ollama", "llama3.1:8b"),
    ("status_update",     0.0, 1.0, "ollama", "llama3.1:8b"),
    ("test_output_parse", 0.0, 1.0, "ollama", "llama3.1:8b"),

    # Remote tasks — complexity determines model
    ("casual_chat",       0.0, 0.4, "openrouter", "anthropic/claude-haiku-4-5"),
    ("casual_chat",       0.4, 1.0, "openrouter", "anthropic/claude-sonnet-4-6"),
    ("code_generation",   0.0, 1.0, "openrouter", "anthropic/claude-sonnet-4-6"),
    ("architecture",      0.0, 1.0, "openrouter", "anthropic/claude-opus-4-6"),
    ("debugging",         0.0, 1.0, "openrouter", "anthropic/claude-sonnet-4-6"),
    ("deployment",        0.0, 1.0, "openrouter", "anthropic/claude-sonnet-4-6"),
    ("reasoning",         0.7, 1.0, "openrouter", "anthropic/claude-opus-4-6"),
    ("reasoning",         0.0, 0.7, "openrouter", "anthropic/claude-sonnet-4-6"),
]


class ModelRouter:
    """Routes tasks to the correct LLM provider and model.

    Uses a routing table keyed by (task_type, complexity) to select
    the optimal (provider, model) pair. Tracks per-session statistics
    and provides automatic fallback chains.

    Usage by other phases:
    - Phase 1 (memory.py): model_router.call(task_type="result_compress", ...)
    - Phase 2 (loop.py):   model_router.call(...) for ToT planning / reflection
    - Phase 3 (context.py): model_router.call(task_type="result_compress", ...)
                             model_router.current_provider_type()
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.routing_enabled = config.get("routing", {}).get("enabled", True)
        self.ollama = OllamaProvider(config)

        # Phase 3: Load routing table from config, fall back to hardcoded default
        config_rules = config.get("routing", {}).get("rules", [])
        if config_rules:
            self._routing_table: list[tuple[str, float, float, str, str]] = [
                (r["task_type"], r.get("complexity_min", 0.0),
                 r.get("complexity_max", 1.0), r["provider"], r["model"])
                for r in config_rules
            ]
            logger.info("Loaded %d custom routing rules from config", len(config_rules))
        else:
            self._routing_table = ROUTING_TABLE  # Use hardcoded default

        self._session_stats: dict[str, Any] = {
            "calls_per_provider": {},
            "estimated_cost": 0.0,
            "latency_sum": {},
            "latency_count": {},
        }
        self._last_provider_type: str = "openrouter"  # tracked for context.py

        # Phase 3: Initialize cost tracker
        from pawbot.providers.cost_tracker import CostTracker
        self._cost_tracker = CostTracker()

    def route(self, task_type: str, complexity: float) -> tuple[str, str]:
        """Returns (provider_name, model_name) for the given task.

        Falls back through: primary → secondary → ollama → error.
        """
        if not self.routing_enabled:
            # Routing disabled: use default model from config
            default = (
                self.config.get("agents", {})
                .get("defaults", {})
                .get("model", "anthropic/claude-sonnet-4-6")
            )
            return "openrouter", default

        for rt_task, rt_min, rt_max, rt_provider, rt_model in self._routing_table:
            if task_type == rt_task and rt_min <= complexity <= rt_max:
                # Check if Ollama provider is available
                if rt_provider == "ollama" and not self.ollama.is_available():
                    logger.warning(
                        "Ollama unavailable for %s, routing to fallback",
                        task_type,
                    )
                    fallback_provider = self.config.get("routing", {}).get("fallback_provider", "openrouter")
                    fallback_model = self.config.get("routing", {}).get("fallback_model", "anthropic/claude-haiku-4-5")
                    return fallback_provider, fallback_model
                return rt_provider, rt_model

        # No match found — default
        logger.warning(
            "No route for task_type='%s', complexity=%s", task_type, complexity
        )
        return "openrouter", "anthropic/claude-sonnet-4-6"

    def call(
        self,
        task_type: str,
        complexity: float,
        prompt: str,
        system: str = "",
        messages: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        """Main entry point. Routes to correct provider and calls LLM.

        Returns the response text string.
        Tracks session stats.
        """
        provider_name, model = self.route(task_type, complexity)
        self._last_provider_type = provider_name
        start = time.time()

        try:
            if provider_name == "ollama":
                response = self.ollama.complete(
                    model=model, prompt=prompt, system=system
                )
            elif provider_name == "openrouter":
                response = self._call_openrouter(
                    model=model, prompt=prompt, system=system, messages=messages
                )
            elif provider_name == "anthropic":
                response = self._call_anthropic(
                    model=model, prompt=prompt, system=system, messages=messages
                )
            elif provider_name == "openai":
                response = self._call_openai(
                    model=model, prompt=prompt, system=system, messages=messages
                )
            else:
                raise ValueError(f"Unknown provider: {provider_name}")

            # Track stats
            elapsed = time.time() - start
            self._record_stats(provider_name, model, elapsed)
            return response

        except Exception as e:
            logger.error(
                "ModelRouter call failed (%s/%s): %s", provider_name, model, e
            )
            # Fallback chain
            return self._fallback_call(
                task_type,
                complexity,
                prompt,
                system,
                failed_provider=provider_name,
            )

    def current_provider_type(self) -> str:
        """Used by context.py to determine if cache markers should be applied."""
        return self._last_provider_type

    def get_session_stats(self) -> dict[str, Any]:
        """Returns a copy of session statistics."""
        return dict(self._session_stats)

    # ── Fallback ─────────────────────────────────────────────────────────

    def _fallback_call(
        self,
        task_type: str,
        complexity: float,
        prompt: str,
        system: str,
        failed_provider: str,
    ) -> str:
        """Fallback chain:
        1. If remote failed → try Ollama
        2. If Ollama failed → raise clear error
        """
        if failed_provider != "ollama" and self.ollama.is_available():
            logger.warning(
                "Fallback: routing '%s' to ollama/%s",
                task_type,
                self.ollama.default_model,
            )
            start = time.time()
            try:
                response = self.ollama.complete(
                    model=self.ollama.default_model,
                    prompt=prompt,
                    system=system,
                )
                elapsed = time.time() - start
                self._record_stats("ollama", self.ollama.default_model, elapsed)
                return response
            except Exception as fallback_err:
                logger.error("Ollama fallback also failed: %s", fallback_err)

        raise RuntimeError(
            "All LLM providers unavailable. "
            "Check API keys in ~/.pawbot/config.json and that Ollama is running."
        )

    # ── Provider call methods ────────────────────────────────────────────

    def _validate_key(self, provider: str, key: str) -> None:
        """Raise ValueError before making any network call if key is missing or placeholder."""
        from pawbot.utils.secrets import is_placeholder
        if not key or is_placeholder(key):
            pretty_name = {
                "openrouter": "OpenRouter",
                "anthropic": "Anthropic",
                "openai": "OpenAI",
            }.get(provider, provider)
            raise ValueError(f"{pretty_name} API key not set")

    def _call_openrouter(
        self,
        model: str,
        prompt: str,
        system: str,
        messages: Optional[list[dict[str, Any]]],
    ) -> str:
        """OpenRouter uses OpenAI-compatible API."""
        from pawbot.utils.retry import call_with_retry

        api_key = (
            self.config.get("providers", {})
            .get("openrouter", {})
            .get("apiKey", "")
        )
        self._validate_key("openrouter", api_key)

        payload = {
            "model": model,
            "messages": messages or self._build_messages(system, prompt),
        }

        def _do_call() -> str:
            r = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120.0,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

        return call_with_retry(_do_call, max_retries=3, base_delay=1.0)

    def _call_anthropic(
        self,
        model: str,
        prompt: str,
        system: str,
        messages: Optional[list[dict[str, Any]]],
    ) -> str:
        """Direct Anthropic API call."""
        from pawbot.utils.retry import call_with_retry

        api_key = (
            self.config.get("providers", {})
            .get("anthropic", {})
            .get("apiKey", "")
        )
        self._validate_key("anthropic", api_key)

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": 4096,
            "messages": messages or self._build_messages(system, prompt),
        }
        if system:
            payload["system"] = system

        def _do_call() -> str:
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120.0,
            )
            r.raise_for_status()
            data = r.json()
            return data["content"][0]["text"]

        return call_with_retry(_do_call, max_retries=3, base_delay=1.0)

    def _call_openai(
        self,
        model: str,
        prompt: str,
        system: str,
        messages: Optional[list[dict[str, Any]]],
    ) -> str:
        """OpenAI API call."""
        from pawbot.utils.retry import call_with_retry

        api_key = (
            self.config.get("providers", {})
            .get("openai", {})
            .get("apiKey", "")
        )
        self._validate_key("openai", api_key)

        def _do_call() -> str:
            r = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages or self._build_messages(system, prompt),
                },
                timeout=120.0,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

        return call_with_retry(_do_call, max_retries=3, base_delay=1.0)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _build_messages(
        self, system: str, prompt: str
    ) -> list[dict[str, str]]:
        """Build a simple messages list from system + user prompt."""
        msgs: list[dict[str, str]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    def _record_stats(
        self, provider: str, model: str, elapsed: float
    ) -> None:
        """Record call statistics for session summary."""
        stats = self._session_stats
        stats["calls_per_provider"][provider] = (
            stats["calls_per_provider"].get(provider, 0) + 1
        )
        stats["latency_sum"][provider] = (
            stats["latency_sum"].get(provider, 0.0) + elapsed
        )
        stats["latency_count"][provider] = (
            stats["latency_count"].get(provider, 0) + 1
        )

        # Phase 3: Record to persistent cost tracker
        try:
            self._cost_tracker.record(
                provider=provider,
                model=model,
                input_tokens=0,   # TODO: extract from response in future
                output_tokens=0,  # TODO: extract from response in future
                latency_ms=elapsed * 1000,
            )
        except Exception:
            pass  # Cost tracking is best-effort

    def log_session_summary(self) -> None:
        """Call at session end to log routing statistics."""
        stats = self._session_stats
        for provider, count in stats["calls_per_provider"].items():
            avg_latency = stats["latency_sum"].get(provider, 0) / max(
                stats["latency_count"].get(provider, 1), 1
            )
            logger.info(
                "Router stats: %s — %d calls, %.2fs avg latency",
                provider,
                count,
                avg_latency,
            )


# ── Startup Validation ──────────────────────────────────────────────────────


def validate_routing_config(config: dict[str, Any]) -> list[str]:
    """Returns list of warning messages for missing/misconfigured providers.

    Called at startup (Phase 16 will formalize this — stub here).
    """
    warnings: list[str] = []

    routing_cfg = config.get("routing", {})
    if routing_cfg.get("enabled", True):
        # Check Ollama
        ollama = OllamaProvider(config)
        if not ollama.is_available():
            warnings.append(
                f"WARNING: Ollama not reachable at {ollama.base_url}. "
                "Local model routing disabled."
            )

    # Check API keys
    providers = config.get("providers", {})
    if not providers.get("openrouter", {}).get("apiKey"):
        warnings.append(
            "WARNING: OpenRouter API key not set. Remote routing disabled."
        )

    return warnings
