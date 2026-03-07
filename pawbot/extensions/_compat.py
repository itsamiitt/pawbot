"""Backward compatibility shim for ``pawbot.skills`` imports.

During the migration period, code that imports from ``pawbot.skills.manifest``
or ``pawbot.skills.loader`` continues to work by delegating to the new
unified ``pawbot.extensions`` system.

This module does NOT replace the existing files — it's used by new code
that needs to interop with the old system.
"""

from __future__ import annotations

from pawbot.extensions.schema import (
    ExtensionManifest,
    ExtensionTool,
)
from pawbot.extensions.registry import ExtensionRegistry
from pawbot.extensions.loader import ExtensionLoader


# ── Shim: SkillManifest → ExtensionManifest ──────────────────────────────────

# The old SkillManifest is still in pawbot.skills.manifest — that file
# remains unchanged.  This shim allows NEW code to use ExtensionManifest
# transparently where SkillManifest was expected.

SkillManifestCompat = ExtensionManifest  # Type alias for gradual migration
SkillToolCompat = ExtensionTool


def skill_manifest_to_extension(skill_data: dict) -> ExtensionManifest:
    """Convert a skill.json dict to ExtensionManifest."""
    return ExtensionManifest.from_skill_json(skill_data)
