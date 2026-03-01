"""Tests for the Phase 7 Coding Engine MCP server."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest


def _load_server_module():
    path = Path(__file__).resolve().parents[1] / "mcp-servers" / "coding" / "server.py"
    spec = importlib.util.spec_from_file_location("coding_mcp", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def coding_module():
    return _load_server_module()


@pytest.fixture()
def isolated_paths(coding_module, monkeypatch, tmp_path: Path):
    idx = tmp_path / "code-indexes"
    registry = tmp_path / "checkpoints" / "registry.json"
    monkeypatch.setattr(coding_module, "INDEX_DIR", str(idx))
    monkeypatch.setattr(coding_module, "CHECKPOINT_REGISTRY", str(registry))
    return {"index_dir": idx, "registry": registry}


class TestCodingMCP:
    def test_server_starts_without_error(self, coding_module):
        assert coding_module.mcp is not None
        assert callable(coding_module.main)

    def test_list_tools_returns_all_tools(self, coding_module):
        tools = coding_module.list_tools()
        assert tools["count"] == len(coding_module.TOOL_NAMES)
        assert set(tools["tools"]) == set(coding_module.TOOL_NAMES)

    def test_each_tool_handles_invalid_args_gracefully(
        self, coding_module, isolated_paths, tmp_path: Path
    ):
        missing = tmp_path / "missing.py"
        checks = [
            coding_module.code_index_project(str(tmp_path / "not-here")),
            coding_module.code_get_context(str(missing)),
            coding_module.code_get_dependencies(str(missing), project_path=str(tmp_path)),
            coding_module.code_search("", str(tmp_path)),
            coding_module.code_edit(str(missing), "a", "b"),
            coding_module.code_run_checks(str(missing)),
            coding_module.code_checkpoint("cp", str(tmp_path / "absent-project")),
            coding_module.code_rollback("unknown-checkpoint"),
        ]
        assert all("error" in result for result in checks)


class TestCodeIndex:
    def test_indexes_python_project(self, coding_module, isolated_paths, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "app.py").write_text("def hello():\n    return 1\n", encoding="utf-8")

        result = coding_module.code_index_project(str(proj))
        assert result["total_files"] == 1
        assert result["total_symbols"] >= 1
        assert Path(result["db_path"]).exists()

    def test_skips_node_modules_and_git(self, coding_module, isolated_paths, tmp_path: Path):
        proj = tmp_path / "proj"
        (proj / "src").mkdir(parents=True, exist_ok=True)
        (proj / "node_modules").mkdir(parents=True, exist_ok=True)
        (proj / ".git").mkdir(parents=True, exist_ok=True)
        (proj / "src" / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")
        (proj / "node_modules" / "skip.py").write_text("def x():\n    pass\n", encoding="utf-8")
        (proj / ".git" / "skip.py").write_text("def y():\n    pass\n", encoding="utf-8")

        result = coding_module.code_index_project(str(proj))
        assert result["total_files"] == 1

    def test_extracts_functions_and_classes(self, coding_module, isolated_paths, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "mod.py").write_text(
            "import os\n\nclass Thing:\n    pass\n\n\ndef func_a():\n    return os.getcwd()\n",
            encoding="utf-8",
        )

        result = coding_module.code_index_project(str(proj))
        with sqlite3.connect(result["db_path"]) as conn:
            rows = conn.execute(
                "SELECT symbol_type, symbol_name FROM symbols WHERE file_path = ?",
                (str(proj / "mod.py"),),
            ).fetchall()
        names = {(row[0], row[1]) for row in rows}
        assert ("class", "Thing") in names
        assert ("function", "func_a") in names

    def test_db_persists_after_reindex(self, coding_module, isolated_paths, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir(parents=True, exist_ok=True)
        file_path = proj / "reindex.py"
        file_path.write_text("def one():\n    return 1\n", encoding="utf-8")

        first = coding_module.code_index_project(str(proj))
        file_path.write_text("def one():\n    return 1\n\ndef two():\n    return 2\n", encoding="utf-8")
        second = coding_module.code_index_project(str(proj))

        assert first["db_path"] == second["db_path"]
        assert second["total_symbols"] >= first["total_symbols"]


class TestCodeGetContext:
    def test_small_file_returns_entirely(self, coding_module, tmp_path: Path):
        f = tmp_path / "small.py"
        f.write_text("a = 1\nb = 2\n", encoding="utf-8")
        result = coding_module.code_get_context(str(f))
        assert result["truncated"] is False
        assert "1: a = 1" in result["content"]

    def test_large_file_returns_relevant_chunks(self, coding_module, tmp_path: Path):
        f = tmp_path / "large.py"
        lines = ["import os", ""]
        for i in range(60):
            lines.extend(
                [
                    f"def func_{i}():",
                    f"    value = 'keyword_{i}'",
                    "    return value",
                    "",
                ]
            )
        f.write_text("\n".join(lines), encoding="utf-8")

        result = coding_module.code_get_context(str(f), query="keyword_42")
        assert result["truncated"] is True
        assert result["chunks_returned"] >= 1
        assert "keyword_42" in result["content"]

    def test_header_always_included(self, coding_module, tmp_path: Path):
        f = tmp_path / "header.py"
        lines = ["import json", "from pathlib import Path", ""]
        for i in range(80):
            lines.extend(
                [
                    f"def section_{i}():",
                    f"    return 'token_{i}'",
                    "",
                ]
            )
        f.write_text("\n".join(lines), encoding="utf-8")
        result = coding_module.code_get_context(str(f), query="token_79")
        assert "1: import json" in result["content"]

    def test_line_numbers_in_output(self, coding_module, tmp_path: Path):
        f = tmp_path / "line_numbers.py"
        f.write_text("first\nsecond\nthird\n", encoding="utf-8")
        result = coding_module.code_get_context(str(f))
        assert "1: first" in result["content"]
        assert "2: second" in result["content"]


class TestCodeWrite:
    def test_write_creates_backup(self, coding_module, tmp_path: Path):
        f = tmp_path / "writer.py"
        f.write_text("x = 1\n", encoding="utf-8")
        result = coding_module.code_write(str(f), "x = 2\n")
        assert result["success"] is True
        assert result["backup"]
        assert Path(result["backup"]).exists()

    def test_syntax_error_restores_backup(self, coding_module, tmp_path: Path):
        f = tmp_path / "bad.py"
        original = "def ok():\n    return 1\n"
        f.write_text(original, encoding="utf-8")

        result = coding_module.code_write(str(f), "def oops(:\n    pass\n")
        assert result["success"] is False
        assert "Syntax check failed" in result["error"]
        assert f.read_text(encoding="utf-8") == original

    def test_creates_parent_dirs(self, coding_module, tmp_path: Path):
        f = tmp_path / "nested" / "pkg" / "new_file.py"
        result = coding_module.code_write(str(f), "value = 3\n", backup=False)
        assert result["success"] is True
        assert f.exists()


class TestCodeEdit:
    def test_single_match_replaced(self, coding_module, tmp_path: Path):
        f = tmp_path / "edit.py"
        f.write_text("value = 1\nprint(value)\n", encoding="utf-8")
        result = coding_module.code_edit(str(f), "value = 1", "value = 2")
        assert result["success"] is True
        assert "value = 2" in f.read_text(encoding="utf-8")

    def test_multiple_matches_requires_disambiguation(self, coding_module, tmp_path: Path):
        f = tmp_path / "edit_multi.py"
        f.write_text("x = 1\nx = 1\n", encoding="utf-8")
        result = coding_module.code_edit(str(f), "x = 1", "x = 2")
        assert "error" in result
        assert "line_range" in result["error"]
        assert len(result["match_lines"]) == 2

    def test_not_found_returns_error(self, coding_module, tmp_path: Path):
        f = tmp_path / "edit_none.py"
        f.write_text("hello\n", encoding="utf-8")
        result = coding_module.code_edit(str(f), "absent", "present")
        assert "error" in result


class TestCodeRunChecks:
    def test_syntax_error_halts_early(self, coding_module, monkeypatch, tmp_path: Path):
        f = tmp_path / "broken.py"
        f.write_text("def bad(:\n    pass\n", encoding="utf-8")
        monkeypatch.setattr(coding_module, "_syntax_check", lambda _: {"ok": False, "error": "syntax"})
        result = coding_module.code_run_checks(str(f), project_path=str(tmp_path))
        assert result["passed"] is False
        assert result["halted_at"] == "syntax"

    def test_finds_related_test_files(self, coding_module, monkeypatch, tmp_path: Path):
        f = tmp_path / "foo.py"
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        related = tests_dir / "test_foo.py"
        f.write_text("def foo():\n    return 1\n", encoding="utf-8")
        related.write_text("def test_ok():\n    assert 1 == 1\n", encoding="utf-8")

        calls: list[list[str]] = []
        monkeypatch.setattr(coding_module, "_syntax_check", lambda _: {"ok": True})

        def _fake_run(args, cwd=None, timeout=30):
            calls.append(args)
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

        monkeypatch.setattr(coding_module, "_run_subprocess", _fake_run)
        result = coding_module.code_run_checks(str(f), project_path=str(tmp_path))
        assert result["passed"] is True
        assert "tests" in result["checks"]
        assert str(related) in result["checks"]["tests"]["test_files"]
        assert any(cmd and cmd[0] == "pytest" for cmd in calls)

    def test_all_passed_true_on_clean_file(self, coding_module, monkeypatch, tmp_path: Path):
        f = tmp_path / "clean.py"
        f.write_text("def clean():\n    return 1\n", encoding="utf-8")
        monkeypatch.setattr(coding_module, "_syntax_check", lambda _: {"ok": True})
        monkeypatch.setattr(
            coding_module,
            "_run_subprocess",
            lambda args, cwd=None, timeout=30: {"ok": True, "stdout": "", "stderr": "", "returncode": 0},
        )
        result = coding_module.code_run_checks(str(f), project_path=str(tmp_path))
        assert result["passed"] is True


class TestCheckpoints:
    def test_git_stash_checkpoint_created(self, coding_module, monkeypatch, tmp_path: Path):
        if not shutil.which("git"):
            pytest.skip("git not available")

        registry = tmp_path / "checkpoints" / "registry.json"
        monkeypatch.setattr(coding_module, "CHECKPOINT_REGISTRY", str(registry))

        proj = tmp_path / "gitproj"
        proj.mkdir(parents=True, exist_ok=True)
        file_path = proj / "app.py"
        file_path.write_text("x = 1\n", encoding="utf-8")

        subprocess.run(["git", "init"], cwd=proj, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=proj, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=proj, check=True)
        subprocess.run(["git", "add", "."], cwd=proj, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=proj, check=True, capture_output=True)

        file_path.write_text("x = 2\n", encoding="utf-8")
        result = coding_module.code_checkpoint("before-change", str(proj))
        assert result["method"] == "git_stash"
        data = json.loads(registry.read_text(encoding="utf-8"))
        assert result["checkpoint_id"] in data

    def test_zip_fallback_when_no_git(self, coding_module, monkeypatch, tmp_path: Path):
        registry = tmp_path / "checkpoints" / "registry.json"
        monkeypatch.setattr(coding_module, "CHECKPOINT_REGISTRY", str(registry))

        proj = tmp_path / "nogit"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "main.py").write_text("print('hi')\n", encoding="utf-8")

        result = coding_module.code_checkpoint("zipcp", str(proj))
        assert result["method"] == "zip"
        data = json.loads(registry.read_text(encoding="utf-8"))
        cp = data[result["checkpoint_id"]]
        assert Path(cp["zip_path"]).exists()

    def test_rollback_restores_files(self, coding_module, monkeypatch, tmp_path: Path):
        registry = tmp_path / "checkpoints" / "registry.json"
        monkeypatch.setattr(coding_module, "CHECKPOINT_REGISTRY", str(registry))

        proj = tmp_path / "rollback_proj"
        proj.mkdir(parents=True, exist_ok=True)
        f = proj / "state.py"
        f.write_text("value = 'old'\n", encoding="utf-8")

        cp = coding_module.code_checkpoint("state", str(proj))
        assert cp["checkpoint_id"]
        f.write_text("value = 'new'\n", encoding="utf-8")

        rolled = coding_module.code_rollback(cp["checkpoint_id"])
        assert rolled["success"] is True
        assert "old" in f.read_text(encoding="utf-8")

    def test_unknown_checkpoint_returns_error(self, coding_module, monkeypatch, tmp_path: Path):
        registry = tmp_path / "checkpoints" / "registry.json"
        monkeypatch.setattr(coding_module, "CHECKPOINT_REGISTRY", str(registry))
        result = coding_module.code_rollback("does-not-exist")
        assert "error" in result
