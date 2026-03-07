"""Per-agent heartbeat helpers."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger


def parse_duration(duration: str) -> int:
    """Parse a compact duration string such as ``30m`` into seconds."""
    match = re.match(r"^(\d+)\s*(s|m|h|d)$", duration.strip().lower())
    if not match:
        raise ValueError(
            f"Invalid duration: '{duration}'. Use 30m, 1h, 6h, or 1d."
        )

    value = int(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers[unit]


class AgentHeartbeat:
    """Periodic heartbeat loop for a single agent."""

    def __init__(
        self,
        agent_id: str,
        interval: str = "30m",
        target: str = "last",
        on_heartbeat: Callable[[str, str], Awaitable[None] | None] | None = None,
    ):
        self.agent_id = agent_id
        self.interval = interval
        self.interval_seconds = parse_duration(interval)
        self.target = target
        self._on_heartbeat = on_heartbeat
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_beat = 0.0
        self._beat_count = 0
        self._errors = 0

    async def start(self) -> None:
        """Start the heartbeat loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._beat_loop())
        logger.info(
            "Heartbeat started for agent '{}' (every {}s)",
            self.agent_id,
            self.interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the heartbeat loop."""
        self._running = False
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _beat_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                if not self._running:
                    break

                self._beat_count += 1
                self._last_beat = time.time()

                if self._on_heartbeat is not None:
                    result = self._on_heartbeat(self.agent_id, self.target)
                    if isinstance(result, Awaitable):
                        await result

                logger.debug(
                    "Heartbeat #{} for agent '{}' (target={})",
                    self._beat_count,
                    self.agent_id,
                    self.target,
                )
            except asyncio.CancelledError:
                break
            except Exception:
                self._errors += 1
                logger.exception("Heartbeat loop error for '{}'", self.agent_id)
                await asyncio.sleep(1)

    @property
    def stats(self) -> dict[str, Any]:
        """Runtime counters for dashboard inspection."""
        return {
            "agent_id": self.agent_id,
            "running": self._running,
            "interval": self.interval,
            "interval_seconds": self.interval_seconds,
            "target": self.target,
            "beat_count": self._beat_count,
            "errors": self._errors,
            "last_beat": self._last_beat,
            "seconds_since_beat": (
                round(time.time() - self._last_beat, 1) if self._last_beat else None
            ),
        }
