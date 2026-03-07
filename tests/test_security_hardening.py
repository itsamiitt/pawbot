"""Security hardening regression checks for subprocess, hash, and timeout fixes."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _assert_no_unsuppressed_shell_true(rel_path: str) -> None:
    source = _read(rel_path)
    offenders: list[str] = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        if "shell=True" in line and "nosec" not in line:
            offenders.append(f"{rel_path}:{line_no}: {line.strip()}")
    assert not offenders, "Unsafe subprocess usage found:\n" + "\n".join(offenders)


def test_deploy_server_no_shell_true() -> None:
    _assert_no_unsuppressed_shell_true("mcp-servers/deploy/server.py")


def test_server_control_no_shell_true() -> None:
    _assert_no_unsuppressed_shell_true("mcp-servers/server_control/server.py")


def test_coding_server_no_shell_true() -> None:
    _assert_no_unsuppressed_shell_true("mcp-servers/coding/server.py")


def test_coding_server_uses_sha256_not_md5() -> None:
    source = _read("mcp-servers/coding/server.py")
    assert "hashlib.sha256" in source
    assert "hashlib.md5" not in source


def test_mcp_tool_has_explicit_timeout() -> None:
    source = _read("pawbot/agent/tools/mcp.py")
    assert "timeout=None" not in source
