"""
pawbot/agent/lane_queue.py

Session Lane Queue — serializes message processing per session.
One asyncio.Queue per session_id. One worker coroutine per queue.
Messages within the same session are processed one at a time.
Different sessions run fully concurrently.

Usage:
    from pawbot.agent.lane_queue import lane_queue
    await lane_queue.enqueue(session_id, handler_coroutine, arg1, arg2)

IMPORTS FROM: pawbot/contracts.py — get_logger(), now()
SINGLETON: lane_queue — import this everywhere, never instantiate LaneQueue directly
"""

import asyncio
from pawbot.contracts import get_logger, now

logger = get_logger(__name__)


class LaneQueue:
    """
    Guarantees sequential message processing per session lane.

    Architecture:
    - Each session_id gets exactly one asyncio.Queue and one worker Task.
    - enqueue() appends (handler, args) to the session queue.
    - The worker Task pulls items one at a time and awaits them.
    - After IDLE_CLEANUP_SECS of no activity, the worker exits and
      the queue is garbage-collected.
    - maxsize=50 prevents memory exhaustion from burst messages.
    """

    IDLE_CLEANUP_SECS: int = 1800  # 30 minutes
    MAX_QUEUE_SIZE: int = 50       # max pending messages per session

    def __init__(self) -> None:
        # session_id -> asyncio.Queue of (handler, args) tuples
        self._queues: dict[str, asyncio.Queue] = {}
        # session_id -> running asyncio.Task (the worker)
        self._workers: dict[str, asyncio.Task] = {}
        # session_id -> Unix timestamp of last enqueue
        self._last_active: dict[str, int] = {}

    async def enqueue(self, session_id: str, handler, *args) -> None:
        """
        Add a message handler to the session queue.

        Args:
            session_id: Unique session identifier (e.g. "telegram_12345")
            handler:    Async coroutine function to call
            *args:      Arguments to pass to handler

        Raises:
            asyncio.QueueFull: if session queue exceeds MAX_QUEUE_SIZE
        """
        # Create queue and worker for new sessions
        if session_id not in self._queues:
            self._queues[session_id] = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)
            self._workers[session_id] = asyncio.create_task(
                self._worker(session_id),
                name=f"lane-worker-{session_id}"
            )
            logger.debug(f"LaneQueue: created lane for session {session_id}")

        await self._queues[session_id].put((handler, args))
        self._last_active[session_id] = now()

        depth = self._queues[session_id].qsize()
        logger.debug(f"LaneQueue: enqueued for {session_id} (depth={depth})")

        if depth >= self.MAX_QUEUE_SIZE * 0.8:
            logger.warning(f"LaneQueue: session {session_id} queue near capacity ({depth})")

    async def _worker(self, session_id: str) -> None:
        """
        Worker coroutine — runs for the lifetime of a session.
        Processes items one at a time. Exits after IDLE_CLEANUP_SECS of inactivity.
        """
        queue = self._queues[session_id]
        logger.debug(f"LaneQueue: worker started for {session_id}")

        while True:
            try:
                # Wait for next item or timeout (idle cleanup)
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=self.IDLE_CLEANUP_SECS
                )
                handler, args = item
                try:
                    await handler(*args)
                except Exception as exc:
                    # Log error but keep the worker alive — never let one bad
                    # message kill the entire session lane
                    logger.error(
                        f"LaneQueue: handler error in session {session_id}: {exc}",
                        exc_info=True
                    )
                finally:
                    queue.task_done()

            except asyncio.TimeoutError:
                # No messages for IDLE_CLEANUP_SECS — this lane is idle, clean up
                logger.debug(f"LaneQueue: session {session_id} idle, cleaning up lane")
                self._cleanup_lane(session_id)
                return

            except asyncio.CancelledError:
                # Graceful shutdown
                logger.info(f"LaneQueue: worker for {session_id} cancelled")
                return

    def _cleanup_lane(self, session_id: str) -> None:
        """Remove a session lane and its worker from internal dicts."""
        self._queues.pop(session_id, None)
        self._workers.pop(session_id, None)
        self._last_active.pop(session_id, None)

    async def shutdown(self) -> None:
        """Gracefully cancel all worker tasks. Call on application shutdown."""
        for session_id, task in list(self._workers.items()):
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._queues.clear()
        self._workers.clear()
        self._last_active.clear()
        logger.info("LaneQueue: all workers shut down")

    def stats(self) -> dict[str, int]:
        """Return current queue depths per session. Used by /health endpoint."""
        return {sid: q.qsize() for sid, q in self._queues.items()}

    def active_sessions(self) -> int:
        """Return number of active session lanes."""
        return len(self._queues)


# ── Singleton ──────────────────────────────────────────────────────────────────
# Import this everywhere. Never instantiate LaneQueue directly.
lane_queue = LaneQueue()
