# Phase 3 — Provider & Model Router Reliability

> **Goal:** Make multi-provider routing production-grade with configurable tables, health monitoring, cost tracking, and streaming.  
> **Duration:** 7-10 days  
> **Risk Level:** Medium (changes routing behavior, but backward compatible)  
> **Depends On:** Phase 1 (ResilientLLMCaller), Phase 2 (llm_usage table)

---

## 3.1 — Configurable Routing Table

### Problem
`ROUTING_TABLE` in `providers/router.py` (lines 32-50) is hardcoded in source. Users cannot customize model routing without editing code.

### Solution
Move routing table to config while keeping the hardcoded one as default:

```python
# Add to pawbot/config/schema.py:

class RoutingRule(BaseModel):
    """Single routing table entry."""
    task_type: str
    complexity_min: float = 0.0
    complexity_max: float = 1.0
    provider: str = "openrouter"
    model: str = "anthropic/claude-sonnet-4-6"


class RoutingConfig(BaseModel):
    """Model routing configuration."""
    enabled: bool = True
    rules: list[RoutingRule] = Field(default_factory=list)
    fallback_provider: str = "ollama"
    fallback_model: str = "llama3.1:8b"
```

### Update `ModelRouter.__init__` to accept config rules:

```python
# In providers/router.py:

class ModelRouter:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.routing_enabled = config.get("routing", {}).get("enabled", True)
        self.ollama = OllamaProvider(config)
        
        # Load routing table from config, fall back to hardcoded default
        config_rules = config.get("routing", {}).get("rules", [])
        if config_rules:
            self._routing_table = [
                (r["task_type"], r.get("complexity_min", 0.0),
                 r.get("complexity_max", 1.0), r["provider"], r["model"])
                for r in config_rules
            ]
            logger.info("Loaded {} custom routing rules from config", len(config_rules))
        else:
            self._routing_table = ROUTING_TABLE  # Use hardcoded default

        self._session_stats = {
            "calls_per_provider": {},
            "estimated_cost": 0.0,
            "latency_sum": {},
            "latency_count": {},
        }
        self._last_provider_type: str = "openrouter"

    def route(self, task_type: str, complexity: float) -> tuple[str, str]:
        """Route using the configurable routing table."""
        if not self.routing_enabled:
            default = (
                self.config.get("agents", {})
                .get("defaults", {})
                .get("model", "anthropic/claude-sonnet-4-6")
            )
            return "openrouter", default

        for rt_task, rt_min, rt_max, rt_provider, rt_model in self._routing_table:
            if task_type == rt_task and rt_min <= complexity <= rt_max:
                if rt_provider == "ollama" and not self.ollama.is_available():
                    logger.warning("Ollama unavailable for %s, routing to fallback", task_type)
                    fallback_provider = self.config.get("routing", {}).get("fallback_provider", "openrouter")
                    fallback_model = self.config.get("routing", {}).get("fallback_model", "anthropic/claude-haiku-4-5")
                    return fallback_provider, fallback_model
                return rt_provider, rt_model

        logger.warning("No route for task_type='%s', complexity=%s", task_type, complexity)
        return "openrouter", "anthropic/claude-sonnet-4-6"
```

---

## 3.2 — Provider Health Monitoring

### Problem
No way to know if a provider is healthy until a call fails.

### Solution

