"""Message debouncing — collect rapid follow-up messages into one (Phase 11.3).

When a user sends multiple messages quickly (e.g., typing line-by-line),
this collects them and delivers as one combined message after a
configurable delay.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from loguru import logger


class MessageDebouncer:
    """Debounce rapid messages from the same user into a single batch.

    Usage:
        debouncer = MessageDebouncer(delay_ms=500, on_deliver=process_fn)
        await debouncer.push(session_key, sender_id, "first message")
        await debouncer.push(session_key, sender_id, "second message")
        # After 500ms, process_fn is called with "first message\nsecond message"
    """

    def __init__(
        self,
        delay_ms: int = 500,
        on_deliver: Callable[[str, str, str], Awaitable[None]] | None = None,
    ):
        self._delay_ms = delay_ms
        self._on_deliver = on_deliver
        self._buffers: dict[str, list[str]] = {}  # session_key -> messages
        self._senders: dict[str, str] = {}         # session_key -> sender_id
        self._timers: dict[str, asyncio.Task[None]] = {}

    @property
    def delay_ms(self) -> int:
        """Current debounce delay in milliseconds."""
        return self._delay_ms

    @property
    def pending_count(self) -> int:
        """Number of sessions with buffered messages."""
        return len(self._buffers)

    async def push(self, session_key: str, sender_id: str, content: str) -> None:
        """Push a message into the debounce buffer."""
        if self._delay_ms <= 0:
            # Debounce disabled — deliver immediately
            if self._on_deliver:
                await self._on_deliver(session_key, sender_id, content)
            return

        # Add to buffer
        if session_key not in self._buffers:
            self._buffers[session_key] = []
        self._buffers[session_key].append(content)
        self._senders[session_key] = sender_id

        # Cancel existing timer
        if session_key in self._timers:
            self._timers[session_key].cancel()

        # Start new timer
        self._timers[session_key] = asyncio.create_task(
            self._flush_after_delay(session_key, sender_id)
        )

    async def _flush_after_delay(self, session_key: str, sender_id: str) -> None:
        """Wait for the debounce delay, then deliver the combined message."""
        try:
            await asyncio.sleep(self._delay_ms / 1000.0)
        except asyncio.CancelledError:
            return

        messages = self._buffers.pop(session_key, [])
        self._timers.pop(session_key, None)
        self._senders.pop(session_key, None)

        if not messages:
            return

        combined = "\n".join(messages)
        if len(messages) > 1:
            logger.debug(
                "Debounced {} messages from '{}' into one",
                len(messages), session_key,
            )

        if self._on_deliver:
            await self._on_deliver(session_key, sender_id, combined)

    async def flush_all(self) -> None:
        """Flush all pending buffers (used during shutdown)."""
        for session_key in list(self._timers.keys()):
            timer = self._timers.pop(session_key, None)
            if timer:
                timer.cancel()
        self._buffers.clear()
        self._senders.clear()

    async def force_flush(self, session_key: str) -> None:
        """Force-flush a specific session's buffer immediately."""
        timer = self._timers.pop(session_key, None)
        if timer:
            timer.cancel()

        messages = self._buffers.pop(session_key, [])
        sender_id = self._senders.pop(session_key, "")

        if messages and self._on_deliver:
            combined = "\n".join(messages)
            await self._on_deliver(session_key, sender_id, combined)
