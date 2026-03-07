"""Shared fixtures for pawbot tests."""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest

from pawbot.agent.memory import MemoryRouter


def _sqlite_only_config(db_path: str) -> dict:
    return {
        "memory": {
            "backends": {
                "redis": {"enabled": False},
                "sqlite": {"enabled": True, "path": db_path},
                "chroma": {"enabled": False},
            }
        }
    }


def _chroma_config(db_path: str, chroma_path: str) -> dict:
    return {
        "memory": {
            "backends": {
                "redis": {"enabled": False},
                "sqlite": {"enabled": True, "path": db_path},
                "chroma": {"enabled": True, "path": chroma_path},
            }
        }
    }


@pytest.fixture(scope="session")
def session_tmp(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("pawbot_session")


@pytest.fixture(scope="session")
def shared_sqlite_router(session_tmp: Path) -> Generator[MemoryRouter, None, None]:
    db_path = str(session_tmp / "shared_facts.db")
    router = MemoryRouter("shared_session", _sqlite_only_config(db_path))
    yield router


@pytest.fixture(scope="session")
def shared_chroma_router(session_tmp: Path) -> Generator[MemoryRouter, None, None]:
    db_path = str(session_tmp / "chroma_facts.db")
    chroma_path = str(session_tmp / "chroma_store")
    router = MemoryRouter("chroma_session", _chroma_config(db_path, chroma_path))
    yield router


@pytest.fixture
def lightweight_memory_config(tmp_path: Path) -> dict:
    return _sqlite_only_config(str(tmp_path / "test_facts.db"))


@pytest.fixture
def fresh_sqlite_router(lightweight_memory_config: dict) -> MemoryRouter:
    return MemoryRouter("test_session", lightweight_memory_config)