```python
# Create: pawbot/providers/health.py

"""Provider health monitoring — background health checks."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx
from loguru import logger


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"       # Slow but working
    UNHEALTHY = "unhealthy"     # Failed health check
    UNKNOWN = "unknown"         # Not checked yet


@dataclass
class ProviderHealth:
    """Health status for a single provider."""
    name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    last_check: float = 0.0
    last_success: float = 0.0
    latency_ms: float = 0.0
    error: str = ""
    consecutive_failures: int = 0
    check_count: int = 0


class ProviderHealthMonitor:
    """Background health checker for all configured providers."""

    CHECK_INTERVAL_S = 60  # Check every 60 seconds
    DEGRADED_LATENCY_MS = 5000  # >5s response = degraded
    UNHEALTHY_AFTER_FAILURES = 3  # Mark unhealthy after 3 consecutive failures

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._providers: dict[str, ProviderHealth] = {}
        self._running = False
        self._task: asyncio.Task | None = None

    def register(self, name: str) -> None:
        """Register a provider for health monitoring."""
        self._providers[name] = ProviderHealth(name=name)

    async def start(self) -> None:
        """Start background health checking."""
        self._running = True
        self._task = asyncio.create_task(self._check_loop())
        logger.info("Provider health monitor started")

    async def stop(self) -> None:
        """Stop health checking."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_status(self, provider: str) -> ProviderHealth:
        """Get current health status for a provider."""
        return self._providers.get(provider, ProviderHealth(name=provider))

    def get_all_status(self) -> dict[str, ProviderHealth]:
        """Get health status for all providers."""
        return dict(self._providers)

    def is_healthy(self, provider: str) -> bool:
        """Check if a provider is healthy or unknown (give benefit of doubt)."""
        status = self._providers.get(provider)
        if status is None:
            return True  # Unknown = assume healthy
        return status.status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED, HealthStatus.UNKNOWN)

    async def _check_loop(self) -> None:
        """Background loop that checks all providers periodically."""
        while self._running:
            for name in list(self._providers.keys()):
                try:
                    await self._check_provider(name)
                except Exception:
                    logger.debug("Health check failed for {}", name)
            await asyncio.sleep(self.CHECK_INTERVAL_S)

    async def _check_provider(self, name: str) -> None:
        """Check health of a single provider."""
        health = self._providers[name]
        health.check_count += 1
        start = time.monotonic()

        try:
            if name == "ollama":
                await self._check_ollama(health)
            elif name == "openrouter":
                await self._check_openrouter(health)
            elif name == "anthropic":
                await self._check_anthropic(health)
            elif name == "openai":
                await self._check_openai(health)
            else:
                health.status = HealthStatus.UNKNOWN
                return

            elapsed = (time.monotonic() - start) * 1000
            health.latency_ms = elapsed
            health.last_check = time.time()
            health.last_success = time.time()
            health.consecutive_failures = 0
            health.error = ""

            if elapsed > self.DEGRADED_LATENCY_MS:
                health.status = HealthStatus.DEGRADED
            else:
                health.status = HealthStatus.HEALTHY

        except Exception as e:
            health.consecutive_failures += 1
            health.last_check = time.time()
            health.error = str(e)[:200]

            if health.consecutive_failures >= self.UNHEALTHY_AFTER_FAILURES:
                health.status = HealthStatus.UNHEALTHY

    async def _check_ollama(self, health: ProviderHealth) -> None:
        """Check Ollama availability."""
        base_url = self.config.get("providers", {}).get("ollama", {}).get("api_base", "http://localhost:11434")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base_url}/api/tags")
            r.raise_for_status()

    async def _check_openrouter(self, health: ProviderHealth) -> None:
        """Check OpenRouter availability."""
        api_key = self.config.get("providers", {}).get("openrouter", {}).get("apiKey", "")
        if not api_key:
            raise ValueError("No API key configured")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            r.raise_for_status()

    async def _check_anthropic(self, health: ProviderHealth) -> None:
        """Check Anthropic availability (lightweight — just auth check)."""
        api_key = self.config.get("providers", {}).get("anthropic", {}).get("apiKey", "")
        if not api_key:
            raise ValueError("No API key configured")
        # Anthropic doesn't have a lightweight health endpoint
        # so we just verify the key format
        if not api_key.startswith("sk-ant-"):
            raise ValueError("Invalid Anthropic key format")
        health.status = HealthStatus.HEALTHY

    async def _check_openai(self, health: ProviderHealth) -> None:
        """Check OpenAI availability."""
        api_key = self.config.get("providers", {}).get("openai", {}).get("apiKey", "")
        if not api_key:
            raise ValueError("No API key configured")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            r.raise_for_status()
```

---

## 3.3 — Cost Tracking

### Problem
No visibility into LLM API costs.

### Solution
Log every call to the `llm_usage` table (created in Phase 2, migration v3):

