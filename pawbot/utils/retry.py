"""Retry with exponential backoff for external API calls."""
import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger("pawbot.utils.retry")
T = TypeVar("T")


def call_with_retry(
    fn: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> T:
    """
    Call fn() with exponential backoff on transient failures.

    Retries on:    429 rate limit, 5xx server errors, network errors.
    Never retries: 401 unauthorized (raises ConfigError), 400 bad request.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            err = str(e).lower()
            if "401" in err or "unauthorized" in err or "authentication" in err:
                from pawbot.errors import ConfigError
                raise ConfigError(
                    "API key is invalid or expired.\n"
                    "Update: ~/.pawbot/config.json\n"
                    "Or run: pawbot onboard --setup"
                ) from e
            if "400" in err and "rate" not in err:
                raise
            is_transient = (
                "429" in err or "rate limit" in err or "too many" in err
                or any(f"{c}" in err for c in [500, 502, 503, 504])
                or "connection" in err or "timeout" in err or "network" in err
            )
            if is_transient and attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                kind = "rate limited" if "429" in err or "rate" in err else "server/network error"
                logger.warning(f"API {kind} — retrying in {delay:.1f}s (attempt {attempt+1}/{max_retries})")
                time.sleep(delay)
                last_error = e
                continue
            raise
    raise last_error  # type: ignore[misc]
