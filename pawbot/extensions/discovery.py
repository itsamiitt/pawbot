"""Extension discovery — scan directories for extensions (Phase E2).

Scans three kinds of extension sources:
  1. Bundled skills: ``pawbot/skills/<name>/SKILL.md``
  2. Installed packages: ``~/.pawbot/extensions/<name>/extension.json``
  3. Legacy installed skills: ``~/.pawbot/skills/<name>/skill.json``

Each discovered extension is registered into the ExtensionRegistry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from pawbot.extensions.registry import ExtensionRecord, ExtensionRegistry
from pawbot.extensions.schema import ExtensionManifest, ExtensionOrigin


# ── Default Directories ─────────────────────────────────────────────────────

BUNDLED_SKILLS_DIR = Path(__file__).parent.parent / "skills"
INSTALLED_EXTENSIONS_DIR = Path.home() / ".pawbot" / "extensions"
LEGACY_SKILLS_DIR = Path.home() / ".pawbot" / "skills"


# ── SKILL.md Parsing ────────────────────────────────────────────────────────


def _parse_skill_md(path: Path) -> dict[str, Any] | None:
    """Parse YAML frontmatter from a SKILL.md file.

    Returns dict with keys: name, description, homepage, metadata
    or None if parsing fails.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Cannot read {}: {}", path, e)
        return None

    # Extract YAML frontmatter between --- markers
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return None

    frontmatter = match.group(1)
    result: dict[str, Any] = {}

    for line in frontmatter.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Simple key: value parsing
        colon_pos = line.find(":")
        if colon_pos < 0:
            continue

        key = line[:colon_pos].strip()
        value = line[colon_pos + 1 :].strip()

        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        # Try to parse JSON values (for metadata field)
        if key == "metadata" and value.startswith("{"):
            try:
                result[key] = json.loads(value)
            except json.JSONDecodeError:
                result[key] = value
        else:
            result[key] = value

    return result if "name" in result else None


# ── Discovery Functions ─────────────────────────────────────────────────────


def discover_bundled_skills(
    skills_dir: Path | None = None,
) -> list[tuple[ExtensionManifest, str]]:
    """Discover bundled SKILL.md-based skills.

    Returns list of (manifest, source_path) tuples.
    """
    base = skills_dir or BUNDLED_SKILLS_DIR
    results: list[tuple[ExtensionManifest, str]] = []

    if not base.is_dir():
        return results

    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir():
            continue
        if skill_dir.name.startswith(("_", ".")):
            continue

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        parsed = _parse_skill_md(skill_md)
        if not parsed:
            logger.debug("Skipping {} — could not parse frontmatter", skill_dir.name)
            continue

        try:
            manifest = ExtensionManifest.from_skill_md(
                name=parsed["name"],
                description=parsed.get("description", ""),
                metadata=parsed.get("metadata"),
            )
            results.append((manifest, str(skill_dir)))
        except Exception as e:
            logger.warning("Failed to create manifest for bundled skill '{}': {}", skill_dir.name, e)

    return results


def discover_installed_extensions(
    extensions_dir: Path | None = None,
) -> list[tuple[ExtensionManifest, str]]:
    """Discover installed extensions with extension.json.

    Returns list of (manifest, source_path) tuples.
    """
    base = extensions_dir or INSTALLED_EXTENSIONS_DIR
    results: list[tuple[ExtensionManifest, str]] = []

    if not base.is_dir():
        return results

    for ext_dir in sorted(base.iterdir()):
        if not ext_dir.is_dir():
            continue
        if ext_dir.name.startswith(("_", ".")):
            continue

        manifest_path = ext_dir / "extension.json"
        if not manifest_path.exists():
            continue

        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = ExtensionManifest.model_validate(data)
            results.append((manifest, str(ext_dir)))
        except Exception as e:
            logger.warning("Failed to load extension '{}': {}", ext_dir.name, e)

    return results


def discover_legacy_skills(
    skills_dir: Path | None = None,
) -> list[tuple[ExtensionManifest, str]]:
    """Discover legacy installed skill.json packages.

    Returns list of (manifest, source_path) tuples.
    """
    base = skills_dir or LEGACY_SKILLS_DIR
    results: list[tuple[ExtensionManifest, str]] = []

    if not base.is_dir():
        return results

    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir():
            continue
        if skill_dir.name.startswith(("_", ".")) or skill_dir.name == "installed.json":
            continue

        skill_json = skill_dir / "skill.json"
        if not skill_json.exists():
            continue

        try:
            data = json.loads(skill_json.read_text(encoding="utf-8"))
            manifest = ExtensionManifest.from_skill_json(data)
            results.append((manifest, str(skill_dir)))
        except Exception as e:
            logger.warning(
                "Failed to load legacy skill '{}': {}", skill_dir.name, e
            )

    return results


# ── Full Discovery ──────────────────────────────────────────────────────────


def discover_all(
    registry: ExtensionRegistry,
    bundled_dir: Path | None = None,
    extensions_dir: Path | None = None,
    legacy_dir: Path | None = None,
    extra_dirs: list[Path] | None = None,
) -> int:
    """Discover all extension sources and register into the registry.

    Scans in order:
      1. Bundled skills (SKILL.md)
      2. Installed extensions (extension.json)
      3. Legacy skills (skill.json)
      4. Extra directories (extension.json or skill.json)

    Returns count of extensions registered.
    """
    count = 0

    # 1. Bundled skills
    for manifest, source in discover_bundled_skills(bundled_dir):
        manifest.origin = ExtensionOrigin.BUNDLED
        registry.register(manifest, source=source)
        count += 1

    # 2. Installed extensions
    for manifest, source in discover_installed_extensions(extensions_dir):
        if not registry.get(manifest.id):  # Don't override bundled
            registry.register(manifest, source=source)
            count += 1

    # 3. Legacy skills
    for manifest, source in discover_legacy_skills(legacy_dir):
        if not registry.get(manifest.id):
            registry.register(manifest, source=source)
            count += 1

    # 4. Extra directories
    for extra in extra_dirs or []:
        if not extra.is_dir():
            continue
        for ext_dir in sorted(extra.iterdir()):
            if not ext_dir.is_dir():
                continue

            manifest_path = ext_dir / "extension.json"
            skill_path = ext_dir / "skill.json"

            try:
                if manifest_path.exists():
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    m = ExtensionManifest.model_validate(data)
                elif skill_path.exists():
                    data = json.loads(skill_path.read_text(encoding="utf-8"))
                    m = ExtensionManifest.from_skill_json(data)
                else:
                    continue

                if not registry.get(m.id):
                    m.origin = ExtensionOrigin.WORKSPACE
                    registry.register(m, source=str(ext_dir))
                    count += 1
            except Exception as e:
                logger.warning("Failed to load extension from '{}': {}", ext_dir, e)

    if count:
        enabled = registry.enabled_count
        logger.info(
            "Discovered {} extensions ({} enabled)", count, enabled
        )

    return count
