"""Tests for refactored code_search mode helpers."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest


def _load_server_module():
    path = Path(__file__).resolve().parents[1] / "mcp-servers" / "coding" / "server.py"
    spec = importlib.util.spec_from_file_location("coding_server_modes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def coding_module():
    return _load_server_module()


def test_keyword_mode_parses_rg_output(coding_module, monkeypatch, tmp_path: Path) -> None:
    fake_stdout = (
        f"{tmp_path}/main.py:42:    result = do_search(query)\n"
        f"{tmp_path}/utils.py:10:def do_search(q):\n"
    )
    monkeypatch.setattr(coding_module, "_run_subprocess", lambda *_a, **_k: {"ok": True, "stdout": fake_stdout})
    results = coding_module._search_keyword_mode("do_search", str(tmp_path))
    assert len(results) == 2
    assert results[0]["line_number"] == 42
    assert results[0]["match_type"] == "keyword"


def test_keyword_mode_falls_back_when_rg_fails(coding_module, monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def _fallback(query: str, root: str):
        calls.append((query, root))
        return []

    monkeypatch.setattr(coding_module, "_run_subprocess", lambda *_a, **_k: {"ok": False, "stderr": "rg missing"})
    monkeypatch.setattr(coding_module, "_search_keyword_fallback", _fallback)
    coding_module._search_keyword_mode("query", str(tmp_path))
    assert calls == [("query", str(tmp_path))]


def test_symbol_mode_returns_error_when_no_index(coding_module, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(coding_module, "INDEX_DIR", str(tmp_path))
    result = coding_module._search_symbol_mode("my_function", str(tmp_path / "project"))
    assert "error" in result
    assert "code_index_project" in result["error"]


def test_symbol_mode_queries_index(coding_module, monkeypatch, tmp_path: Path) -> None:
    project_path = str(tmp_path / "project")
    os.makedirs(project_path, exist_ok=True)
    monkeypatch.setattr(coding_module, "INDEX_DIR", str(tmp_path))
    db_path = tmp_path / f"{coding_module._get_project_hash(project_path)}.db"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE symbols (file_path TEXT, symbol_type TEXT, symbol_name TEXT, line_number INTEGER)"
        )
        conn.execute("INSERT INTO symbols VALUES (?, ?, ?, ?)", ("main.py", "function", "my_function", 15))

    results = coding_module._search_symbol_mode("my_function", project_path)
    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0]["line_number"] == 15
    assert results[0]["match_type"] == "symbol"


def test_error_mode_finds_log_entries(coding_module, tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    log_file.write_text(
        "2026-03-01 normal line\n"
        "2026-03-02 ERROR: connection refused\n"
        "2026-03-03 normal line\n",
        encoding="utf-8",
    )
    results = coding_module._search_error_mode("connection refused", str(tmp_path))
    assert results
    assert "connection refused" in results[0]["snippet"].lower()
    assert results[0]["match_type"] == "error"


def test_semantic_alias_points_to_keyword(coding_module) -> None:
    assert coding_module._SEARCH_MODE_ALIASES.get("semantic") == "keyword"


def test_search_modes_complete(coding_module) -> None:
    for mode in ("keyword", "symbol", "error"):
        assert mode in coding_module._SEARCH_MODES
        assert callable(coding_module._SEARCH_MODES[mode])
