"""Tests for the Phase 6 Deployment Pipeline MCP server."""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
import shutil
import types
from pathlib import Path

import pytest


def _load_server_module():
    path = Path(__file__).resolve().parents[1] / "mcp-servers" / "deploy" / "server.py"
    spec = importlib.util.spec_from_file_location("deploy_mcp", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def deploy_module():
    return _load_server_module()


class TestDeployMCP:
    def test_server_starts_without_error(self, deploy_module):
        assert deploy_module.mcp is not None
        assert callable(deploy_module.main)

    def test_list_tools_returns_all_tools(self, deploy_module):
        tools = deploy_module.list_tools()
        assert tools["count"] == len(deploy_module.TOOL_NAMES)
        assert set(tools["tools"]) == set(deploy_module.TOOL_NAMES)


class TestDeployApp:
    def test_rollback_snapshot_created(self, deploy_module, monkeypatch, tmp_path: Path):
        app_path = tmp_path / "app"
        app_path.mkdir(parents=True, exist_ok=True)
        rollback_path = tmp_path / ".rollback_demo"
        commands: list[str] = []

        def _fake_run(cmd: str, cwd: str | None = None, timeout: int = 120):
            commands.append(cmd)
            if cmd.startswith("git rev-parse HEAD"):
                return {"ok": True, "stdout": "abc123\n", "stderr": "", "returncode": 0}
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

        monkeypatch.setattr(deploy_module, "_run", _fake_run)
        monkeypatch.setattr(deploy_module, "_rollback_file", lambda app_name: str(rollback_path))
        monkeypatch.setattr(deploy_module, "run_migrations", lambda deploy_path: {"ok": True, "skipped": True})
        monkeypatch.setattr(deploy_module.time, "sleep", lambda _: None)

        result = deploy_module.deploy_app(
            app_name="demo",
            repo_url="https://example.com/repo.git",
            deploy_path=str(app_path),
            use_pm2=False,
        )

        assert result["success"] is True
        assert rollback_path.exists()
        assert rollback_path.read_text(encoding="utf-8").strip() == "abc123"
        assert any(cmd.startswith("git pull origin") for cmd in commands)

    def test_git_clone_if_path_not_exists(self, deploy_module, monkeypatch, tmp_path: Path):
        deploy_path = tmp_path / "missing-app"
        commands: list[str] = []

        def _fake_run(cmd: str, cwd: str | None = None, timeout: int = 120):
            commands.append(cmd)
            if cmd.startswith("git clone"):
                deploy_path.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

        monkeypatch.setattr(deploy_module, "_run", _fake_run)
        monkeypatch.setattr(deploy_module, "run_migrations", lambda deploy_root: {"ok": True, "skipped": True})
        monkeypatch.setattr(deploy_module.time, "sleep", lambda _: None)

        result = deploy_module.deploy_app(
            app_name="demo",
            repo_url="https://example.com/repo.git",
            deploy_path=str(deploy_path),
            use_pm2=False,
        )
        assert result["success"] is True
        assert any(cmd.startswith("git clone https://example.com/repo.git") for cmd in commands)

    def test_nodejs_install_uses_pnpm_when_lockfile_present(
        self, deploy_module, monkeypatch, tmp_path: Path
    ):
        app_path = tmp_path / "node-app"
        app_path.mkdir(parents=True, exist_ok=True)
        (app_path / "package.json").write_text("{}", encoding="utf-8")
        (app_path / "pnpm-lock.yaml").write_text("lockfileVersion: 6", encoding="utf-8")
        commands: list[str] = []

        def _fake_run(cmd: str, cwd: str | None = None, timeout: int = 120):
            commands.append(cmd)
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

        monkeypatch.setattr(deploy_module, "_run", _fake_run)
        monkeypatch.setattr(deploy_module, "run_migrations", lambda deploy_root: {"ok": True, "skipped": True})
        monkeypatch.setattr(deploy_module.time, "sleep", lambda _: None)

        result = deploy_module.deploy_app(app_name="node-app", deploy_path=str(app_path), use_pm2=False)
        assert result["success"] is True
        assert "pnpm install" in commands

    def test_migration_failure_triggers_rollback(self, deploy_module, monkeypatch, tmp_path: Path):
        app_path = tmp_path / "app"
        app_path.mkdir(parents=True, exist_ok=True)
        rollback_called = {"value": False}

        monkeypatch.setattr(
            deploy_module,
            "_run",
            lambda cmd, cwd=None, timeout=120: {"ok": True, "stdout": "", "stderr": "", "returncode": 0},
        )
        monkeypatch.setattr(
            deploy_module,
            "run_migrations",
            lambda deploy_root: {"ok": False, "tool": "alembic", "error": "migration failed"},
        )

        def _fake_rollback(app_name: str, deploy_path: str):
            rollback_called["value"] = True
            return {"success": True}

        monkeypatch.setattr(deploy_module, "deploy_rollback", _fake_rollback)
        monkeypatch.setattr(deploy_module.time, "sleep", lambda _: None)

        result = deploy_module.deploy_app(app_name="demo", deploy_path=str(app_path), use_pm2=False)
        assert result["success"] is False
        assert result["rollback"] == "triggered"
        assert rollback_called["value"] is True

    def test_pm2_reload_if_app_exists(self, deploy_module, monkeypatch, tmp_path: Path):
        app_path = tmp_path / "pm2-app"
        app_path.mkdir(parents=True, exist_ok=True)
        commands: list[str] = []

        def _fake_run(cmd: str, cwd: str | None = None, timeout: int = 120):
            commands.append(cmd)
            if cmd.startswith("pm2 show"):
                return {"ok": True, "stdout": "status: online", "stderr": "", "returncode": 0}
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

        monkeypatch.setattr(deploy_module, "_run", _fake_run)
        monkeypatch.setattr(deploy_module, "run_migrations", lambda deploy_root: {"ok": True, "skipped": True})
        monkeypatch.setattr(deploy_module.time, "sleep", lambda _: None)

        result = deploy_module.deploy_app(app_name="pm2-app", deploy_path=str(app_path), use_pm2=True)
        assert result["success"] is True
        assert any(cmd == "pm2 reload pm2-app" for cmd in commands)

    def test_step_summary_returned(self, deploy_module, monkeypatch, tmp_path: Path):
        app_path = tmp_path / "summary-app"
        app_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            deploy_module,
            "_run",
            lambda cmd, cwd=None, timeout=120: {"ok": True, "stdout": "", "stderr": "", "returncode": 0},
        )
        monkeypatch.setattr(deploy_module, "run_migrations", lambda deploy_root: {"ok": True, "skipped": True})
        monkeypatch.setattr(deploy_module.time, "sleep", lambda _: None)

        result = deploy_module.deploy_app(app_name="summary-app", deploy_path=str(app_path), use_pm2=False)
        assert isinstance(result.get("steps"), list)
        assert result["steps"]
        for step in result["steps"]:
            assert "step" in step
            assert "status" in step
            assert "elapsed_s" in step


