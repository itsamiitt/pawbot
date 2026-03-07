"""Lint and correctness regression checks."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _count_class_definitions(source: str, class_name: str) -> int:
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )


def test_matrix_config_defined_once() -> None:
    source = _read("pawbot/config/schema.py")
    assert _count_class_definitions(source, "MatrixConfig") == 1


def test_schema_has_no_duplicate_class_names() -> None:
    source = _read("pawbot/config/schema.py")
    tree = ast.parse(source)
    names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    assert len(names) == len(set(names)), "Duplicate class definitions found in schema.py"


def test_memory_migration_uses_atomic_write() -> None:
    source = _read("pawbot/agent/memory.py")
    assert "atomic_write_text(Path(migrated_flag), \"\")" in source
    assert "open(migrated_flag, \"w\"" not in source


def test_dashboard_has_no_ambiguous_loop_vars() -> None:
    source = _read("pawbot/dashboard/server.py")
    bad_patterns = (" for l in ", " for I in ", " for O in ")
    for line_no, line in enumerate(source.splitlines(), start=1):
        for pattern in bad_patterns:
            assert pattern not in line, (
                f"Ambiguous loop variable in dashboard/server.py:{line_no}: {line}"
            )
