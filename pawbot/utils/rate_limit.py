"""Small in-memory request rate limiter used by gateway and dashboard routes."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


class RateLimitExceeded(Exception):
    """Raised when a request exceeds its configured rate limit."""

    def __init__(self, limit: str, retry_after: float):
        self.limit = limit
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded ({limit})")


@dataclass(frozen=True)
class RateLimitRule:
    """Parsed rate limit rule."""

    limit: int
    window_seconds: int
    raw: str


@lru_cache(maxsize=32)
def parse_rate_limit(limit: str) -> RateLimitRule:
    """Parse rules like ``10/minute`` or ``60/hour``."""
    try:
        count_raw, unit_raw = limit.strip().lower().split("/", 1)
        count = int(count_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid rate limit: {limit!r}") from exc

    unit = unit_raw.rstrip("s")
    window_seconds = {
        "second": 1,
        "minute": 60,
        "hour": 3600,
        "day": 86400,
    }.get(unit)
    if window_seconds is None:
        raise ValueError(f"Unsupported rate limit unit: {unit_raw!r}")
    if count <= 0:
        raise ValueError("Rate limit count must be positive")

    return RateLimitRule(limit=count, window_seconds=window_seconds, raw=limit)


class RequestRateLimiter:
    """Thread-safe sliding-window limiter keyed by route and remote client."""

    def __init__(self, time_source: Any | None = None):
        self._time = time_source or time.monotonic
        self._lock = threading.Lock()
        self._events: dict[tuple[str, str, str], deque[float]] = {}

    @staticmethod
    def client_key(request: Any) -> str:
        """Resolve a stable client identifier from a FastAPI request."""
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip() or "forwarded"

        if getattr(request, "client", None) and request.client.host:
            return request.client.host

        return "unknown"

    def allow(self, scope: str, client: str, limit: str) -> tuple[bool, float]:
        """Check whether a client may proceed under a limit."""
        rule = parse_rate_limit(limit)
        key = (scope, client, rule.raw)
        now = self._time()

        with self._lock:
            bucket = self._events.setdefault(key, deque())
            cutoff = now - rule.window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= rule.limit:
                retry_after = max(0.0, rule.window_seconds - (now - bucket[0]))
                return False, retry_after

            bucket.append(now)
            return True, 0.0

    def check_request(self, request: Any, scope: str, limit: str) -> None:
        """Raise ``RateLimitExceeded`` when a request is over limit."""
        client = self.client_key(request)
        allowed, retry_after = self.allow(scope=scope, client=client, limit=limit)
        if not allowed:
            raise RateLimitExceeded(limit=limit, retry_after=retry_after)

    def reset(self) -> None:
        """Clear all in-memory counters. Intended for tests."""
        with self._lock:
            self._events.clear()
