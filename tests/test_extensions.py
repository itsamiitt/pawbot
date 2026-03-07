"""Tests for the unified extension system (Phase E2–E5).

Covers:
  - ExtensionManifest schema and factory methods
  - ExtensionRegistry with policy engine and auto-enable
  - Extension discovery (bundled, installed, legacy)
  - Extension loader (tool import, prompt loading)
  - Lifecycle hooks (dispatch, priority, blocking)
  - Extension installer (scaffold, directory install)
  - OpenClaw adapter
  - CLI commands
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Schema Tests ─────────────────────────────────────────────────────────────


class TestExtensionManifest:
    """Test ExtensionManifest Pydantic model."""

    def test_minimal_manifest(self):
        from pawbot.extensions.schema import ExtensionManifest

        m = ExtensionManifest(id="test")
        assert m.id == "test"
        assert m.name == "test"  # Auto-set from id
        assert m.version == "0.1.0"
        assert m.tools == []
        assert m.origin.value == "local"

    def test_full_manifest(self):
        from pawbot.extensions.schema import (
            ExtensionManifest,
            ExtensionTool,
            RiskLevel,
        )

        m = ExtensionManifest(
            id="shopify",
            name="Shopify Tools",
            version="1.0.0",
            description="Shopify integration",
            tools=[
                ExtensionTool(
                    name="search",
                    description="Search products",
                    function="tools.search",
                    risk_level=RiskLevel.LOW,
                )
            ],
            prompts=["prompts/system.md"],
        )
        assert m.id == "shopify"
        assert len(m.tools) == 1
        assert m.tools[0].name == "search"
        assert m.tools[0].risk_level == RiskLevel.LOW

    def test_from_skill_json(self):
        from pawbot.extensions.schema import ExtensionManifest

        data = {
            "name": "weather-tools",
            "version": "2.0.0",
            "description": "Weather utilities",
            "author": "Test",
            "tools": [
                {
                    "name": "get_temp",
                    "description": "Get temperature",
                    "function": "tools.temp",
                }
            ],
            "python_dependencies": ["requests>=2.28"],
            "requires_network": True,
            "requires_filesystem": False,
        }
        m = ExtensionManifest.from_skill_json(data)
        assert m.id == "weather-tools"
        assert m.version == "2.0.0"
        assert len(m.tools) == 1
        assert "requests>=2.28" in m.dependencies.python
        assert m.permissions.network is True
        assert m.permissions.filesystem is False

    def test_from_skill_md(self):
        from pawbot.extensions.schema import ExtensionManifest

        m = ExtensionManifest.from_skill_md(
            name="weather",
            description="Get weather forecasts",
            metadata={"pawbot": {"emoji": "🌤️", "requires": {"bins": ["curl"]}}},
        )
        assert m.id == "weather"
        assert m.origin.value == "bundled"
        assert "curl" in m.dependencies.system
        assert "prompt-only" in m.capabilities

    def test_from_openclaw_plugin(self):
        from pawbot.extensions.schema import ExtensionManifest

        plugin_data = {
            "id": "slack",
            "channels": ["slack"],
            "configSchema": {"type": "object"},
        }
        pkg_data = {
            "name": "@openclaw/slack",
            "version": "2026.2.26",
            "description": "Slack channel plugin",
        }
        m = ExtensionManifest.from_openclaw_plugin(plugin_data, pkg_data)
        assert m.id == "slack"
        assert "slack" in m.channels
        assert m.origin.value == "openclaw"
        assert m.compatibility.runtime.value == "node"

    def test_legacy_field_migration(self):
        from pawbot.extensions.schema import ExtensionManifest

        m = ExtensionManifest(
            id="test",
            python_dependencies=["aiohttp"],
            requires_network=True,
            requires_browser=True,
            min_pawbot_version="2.0.0",
        )
        assert "aiohttp" in m.dependencies.python
        assert m.permissions.network is True
        assert m.permissions.browser is True
        assert m.compatibility.min_pawbot_version == "2.0.0"

    def test_serialization_roundtrip(self):
        from pawbot.extensions.schema import ExtensionManifest, ExtensionTool

        m = ExtensionManifest(
            id="test",
            tools=[
                ExtensionTool(
                    name="hello",
                    description="Say hello",
                    function="tools.hello",
                )
            ],
        )
        data = json.loads(m.model_dump_json())
        m2 = ExtensionManifest.model_validate(data)
        assert m2.id == m.id
        assert len(m2.tools) == 1


# ── Registry Tests ───────────────────────────────────────────────────────────


class TestExtensionRegistry:
    """Test ExtensionRegistry with policy and auto-enable."""

    def test_register_and_query(self, tmp_path):
        from pawbot.extensions.registry import ExtensionRegistry
        from pawbot.extensions.schema import ExtensionManifest

        registry = ExtensionRegistry(extensions_dir=tmp_path)
        m = ExtensionManifest(id="test-ext", description="Test")
        record = registry.register(m, source="/path/to/test")

        assert record.id == "test-ext"
        assert record.enabled is True
        assert registry.count == 1
        assert registry.enabled_count == 1
        assert registry.get("test-ext") is not None

    def test_enable_disable(self, tmp_path):
        from pawbot.extensions.registry import ExtensionRegistry
        from pawbot.extensions.schema import ExtensionManifest

        registry = ExtensionRegistry(extensions_dir=tmp_path)
        registry.register(ExtensionManifest(id="ext1"))

        assert registry.disable("ext1") is True
        assert registry.get("ext1").enabled is False
        assert registry.enabled_count == 0

        assert registry.enable("ext1") is True
        assert registry.get("ext1").enabled is True

    def test_policy_deny(self, tmp_path):
        from pawbot.extensions.registry import ExtensionRegistry, PolicyConfig
        from pawbot.extensions.schema import ExtensionManifest

        policy = PolicyConfig(deny=["dangerous-*"])
        registry = ExtensionRegistry(extensions_dir=tmp_path, policy=policy)

        record = registry.register(ExtensionManifest(id="dangerous-tool"))
        assert record.enabled is False
        assert record.status == "policy_denied"

    def test_policy_allow(self, tmp_path):
        from pawbot.extensions.registry import ExtensionRegistry, PolicyConfig
        from pawbot.extensions.schema import ExtensionManifest

        policy = PolicyConfig(allow=["safe-*"])
        registry = ExtensionRegistry(extensions_dir=tmp_path, policy=policy)

        safe = registry.register(ExtensionManifest(id="safe-tool"))
        unsafe = registry.register(ExtensionManifest(id="other-tool"))
        assert safe.enabled is True
        assert unsafe.enabled is False

    def test_capability_deny(self, tmp_path):
        from pawbot.extensions.registry import ExtensionRegistry, PolicyConfig
        from pawbot.extensions.schema import ExtensionManifest

        policy = PolicyConfig(capability_deny=["exec"])
        registry = ExtensionRegistry(extensions_dir=tmp_path, policy=policy)

        record = registry.register(
            ExtensionManifest(id="exec-tool", capabilities=["exec", "fs"])
        )
        assert record.enabled is False

    def test_auto_enable_env(self, tmp_path):
        from pawbot.extensions.registry import (
            AutoEnableRule,
            ExtensionRegistry,
        )
        from pawbot.extensions.schema import ExtensionManifest

        rules = [
            AutoEnableRule(
                extension_id="slack",
                condition="env:SLACK_TOKEN",
            )
        ]
        registry = ExtensionRegistry(
            extensions_dir=tmp_path, auto_enable_rules=rules
        )

        with patch.dict(os.environ, {"SLACK_TOKEN": "xoxb-123"}):
            record = registry.register(ExtensionManifest(id="slack"))
            assert record.enabled is True

    def test_persistence(self, tmp_path):
        from pawbot.extensions.registry import ExtensionRegistry
        from pawbot.extensions.schema import ExtensionManifest

        # Create and save
        r1 = ExtensionRegistry(extensions_dir=tmp_path)
        r1.register(ExtensionManifest(id="ext1"))
        r1.disable("ext1")

        # Load fresh
        r2 = ExtensionRegistry(extensions_dir=tmp_path)
        r2.register(ExtensionManifest(id="ext1"))
        assert r2.get("ext1").enabled is False  # Restored from disk

    def test_unregister(self, tmp_path):
        from pawbot.extensions.registry import ExtensionRegistry
        from pawbot.extensions.schema import ExtensionManifest

        registry = ExtensionRegistry(extensions_dir=tmp_path)
        registry.register(ExtensionManifest(id="ext1"))
        assert registry.unregister("ext1") is True
        assert registry.get("ext1") is None

    def test_mark_error(self, tmp_path):
        from pawbot.extensions.registry import ExtensionRegistry
        from pawbot.extensions.schema import ExtensionManifest

        registry = ExtensionRegistry(extensions_dir=tmp_path)
        registry.register(ExtensionManifest(id="ext1"))
        registry.mark_error("ext1", "Missing module")
        rec = registry.get("ext1")
        assert rec.status == "error"
        assert rec.enabled is False
        assert "Missing module" in rec.error


# ── Discovery Tests ──────────────────────────────────────────────────────────


class TestDiscovery:
    """Test extension discovery."""

    def test_discover_bundled_skills(self):
        """Test that bundled SKILL.md files are discovered."""
        from pawbot.extensions.discovery import (
            BUNDLED_SKILLS_DIR,
            discover_bundled_skills,
        )

        if not BUNDLED_SKILLS_DIR.exists():
            pytest.skip("Bundled skills directory not found")

        results = discover_bundled_skills()
        assert len(results) > 0  # At least some bundled skills exist
        # Check that weather skill is discovered
        names = [m.id for m, _ in results]
        assert "weather" in names

    def test_discover_installed_extensions(self, tmp_path):
        from pawbot.extensions.discovery import discover_installed_extensions
        from pawbot.extensions.schema import ExtensionManifest

        # Create a fake installed extension
        ext_dir = tmp_path / "my-ext"
        ext_dir.mkdir()
        manifest = ExtensionManifest(id="my-ext", version="1.0.0")
        (ext_dir / "extension.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )

        results = discover_installed_extensions(tmp_path)
        assert len(results) == 1
        assert results[0][0].id == "my-ext"

    def test_discover_legacy_skills(self, tmp_path):
        from pawbot.extensions.discovery import discover_legacy_skills

        # Create a fake legacy skill
        skill_dir = tmp_path / "old-skill"
        skill_dir.mkdir()
        (skill_dir / "skill.json").write_text(
            json.dumps(
                {
                    "name": "old-skill",
                    "version": "0.5.0",
                    "tools": [],
                }
            ),
            encoding="utf-8",
        )

        results = discover_legacy_skills(tmp_path)
        assert len(results) == 1
        assert results[0][0].id == "old-skill"

    def test_discover_all(self, tmp_path):
        from pawbot.extensions.discovery import discover_all
        from pawbot.extensions.registry import ExtensionRegistry

        # Create some extensions
        ext = tmp_path / "extensions"
        ext.mkdir()
        (ext / "ext1").mkdir()
        (ext / "ext1" / "extension.json").write_text(
            json.dumps({"id": "ext1"}), encoding="utf-8"
        )

        registry = ExtensionRegistry(extensions_dir=tmp_path)
        count = discover_all(
            registry,
            bundled_dir=tmp_path / "nonexistent",
            extensions_dir=ext,
            legacy_dir=tmp_path / "nonexistent2",
        )
        assert count == 1
        assert registry.get("ext1") is not None


# ── Lifecycle Tests ──────────────────────────────────────────────────────────


class TestLifecycle:
    """Test lifecycle hook dispatcher."""

    def test_register_and_dispatch(self):
        from pawbot.extensions.lifecycle import HookName, LifecycleDispatcher

        dispatcher = LifecycleDispatcher()
        calls = []

        dispatcher.register(
            HookName.ON_LOAD,
            "ext1",
            lambda **kw: calls.append(("ext1", kw)),
        )
        dispatcher.dispatch(HookName.ON_LOAD, extension_id="ext1")
        assert len(calls) == 1

    def test_priority_order(self):
        from pawbot.extensions.lifecycle import HookName, LifecycleDispatcher

        dispatcher = LifecycleDispatcher()
        order = []

        dispatcher.register(
            HookName.ON_AGENT_START, "ext1", lambda **kw: order.append("ext1"), priority=200
        )
        dispatcher.register(
            HookName.ON_AGENT_START, "ext2", lambda **kw: order.append("ext2"), priority=50
        )
        dispatcher.register(
            HookName.ON_AGENT_START, "ext3", lambda **kw: order.append("ext3"), priority=100
        )

        dispatcher.dispatch(HookName.ON_AGENT_START)
        assert order == ["ext2", "ext3", "ext1"]

    def test_error_handling(self):
        from pawbot.extensions.lifecycle import HookName, LifecycleDispatcher

        dispatcher = LifecycleDispatcher()

        def bad_handler(**kw):
            raise ValueError("boom")

        dispatcher.register(HookName.ON_LOAD, "ext1", bad_handler)
        # Should not raise
        results = dispatcher.dispatch(HookName.ON_LOAD)
        assert results == []

    def test_unregister(self):
        from pawbot.extensions.lifecycle import HookName, LifecycleDispatcher

        dispatcher = LifecycleDispatcher()
        dispatcher.register(HookName.ON_LOAD, "ext1", lambda **kw: None)
        dispatcher.register(HookName.ON_AGENT_START, "ext1", lambda **kw: None)

        removed = dispatcher.unregister("ext1")
        assert removed == 2
        assert dispatcher.total_hooks == 0

    @pytest.mark.asyncio
    async def test_dispatch_async(self):
        from pawbot.extensions.lifecycle import HookName, LifecycleDispatcher

        dispatcher = LifecycleDispatcher()
        calls = []

        async def async_handler(**kw):
            calls.append("async")

        dispatcher.register(HookName.ON_AGENT_END, "ext1", async_handler)
        await dispatcher.dispatch_async(HookName.ON_AGENT_END)
        assert calls == ["async"]

    @pytest.mark.asyncio
    async def test_before_tool_call_block(self):
        from pawbot.extensions.lifecycle import HookName, LifecycleDispatcher

        dispatcher = LifecycleDispatcher()

        def blocker(**kw):
            return {"block": True, "block_reason": "Not allowed"}

        dispatcher.register(HookName.ON_TOOL_CALL, "guard", blocker)
        params, blocked, reason = await dispatcher.dispatch_before_tool_call(
            "exec", {"command": "rm -rf /"}
        )
        assert blocked is True
        assert "Not allowed" in reason

    def test_hook_count(self):
        from pawbot.extensions.lifecycle import HookName, LifecycleDispatcher

        dispatcher = LifecycleDispatcher()
        assert dispatcher.total_hooks == 0

        dispatcher.register(HookName.ON_LOAD, "a", lambda **kw: None)
        dispatcher.register(HookName.ON_LOAD, "b", lambda **kw: None)
        assert dispatcher.hook_count(HookName.ON_LOAD) == 2
        assert dispatcher.total_hooks == 2


# ── Loader Tests ─────────────────────────────────────────────────────────────


class TestExtensionLoader:
    """Test extension loader."""

    def test_load_prompt_only_extension(self, tmp_path):
        from pawbot.extensions.loader import ExtensionLoader
        from pawbot.extensions.registry import ExtensionRegistry
        from pawbot.extensions.schema import ExtensionManifest

        # Create a SKILL.md-only extension
        ext_dir = tmp_path / "weather"
        ext_dir.mkdir()
        (ext_dir / "SKILL.md").write_text(
            "---\nname: weather\ndescription: Weather tools\n---\n\n# Weather\nUse curl.",
            encoding="utf-8",
        )

        registry = ExtensionRegistry(extensions_dir=tmp_path)
        registry.register(
            ExtensionManifest.from_skill_md("weather", "Weather tools"),
            source=str(ext_dir),
        )

        loader = ExtensionLoader(registry)
        count = loader.load_all()
        assert count == 1

        fragments = loader.get_prompt_fragments()
        assert "Weather" in fragments or "weather" in fragments

    def test_load_python_tool(self, tmp_path):
        from pawbot.extensions.loader import ExtensionLoader
        from pawbot.extensions.registry import ExtensionRegistry
        from pawbot.extensions.schema import ExtensionManifest, ExtensionTool

        # Create extension with Python tool
        ext_dir = tmp_path / "echo"
        ext_dir.mkdir()
        (ext_dir / "tools.py").write_text(
            'def echo(message: str) -> str:\n    return f"echo: {message}"\n',
            encoding="utf-8",
        )
        manifest = ExtensionManifest(
            id="echo",
            tools=[
                ExtensionTool(
                    name="echo",
                    description="Echo a message",
                    function="tools.echo",
                )
            ],
        )
        (ext_dir / "extension.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )

        registry = ExtensionRegistry(extensions_dir=tmp_path)
        registry.register(manifest, source=str(ext_dir))

        loader = ExtensionLoader(registry)
        count = loader.load_all()
        assert count == 1
        assert loader.tool_count == 1

        fn = loader.get_tool("echo.echo")
        assert fn is not None
        result = fn("hello")
        assert result == "echo: hello"

    def test_get_tool_definitions(self, tmp_path):
        from pawbot.extensions.loader import ExtensionLoader
        from pawbot.extensions.registry import ExtensionRegistry
        from pawbot.extensions.schema import ExtensionManifest, ExtensionTool

        ext_dir = tmp_path / "myext"
        ext_dir.mkdir()
        (ext_dir / "tools.py").write_text(
            "def greet():\n    return 'hi'\n", encoding="utf-8"
        )
        manifest = ExtensionManifest(
            id="myext",
            tools=[
                ExtensionTool(
                    name="greet",
                    description="Say greeting",
                    function="tools.greet",
                    parameters={"type": "object", "properties": {}},
                )
            ],
        )
        (ext_dir / "extension.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )

        registry = ExtensionRegistry(extensions_dir=tmp_path)
        registry.register(manifest, source=str(ext_dir))

        loader = ExtensionLoader(registry)
        loader.load_all()
        defs = loader.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "myext.greet"

    def test_unload_extension(self, tmp_path):
        from pawbot.extensions.loader import ExtensionLoader
        from pawbot.extensions.registry import ExtensionRegistry
        from pawbot.extensions.schema import ExtensionManifest, ExtensionTool

        ext_dir = tmp_path / "temp"
        ext_dir.mkdir()
        (ext_dir / "tools.py").write_text(
            "def fn():\n    pass\n", encoding="utf-8"
        )
        manifest = ExtensionManifest(
            id="temp",
            tools=[ExtensionTool(name="fn", function="tools.fn")],
        )
        (ext_dir / "extension.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )

        registry = ExtensionRegistry(extensions_dir=tmp_path)
        registry.register(manifest, source=str(ext_dir))

        loader = ExtensionLoader(registry)
        loader.load_all()
        assert loader.tool_count == 1

        loader.unload("temp")
        assert loader.tool_count == 0


# ── Installer Tests ──────────────────────────────────────────────────────────


class TestExtensionInstaller:
    """Test extension installer."""

    def test_install_from_directory(self, tmp_path):
        from pawbot.extensions.installer import ExtensionInstaller

        source = tmp_path / "source"
        source.mkdir()
        (source / "extension.json").write_text(
            json.dumps({"id": "test-install", "version": "1.0.0"}),
            encoding="utf-8",
        )

        install_dir = tmp_path / "installed"
        installer = ExtensionInstaller(extensions_dir=install_dir)
        manifest = installer.install_from_directory(source)

        assert manifest.id == "test-install"
        assert (install_dir / "test-install" / "extension.json").exists()

    def test_install_from_skill_json(self, tmp_path):
        from pawbot.extensions.installer import ExtensionInstaller

        source = tmp_path / "source"
        source.mkdir()
        (source / "skill.json").write_text(
            json.dumps({"name": "legacy-skill", "version": "0.5.0", "tools": []}),
            encoding="utf-8",
        )

        install_dir = tmp_path / "installed"
        installer = ExtensionInstaller(extensions_dir=install_dir)
        manifest = installer.install_from_directory(source)

        assert manifest.id == "legacy-skill"
        # Should have created extension.json
        assert (install_dir / "legacy-skill" / "extension.json").exists()

    def test_uninstall(self, tmp_path):
        from pawbot.extensions.installer import ExtensionInstaller
        from pawbot.extensions.registry import ExtensionRegistry

        source = tmp_path / "source"
        source.mkdir()
        (source / "extension.json").write_text(
            json.dumps({"id": "removeme"}), encoding="utf-8"
        )

        install_dir = tmp_path / "installed"
        registry = ExtensionRegistry(extensions_dir=install_dir)
        installer = ExtensionInstaller(
            extensions_dir=install_dir, registry=registry
        )

        installer.install_from_directory(source)
        assert installer.is_installed("removeme")

        installer.uninstall("removeme")
        assert not installer.is_installed("removeme")

    def test_create_scaffold(self, tmp_path):
        from pawbot.extensions.installer import ExtensionInstaller

        installer = ExtensionInstaller(extensions_dir=tmp_path)
        dest = installer.create_scaffold("my-new-ext", dest_dir=tmp_path / "my-new-ext")

        assert dest.exists()
        assert (dest / "extension.json").exists()
        assert (dest / "tools" / "example.py").exists()
        assert (dest / "prompts" / "system.md").exists()

        # Verify the manifest is valid
        from pawbot.extensions.schema import ExtensionManifest

        data = json.loads((dest / "extension.json").read_text(encoding="utf-8"))
        m = ExtensionManifest.model_validate(data)
        assert m.id == "my-new-ext"

    def test_smart_install_detect_openclaw(self, tmp_path):
        from pawbot.extensions.installer import ExtensionInstaller

        installer = ExtensionInstaller(extensions_dir=tmp_path)
        # Should detect openclaw: prefix
        with pytest.raises(Exception):
            # Will fail because OpenClaw may not be installed, but
            # should try the openclaw path, not the directory path
            installer.install("openclaw:nonexistent-skill")

    def test_list_installed(self, tmp_path):
        from pawbot.extensions.installer import ExtensionInstaller

        source = tmp_path / "source"
        source.mkdir()
        (source / "extension.json").write_text(
            json.dumps({"id": "listed-ext", "version": "1.2.3"}),
            encoding="utf-8",
        )

        install_dir = tmp_path / "installed"
        installer = ExtensionInstaller(extensions_dir=install_dir)
        installer.install_from_directory(source)

        installed = installer.list_installed()
        assert len(installed) == 1
        assert installed[0]["id"] == "listed-ext"
        assert installed[0]["version"] == "1.2.3"


# ── OpenClaw Adapter Tests ───────────────────────────────────────────────────


class TestOpenClawAdapter:
    """Test OpenClaw adapter."""

    def test_adapter_init(self):
        from pawbot.extensions.adapters.openclaw import OpenClawAdapter

        adapter = OpenClawAdapter()
        # May or may not be available depending on environment
        assert isinstance(adapter.available, bool)

    def test_translate_skill(self, tmp_path):
        """Test translating a SKILL.md skill from a mock OpenClaw dir."""
        from pawbot.extensions.adapters.openclaw import OpenClawAdapter

        # Create mock OpenClaw directory
        oc_dir = tmp_path / "openclaw"
        skills_dir = oc_dir / "skills" / "weather"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: weather\ndescription: Get weather\n---\n\n# Weather\nUse curl.",
            encoding="utf-8",
        )

        adapter = OpenClawAdapter(openclaw_dir=oc_dir)
        assert adapter.available is True

        manifest = adapter.translate("weather")
        assert manifest is not None
        assert manifest.id == "weather"
        assert manifest.origin.value == "openclaw"

    def test_translate_plugin(self, tmp_path):
        """Test translating a plugin from a mock OpenClaw dir."""
        from pawbot.extensions.adapters.openclaw import OpenClawAdapter

        oc_dir = tmp_path / "openclaw"
        ext_dir = oc_dir / "extensions" / "slack"
        ext_dir.mkdir(parents=True)
        (ext_dir / "openclaw.plugin.json").write_text(
            json.dumps({"id": "slack", "channels": ["slack"]}),
            encoding="utf-8",
        )
        (ext_dir / "package.json").write_text(
            json.dumps({"name": "@openclaw/slack", "version": "1.0.0"}),
            encoding="utf-8",
        )

        adapter = OpenClawAdapter(openclaw_dir=oc_dir)
        manifest = adapter.translate("slack")
        assert manifest is not None
        assert manifest.id == "slack"
        assert "slack" in manifest.channels

    def test_list_available(self, tmp_path):
        from pawbot.extensions.adapters.openclaw import OpenClawAdapter

        oc_dir = tmp_path / "openclaw"
        (oc_dir / "skills" / "weather").mkdir(parents=True)
        (oc_dir / "skills" / "weather" / "SKILL.md").write_text("---\nname: weather\n---\n")
        (oc_dir / "extensions" / "slack").mkdir(parents=True)
        (oc_dir / "extensions" / "slack" / "openclaw.plugin.json").write_text("{}")

        adapter = OpenClawAdapter(openclaw_dir=oc_dir)
        assert "weather" in adapter.list_available_skills()
        assert "slack" in adapter.list_available_plugins()

    def test_not_found(self, tmp_path):
        from pawbot.extensions.adapters.openclaw import OpenClawAdapter

        oc_dir = tmp_path / "openclaw"
        (oc_dir / "skills").mkdir(parents=True)
        (oc_dir / "extensions").mkdir(parents=True)

        adapter = OpenClawAdapter(openclaw_dir=oc_dir)
        result = adapter.translate("nonexistent")
        assert result is None


# ── Compat Tests ─────────────────────────────────────────────────────────────


class TestCompat:
    """Test backward compatibility shims."""

    def test_compat_alias(self):
        from pawbot.extensions._compat import SkillManifestCompat
        from pawbot.extensions.schema import ExtensionManifest

        assert SkillManifestCompat is ExtensionManifest

    def test_conversion(self):
        from pawbot.extensions._compat import skill_manifest_to_extension

        m = skill_manifest_to_extension({"name": "old", "version": "1.0"})
        assert m.id == "old"


# ── Integration Test ─────────────────────────────────────────────────────────


class TestIntegration:
    """End-to-end integration test."""

    def test_full_lifecycle(self, tmp_path):
        """Test: discover → register → load → query → unload."""
        from pawbot.extensions.discovery import discover_all
        from pawbot.extensions.lifecycle import LifecycleDispatcher
        from pawbot.extensions.loader import ExtensionLoader
        from pawbot.extensions.registry import ExtensionRegistry
        from pawbot.extensions.schema import ExtensionManifest, ExtensionTool

        # Setup: create an extension directory
        ext_dir = tmp_path / "extensions" / "greeter"
        ext_dir.mkdir(parents=True)
        (ext_dir / "tools.py").write_text(
            'def greet(name: str) -> str:\n    return f"Hello, {name}!"\n',
            encoding="utf-8",
        )
        manifest = ExtensionManifest(
            id="greeter",
            version="1.0.0",
            description="A greeting extension",
            tools=[
                ExtensionTool(
                    name="greet",
                    description="Greet someone",
                    function="tools.greet",
                    parameters={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                )
            ],
        )
        (ext_dir / "extension.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )

        # Discover
        registry = ExtensionRegistry(extensions_dir=tmp_path)
        count = discover_all(
            registry,
            bundled_dir=tmp_path / "nonexistent",
            extensions_dir=tmp_path / "extensions",
            legacy_dir=tmp_path / "nonexistent2",
        )
        assert count == 1
        assert registry.get("greeter") is not None

        # Load
        lifecycle = LifecycleDispatcher()
        hook_log = []

        from pawbot.extensions.lifecycle import HookName

        lifecycle.register(
            HookName.ON_LOAD,
            "observer",
            lambda **kw: hook_log.append(("loaded", kw.get("extension_id"))),
        )

        loader = ExtensionLoader(registry, lifecycle)
        loaded = loader.load_all()
        assert loaded == 1
        assert loader.tool_count == 1

        # Execute tool
        fn = loader.get_tool("greeter.greet")
        assert fn is not None
        assert fn("World") == "Hello, World!"

        # Check definitions
        defs = loader.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "greeter.greet"

        # Unload
        loader.unload("greeter")
        assert loader.tool_count == 0
        assert loader.get_tool("greeter.greet") is None
