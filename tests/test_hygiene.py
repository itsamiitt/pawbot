"""Repository hygiene checks."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_gitignore_exists() -> None:
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.exists(), f".gitignore not found at {gitignore}"


def test_gitignore_covers_pycache() -> None:
    content = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "__pycache__/" in content
    assert "*.py[cod]" in content or "*.pyc" in content


def test_no_pyc_tracked_in_git() -> None:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    tracked = [
        line for line in result.stdout.splitlines()
        if line.endswith(".pyc") or "__pycache__/" in line.replace("\\", "/")
    ]
    assert not tracked, "Tracked bytecode artifacts found:\n" + "\n".join(tracked[:50])
