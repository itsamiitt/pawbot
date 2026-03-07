"""Provider health monitoring — background health checks.

Phase 3: Tracks provider health status with periodic checks,
latency tracking, and automatic degradation detection.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

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
    """Background health checker for all configured providers.

    Usage::

        monitor = ProviderHealthMonitor(config)
        monitor.register("ollama")
        monitor.register("openrouter")
        await monitor.start()
        # ... later ...
        if monitor.is_healthy("ollama"):
            ...
        await monitor.stop()
    """

    CHECK_INTERVAL_S = 60       # Check every 60 seconds
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

    def record_success(self, provider: str, latency_ms: float) -> None:
        """Record a successful call (called by ResilientLLMCaller)."""
        health = self._providers.get(provider)
        if health is None:
            return
        health.last_success = time.time()
        health.consecutive_failures = 0
        health.latency_ms = latency_ms
        health.error = ""
        if latency_ms > self.DEGRADED_LATENCY_MS:
            health.status = HealthStatus.DEGRADED
        else:
            health.status = HealthStatus.HEALTHY

    def record_failure(self, provider: str, error: str) -> None:
        """Record a failed call (called by ResilientLLMCaller)."""
        health = self._providers.get(provider)
        if health is None:
            return
        health.consecutive_failures += 1
        health.error = error[:200]
        if health.consecutive_failures >= self.UNHEALTHY_AFTER_FAILURES:
            health.status = HealthStatus.UNHEALTHY

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
        try:
            import httpx
        except ImportError:
            health.status = HealthStatus.UNKNOWN
            return
        base_url = self.config.get("providers", {}).get("ollama", {}).get("api_base", "http://localhost:11434")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base_url}/api/tags")
            r.raise_for_status()

    async def _check_openrouter(self, health: ProviderHealth) -> None:
        """Check OpenRouter availability."""
        try:
            import httpx
        except ImportError:
            health.status = HealthStatus.UNKNOWN
            return
        api_key = self.config.get("providers", {}).get("openrouter", {}).get("api_key", "")
        if not api_key:
            raise ValueError("No API key configured")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            r.raise_for_status()

    async def _check_anthropic(self, health: ProviderHealth) -> None:
        """Check Anthropic availability (lightweight — just key format check)."""
        api_key = self.config.get("providers", {}).get("anthropic", {}).get("api_key", "")
        if not api_key:
            raise ValueError("No API key configured")
        # Anthropic doesn't have a lightweight health endpoint
        if not api_key.startswith("sk-ant-"):
            raise ValueError("Invalid Anthropic key format")
        health.status = HealthStatus.HEALTHY

    async def _check_openai(self, health: ProviderHealth) -> None:
        """Check OpenAI availability."""
        try:
            import httpx
        except ImportError:
            health.status = HealthStatus.UNKNOWN
            return
        api_key = self.config.get("providers", {}).get("openai", {}).get("api_key", "")
        if not api_key:
            raise ValueError("No API key configured")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            r.raise_for_status()