class TestDeployDocker:
    def test_build_failure_halts_early(self, deploy_module, monkeypatch):
        calls: list[str] = []

        def _fake_run(cmd: str, cwd: str | None = None, timeout: int = 120):
            calls.append(cmd)
            if cmd.startswith("docker build"):
                return {"ok": False, "stdout": "", "stderr": "build failed", "returncode": 1}
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

        monkeypatch.setattr(deploy_module, "_run", _fake_run)

        result = deploy_module.deploy_docker("demo")
        assert result["success"] is False
        assert len(result["steps"]) == 1
        assert result["steps"][0]["step"] == "docker_build"

    def test_inspect_after_start(self, deploy_module, monkeypatch):
        def _fake_run(cmd: str, cwd: str | None = None, timeout: int = 120):
            if cmd.startswith("docker inspect"):
                payload = [{"State": {"Running": True}, "Id": "1234567890abcdef"}]
                return {"ok": True, "stdout": json.dumps(payload), "stderr": "", "returncode": 0}
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

        monkeypatch.setattr(deploy_module, "_run", _fake_run)
        monkeypatch.setattr(deploy_module.time, "sleep", lambda _: None)

        result = deploy_module.deploy_docker("demo")
        assert result["success"] is True
        assert result["container_id"] == "1234567890ab"


class TestNginxGenerator:
    def test_ssl_redirect_block_present_when_ssl_true(self, deploy_module):
        result = deploy_module.nginx_generate_config("example.com", 3000, ssl=True)
        assert "return 301 https://$host$request_uri;" in result["config"]

    def test_websocket_headers_present(self, deploy_module):
        result = deploy_module.nginx_generate_config("example.com", 3000, websocket=True)
        assert "proxy_set_header Upgrade $http_upgrade;" in result["config"]
        assert 'proxy_set_header Connection "upgrade";' in result["config"]

    def test_rate_limiting_included(self, deploy_module):
        result = deploy_module.nginx_generate_config("example.com", 3000)
        assert "limit_req_zone" in result["config"]
        assert "limit_req zone=" in result["config"]


