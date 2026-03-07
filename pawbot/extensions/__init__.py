"""Unified extension system for pawbot.

Replaces the dual skill system (SKILL.md + skill.json) with a single
extension concept covering: tools, prompt fragments, channel adapters,
MCP servers, CLI commands, hooks, and services.

Public API:
    ExtensionManifest  — Pydantic model for extension.json
    ExtensionRegistry  — in-memory + persisted registry with allow/deny policy
    ExtensionLoader    — load extensions and register tools/hooks
    ExtensionInstaller — install from dir/git/pip/openclaw URI
    LifecycleDispatcher — dispatch lifecycle hooks
"""

from pawbot.extensions.schema import (
    ExtensionManifest,
    ExtensionTool,
    ExtensionDependencies,
    ExtensionPermissions,
    ExtensionCompatibility,
)
from pawbot.extensions.registry import ExtensionRegistry, ExtensionRecord
from pawbot.extensions.lifecycle import LifecycleDispatcher, HookName

__all__ = [
    "ExtensionManifest",
    "ExtensionTool",
    "ExtensionDependencies",
    "ExtensionPermissions",
    "ExtensionCompatibility",
    "ExtensionRegistry",
    "ExtensionRecord",
    "LifecycleDispatcher",
    "HookName",
]
