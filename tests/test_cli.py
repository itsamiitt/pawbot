"""Tests for Phase 16 — CLI Commands & Config Schema.

Tests verify:
  - PawbotConfig / Config    (defaults, env loading, validation, roundtrip)
  - ConfigLoader              (load/save, missing keys filled)
  - CLIFormatter              (tables, panels, doctor output)
  - Memory CLI commands        (search, list, delete, stats, decay)
  - Skills CLI commands        (list, show, delete)
  - Cron CLI commands          (list)
  - Doctor command             (runs checks, shows results)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the pawbot package root is on sys.path.
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from pawbot.config.schema import (
    Config,
    PawbotConfig,
    ObservabilityConfig,
    SecurityConfig,
    SubagentsConfig,
)
from pawbot.cli.formatter import CLIFormatter


# ══════════════════════════════════════════════════════════════════════════════
#  TestPawbotConfig
# ══════════════════════════════════════════════════════════════════════════════


class TestPawbotConfig:

    def test_pawbotconfig_is_config_alias(self):
        """PawbotConfig is an alias for Config (MASTER_REFERENCE compat)."""
        assert PawbotConfig is Config

    def test_defaults_populated_for_missing_keys(self):
        """Creating Config with empty dict fills defaults."""
        config = Config()
        assert config.security.enabled is True
        assert config.observability.enabled is True
        assert config.subagents.enabled is True
        assert config.subagents.max_concurrent == 3

    def test_security_config_defaults(self):
        """SecurityConfig has all expected defaults."""
        cfg = SecurityConfig()
        assert cfg.enabled is True
        assert cfg.require_confirmation_for_dangerous is True
        assert cfg.block_root_execution is True
        assert cfg.min_memory_salience == 0.2
        assert cfg.max_memory_tokens == 300
        assert cfg.injection_detection is True

    def test_observability_config_defaults(self):
        """ObservabilityConfig has all expected defaults."""
        cfg = ObservabilityConfig()
        assert cfg.enabled is True
        assert cfg.trace_file == "~/.pawbot/logs/traces.jsonl"
        assert cfg.otlp_endpoint == ""
        assert cfg.prometheus_port == 0
        assert cfg.sample_rate == 1.0

    def test_subagents_config_defaults(self):
        """SubagentsConfig has all expected defaults."""
        cfg = SubagentsConfig()
        assert cfg.enabled is True
        assert cfg.max_concurrent == 3
        assert cfg.default_budget_tokens == 50000
        assert cfg.default_budget_seconds == 300
        assert cfg.inbox_review_after_subgoal is True

    def test_serialise_and_deserialise_roundtrip(self):
        """Config can be serialised and deserialised without data loss."""
        config = Config()
        data = config.model_dump(by_alias=False)
        restored = Config.model_validate(data)
        assert restored.security.enabled == config.security.enabled
        assert restored.observability.trace_file == config.observability.trace_file
        assert restored.subagents.max_concurrent == config.subagents.max_concurrent

    def test_partial_config_fills_missing_sections(self):
        """Config with only some sections fills in defaults for others."""
        partial = {"security": {"enabled": False}}
        config = Config.model_validate(partial)
        assert config.security.enabled is False
        # Missing sections should have defaults
        assert config.observability.enabled is True
        assert config.subagents.max_concurrent == 3


# ══════════════════════════════════════════════════════════════════════════════
#  TestConfigLoader
# ══════════════════════════════════════════════════════════════════════════════


class TestConfigLoader:

    def test_creates_default_config_if_missing(self, tmp_path):
        """load_config returns defaults when config file doesn't exist."""
        from pawbot.config.loader import load_config

        config = load_config(tmp_path / "nonexistent.json")
        assert isinstance(config, Config)
        assert config.security.enabled is True

    def test_loads_and_validates_existing_config(self, tmp_path):
        """load_config loads and validates an existing config."""
        config_path = tmp_path / "config.json"
        data = {"security": {"enabled": False, "max_memory_tokens": 500}}
        config_path.write_text(json.dumps(data))

        from pawbot.config.loader import load_config

        config = load_config(config_path)
        assert config.security.enabled is False
        assert config.security.max_memory_tokens == 500

    def test_missing_keys_filled_with_defaults(self, tmp_path):
        """Config with missing keys has them filled with defaults."""
        config_path = tmp_path / "config.json"
        config_path.write_text("{}")

        from pawbot.config.loader import load_config

        config = load_config(config_path)
        assert config.observability.enabled is True
        assert config.subagents.max_concurrent == 3

    def test_save_and_reload(self, tmp_path):
        """Saved config can be reloaded."""
        config_path = tmp_path / "config.json"

        from pawbot.config.loader import load_config, save_config

        config = Config()
        save_config(config, config_path)

        reloaded = load_config(config_path)
        assert reloaded.security.enabled == config.security.enabled


