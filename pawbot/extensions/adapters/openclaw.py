"""OpenClaw adapter — translate OpenClaw extensions and skills for pawbot (Phase E4).

Handles two OpenClaw extension types:
  1. SKILL.md-based skills — format identical to pawbot, zero-copy adaptation
  2. Plugin extensions — reads ``openclaw.plugin.json`` + ``package.json``,
     translates to ``ExtensionManifest``, and can wrap Node.js tool entrypoints
     as subprocess calls.

Node.js does NOT need to be installed for SKILL.md skills.
Node.js IS needed to run plugin tools (detected at runtime).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from pawbot.extensions.schema import (
    ExtensionCompatibility,
    ExtensionDependencies,
    ExtensionManifest,
    ExtensionOrigin,
    ExtensionTool,
    ToolRuntime,
)


# ── Default OpenClaw locations (npm global install) ─────────────────────────

def _find_openclaw_dir() -> Path | None:
    """Find the OpenClaw installation directory."""
    candidates = []

    # npm global
    npm_global = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "openclaw"
    if npm_global.is_dir():
        candidates.append(npm_global)

    # Linux/macOS npm global
    for prefix in ("/usr/local/lib", "/usr/lib"):
        p = Path(prefix) / "node_modules" / "openclaw"
        if p.is_dir():
            candidates.append(p)

    # OPENCLAW_DIR env override
    env_dir = os.environ.get("OPENCLAW_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.is_dir():
            candidates.insert(0, p)

    return candidates[0] if candidates else None


class OpenClawAdapter:
    """Adapter for translating OpenClaw extensions to pawbot format.

    Usage:
        adapter = OpenClawAdapter()
        manifest = adapter.translate("weather")  # SKILL.md skill
        manifest = adapter.translate("slack")     # Plugin extension
    """

    def __init__(self, openclaw_dir: Path | None = None):
        self._openclaw_dir = openclaw_dir or _find_openclaw_dir()
        self._translated_dir = Path(tempfile.mkdtemp(prefix="pawbot_oc_"))

    @property
    def openclaw_dir(self) -> Path | None:
        """The OpenClaw installation directory, or None if not found."""
        return self._openclaw_dir

    @property
    def available(self) -> bool:
        """Whether OpenClaw is installed and accessible."""
        return self._openclaw_dir is not None

    # ── Translate ────────────────────────────────────────────────────────

    def translate(self, name: str) -> ExtensionManifest | None:
        """Translate an OpenClaw extension or skill to an ExtensionManifest.

        Tries skills first, then extensions.

        Returns:
            ExtensionManifest or None if not found.
        """
        if not self.available:
            logger.warning("OpenClaw not installed — cannot translate '{}'", name)
            return None

        # Try SKILL.md skill first (simpler, more common)
        manifest = self._translate_skill(name)
        if manifest:
            return manifest

        # Try plugin extension
        manifest = self._translate_plugin(name)
        if manifest:
            return manifest

        logger.warning("OpenClaw: '{}' not found as skill or extension", name)
        return None

    def _translate_skill(self, name: str) -> ExtensionManifest | None:
        """Translate an OpenClaw SKILL.md skill."""
        assert self._openclaw_dir is not None
        skill_dir = self._openclaw_dir / "skills" / name

        if not skill_dir.is_dir():
            return None

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None

        # Parse SKILL.md frontmatter
        from pawbot.extensions.discovery import _parse_skill_md

        parsed = _parse_skill_md(skill_md)
        if not parsed:
            logger.warning("OpenClaw skill '{}': failed to parse SKILL.md", name)
            return None

        manifest = ExtensionManifest.from_skill_md(
            name=parsed.get("name", name),
            description=parsed.get("description", ""),
            metadata=parsed.get("metadata"),
        )
        manifest.origin = ExtensionOrigin.OPENCLAW

        # Copy SKILL.md to translated dir
        dest = self._translated_dir / name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_md, dest / "SKILL.md")

        # Write extension.json
        (dest / "extension.json").write_text(
            manifest.model_dump_json(indent=2, exclude_defaults=True),
            encoding="utf-8",
        )

        logger.debug("Translated OpenClaw skill '{}' → extension", name)
        return manifest

    def _translate_plugin(self, name: str) -> ExtensionManifest | None:
        """Translate an OpenClaw plugin extension."""
        assert self._openclaw_dir is not None
        ext_dir = self._openclaw_dir / "extensions" / name

        if not ext_dir.is_dir():
            return None

        # Read openclaw.plugin.json
        plugin_json = ext_dir / "openclaw.plugin.json"
        if not plugin_json.exists():
            return None

        try:
            plugin_data = json.loads(plugin_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("OpenClaw plugin '{}': cannot read manifest: {}", name, e)
            return None

        # Read package.json if available
        pkg_data: dict[str, Any] | None = None
        pkg_json = ext_dir / "package.json"
        if pkg_json.exists():
            try:
                pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        manifest = ExtensionManifest.from_openclaw_plugin(plugin_data, pkg_data)

        # Copy to translated dir
        dest = self._translated_dir / name
        dest.mkdir(parents=True, exist_ok=True)

        # Write extension.json
        (dest / "extension.json").write_text(
            manifest.model_dump_json(indent=2, exclude_defaults=True),
            encoding="utf-8",
        )

        # Copy plugin files for reference
        shutil.copy2(plugin_json, dest / "openclaw.plugin.json")
        if pkg_json.exists():
            shutil.copy2(pkg_json, dest / "package.json")

        logger.debug("Translated OpenClaw plugin '{}' → extension", name)
        return manifest

    # ── Tool Execution (Node.js subprocess) ──────────────────────────────

    @staticmethod
    def can_execute_node() -> bool:
        """Check if Node.js is available for executing plugin tools."""
        try:
            result = subprocess.run(
                ["node", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def execute_node_tool(
        script_path: str,
        function_name: str,
        args: dict[str, Any],
        timeout: int = 60,
    ) -> str:
        """Execute an OpenClaw Node.js tool via subprocess.

        Creates a tiny runner script that imports the tool module,
        calls the function, and outputs the result as JSON.

        Args:
            script_path: Path to the Node.js module file.
            function_name: Export name to call.
            args: Arguments as a dict (serialized to JSON).
            timeout: Execution timeout in seconds.

        Returns:
            Tool result as string.
        """
        # Create a runner script
        runner = f"""
