"""Legacy skill adapter — skill.json → ExtensionManifest (Phase E4).

This module provides backward compatibility for skill.json manifests,
mapping them to the unified ExtensionManifest schema.

Also provides shim classes so that existing code importing from
``pawbot.skills.manifest`` and ``pawbot.skills.loader`` continues
to work during the migration period.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from pawbot.extensions.schema import (
    ExtensionManifest,
    ExtensionOrigin,
)


def translate_skill_json(data: dict[str, Any]) -> ExtensionManifest:
    """Translate a skill.json dict to an ExtensionManifest.

    This is a convenience wrapper around ``ExtensionManifest.from_skill_json``.
    """
    return ExtensionManifest.from_skill_json(data)


def translate_skill_json_file(path: Path) -> ExtensionManifest:
    """Load and translate a skill.json file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest = ExtensionManifest.from_skill_json(data)
    manifest.origin = ExtensionOrigin.LOCAL
    return manifest