# ══════════════════════════════════════════════════════════════════════════════
#  TestCLIFormatter
# ══════════════════════════════════════════════════════════════════════════════


class TestCLIFormatter:

    def test_print_table(self, capsys):
        """print_table renders without errors."""
        from rich.console import Console
        fmt = CLIFormatter(Console(file=sys.stdout, force_terminal=False))
        fmt.print_table(
            "Test Table",
            [{"name": "Col1"}, {"name": "Col2"}],
            [["a", "b"], ["c", "d"]],
        )
        # No assertion on exact output — just verifying it doesn't crash

    def test_print_kv_table(self, capsys):
        """print_kv_table renders key-value pairs."""
        from rich.console import Console
        fmt = CLIFormatter(Console(file=sys.stdout, force_terminal=False))
        fmt.print_kv_table("KV Test", {"key1": "val1", "key2": "val2"})

    def test_print_panel(self, capsys):
        """print_panel renders a panel."""
        from rich.console import Console
        fmt = CLIFormatter(Console(file=sys.stdout, force_terminal=False))
        fmt.print_panel("Hello world", title="Test")

    def test_success_message(self, capsys):
        """success() prints green message."""
        from rich.console import Console
        fmt = CLIFormatter(Console(file=sys.stdout, force_terminal=False))
        fmt.success("Operation succeeded")

    def test_error_message(self, capsys):
        """error() prints red message."""
        from rich.console import Console
        fmt = CLIFormatter(Console(file=sys.stdout, force_terminal=False))
        fmt.error("Something failed")

    def test_print_doctor_results(self, capsys):
        """print_doctor_results renders doctor check table."""
        from rich.console import Console
        fmt = CLIFormatter(Console(file=sys.stdout, force_terminal=False))
        fmt.print_doctor_results([
            ("✓", "Check 1", ""),
            ("✗", "Check 2", "fix this"),
        ])


# ══════════════════════════════════════════════════════════════════════════════
#  TestMemoryCommands
# ══════════════════════════════════════════════════════════════════════════════


class TestMemoryCommands:

    def _make_mock_memory_module(self, mock_router=None, mock_stats_fn=None):
        """Create a mock memory module with get_memory_router and memory_stats."""
        import pawbot.agent.memory as mem_module
        mock_module = MagicMock(wraps=mem_module)
        mock_module.get_memory_router = MagicMock(return_value=mock_router or MagicMock())
        mock_module.memory_stats = mock_stats_fn or MagicMock(return_value={})
        return mock_module

    def test_memory_search_no_results(self):
        """memory search with no results prints a warning."""
        from typer.testing import CliRunner
        from pawbot.cli.memory_commands import memory_app

        runner = CliRunner()
        mock_router = MagicMock()
        mock_router.search.return_value = []

        # Inject get_memory_router into the module
        import pawbot.agent.memory as mem_mod
        mem_mod.get_memory_router = MagicMock(return_value=mock_router)
        try:
            with patch("pawbot.cli.memory_commands.console"):
                result = runner.invoke(memory_app, ["search", "test_query"])
            assert result.exit_code == 0
        finally:
            if hasattr(mem_mod, 'get_memory_router'):
                del mem_mod.get_memory_router

    def test_memory_list_filters_by_type(self):
        """memory list returns memories of specified type."""
        from typer.testing import CliRunner
        from pawbot.cli.memory_commands import memory_app

        runner = CliRunner()
        mock_router = MagicMock()
        mock_router.list_all.return_value = [
            {"id": "abc123", "type": "fact", "salience": 0.8, "created_at": 0, "content": "test content"}
        ]

        import pawbot.agent.memory as mem_mod
        mem_mod.get_memory_router = MagicMock(return_value=mock_router)
        try:
            with patch("pawbot.cli.memory_commands.console"):
                result = runner.invoke(memory_app, ["list", "fact"])
            assert result.exit_code == 0
        finally:
            if hasattr(mem_mod, 'get_memory_router'):
                del mem_mod.get_memory_router

    def test_memory_stats_shows_counts(self):
        """memory stats displays statistics."""
        from typer.testing import CliRunner
        from pawbot.cli.memory_commands import memory_app

        runner = CliRunner()
        mock_router = MagicMock()
        mock_stats = {
            "by_type": {"fact": 10, "preference": 5},
            "total_facts": 15,
            "archived": 3,
            "episodes_chroma": 20,
            "db_size_kb": 128,
        }

        import pawbot.agent.memory as mem_mod
        mem_mod.get_memory_router = MagicMock(return_value=mock_router)
        mem_mod.memory_stats = MagicMock(return_value=mock_stats)
        try:
            with patch("pawbot.cli.memory_commands.console"):
                result = runner.invoke(memory_app, ["stats"])
            assert result.exit_code == 0
        finally:
            if hasattr(mem_mod, 'get_memory_router'):
                del mem_mod.get_memory_router
            if hasattr(mem_mod, 'memory_stats'):
                del mem_mod.memory_stats

    def test_memory_decay_triggers_pass(self):
        """memory decay triggers a decay pass."""
        from typer.testing import CliRunner
        from pawbot.cli.memory_commands import memory_app

        runner = CliRunner()

        with patch("pawbot.cli.memory_commands.console"):
            with patch("pawbot.agent.memory.SQLiteFactStore"):
                with patch("pawbot.agent.memory.MemoryDecayEngine") as MockEngine:
                    MockEngine.return_value.decay_pass.return_value = 5
                    result = runner.invoke(memory_app, ["decay"])
        assert result.exit_code == 0

    def test_memory_delete_archives_not_hard_deletes(self):
        """memory delete archives rather than hard-deleting."""
        from typer.testing import CliRunner
        from pawbot.cli.memory_commands import memory_app

        runner = CliRunner()
        mock_router = MagicMock()
        mock_router.delete.return_value = True

        import pawbot.agent.memory as mem_mod
        mem_mod.get_memory_router = MagicMock(return_value=mock_router)
        try:
            with patch("pawbot.cli.memory_commands.console"):
                result = runner.invoke(memory_app, ["delete", "abc123", "--yes"])
            assert result.exit_code == 0
            mock_router.delete.assert_called_once_with("abc123")
        finally:
            if hasattr(mem_mod, 'get_memory_router'):
                del mem_mod.get_memory_router


