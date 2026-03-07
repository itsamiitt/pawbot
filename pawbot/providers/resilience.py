"""LLM call resilience — retry, timeout, circuit breaker.

Phase 1: Wraps LLM provider calls with automatic retry (exponential backoff),
per-call timeout, and call statistics tracking.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from loguru import logger

from pawbot.errors import ProviderError, ProviderUnavailableError


# Exceptions that should trigger retry
_RETRYABLE = (
    asyncio.TimeoutError,
    ConnectionError,
    ProviderError,
    OSError,
)

# Exceptions that should NOT retry (bad request, auth failure, etc.)
_NON_RETRYABLE = (
    ValueError,
    ProviderUnavailableError,
)


class ResilientLLMCaller:
    """Wraps LLM provider calls with retry, timeout, and metrics.

    Usage::

        caller = ResilientLLMCaller(max_retries=3, timeout_seconds=120)
        response = await caller.call(provider.chat, messages=msgs, model="gpt-4o")
    """

    def __init__(
        self,
        max_retries: int = 3,
        timeout_seconds: float = 120.0,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
    ):
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._call_count = 0
        self._error_count = 0
        self._total_latency = 0.0

    async def call(self, provider_fn: Callable[..., Awaitable[Any]], **kwargs: Any) -> Any:
        """Call an async LLM provider function with retry and timeout.

        Args:
            provider_fn: The async provider method to call (e.g., provider.chat)
            **kwargs: Arguments to pass to the provider function

        Returns:
            The provider response

        Raises:
            ProviderUnavailableError: If all retries are exhausted
            asyncio.TimeoutError: If the call exceeds timeout_seconds
        """
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            self._call_count += 1
            start = time.monotonic()

            try:
                result = await asyncio.wait_for(
                    provider_fn(**kwargs),
                    timeout=self.timeout_seconds,
                )
                elapsed = time.monotonic() - start
                self._total_latency += elapsed

                if attempt > 1:
                    logger.info(
                        "LLM call succeeded on attempt {}/{} after {:.1f}s",
                        attempt, self.max_retries, elapsed,
                    )
                return result

            except _NON_RETRYABLE as e:
                # Don't retry bad requests or auth failures
                logger.error("LLM call failed (non-retryable): {}", e)
                raise

            except _RETRYABLE as e:
                last_error = e
                self._error_count += 1
                elapsed = time.monotonic() - start

                if attempt < self.max_retries:
                    delay = min(
                        self.base_delay * (2 ** (attempt - 1)),
                        self.max_delay,
                    )
                    logger.warning(
                        "LLM call failed (attempt {}/{}): {} — retrying in {:.1f}s",
                        attempt, self.max_retries, e, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "LLM call failed after {} attempts: {}",
                        self.max_retries, e,
                    )

            except Exception as e:
                # Unexpected errors — log and raise without retry
                logger.error("LLM call failed with unexpected error: {}", e)
                raise

        raise ProviderUnavailableError(
            f"LLM provider unavailable after {self.max_retries} attempts. "
            f"Last error: {last_error}"
        )

    @property
    def stats(self) -> dict[str, Any]:
        """Return call statistics."""
        successful = max(self._call_count - self._error_count, 1)
        return {
            "total_calls": self._call_count,
            "total_errors": self._error_count,
            "avg_latency_ms": (self._total_latency / successful) * 1000,
        }