const mod = require({json.dumps(script_path)});
const fn = mod[{json.dumps(function_name)}] || mod.default;
if (!fn) {{
    process.stderr.write('Function {function_name} not found');
    process.exit(1);
}}
const args = {json.dumps(args)};
Promise.resolve(fn(args)).then(result => {{
    process.stdout.write(JSON.stringify(result));
}}).catch(err => {{
    process.stderr.write(String(err));
    process.exit(1);
}});
"""
        try:
            result = subprocess.run(
                ["node", "-e", runner],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                return f"Error: {result.stderr.strip()}"
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return f"Error: Node.js tool timed out after {timeout}s"
        except FileNotFoundError:
            return "Error: Node.js is not installed. Install Node.js to use OpenClaw plugin tools."

    # ── Query ────────────────────────────────────────────────────────────

    def list_available_skills(self) -> list[str]:
        """List all available OpenClaw skills."""
        if not self.available:
            return []
        assert self._openclaw_dir is not None
        skills_dir = self._openclaw_dir / "skills"
        if not skills_dir.is_dir():
            return []
        return sorted(
            d.name
            for d in skills_dir.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists()
        )

    def list_available_plugins(self) -> list[str]:
        """List all available OpenClaw plugin extensions."""
        if not self.available:
            return []
        assert self._openclaw_dir is not None
        ext_dir = self._openclaw_dir / "extensions"
        if not ext_dir.is_dir():
            return []
        return sorted(
            d.name
            for d in ext_dir.iterdir()
            if d.is_dir() and (d / "openclaw.plugin.json").exists()
        )

    def get_source_dir(self, name: str) -> Path | None:
        """Get the source directory for an OpenClaw extension or skill."""
        if not self.available:
            return None
        assert self._openclaw_dir is not None

        for subdir in ("skills", "extensions"):
            d = self._openclaw_dir / subdir / name
            if d.is_dir():
                return d

        return None

    def get_translated_dir(self, name: str) -> Path | None:
        """Get the translated directory for an extension (if created)."""
        d = self._translated_dir / name
        return d if d.is_dir() else None
