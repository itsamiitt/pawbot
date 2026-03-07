"""Tests for Phase 13 — DevOps Tooling: Completions, Config Backup, Update Checker, Startup Scripts."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pawbot.cli.completions import generate_completion, VALID_SHELLS, EXT_MAP
from pawbot.config.backup import ConfigBackupManager
from pawbot.utils.update_checker import UpdateChecker
from pawbot.scripts.startup import (
    generate_gateway_script,
    generate_node_script,
    generate_agent_script,
    install_scripts,
)


# ── Shell Completions Tests ──────────────────────────────────────────────────


class TestShellCompletions:
    """Test shell completion generation (Phase 13.1)."""

    def test_valid_shells(self):
        assert "bash" in VALID_SHELLS
        assert "zsh" in VALID_SHELLS
        assert "fish" in VALID_SHELLS
        assert "powershell" in VALID_SHELLS
        assert "pwsh" in VALID_SHELLS

    def test_ext_map(self):
        assert EXT_MAP["bash"] == ".bash"
        assert EXT_MAP["zsh"] == ".zsh"
        assert EXT_MAP["fish"] == ".fish"
        assert EXT_MAP["powershell"] == ".ps1"

    def test_bash_completion_generated(self):
        script = generate_completion("bash")
        assert script
        assert "pawbot" in script
        assert "COMPREPLY" in script
        assert "agent" in script
        assert "skills" in script
        assert "completions" in script

    def test_zsh_completion_generated(self):
        script = generate_completion("zsh")
        assert script
        assert "#compdef pawbot" in script
        assert "agent" in script
        assert "_pawbot" in script

    def test_fish_completion_generated(self):
        script = generate_completion("fish")
        assert script
        assert "complete -c pawbot" in script
        assert "agent" in script
        assert "skills" in script

    def test_powershell_completion_generated(self):
        script = generate_completion("powershell")
        assert script
        assert "Register-ArgumentCompleter" in script
        assert "pawbot" in script
        assert "CompletionResult" in script

    def test_pwsh_same_as_powershell(self):
        ps = generate_completion("powershell")
        pwsh = generate_completion("pwsh")
        assert ps == pwsh

    def test_invalid_shell_returns_empty(self):
        assert generate_completion("cmd") == ""
        assert generate_completion("tcsh") == ""

    def test_all_commands_in_bash(self):
        script = generate_completion("bash")
        for cmd in ["agent", "gateway", "dashboard", "channels", "memory", "skills", "cron"]:
            assert cmd in script, f"Missing command: {cmd}"

    def test_skills_subcommands_in_zsh(self):
        script = generate_completion("zsh")
        for subcmd in ["install", "uninstall", "list", "info"]:
            assert subcmd in script


# ── Config Backup Tests ──────────────────────────────────────────────────────


class TestConfigBackupManager:
    """Test config backup system (Phase 13.2)."""

    def test_backup_creates_bak_file(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text('{"key": "value"}')

        mgr = ConfigBackupManager(config)
        result = mgr.backup_before_write()

        assert result is not None
        bak = tmp_path / "config.json.bak"
        assert bak.exists()
        assert bak.read_text() == '{"key": "value"}'

    def test_backup_returns_none_if_no_config(self, tmp_path):
        mgr = ConfigBackupManager(tmp_path / "nonexistent.json")
        assert mgr.backup_before_write() is None

    def test_backup_rotation(self, tmp_path):
        config = tmp_path / "config.json"

        # Create multiple backups by resetting the rate limit
        for i in range(4):
            config.write_text(f'{{"version": {i}}}')
            mgr = ConfigBackupManager(config)
            mgr._last_backup_time = 0  # Reset rate limit
            mgr.backup_before_write()

        # Should have .bak, .bak.1, .bak.2, .bak.3
        assert (tmp_path / "config.json.bak").exists()
        assert (tmp_path / "config.json.bak.1").exists()

    def test_rate_limiting(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text('{"key": "v1"}')

        mgr = ConfigBackupManager(config)
        first = mgr.backup_before_write()
        assert first is not None

        # Second call within MIN_BACKUP_INTERVAL should be skipped
        second = mgr.backup_before_write()
        assert second is None

    def test_restore_from_backup(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text('{"old": true}')

        mgr = ConfigBackupManager(config)
        mgr.backup_before_write()

        # Modify config
        config.write_text('{"new": true}')

        # restore() internally calls backup_before_write() which is
        # rate-limited (too soon after the first call), so .bak still
        # contains the original {"old": true}
        result = mgr.restore(version=0)
        assert result is True
        assert json.loads(config.read_text()) == {"old": True}

    def test_restore_nonexistent_version(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text('{"key": "v"}')
        mgr = ConfigBackupManager(config)
        assert mgr.restore(version=99) is False

    def test_list_backups(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text('{"key": "v"}')

        mgr = ConfigBackupManager(config)
        mgr.backup_before_write()

        backups = mgr.list_backups()
        assert len(backups) >= 1
        assert backups[0]["version"] == 0
        assert "size_bytes" in backups[0]
        assert "age_hours" in backups[0]

    def test_list_backups_empty(self, tmp_path):
        mgr = ConfigBackupManager(tmp_path / "config.json")
        assert mgr.list_backups() == []

    def test_diff(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text('{"a": 1, "b": 2}')

        mgr = ConfigBackupManager(config)
        mgr.backup_before_write()

        # Modify config
        config.write_text('{"a": 1, "b": 99, "c": 3}')

        d = mgr.diff(version=0)
        assert "c" in d["added"]
        assert "b" in d["changed"]
        assert d["removed"] == []

    def test_diff_with_removed(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text('{"a": 1, "b": 2, "c": 3}')

        mgr = ConfigBackupManager(config)
        mgr.backup_before_write()

        config.write_text('{"a": 1}')

        d = mgr.diff(version=0)
        assert "b" in d["removed"]
        assert "c" in d["removed"]

    def test_diff_no_files(self, tmp_path):
        mgr = ConfigBackupManager(tmp_path / "config.json")
        d = mgr.diff(version=0)
        assert "error" in d

    def test_deep_diff_nested(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text('{"outer": {"inner": 1}}')

        mgr = ConfigBackupManager(config)
        mgr.backup_before_write()

        config.write_text('{"outer": {"inner": 2, "new": true}}')

        d = mgr.diff(version=0)
        assert "outer.inner" in d["changed"]
        assert "outer.new" in d["added"]

    def test_max_backups_rotation(self, tmp_path):
        """Verify oldest backup is deleted when exceeding MAX_BACKUPS."""
        config = tmp_path / "config.json"
        mgr = ConfigBackupManager(config)
        mgr.MAX_BACKUPS = 3

        for i in range(5):
            config.write_text(f'{{"v": {i}}}')
            mgr._last_backup_time = 0
            mgr.backup_before_write()

        # Should have at most .bak, .bak.1, .bak.2, .bak.3
        backups = mgr.list_backups()
        assert len(backups) <= mgr.MAX_BACKUPS + 1


# ── Update Checker Tests ─────────────────────────────────────────────────────


class TestUpdateChecker:
    """Test update checker (Phase 13.3)."""

    def test_should_check_first_time(self, tmp_path):
        checker = UpdateChecker(check_file=tmp_path / "update.json")
        assert checker.should_check() is True

    def test_should_not_check_recently(self, tmp_path):
        check_file = tmp_path / "update.json"
        check_file.write_text(json.dumps({
            "last_check": time.time(),
            "latest_version": "1.0.0",
            "current_version": "1.0.0",
        }))
        checker = UpdateChecker(check_file=check_file)
        assert checker.should_check() is False

    def test_should_check_after_interval(self, tmp_path):
        check_file = tmp_path / "update.json"
        check_file.write_text(json.dumps({
            "last_check": time.time() - (25 * 3600),  # 25 hours ago
        }))
        checker = UpdateChecker(check_file=check_file)
        assert checker.should_check() is True

    def test_cached_status(self, tmp_path):
        check_file = tmp_path / "update.json"
        check_file.write_text(json.dumps({
            "last_check": time.time(),
            "latest_version": "2.0.0",
            "current_version": "1.0.0",
            "update_available": True,
        }))
        checker = UpdateChecker(check_file=check_file)
        status = checker.get_cached_status()
        assert status["update_available"] is True
        assert status["latest_version"] == "2.0.0"

    def test_mark_dismissed(self, tmp_path):
        check_file = tmp_path / "update.json"
        check_file.write_text(json.dumps({
            "last_check": time.time(),
            "latest_version": "2.0.0",
            "current_version": "1.0.0",
            "update_available": True,
        }))
        checker = UpdateChecker(check_file=check_file)
        checker.mark_dismissed()

        assert checker.get_cached_status()["dismissed_version"] == "2.0.0"

    def test_is_newer(self):
        assert UpdateChecker._is_newer("2.0.0", "1.0.0") is True
        assert UpdateChecker._is_newer("1.0.0", "1.0.0") is False
        assert UpdateChecker._is_newer("1.0.0", "2.0.0") is False
        assert UpdateChecker._is_newer("", "1.0.0") is False
        assert UpdateChecker._is_newer("1.0.0", "") is False

    def test_get_current_version(self):
        version = UpdateChecker._get_current_version()
        assert version  # Should return something

    def test_corrupt_check_file(self, tmp_path):
        check_file = tmp_path / "update.json"
        check_file.write_text("NOT JSON!")
        checker = UpdateChecker(check_file=check_file)
        assert checker.should_check() is True


# ── Startup Scripts Tests ────────────────────────────────────────────────────


class TestStartupScripts:
    """Test startup script generation (Phase 13.4)."""

    def test_gateway_script_contains_python(self):
        script = generate_gateway_script()
        assert "pawbot" in script
        assert "gateway" in script
        assert "start" in script

    def test_node_script_contains_dashboard(self):
        script = generate_node_script()
        assert "pawbot" in script
        assert "--with-dashboard" in script

    def test_agent_script_contains_chat(self):
        script = generate_agent_script()
        assert "pawbot" in script
        assert "chat" in script

    def test_windows_script_format(self):
        import sys
        if sys.platform == "win32":
            script = generate_gateway_script()
            assert "@echo off" in script
            assert "%*" in script

    def test_install_scripts(self, tmp_path):
        installed = install_scripts(target_dir=tmp_path)
        assert len(installed) == 3

        ext = ".cmd" if __import__("sys").platform == "win32" else ".sh"
        assert (tmp_path / f"gateway{ext}").exists()
        assert (tmp_path / f"node{ext}").exists()
        assert (tmp_path / f"agent{ext}").exists()

    def test_install_scripts_content(self, tmp_path):
        install_scripts(target_dir=tmp_path)
        ext = ".cmd" if __import__("sys").platform == "win32" else ".sh"

        gateway = (tmp_path / f"gateway{ext}").read_text()
        assert "gateway start" in gateway

        node = (tmp_path / f"node{ext}").read_text()
        assert "--with-dashboard" in node

    def test_scripts_created_fresh(self, tmp_path):
        """Installing twice should overwrite without error."""
        install_scripts(target_dir=tmp_path)
        install_scripts(target_dir=tmp_path)  # Should not raise
        ext = ".cmd" if __import__("sys").platform == "win32" else ".sh"
        assert (tmp_path / f"gateway{ext}").exists()