class TestMigrations:
    def test_prisma_detected_by_schema_file(self, deploy_module, monkeypatch, tmp_path: Path):
        (tmp_path / "prisma").mkdir(parents=True, exist_ok=True)
        (tmp_path / "prisma" / "schema.prisma").write_text("datasource db {}", encoding="utf-8")
        calls: list[str] = []

        def _fake_run(cmd: str, cwd: str | None = None, timeout: int = 120):
            calls.append(cmd)
            return {"ok": True, "stdout": "done", "stderr": "", "returncode": 0}

        monkeypatch.setattr(deploy_module, "_run", _fake_run)
        result = deploy_module.run_migrations(str(tmp_path))
        assert result["ok"] is True
        assert result["tool"] == "prisma"
        assert "npx prisma migrate deploy" in calls

    def test_alembic_detected_by_ini_file(self, deploy_module, monkeypatch, tmp_path: Path):
        (tmp_path / "alembic.ini").write_text("[alembic]", encoding="utf-8")
        calls: list[str] = []

        def _fake_run(cmd: str, cwd: str | None = None, timeout: int = 120):
            calls.append(cmd)
            return {"ok": True, "stdout": "ok", "stderr": "", "returncode": 0}

        monkeypatch.setattr(deploy_module, "_run", _fake_run)
        result = deploy_module.run_migrations(str(tmp_path))
        assert result["ok"] is True
        assert result["tool"] == "alembic"
        assert calls[0] == "alembic upgrade head"

    def test_no_migration_returns_ok_skipped(self, deploy_module, tmp_path: Path):
        result = deploy_module.run_migrations(str(tmp_path))
        assert result["ok"] is True
        assert result["skipped"] is True

    def test_migration_failure_returns_ok_false(self, deploy_module, monkeypatch, tmp_path: Path):
        (tmp_path / "alembic.ini").write_text("[alembic]", encoding="utf-8")
        monkeypatch.setattr(
            deploy_module,
            "_run",
            lambda cmd, cwd=None, timeout=120: {"ok": False, "stdout": "", "stderr": "boom", "returncode": 1},
        )
        result = deploy_module.run_migrations(str(tmp_path))
        assert result["ok"] is False
        assert result["tool"] == "alembic"


class TestDbBackup:
    def test_unknown_db_type_returns_error(self, deploy_module):
        result = deploy_module.db_backup("oracle", "demo")
        assert "error" in result

    def test_sqlite_backup_creates_gz_file(self, deploy_module, monkeypatch, tmp_path: Path):
        src = tmp_path / "demo.sqlite"
        src.write_text("sqlite-bytes", encoding="utf-8")
        backup = tmp_path / "backup.sql"

        def _fake_run(cmd: str, cwd: str | None = None, timeout: int = 120):
            if cmd.startswith("cp "):
                _, from_path, to_path = cmd.split(" ", 2)
                shutil.copy2(from_path, to_path)
                return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}
            if cmd.startswith("gzip -f "):
                file_path = cmd.replace("gzip -f ", "", 1)
                with open(file_path, "rb") as src_f, gzip.open(f"{file_path}.gz", "wb") as gz_f:
                    gz_f.write(src_f.read())
                os.remove(file_path)
                return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

        monkeypatch.setattr(deploy_module, "_run", _fake_run)
        result = deploy_module.db_backup("sqlite", str(src), output_path=str(backup))
        assert result["success"] is True
        assert Path(result["path"]).exists()

    def test_empty_backup_detected(self, deploy_module, monkeypatch, tmp_path: Path):
        src = tmp_path / "demo.sqlite"
        src.write_text("sqlite-bytes", encoding="utf-8")
        backup = tmp_path / "backup.sql"

        def _fake_run(cmd: str, cwd: str | None = None, timeout: int = 120):
            if cmd.startswith("cp "):
                _, _, to_path = cmd.split(" ", 2)
                Path(to_path).write_text("", encoding="utf-8")
                return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}
            if cmd.startswith("gzip -f "):
                file_path = cmd.replace("gzip -f ", "", 1)
                Path(f"{file_path}.gz").write_bytes(b"")
                if Path(file_path).exists():
                    Path(file_path).unlink()
                return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

        monkeypatch.setattr(deploy_module, "_run", _fake_run)
        result = deploy_module.db_backup("sqlite", str(src), output_path=str(backup))
        assert "error" in result
        assert "empty" in result["error"].lower()

    def test_old_backups_cleaned(self, deploy_module, monkeypatch, tmp_path: Path):
        src = tmp_path / "demo.sqlite"
        src.write_text("sqlite-bytes", encoding="utf-8")
        backup = tmp_path / "backup.sql"
        old = tmp_path / "old_backup.sql.gz"
        old.write_bytes(b"old")
        old_mtime = deploy_module.time.time() - (9 * 86400)
        os.utime(old, (old_mtime, old_mtime))

        def _fake_run(cmd: str, cwd: str | None = None, timeout: int = 120):
            if cmd.startswith("cp "):
                _, from_path, to_path = cmd.split(" ", 2)
                shutil.copy2(from_path, to_path)
                return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}
            if cmd.startswith("gzip -f "):
                file_path = cmd.replace("gzip -f ", "", 1)
                with open(file_path, "rb") as src_f, gzip.open(f"{file_path}.gz", "wb") as gz_f:
                    gz_f.write(src_f.read())
                os.remove(file_path)
                return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

        monkeypatch.setattr(deploy_module, "_run", _fake_run)
        result = deploy_module.db_backup("sqlite", str(src), output_path=str(backup))
        assert result["success"] is True
        assert result["cleaned_old_backups"] >= 1
        assert not old.exists()
