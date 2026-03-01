# PHASE 4 — MODEL ROUTER
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)  
> **Implementation Day:** Day 5  
> **Primary Files:** `~/nanobot/providers/router.py` (NEW), `~/nanobot/providers/ollama.py` (NEW)  
> **Test File:** `~/nanobot/tests/test_model_router.py`  
> **Depends on:** Phase 1 (indirectly — memory ops route to Ollama), existing `providers/` structure

---

## BEFORE YOU START — READ THESE FILES

```bash
ls ~/nanobot/providers/            # see existing provider structure
cat ~/nanobot/providers/*.py       # understand existing provider interface
cat ~/nanobot/config/              # understand how config is loaded
cat ~/.nanobot/config.json         # see current keys and values
```

**Existing interface to preserve:** Whatever interface `loop.py` currently uses to call the LLM. `ModelRouter` must expose the same method signatures.

---

## FEATURE 4.1 — MULTI-PROVIDER MODEL ROUTER

### New Files to Create

```
~/nanobot/providers/router.py    ← ModelRouter class
~/nanobot/providers/ollama.py    ← OllamaProvider class
```

### Add Dependencies

```toml
# In ~/nanobot/pyproject.toml [project.dependencies]:
"httpx>=0.25.0",
```

Install: `pip install httpx`

---

### OllamaProvider

**Class name:** `OllamaProvider`  
**File:** `providers/ollama.py`

```python
import httpx
import json
import asyncio
import logging

logger = logging.getLogger("nanobot")

class OllamaProvider:
    """
    Provides LLM completions and embeddings via local Ollama server.
    Base URL: http://localhost:11434 (configurable)
    """
    DEFAULT_BASE_URL = "http://localhost:11434"

    def __init__(self, config: dict):
        ollama_cfg = config.get("providers", {}).get("ollama", {})
        self.base_url = ollama_cfg.get("base_url", self.DEFAULT_BASE_URL)
        self.default_model = ollama_cfg.get("default_model", "llama3.1:8b")
        self.embedding_model = ollama_cfg.get("embedding_model", "nomic-embed-text")
        self.coding_model = ollama_cfg.get("coding_model", "deepseek-coder:6.7b")
        self._available_models: list[str] = []

    def is_available(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def get_available_models(self) -> list[str]:
        """Returns list of model names currently pulled in Ollama."""
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=5.0)
            data = r.json()
            self._available_models = [m["name"] for m in data.get("models", [])]
            return self._available_models
        except Exception as e:
            logger.warning(f"Ollama model list failed: {e}")
            return []

    def ensure_model(self, model_name: str):
        """Auto-pull model if not available. Blocks until complete."""
        available = self.get_available_models()
        # Check if model name matches any available (Ollama uses "name:tag" format)
        if any(model_name in m for m in available):
            return

        logger.info(f"Ollama: pulling model {model_name}...")
        with httpx.stream(
            "POST",
            f"{self.base_url}/api/pull",
            json={"name": model_name},
            timeout=300.0
        ) as r:
            for line in r.iter_lines():
                if line:
                    try:
                        progress = json.loads(line)
                        if progress.get("status") == "success":
                            logger.info(f"Ollama: model {model_name} ready")
                            break
                    except json.JSONDecodeError:
                        pass

    def complete(self, model: str, prompt: str, system: str = "") -> str:
        """
        Synchronous completion via /api/generate.
        Returns the generated text string.
        """
        self.ensure_model(model)

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system

        try:
            r = httpx.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=120.0
            )
            r.raise_for_status()
            return r.json().get("response", "")
        except Exception as e:
            logger.error(f"Ollama complete failed: {e}")
            raise

    def embed(self, text: str) -> list[float]:
        """Returns embedding vector for text using embedding_model."""
        self.ensure_model(self.embedding_model)
        try:
            r = httpx.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.embedding_model, "prompt": text},
                timeout=30.0
            )
            r.raise_for_status()
            return r.json().get("embedding", [])
        except Exception as e:
            logger.error(f"Ollama embed failed: {e}")
            raise
```

---

### ModelRouter

**Class name:** `ModelRouter`  
**File:** `providers/router.py`

