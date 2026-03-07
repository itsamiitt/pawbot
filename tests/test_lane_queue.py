"""
tests/test_lane_queue.py

Tests for the Session Lane Queue.
Run: pytest tests/test_lane_queue.py -v
"""

import asyncio
import pytest
from pawbot.agent.lane_queue import LaneQueue  # import class (not singleton) for test isolation


@pytest.mark.asyncio
async def test_sequential_execution_within_session():
    """Messages on the same session must execute in order, never in parallel."""
    lq = LaneQueue()
    results = []

    async def slow_handler(val):
        await asyncio.sleep(0.05)
        results.append(val)

    await lq.enqueue("sess_A", slow_handler, 1)
    await lq.enqueue("sess_A", slow_handler, 2)
    await lq.enqueue("sess_A", slow_handler, 3)
    await asyncio.sleep(0.4)  # wait for all to complete

    assert results == [1, 2, 3], f"Expected [1,2,3] got {results}"
    await lq.shutdown()


@pytest.mark.asyncio
async def test_concurrent_execution_across_sessions():
    """Different sessions must run concurrently — not blocked by each other."""
    lq = LaneQueue()
    start_times: dict[str, float] = {}
    end_times: dict[str, float] = {}

    async def timed_handler(session_id: str):
        import time
        start_times[session_id] = time.monotonic()
        await asyncio.sleep(0.1)
        end_times[session_id] = time.monotonic()

    await lq.enqueue("sess_A", timed_handler, "sess_A")
    await lq.enqueue("sess_B", timed_handler, "sess_B")
    await asyncio.sleep(0.3)

    # Both sessions should overlap — sess_B should start before sess_A finishes
    assert "sess_A" in start_times and "sess_B" in start_times
    overlap = start_times["sess_B"] < end_times["sess_A"]
    assert overlap, "Sessions should run concurrently"
    await lq.shutdown()


@pytest.mark.asyncio
async def test_handler_error_does_not_kill_lane():
    """A crashing handler must not stop the worker — next message must still process."""
    lq = LaneQueue()
    results = []

    async def bad_handler():
        raise ValueError("intentional test error")

    async def good_handler(val):
        results.append(val)

    await lq.enqueue("sess_A", bad_handler)
    await lq.enqueue("sess_A", good_handler, "survived")
    await asyncio.sleep(0.3)

    assert results == ["survived"], f"Lane should survive handler error, got {results}"
    await lq.shutdown()


@pytest.mark.asyncio
async def test_stats_returns_queue_depths():
    """stats() must return {session_id: queue_depth} dict for /health endpoint."""
    lq = LaneQueue()

    async def slow(_):
        await asyncio.sleep(0.2)

    # Enqueue 3 items — first starts immediately, 2 wait
    await lq.enqueue("sess_X", slow, None)
    await lq.enqueue("sess_X", slow, None)
    await lq.enqueue("sess_X", slow, None)

    stats = lq.stats()
    assert "sess_X" in stats
    # Depth will be 2 (first item dequeued by worker, 2 waiting)
    assert stats["sess_X"] >= 1
    await lq.shutdown()


@pytest.mark.asyncio
async def test_idle_cleanup():
    """After IDLE_CLEANUP_SECS of no messages, the lane must self-clean."""
    lq = LaneQueue()
    lq.IDLE_CLEANUP_SECS = 0.1  # override to 100ms for test speed

    async def noop():
        pass

    await lq.enqueue("sess_Y", noop)
    await asyncio.sleep(0.5)  # wait for idle cleanup

    assert "sess_Y" not in lq._queues, "Idle lane should have been cleaned up"
    assert "sess_Y" not in lq._workers


@pytest.mark.asyncio
async def test_shutdown_cancels_all_workers():
    """shutdown() must cancel all worker tasks without hanging."""
    lq = LaneQueue()

    async def long_task():
        await asyncio.sleep(60)  # will be cancelled

    await lq.enqueue("sess_A", long_task)
    await lq.enqueue("sess_B", long_task)
    assert lq.active_sessions() == 2

    await asyncio.wait_for(lq.shutdown(), timeout=3.0)
    assert lq.active_sessions() == 0, "All sessions should be cleared after shutdown"
