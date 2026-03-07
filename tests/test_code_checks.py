"""Tests for extracted code_run_checks helper functions."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_server_module():
    path = Path(__file__).resolve().parents[1] / "mcp-servers" / "coding" / "server.py"
    spec = importlib.util.spec_from_file_location("coding_server_checks", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def coding_module():
    return _load_server_module()


def test_lint_python_passes_clean_file(coding_module, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        coding_module,
        "_run_subprocess",
        lambda *_a, **_k: {"ok": True, "stdout": "", "stderr": ""},
    )
    result = coding_module._check_lint_python(str(tmp_path / "main.py"))
    assert result["ok"] is True


def test_lint_python_skips_when_ruff_missing(coding_module, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        coding_module,
        "_run_subprocess",
        lambda *_a, **_k: {"ok": False, "error": "Command not found: ruff"},
    )
    result = coding_module._check_lint_python(str(tmp_path / "main.py"))
    assert result["ok"] is True
    assert result.get("skipped") is True


def test_lint_js_skips_when_no_eslint_config(coding_module, tmp_path: Path) -> None:
    result = coding_module._check_lint_js(str(tmp_path / "app.js"), str(tmp_path))
    assert result["ok"] is True
    assert result.get("skipped") is True


def test_typecheck_returns_none_when_no_mypy_config(coding_module, tmp_path: Path) -> None:
    result = coding_module._check_typecheck_python(str(tmp_path / "main.py"), str(tmp_path))
    assert result is None


def test_find_related_tests_discovers_test_file(coding_module, tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_file = tests_dir / "test_main.py"
    test_file.write_text("# test stub\n", encoding="utf-8")
    source_file = tmp_path / "main.py"
    source_file.write_text("# source stub\n", encoding="utf-8")

    results = coding_module._find_related_tests(str(source_file), str(tmp_path))
    assert str(test_file) in results


def test_find_related_tests_returns_empty_when_none(coding_module, tmp_path: Path) -> None:
    source_file = tmp_path / "orphan.py"
    source_file.write_text("# source stub\n", encoding="utf-8")
    results = coding_module._find_related_tests(str(source_file), str(tmp_path))
    assert results == []
