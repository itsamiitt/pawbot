"""Speed and fixture-sharing regression checks."""

from __future__ import annotations

import time

from pawbot.agent.memory import MemoryRouter


def test_shared_sqlite_router_is_session_scoped(shared_sqlite_router, request) -> None:
    same = request.getfixturevalue("shared_sqlite_router")
    assert same is shared_sqlite_router


def test_sqlite_router_init_is_fast(tmp_path) -> None:
    config = {
        "memory": {
            "backends": {
                "redis": {"enabled": False},
                "sqlite": {"enabled": True, "path": str(tmp_path / "speed_test.db")},
                "chroma": {"enabled": False},
            }
        }
    }
    start = time.monotonic()
    router = MemoryRouter("speed_test", config)
    elapsed = time.monotonic() - start
    assert router is not None
    assert elapsed < 5.0, f"SQLite-only MemoryRouter init too slow: {elapsed:.2f}s"


def test_shared_router_can_save_and_search(shared_sqlite_router) -> None:
    mem_id = shared_sqlite_router.save(
        "fact",
        {"text": "test_suite_speed fixture verification", "source": "test"},
    )
    assert mem_id is not None
    results = shared_sqlite_router.search("fixture verification", limit=5)
    assert isinstance(results, list)