```python
# Create: pawbot/providers/cost_tracker.py

"""LLM cost tracking — logs usage to SQLite for analysis."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from loguru import logger


# Approximate cost per 1M tokens (input, output) in USD
MODEL_COSTS: dict[str, tuple[float, float]] = {
    # Anthropic via OpenRouter
    "anthropic/claude-sonnet-4-6": (3.0, 15.0),
    "anthropic/claude-opus-4-6": (15.0, 75.0),
    "anthropic/claude-haiku-4-5": (0.80, 4.0),
    # OpenAI
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    # Ollama (free, local)
    "llama3.1:8b": (0.0, 0.0),
    "nomic-embed-text": (0.0, 0.0),
    "deepseek-coder:6.7b": (0.0, 0.0),
}


class CostTracker:
    """Track LLM API usage and costs."""

    def __init__(self, db_path: str = "~/.pawbot/memory/facts.db"):
        import os
        self.db_path = os.path.expanduser(db_path)

    def record(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        task_type: str = "",
        session_key: str = "",
    ) -> None:
        """Record a single LLM call."""
        cost = self._estimate_cost(model, input_tokens, output_tokens)

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO llm_usage "
                "(timestamp, provider, model, input_tokens, output_tokens, "
                "latency_ms, cost_usd, task_type, session_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), provider, model, input_tokens, output_tokens,
                 latency_ms, cost, task_type, session_key),
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.debug("Cost tracking write failed (table may not exist yet)")

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD."""
        costs = MODEL_COSTS.get(model)
        if costs is None:
            # Try partial match
            for key, val in MODEL_COSTS.items():
                if key in model or model in key:
                    costs = val
                    break
        if costs is None:
            return 0.0

        input_cost, output_cost = costs
        return (input_tokens * input_cost + output_tokens * output_cost) / 1_000_000

    def get_usage_summary(self, hours: int = 24) -> dict[str, Any]:
        """Get usage summary for the last N hours."""
        try:
            conn = sqlite3.connect(self.db_path)
            since = time.time() - (hours * 3600)
            cursor = conn.execute(
                "SELECT provider, model, "
                "SUM(input_tokens), SUM(output_tokens), "
                "SUM(cost_usd), COUNT(*), AVG(latency_ms) "
                "FROM llm_usage WHERE timestamp > ? "
                "GROUP BY provider, model "
                "ORDER BY SUM(cost_usd) DESC",
                (since,),
            )
            rows = cursor.fetchall()
            conn.close()

            return {
                "period_hours": hours,
                "by_model": [
                    {
                        "provider": r[0],
                        "model": r[1],
                        "input_tokens": r[2],
                        "output_tokens": r[3],
                        "cost_usd": round(r[4], 4),
                        "calls": r[5],
                        "avg_latency_ms": round(r[6], 1),
                    }
                    for r in rows
                ],
                "total_cost_usd": round(sum(r[4] for r in rows), 4),
                "total_calls": sum(r[5] for r in rows),
            }
        except Exception:
            return {"period_hours": hours, "by_model": [], "total_cost_usd": 0, "total_calls": 0}
```

---

## 3.4 — Streaming Response Support

### Problem
Users wait 10-30s for a response with no feedback.

### Implementation
Add SSE (Server-Sent Events) streaming support. This requires changes to:

1. **Provider base class** — add `chat_stream()` method
2. **Gateway server** — add SSE endpoint
3. **Agent loop** — yield partial responses

```python
# Add to pawbot/providers/base.py:

from typing import AsyncIterator

class LLMProvider:
    # ... existing methods ...

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream chat completion tokens.
        
        Default implementation falls back to non-streaming chat.
        Override in provider subclasses for true streaming.
        """
        response = await self.chat(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
        if response.content:
            yield response.content
```

> [!NOTE]
> Full streaming implementation requires changes to the OpenAI/Anthropic provider subclasses to use their native streaming APIs. This is a larger effort that can be done incrementally — the base class fallback ensures everything works without streaming first.

---

## Verification Checklist — Phase 3 Complete

- [ ] Routing table is configurable via `config.json` → `routing.rules`
- [ ] `ProviderHealthMonitor` runs in background during gateway mode
- [ ] `/api/providers/health` endpoint returns health status for all providers
- [ ] `CostTracker` records every LLM call to `llm_usage` table
- [ ] `/api/metrics/usage` endpoint returns cost summary
- [ ] Base `chat_stream()` method exists on `LLMProvider`
- [ ] All tests pass: `pytest tests/ -v --tb=short`
- [ ] Agent works normally with no routing config (uses hardcoded defaults)