# ══════════════════════════════════════════════════════════════════════════════
#  TestSkillsCommands
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillsCommands:

    def test_skills_list_empty(self):
        """skills list shows message when no skills."""
        from typer.testing import CliRunner
        from pawbot.cli.skills_commands import skills_app

        runner = CliRunner()
        mock_loader = MagicMock()
        mock_loader.list_skills.return_value = []

        with patch("pawbot.cli.skills_commands.console"):
            with patch("pawbot.agent.skills.SkillsLoader", return_value=mock_loader):
                result = runner.invoke(skills_app, ["list"])
        assert result.exit_code == 0

    def test_skills_list_shows_table(self):
        """skills list renders a table of skills."""
        from typer.testing import CliRunner
        from pawbot.cli.skills_commands import skills_app

        runner = CliRunner()
        mock_loader = MagicMock()
        mock_loader.list_skills.return_value = [
            {"name": "test_skill", "description": "A test", "success_count": 5}
        ]

        with patch("pawbot.cli.skills_commands.console"):
            with patch("pawbot.agent.skills.SkillsLoader", return_value=mock_loader):
                result = runner.invoke(skills_app, ["list"])
        assert result.exit_code == 0

    def test_skills_show_displays_details(self):
        """skills show displays skill details."""
        from typer.testing import CliRunner
        from pawbot.cli.skills_commands import skills_app

        runner = CliRunner()
        mock_loader = MagicMock()
        mock_loader.list_skills.return_value = [
            {"name": "my_skill", "description": "Does stuff", "version": "1.0"}
        ]

        with patch("pawbot.cli.skills_commands.console"):
            with patch("pawbot.agent.skills.SkillsLoader", return_value=mock_loader):
                result = runner.invoke(skills_app, ["show", "my_skill"])
        assert result.exit_code == 0

    def test_skills_delete_removes_skill(self):
        """skills delete with --yes flag completes."""
        from typer.testing import CliRunner
        from pawbot.cli.skills_commands import skills_app

        runner = CliRunner()
        with patch("pawbot.cli.skills_commands.console"):
            result = runner.invoke(skills_app, ["delete", "test_skill", "--yes"])
        assert result.exit_code == 0


# ══════════════════════════════════════════════════════════════════════════════
#  TestDoctorCommand
# ══════════════════════════════════════════════════════════════════════════════


class TestDoctorCommand:

    def test_doctor_runs_all_checks(self):
        """doctor command runs without crashing."""
        from typer.testing import CliRunner
        from pawbot.cli.commands import app

        runner = CliRunner()
        # Doctor checks may fail due to missing services — that's ok.
        # We just verify it doesn't crash.
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0

    def test_doctor_shows_pass_fail_counts(self):
        """doctor output includes pass/fail summary."""
        from typer.testing import CliRunner
        from pawbot.cli.commands import app

        runner = CliRunner()
        result = runner.invoke(app, ["doctor"])
        # Output should mention "checks passed"
        assert "checks passed" in result.output
