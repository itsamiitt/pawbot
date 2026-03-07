"""Tests for extracted nginx action helpers in server_control MCP."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_server_module():
    path = Path(__file__).resolve().parents[1] / "mcp-servers" / "server_control" / "server.py"
    spec = importlib.util.spec_from_file_location("server_control_nginx", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def server_module():
    return _load_server_module()


def test_nginx_test_returns_ok_on_success(server_module, monkeypatch) -> None:
    monkeypatch.setattr(
        server_module,
        "_run_command",
        lambda *_a, **_k: {"returncode": 0, "stderr": "syntax is ok"},
    )
    result = server_module._nginx_test()
    assert result["ok"] is True
    assert "syntax is ok" in result["output"]


def test_nginx_reload_aborts_on_failed_test(server_module, monkeypatch) -> None:
    monkeypatch.setattr(server_module, "_nginx_test", lambda: {"ok": False, "output": "config error"})
    result = server_module._nginx_reload()
    assert "error" in result
    assert "config test failed" in result["error"]


def test_nginx_list_vhosts_returns_names(server_module, tmp_path: Path) -> None:
    (tmp_path / "example.com").write_text("server {}", encoding="utf-8")
    (tmp_path / "api.example.com").write_text("server {}", encoding="utf-8")
    result = server_module._nginx_list_vhosts(tmp_path)
    assert "example.com" in result["vhosts"]
    assert "api.example.com" in result["vhosts"]


def test_nginx_list_vhosts_empty_when_dir_missing(server_module) -> None:
    result = server_module._nginx_list_vhosts(Path("/definitely/nonexistent/nginx-sites"))
    assert result == {"vhosts": []}


def test_nginx_add_vhost_requires_domain_and_config(server_module, tmp_path: Path) -> None:
    result = server_module._nginx_add_vhost("", "", tmp_path, tmp_path)
    assert "error" in result


def test_nginx_remove_vhost_requires_domain(server_module, tmp_path: Path) -> None:
    result = server_module._nginx_remove_vhost("", tmp_path, tmp_path)
    assert "error" in result
    assert "domain is required" in result["error"]