```python
import time
import logging
from typing import Optional
from .ollama import OllamaProvider

logger = logging.getLogger("nanobot")

# ── Routing Table ────────────────────────────────────────────────────────────
# Canonical — matches MASTER_REFERENCE.md
# (task_type, complexity_min, complexity_max) → (provider, model)
ROUTING_TABLE = [
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
    def __init__(self, config: dict):
        self.config = config
        self.routing_enabled = config.get("routing", {}).get("enabled", True)
        self.ollama = OllamaProvider(config)
        self._session_stats = {
            "calls_per_provider": {},
            "estimated_cost": 0.0,
            "latency_sum": {},
            "latency_count": {},
        }
        self._last_provider_type = "openrouter"  # tracked for context.py

    def route(self, task_type: str, complexity: float) -> tuple[str, str]:
        """
        Returns (provider_name, model_name) for the given task.
        Falls back through: primary → secondary → ollama → error.
        """
        if not self.routing_enabled:
            # Routing disabled: use default model from config
            default = self.config.get("agents", {}).get("defaults", {}).get(
                "model", "anthropic/claude-sonnet-4-6"
            )
            return "openrouter", default

        for (rt_task, rt_min, rt_max, rt_provider, rt_model) in ROUTING_TABLE:
            if task_type == rt_task and rt_min <= complexity <= rt_max:
                # Check if provider is available
                if rt_provider == "ollama" and not self.ollama.is_available():
                    logger.warning(f"Ollama unavailable for {task_type}, routing to openrouter")
                    return "openrouter", "anthropic/claude-haiku-4-5"
                return rt_provider, rt_model

        # No match found — default
        logger.warning(f"No route for task_type='{task_type}', complexity={complexity}")
        return "openrouter", "anthropic/claude-sonnet-4-6"

    def call(self, task_type: str, complexity: float, prompt: str,
             system: str = "", messages: list = None) -> str:
        """
        Main entry point. Routes to correct provider and calls LLM.
        Returns the response text string.
        Tracks session stats.
        """
        provider_name, model = self.route(task_type, complexity)
        self._last_provider_type = provider_name
        start = time.time()

        try:
            if provider_name == "ollama":
                response = self.ollama.complete(model=model, prompt=prompt, system=system)
            elif provider_name == "openrouter":
                response = self._call_openrouter(model=model, prompt=prompt,
                                                  system=system, messages=messages)
            elif provider_name == "anthropic":
                response = self._call_anthropic(model=model, prompt=prompt,
                                                 system=system, messages=messages)
            elif provider_name == "openai":
                response = self._call_openai(model=model, prompt=prompt,
                                              system=system, messages=messages)
            else:
                raise ValueError(f"Unknown provider: {provider_name}")

            # Track stats
            elapsed = time.time() - start
            self._record_stats(provider_name, model, elapsed)
            return response

        except Exception as e:
            logger.error(f"ModelRouter call failed ({provider_name}/{model}): {e}")
            # Fallback chain
            return self._fallback_call(task_type, complexity, prompt, system,
                                        failed_provider=provider_name)

    def current_provider_type(self) -> str:
        """Used by context.py to determine if cache markers should be applied."""
        return self._last_provider_type

    def _fallback_call(self, task_type: str, complexity: float,
                        prompt: str, system: str, failed_provider: str) -> str:
        """
        Fallback chain:
        1. If remote failed → try Ollama
        2. If Ollama failed → raise clear error
        """
        if failed_provider != "ollama" and self.ollama.is_available():
            logger.warning(f"Fallback: routing '{task_type}' to ollama/llama3.1:8b")
            return self.ollama.complete(
                model=self.ollama.default_model,
                prompt=prompt,
                system=system
            )
        raise RuntimeError(
            f"All LLM providers unavailable. "
            f"Check API keys in ~/.nanobot/config.json and that Ollama is running."
        )

    def _call_openrouter(self, model: str, prompt: str,
                          system: str, messages: list) -> str:
        """OpenRouter uses OpenAI-compatible API."""
        api_key = self.config.get("providers", {}).get("openrouter", {}).get("apiKey", "")
        if not api_key:
            raise ValueError("OpenRouter API key not set in config.json")

        import httpx
        payload = {
            "model": model,
            "messages": messages or self._build_messages(system, prompt),
        }
        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120.0
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def _call_anthropic(self, model: str, prompt: str,
                         system: str, messages: list) -> str:
        """Direct Anthropic API call."""
        api_key = self.config.get("providers", {}).get("anthropic", {}).get("apiKey", "")
        if not api_key:
            raise ValueError("Anthropic API key not set in config.json")

        import httpx
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": messages or self._build_messages(system, prompt),
        }
        if system:
            payload["system"] = system

        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120.0
        )
        r.raise_for_status()
        data = r.json()
        return data["content"][0]["text"]

    def _call_openai(self, model: str, prompt: str,
                      system: str, messages: list) -> str:
        """OpenAI API call."""
        api_key = self.config.get("providers", {}).get("openai", {}).get("apiKey", "")
        if not api_key:
            raise ValueError("OpenAI API key not set in config.json")

        import httpx
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
            timeout=120.0
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def _build_messages(self, system: str, prompt: str) -> list[dict]:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    def _record_stats(self, provider: str, model: str, elapsed: float):
        stats = self._session_stats
        stats["calls_per_provider"][provider] = stats["calls_per_provider"].get(provider, 0) + 1
        stats["latency_sum"][provider] = stats["latency_sum"].get(provider, 0.0) + elapsed
        stats["latency_count"][provider] = stats["latency_count"].get(provider, 0) + 1

    def log_session_summary(self):
        """Call at session end to log routing statistics."""
        stats = self._session_stats
        for provider, count in stats["calls_per_provider"].items():
            avg_latency = (
                stats["latency_sum"].get(provider, 0) /
                max(stats["latency_count"].get(provider, 1), 1)
            )
            logger.info(f"Router stats: {provider} — {count} calls, "
                        f"{avg_latency:.2f}s avg latency")
```

