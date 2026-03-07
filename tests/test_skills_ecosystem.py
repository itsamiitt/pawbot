"""Tests for Phase 9 — Plugin & Skill Ecosystem."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pawbot.skills.manifest import SkillManifest, SkillTool
from pawbot.skills.installer import SkillInstaller
from pawbot.skills.loader import SkillRuntime
from pawbot.config.schema import AgentToolsConfig, AgentsConfig, Config


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_manifest_dict():
    """A valid skill.json as a dict."""
    return {
        "name": "test-skill",
        "version": "1.0.0",
        "description": "A test skill for unit tests",
        "author": "Test Author",
        "tools": [
            {
                "name": "greet",
                "description": "Say hello",
                "function": "tools.greet",
                "risk_level": "low",
            },
            {
                "name": "calculate",
                "description": "Do math",
                "function": "tools.calculate",
                "risk_level": "caution",
                "timeout": 30,
            },
        ],
        "prompts": ["prompts/default.md"],
        "requires_api_key": False,
    }


@pytest.fixture
def skill_package_dir(tmp_path, sample_manifest_dict):
    """Create a temporary skill package directory."""
    skill_dir = tmp_path / "test-skill-source"
    skill_dir.mkdir()

    # Write skill.json
    (skill_dir / "skill.json").write_text(
        json.dumps(sample_manifest_dict), encoding="utf-8"
    )

    # Write tools.py
    (skill_dir / "tools.py").write_text(
        'def greet(name="World"):\n    return f"Hello, {name}!"\n\n'
        'def calculate(a, b, op="+"):\n    return eval(f"{a}{op}{b}")\n',
        encoding="utf-8",
    )

    # Write prompts
    prompts_dir = skill_dir / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "default.md").write_text(
        "You have access to greeting and calculation tools.",
        encoding="utf-8",
    )

    return skill_dir


@pytest.fixture
def skills_dir(tmp_path):
    """Isolated skills directory for installers."""
    d = tmp_path / "skills"
    d.mkdir()
    return d


# ── SkillManifest Tests ──────────────────────────────────────────────────────


class TestSkillManifest:
    """Test skill manifest schema (Phase 9.1)."""

    def test_minimal_manifest(self):
        m = SkillManifest(name="hello")
        assert m.name == "hello"
        assert m.version == "0.1.0"
        assert m.tools == []
        assert m.requires_api_key is False

    def test_full_manifest(self, sample_manifest_dict):
        m = SkillManifest(**sample_manifest_dict)
        assert m.name == "test-skill"
        assert m.version == "1.0.0"
        assert len(m.tools) == 2
        assert m.tools[0].name == "greet"
        assert m.tools[1].risk_level == "caution"
        assert m.tools[1].timeout == 30

    def test_manifest_from_json(self, sample_manifest_dict):
        j = json.dumps(sample_manifest_dict)
        m = SkillManifest.model_validate_json(j)
        assert m.name == "test-skill"
        assert len(m.tools) == 2

    def test_skill_tool_defaults(self):
        t = SkillTool(name="test")
        assert t.risk_level == "low"
        assert t.timeout == 60
        assert t.function == ""

    def test_manifest_permissions(self):
        m = SkillManifest(
            name="net-skill",
            requires_network=True,
            requires_filesystem=True,
            requires_browser=True,
        )
        assert m.requires_network is True
        assert m.requires_filesystem is True
        assert m.requires_browser is True

    def test_manifest_api_key(self):
        m = SkillManifest(
            name="api-skill",
            requires_api_key=True,
            api_key_env_var="MY_API_KEY",
        )
        assert m.requires_api_key is True
        assert m.api_key_env_var == "MY_API_KEY"


# ── SkillInstaller Tests ─────────────────────────────────────────────────────


class TestSkillInstaller:
    """Test skill installer (Phase 9.2)."""

    def test_install_from_directory(self, skill_package_dir, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        manifest = installer.install_from_directory(skill_package_dir)

        assert manifest.name == "test-skill"
        assert manifest.version == "1.0.0"
        assert len(manifest.tools) == 2

        # Check files were copied
        installed_dir = skills_dir / "test-skill"
        assert installed_dir.exists()
        assert (installed_dir / "skill.json").exists()
        assert (installed_dir / "tools.py").exists()

    def test_registry_updated_after_install(self, skill_package_dir, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        installer.install_from_directory(skill_package_dir)

        registry_file = skills_dir / "installed.json"
        assert registry_file.exists()

        registry = json.loads(registry_file.read_text())
        assert "test-skill" in registry["skills"]
        assert registry["skills"]["test-skill"]["version"] == "1.0.0"
        assert "greet" in registry["skills"]["test-skill"]["tools"]

    def test_upgrade_replaces_old(self, skill_package_dir, skills_dir, sample_manifest_dict):
        installer = SkillInstaller(skills_dir=skills_dir)

        # Install v1.0.0
        installer.install_from_directory(skill_package_dir)

        # Bump version
        sample_manifest_dict["version"] = "2.0.0"
        (skill_package_dir / "skill.json").write_text(
            json.dumps(sample_manifest_dict), encoding="utf-8"
        )

        # Re-install (upgrade)
        manifest = installer.install_from_directory(skill_package_dir)
        assert manifest.version == "2.0.0"

        registry = json.loads((skills_dir / "installed.json").read_text())
        assert registry["skills"]["test-skill"]["version"] == "2.0.0"

    def test_uninstall(self, skill_package_dir, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        installer.install_from_directory(skill_package_dir)

        assert installer.uninstall("test-skill") is True
        assert not (skills_dir / "test-skill").exists()

        registry = json.loads((skills_dir / "installed.json").read_text())
        assert "test-skill" not in registry["skills"]

    def test_uninstall_nonexistent(self, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        assert installer.uninstall("not-real") is False

    def test_list_installed(self, skill_package_dir, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        installer.install_from_directory(skill_package_dir)

        installed = installer.list_installed()
        assert len(installed) == 1
        assert installed[0]["name"] == "test-skill"
        assert installed[0]["version"] == "1.0.0"
        assert "greet" in installed[0]["tools"]

    def test_list_installed_empty(self, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        assert installer.list_installed() == []

    def test_is_installed(self, skill_package_dir, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        assert installer.is_installed("test-skill") is False
        installer.install_from_directory(skill_package_dir)
        assert installer.is_installed("test-skill") is True

    def test_get_manifest(self, skill_package_dir, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        installer.install_from_directory(skill_package_dir)

        m = installer.get_manifest("test-skill")
        assert m is not None
        assert m.name == "test-skill"

    def test_get_manifest_not_installed(self, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        assert installer.get_manifest("nope") is None

    def test_no_skill_json_raises(self, tmp_path, skills_dir):
        empty_dir = tmp_path / "empty-skill"
        empty_dir.mkdir()

        installer = SkillInstaller(skills_dir=skills_dir)
        with pytest.raises(FileNotFoundError, match="No skill.json"):
            installer.install_from_directory(empty_dir)

    def test_install_with_python_deps(self, skill_package_dir, skills_dir, sample_manifest_dict):
        sample_manifest_dict["python_dependencies"] = ["requests>=2.0.0"]
        (skill_package_dir / "skill.json").write_text(
            json.dumps(sample_manifest_dict), encoding="utf-8"
        )

        installer = SkillInstaller(skills_dir=skills_dir)
        with patch("pawbot.skills.installer.subprocess.check_call") as mock_pip:
            manifest = installer.install_from_directory(skill_package_dir)
            # Verify pip install was called
            mock_pip.assert_called_once()
            assert "requests>=2.0.0" in mock_pip.call_args[0][0]


# ── SkillRuntime / Loader Tests ──────────────────────────────────────────────


class TestSkillRuntime:
    """Test skill runtime loader (Phase 9.3)."""

    def test_load_all_empty(self, tmp_path):
        runtime = SkillRuntime(skills_dir=tmp_path)
        count = runtime.load_all()
        assert count == 0
        assert runtime.tool_count == 0

    def test_load_all_nonexistent_dir(self):
        runtime = SkillRuntime(skills_dir=Path("/nonexistent/path"))
        assert runtime.load_all() == 0

    def test_load_skill_with_tools(self, skill_package_dir, skills_dir):
        # First install the package
        installer = SkillInstaller(skills_dir=skills_dir)
        installer.install_from_directory(skill_package_dir)

        # Then load
        runtime = SkillRuntime(skills_dir=skills_dir)
        count = runtime.load_all()
        assert count == 1
        assert runtime.tool_count == 2

    def test_loaded_skills_dict(self, skill_package_dir, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        installer.install_from_directory(skill_package_dir)

        runtime = SkillRuntime(skills_dir=skills_dir)
        runtime.load_all()

        skills = runtime.loaded_skills
        assert "test-skill" in skills
        assert skills["test-skill"].version == "1.0.0"

    def test_get_tool(self, skill_package_dir, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        installer.install_from_directory(skill_package_dir)

        runtime = SkillRuntime(skills_dir=skills_dir)
        runtime.load_all()

        greet = runtime.get_tool("test-skill.greet")
        assert greet is not None
        assert callable(greet)
        assert greet(name="PawBot") == "Hello, PawBot!"

    def test_get_tool_not_found(self, skills_dir):
        runtime = SkillRuntime(skills_dir=skills_dir)
        assert runtime.get_tool("nonexistent.tool") is None

    def test_get_tool_definitions(self, skill_package_dir, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        installer.install_from_directory(skill_package_dir)

        runtime = SkillRuntime(skills_dir=skills_dir)
        runtime.load_all()

        defs = runtime.get_tool_definitions()
        assert len(defs) == 2
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "test-skill.greet"
        assert "[test-skill]" in defs[0]["function"]["description"]

    def test_get_prompt_fragments(self, skill_package_dir, skills_dir):
        installer = SkillInstaller(skills_dir=skills_dir)
        installer.install_from_directory(skill_package_dir)

        runtime = SkillRuntime(skills_dir=skills_dir)
        runtime.load_all()

        fragment = runtime.get_prompt_fragments()
        assert "test-skill" in fragment
        assert "greeting" in fragment.lower()

    def test_api_key_required_but_missing(self, tmp_path, skills_dir):
        """Skill requiring API key without one configured should be skipped."""
        skill_dir = tmp_path / "api-skill-src"
        skill_dir.mkdir()
        (skill_dir / "skill.json").write_text(json.dumps({
            "name": "api-skill",
            "version": "1.0.0",
            "requires_api_key": True,
            "api_key_env_var": "NONEXISTENT_KEY_123456",
            "tools": [{"name": "do_thing", "function": "tools.do", "description": "x"}],
        }))
        (skill_dir / "tools.py").write_text("def do(): return 'ok'\n")

        installer = SkillInstaller(skills_dir=skills_dir)
        installer.install_from_directory(skill_dir)

        runtime = SkillRuntime(skills_dir=skills_dir)
        count = runtime.load_all()
        # Should still "count" as loaded dir but skip due to API key
        assert runtime.tool_count == 0

    def test_get_api_key(self, skill_package_dir, skills_dir):
        runtime = SkillRuntime(skills_dir=skills_dir)
        assert runtime.get_api_key("test-skill") is None


# ── Config Schema Tests ──────────────────────────────────────────────────────


class TestAgentToolsConfig:
    """Test per-agent tool allow/deny lists (Phase 9.4)."""

    def test_defaults(self):
        cfg = AgentToolsConfig()
        assert cfg.allow == []
        assert cfg.deny == []
        assert cfg.max_calls_per_session == 200

    def test_allow_list(self):
        cfg = AgentToolsConfig(allow=["browse", "browser_*"])
        assert len(cfg.allow) == 2
        assert "browse" in cfg.allow

    def test_deny_list(self):
        cfg = AgentToolsConfig(deny=["exec", "browser_eval"])
        assert len(cfg.deny) == 2

    def test_agents_config_has_tools(self):
        cfg = AgentsConfig()
        assert hasattr(cfg, "tools")
        assert isinstance(cfg.tools, AgentToolsConfig)

    def test_root_config_has_agent_tools(self):
        cfg = Config()
        assert hasattr(cfg.agents, "tools")
        assert isinstance(cfg.agents.tools, AgentToolsConfig)


class TestToolFiltering:
    """Test glob-based tool filtering logic."""

    def _make_tool_def(self, name: str) -> dict:
        return {"type": "function", "function": {"name": name, "description": name}}

    def test_no_filter_returns_all(self):
        """With empty allow/deny, all tools pass through."""
        import fnmatch

        tools = [self._make_tool_def(n) for n in ["read_file", "exec", "browse"]]
        allow = []
        deny = []

        if not allow and not deny:
            result = tools
        else:
            result = []
        assert len(result) == 3

    def test_deny_blocks_tool(self):
        import fnmatch

        tools = [self._make_tool_def(n) for n in ["read_file", "exec", "browse"]]
        deny = ["exec"]

        filtered = []
        for tool in tools:
            name = tool["function"]["name"]
            if any(fnmatch.fnmatch(name, p) for p in deny):
                continue
            filtered.append(tool)

        assert len(filtered) == 2
        names = [t["function"]["name"] for t in filtered]
        assert "exec" not in names

    def test_allow_restricts_tools(self):
        import fnmatch

        tools = [self._make_tool_def(n) for n in ["read_file", "exec", "browse", "browser_click"]]
        allow = ["browser_*", "browse"]

        filtered = []
        for tool in tools:
            name = tool["function"]["name"]
            if not any(fnmatch.fnmatch(name, p) for p in allow):
                continue
            filtered.append(tool)

        assert len(filtered) == 2
        names = [t["function"]["name"] for t in filtered]
        assert "browse" in names
        assert "browser_click" in names

    def test_deny_overrides_allow(self):
        import fnmatch

        tools = [self._make_tool_def(n) for n in ["browse", "browser_click", "browser_eval"]]
        allow = ["browser_*", "browse"]
        deny = ["browser_eval"]

        filtered = []
        for tool in tools:
            name = tool["function"]["name"]
            if any(fnmatch.fnmatch(name, p) for p in deny):
                continue
            if allow and not any(fnmatch.fnmatch(name, p) for p in allow):
                continue
            filtered.append(tool)

        assert len(filtered) == 2
        names = [t["function"]["name"] for t in filtered]
        assert "browser_eval" not in names

    def test_wildcard_patterns(self):
        import fnmatch

        tools = [self._make_tool_def(n) for n in [
            "shopify.search", "shopify.update", "github.pr", "read_file"
        ]]
        allow = ["shopify.*"]

        filtered = [
            t for t in tools
            if any(fnmatch.fnmatch(t["function"]["name"], p) for p in allow)
        ]

        assert len(filtered) == 2
        names = [t["function"]["name"] for t in filtered]
        assert all(n.startswith("shopify.") for n in names)
