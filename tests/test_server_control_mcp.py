"""Tests for the Phase 5 Server Control MCP server."""

from __future__ import annotations

import importlib.util
import subprocess
import types
from pathlib import Path

import pytest


def _load_server_module():
    path = Path(__file__).resolve().parents[1] / "mcp-servers" / "server_control" / "server.py"
    spec = importlib.util.spec_from_file_location("server_control_mcp", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def server_module():
    return _load_server_module()


class _FakeProcess:
    def __init__(self, pid: int, name: str, cpu: float = 1.0, mem: float = 1.0):
        self.info = {
            "pid": pid,
            "name": name,
            "cpu_percent": cpu,
            "memory_percent": mem,
            "status": "running",
            "username": "tester",
            "cmdline": [name, "--flag"],
        }
        self._running = True

    def name(self) -> str:
        return self.info["name"]

    def terminate(self) -> None:
        self._running = False

    def wait(self, timeout: float | None = None) -> None:
        self._running = False

    def kill(self) -> None:
        self._running = False

    def is_running(self) -> bool:
        return self._running


class _FakePsutil:
    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    def __init__(self):
        self._procs = [
            _FakeProcess(101, "python", cpu=12.5, mem=4.2),
            _FakeProcess(102, "nginx", cpu=8.0, mem=1.1),
            _FakeProcess(103, "redis-server", cpu=2.0, mem=0.8),
        ]

    def cpu_percent(self, interval: float = 0.1) -> float:
        return 37.5

    def virtual_memory(self):
        return types.SimpleNamespace(used=3_000_000_000, total=8_000_000_000, percent=37.5)

    def disk_usage(self, path: str):
        return types.SimpleNamespace(used=20_000_000_000, total=100_000_000_000, percent=20.0)

    def getloadavg(self):
        return (0.8, 0.6, 0.4)

    def boot_time(self) -> float:
        return 1_700_000_000.0

    def process_iter(self, attrs):
        return list(self._procs)

    def Process(self, pid: int):
        for proc in self._procs:
            if proc.info["pid"] == pid:
                return proc
        raise self.NoSuchProcess(pid)

    def net_connections(self, kind: str = "inet"):
        return [
            types.SimpleNamespace(
                status="LISTEN",
                laddr=types.SimpleNamespace(ip="127.0.0.1", port=8000),
                type=1,
                pid=101,
            ),
            types.SimpleNamespace(
                status="ESTABLISHED",
                laddr=types.SimpleNamespace(ip="127.0.0.1", port=9000),
                type=1,
                pid=102,
            ),
        ]


class TestServerControlMCP:
    def test_server_starts_without_error(self, server_module):
        assert server_module.mcp is not None
        assert callable(server_module.main)

    def test_list_tools_returns_all_tools(self, server_module):
        tools = server_module.list_tools()
        assert tools["count"] == len(server_module.TOOL_NAMES)
        assert set(tools["tools"]) == set(server_module.TOOL_NAMES)

    def test_each_tool_callable_with_valid_args(self, server_module, monkeypatch, tmp_path: Path):
        fake_psutil = _FakePsutil()
        monkeypatch.setattr(server_module, "psutil", fake_psutil)
        monkeypatch.setattr(server_module, "_is_root_user", lambda: False)

        def _fake_run(*args, **kwargs):
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(server_module.subprocess, "run", _fake_run)
        monkeypatch.setattr(
            server_module.subprocess,
            "Popen",
            lambda *a, **k: types.SimpleNamespace(pid=99999),
        )

        f = tmp_path / "file.txt"
        f.write_text("hello\nworld\n", encoding="utf-8")

        calls = [
            server_module.server_run("echo ok", confirmed=True),
            server_module.server_status(),
            server_module.server_processes("python"),
            server_module.server_kill("does-not-exist"),
            server_module.server_read_file(str(f), lines=1),
            server_module.server_write_file(str(f), "new", mode="append"),
            server_module.server_list_dir(str(tmp_path), depth=1),
            server_module.service_control("nginx", "status"),
            server_module.server_ports(),
            server_module.server_nginx("list_vhosts"),
            server_module.cron_manage("list"),
            server_module.env_manage("list", str(tmp_path / ".env")),
        ]
        assert all(isinstance(item, dict) for item in calls)

    def test_each_tool_handles_invalid_args_gracefully(self, server_module, tmp_path: Path):
        checks = [
            server_module.service_control("nginx", "invalid_action"),
            server_module.server_read_file(str(tmp_path / "missing.txt")),
            server_module.server_write_file("/etc/passwd", "nope"),
            server_module.server_nginx("unknown_action"),
            server_module.cron_manage("add", name="only-name"),
            server_module.env_manage("unknown", str(tmp_path / ".env")),
        ]
        assert all("error" in result for result in checks)

    def test_run_simple_echo_command(self, server_module, monkeypatch):
        monkeypatch.setattr(server_module, "_is_root_user", lambda: False)
        result = server_module.server_run("echo hello", timeout=5)
        assert result.get("returncode") == 0
        assert "hello" in result.get("output", "").lower()

    def test_run_refuses_root_execution(self, server_module, monkeypatch):
        monkeypatch.setattr(server_module, "_is_root_user", lambda: True)
        result = server_module.server_run("echo hello")
        assert "error" in result
        assert "root" in result["error"].lower()

    def test_run_irreversible_requires_confirmed_flag(self, server_module, monkeypatch):
        monkeypatch.setattr(server_module, "_is_root_user", lambda: False)
        result = server_module.server_run("rm -rf /tmp/test")
        assert result.get("error") == "CONFIRMATION_REQUIRED"

    def test_run_background_returns_pid(self, server_module, monkeypatch):
        monkeypatch.setattr(server_module, "_is_root_user", lambda: False)
        monkeypatch.setattr(
            server_module.subprocess,
            "Popen",
            lambda *a, **k: types.SimpleNamespace(pid=12345),
        )
        result = server_module.server_run("echo bg", background=True, confirmed=True)
        assert result["background"] is True
        assert result["pid"] == 12345

    def test_run_timeout_returns_error_not_exception(self, server_module, monkeypatch):
        monkeypatch.setattr(server_module, "_is_root_user", lambda: False)

        def _raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="echo test", timeout=1)

        monkeypatch.setattr(server_module.subprocess, "run", _raise_timeout)
        result = server_module.server_run("echo test", timeout=1)
        assert "error" in result
        assert "timed out" in result["error"].lower()

    def test_status_returns_required_keys(self, server_module, monkeypatch):
        monkeypatch.setattr(server_module, "psutil", _FakePsutil())
        result = server_module.server_status()
        required = {
            "cpu_percent",
            "ram_used_gb",
            "ram_total_gb",
            "ram_percent",
            "disk_used_gb",
            "disk_total_gb",
            "disk_percent",
            "load_avg_1m",
            "load_avg_5m",
            "uptime_seconds",
            "top_processes",
        }
        assert required.issubset(result.keys())

    def test_status_cpu_is_float(self, server_module, monkeypatch):
        monkeypatch.setattr(server_module, "psutil", _FakePsutil())
        result = server_module.server_status()
        assert isinstance(result["cpu_percent"], float)

    def test_processes_returns_list(self, server_module, monkeypatch):
        monkeypatch.setattr(server_module, "psutil", _FakePsutil())
        result = server_module.server_processes()
        assert isinstance(result["processes"], list)

    def test_processes_filter_by_name(self, server_module, monkeypatch):
        monkeypatch.setattr(server_module, "psutil", _FakePsutil())
        result = server_module.server_processes(filter="python")
        assert result["processes"]
        assert all("python" in p["name"].lower() for p in result["processes"])

    def test_kill_by_name_requires_confirmation(self, server_module, monkeypatch):
        monkeypatch.setattr(server_module, "psutil", _FakePsutil())
        result = server_module.server_kill("python", confirmed=False)
        assert result.get("error") == "CONFIRMATION_REQUIRED"
        assert result.get("processes")

    def test_kill_nonexistent_returns_error(self, server_module, monkeypatch):
        monkeypatch.setattr(server_module, "psutil", _FakePsutil())
        result = server_module.server_kill("definitely-not-a-real-process", confirmed=True)
        assert "error" in result

    def test_read_existing_file(self, server_module, tmp_path: Path):
        path = tmp_path / "demo.log"
        path.write_text("line1\nline2\nline3\n", encoding="utf-8")
        result = server_module.server_read_file(str(path), lines=2)
        assert "content" in result
        assert "line2" in result["content"]

    def test_read_nonexistent_returns_error(self, server_module, tmp_path: Path):
        result = server_module.server_read_file(str(tmp_path / "missing.log"))
        assert "error" in result

    def test_write_creates_backup(self, server_module, tmp_path: Path):
        path = tmp_path / "app.conf"
        path.write_text("old-content", encoding="utf-8")
        result = server_module.server_write_file(str(path), "new-content", mode="write")
        assert result["written"] is True
        assert (tmp_path / "app.conf.bak").exists()

    def test_write_refuses_protected_path(self, server_module):
        result = server_module.server_write_file("/etc/passwd", "blocked", mode="write")
        assert "error" in result

    def test_cron_add_list_remove_cycle(self, server_module, monkeypatch, tmp_path: Path):
        registry = tmp_path / "crons.json"
        monkeypatch.setattr(server_module, "CRONS_REGISTRY", str(registry))
        state = {"tab": ""}

        def _fake_run(args, **kwargs):
            if args == ["crontab", "-l"]:
                return types.SimpleNamespace(returncode=0, stdout=state["tab"], stderr="")
            if args == ["crontab", "-"]:
                state["tab"] = kwargs.get("input", "")
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(server_module.subprocess, "run", _fake_run)
        added = server_module.cron_manage(
            action="add",
            name="nightly-backup",
            schedule="0 3 * * *",
            command="/usr/local/bin/backup.sh",
        )
        listed = server_module.cron_manage(action="list")
        removed = server_module.cron_manage(action="remove", name="nightly-backup")

        assert added.get("added") is True
        assert "nightly-backup" in listed["crons"]
        assert removed.get("removed") is True

    def test_env_set_and_get(self, server_module, tmp_path: Path):
        env_path = tmp_path / ".env"
        set_result = server_module.env_manage("set", str(env_path), key="APP_MODE", value="prod")
        get_result = server_module.env_manage("get", str(env_path), key="APP_MODE")
        assert set_result.get("set") is True
        assert get_result.get("value") == "prod"

    def test_env_sensitive_key_masked_in_list(self, server_module, tmp_path: Path):
        env_path = tmp_path / ".env"
        server_module.env_manage("set", str(env_path), key="API_TOKEN", value="secret-value")
        listed = server_module.env_manage("list", str(env_path))
        assert listed["vars"]["API_TOKEN"] == "***"