---

## STARTUP VALIDATION

Add to config validation (Phase 16 will formalize this — add stub here):

```python
def validate_routing_config(config: dict) -> list[str]:
    """
    Returns list of warning messages for missing/misconfigured providers.
    Called at startup.
    """
    warnings = []

    routing_cfg = config.get("routing", {})
    if routing_cfg.get("enabled", True):
        # Check Ollama
        ollama = OllamaProvider(config)
        if not ollama.is_available():
            warnings.append("WARNING: Ollama not reachable at "
                            f"{ollama.base_url}. Local model routing disabled.")

    # Check API keys
    providers = config.get("providers", {})
    if not providers.get("openrouter", {}).get("apiKey"):
        warnings.append("WARNING: OpenRouter API key not set. Remote routing disabled.")

    return warnings
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_model_router.py`

```python
class TestModelRouter:
    def test_routes_memory_tasks_to_ollama()
    def test_routes_architecture_to_opus()
    def test_routes_casual_low_complexity_to_haiku()
    def test_routes_casual_high_complexity_to_sonnet()
    def test_ollama_unavailable_fallback_to_openrouter()
    def test_all_remote_unavailable_raises_clear_error()
    def test_routing_disabled_uses_default_model()
    def test_session_stats_tracked()
    def test_log_session_summary_no_crash()

class TestOllamaProvider:
    def test_is_available_returns_false_when_offline()
    def test_ensure_model_pulls_if_missing()
    def test_complete_returns_string()
    def test_embed_returns_float_list()
    def test_timeout_handled_gracefully()
```

---

## CROSS-REFERENCES

- **Phase 1** (memory.py) calls: `model_router.call(task_type="result_compress", ...)` for reflection generation and `model_router.call(task_type="memory_search", ...)` for semantic queries
- **Phase 2** (loop.py) calls: `model_router.call(...)` for ToT planning and reflection generation
- **Phase 3** (context.py) calls: `model_router.call(task_type="result_compress", ...)` for task type detection and `model_router.current_provider_type()` for cache markers
- **Phase 5–9** MCP servers: do NOT call ModelRouter directly — they are tools called by loop.py
- **Phase 16** (CLI startup): calls `validate_routing_config(config)` and logs warnings

All provider names, model strings, and routing table in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
