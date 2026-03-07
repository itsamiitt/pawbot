"""Extension installer — install from dir, git, pip, openclaw URI (Phase E3).

Replaces ``pawbot/skills/installer.py`` with a unified installer that
supports all original sources plus a new ``openclaw:`` URI scheme.

Install sources:
  1. Local directory — copies to ``~/.pawbot/extensions/<name>/``
  2. Git repository — shallow clone → install from directory
  3. Pip package — pip install → auto-discover manifest
  4. ``openclaw:<name>`` — adapts an OpenClaw extension/skill directory
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from loguru import logger

from pawbot.extensions.registry import EXTENSIONS_DIR, ExtensionRegistry
from pawbot.extensions.schema import ExtensionManifest


class ExtensionInstaller:
    """Install and manage pawbot extensions."""

    def __init__(
        self,
        extensions_dir: Path | None = None,
        registry: ExtensionRegistry | None = None,
    ):
        self.extensions_dir = extensions_dir or EXTENSIONS_DIR
        self.extensions_dir.mkdir(parents=True, exist_ok=True)
        self._registry = registry

    # ── Install Methods ──────────────────────────────────────────────────

    def install_from_directory(
        self, source_path: str | Path
    ) -> ExtensionManifest:
        """Install an extension from a local directory."""
        source = Path(source_path).resolve()

        # Try extension.json first, then skill.json
        manifest_path = source / "extension.json"
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = ExtensionManifest.model_validate(data)
        else:
            skill_path = source / "skill.json"
            if not skill_path.exists():
                raise FileNotFoundError(
                    f"No extension.json or skill.json found in {source}"
                )
            data = json.loads(skill_path.read_text(encoding="utf-8"))
            manifest = ExtensionManifest.from_skill_json(data)

        dest = self.extensions_dir / manifest.id

        # Upgrade if already installed
        if dest.exists():
            logger.info(
                "Extension '{}' already installed. Upgrading to v{}",
                manifest.id,
                manifest.version,
            )
            shutil.rmtree(dest, ignore_errors=True)

        # Copy extension files
        shutil.copytree(source, dest, dirs_exist_ok=True)

        # Write extension.json if only skill.json existed (normalize format)
        ext_json_path = dest / "extension.json"
        if not ext_json_path.exists():
            ext_json_path.write_text(
                manifest.model_dump_json(indent=2, exclude_defaults=True),
                encoding="utf-8",
            )

        # Install Python dependencies
        if manifest.dependencies.python:
            self._install_python_deps(manifest.dependencies.python)

        # Register if we have a registry
        if self._registry:
            from pawbot.extensions.schema import ExtensionOrigin

            manifest.origin = ExtensionOrigin.LOCAL
            self._registry.register(manifest, source=str(dest))

        logger.info(
            "Extension '{}' v{} installed ({} tools)",
            manifest.id,
            manifest.version,
            len(manifest.tools),
        )
        return manifest

    def install_from_git(
        self, repo_url: str, branch: str = "main"
    ) -> ExtensionManifest:
        """Install an extension from a Git repository (shallow clone)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                subprocess.run(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        "--branch",
                        branch,
                        repo_url,
                        tmpdir,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError:
                raise RuntimeError(
                    "Git is not installed. Install git to use --git installs."
                )
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Git clone failed: {e.stderr.strip()}")

            manifest = self.install_from_directory(tmpdir)

            # Update source origin
            if self._registry:
                record = self._registry.get(manifest.id)
                if record:
                    record.source = repo_url
                    from pawbot.extensions.schema import ExtensionOrigin

                    record.origin = ExtensionOrigin.GIT

            return manifest

    def install_from_pip(self, package_name: str) -> ExtensionManifest:
        """Install an extension from a pip package."""
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-q", package_name],
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"pip install failed for '{package_name}': {e}"
            )

        # Try to find the manifest in the installed package
        try:
            import importlib

            mod = importlib.import_module(package_name.replace("-", "_"))
            pkg_dir = Path(mod.__file__).parent

            for manifest_name in ("extension.json", "skill.json"):
                manifest_path = pkg_dir / manifest_name
                if manifest_path.exists():
                    manifest = self.install_from_directory(pkg_dir)

                    if self._registry:
                        record = self._registry.get(manifest.id)
                        if record:
                            record.source = f"pip:{package_name}"
                            from pawbot.extensions.schema import ExtensionOrigin

                            record.origin = ExtensionOrigin.PIP

                    return manifest
        except ImportError:
            pass
        except Exception as e:
            logger.warning(
                "Could not auto-register pip extension: {}", e
            )

        raise RuntimeError(
            f"Package '{package_name}' installed but no manifest found"
        )

    def install_from_openclaw(self, name: str) -> ExtensionManifest:
        """Install an OpenClaw extension or skill.

        Searches for the named extension/skill in the OpenClaw installation,
        translates its manifest, and installs it.

        Args:
            name: OpenClaw extension or skill name (e.g. "weather", "slack")
        """
        from pawbot.extensions.adapters.openclaw import OpenClawAdapter

        adapter = OpenClawAdapter()
        manifest = adapter.translate(name)

        if manifest is None:
            raise FileNotFoundError(
                f"OpenClaw extension/skill '{name}' not found"
            )

        # If the adapter created a translated directory, install from there
        translated_dir = adapter.get_translated_dir(name)
        if translated_dir and translated_dir.exists():
            result = self.install_from_directory(translated_dir)
        else:
            # For SKILL.md-only skills, create a minimal directory
            dest = self.extensions_dir / manifest.id
            dest.mkdir(parents=True, exist_ok=True)

            # Write extension.json
            (dest / "extension.json").write_text(
                manifest.model_dump_json(indent=2, exclude_defaults=True),
                encoding="utf-8",
            )

            # Copy SKILL.md if it exists
            source_dir = adapter.get_source_dir(name)
            if source_dir:
                skill_md = source_dir / "SKILL.md"
                if skill_md.exists():
                    shutil.copy2(skill_md, dest / "SKILL.md")

            if self._registry:
                self._registry.register(manifest, source=str(dest))

            result = manifest

        logger.info(
            "OpenClaw extension '{}' installed", manifest.id
        )
        return result

    # ── Smart Install (auto-detect source) ───────────────────────────────

    def install(self, source: str, **kwargs: Any) -> ExtensionManifest:
        """Auto-detect source type and install.

        Supports:
          - ``openclaw:<name>`` — OpenClaw extension
          - ``git+<url>`` or ``https://...*.git`` — Git repository
          - ``pip:<package>`` — pip package
          - Local path — directory install
        """
        if source.startswith("openclaw:"):
            name = source[9:]
            return self.install_from_openclaw(name)

        if source.startswith("git+") or source.endswith(".git"):
            url = source.removeprefix("git+")
            branch = kwargs.get("branch", "main")
            return self.install_from_git(url, branch=branch)

        if source.startswith("pip:"):
            package = source[4:]
            return self.install_from_pip(package)

        # Default: local directory
        return self.install_from_directory(source)

    # ── Uninstall ────────────────────────────────────────────────────────

    def uninstall(self, ext_id: str) -> bool:
        """Uninstall an extension by id."""
        dest = self.extensions_dir / ext_id
        if dest.exists():
            shutil.rmtree(dest)

        if self._registry:
            self._registry.unregister(ext_id)
            logger.info("Extension '{}' uninstalled", ext_id)
            return True

        if not dest.exists():
            logger.warning("Extension '{}' is not installed", ext_id)
            return False

        return True

    # ── Query ────────────────────────────────────────────────────────────

    def list_installed(self) -> list[dict[str, Any]]:
        """List all installed extensions."""
        results: list[dict[str, Any]] = []

        if not self.extensions_dir.exists():
            return results

        for ext_dir in sorted(self.extensions_dir.iterdir()):
            if not ext_dir.is_dir():
                continue
            if ext_dir.name.startswith(("_", ".")):
                continue

            manifest_path = ext_dir / "extension.json"
            skill_path = ext_dir / "skill.json"

            try:
                if manifest_path.exists():
                    data = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    manifest = ExtensionManifest.model_validate(data)
                elif skill_path.exists():
                    data = json.loads(
                        skill_path.read_text(encoding="utf-8")
                    )
                    manifest = ExtensionManifest.from_skill_json(data)
                else:
                    results.append(
                        {"id": ext_dir.name, "broken": True}
                    )
                    continue

                results.append(
                    {
                        "id": manifest.id,
                        "name": manifest.name,
                        "version": manifest.version,
                        "description": manifest.description,
                        "tools": [t.name for t in manifest.tools],
                        "origin": manifest.origin.value,
                    }
                )
            except Exception:
                results.append(
                    {"id": ext_dir.name, "broken": True}
                )

        return results

    def is_installed(self, ext_id: str) -> bool:
        """Check if an extension is installed."""
        dest = self.extensions_dir / ext_id
        return dest.is_dir() and (
            (dest / "extension.json").exists()
            or (dest / "skill.json").exists()
            or (dest / "SKILL.md").exists()
        )

    # ── Scaffold ─────────────────────────────────────────────────────────

    def create_scaffold(self, name: str, dest_dir: Path | None = None) -> Path:
        """Create a scaffold for a new extension.

        Creates a minimal working extension directory with:
          - extension.json (template)
          - tools/example.py (example tool)
          - prompts/system.md (example prompt)
        """
        dest = dest_dir or Path.cwd() / name
        dest.mkdir(parents=True, exist_ok=True)

        # extension.json
        manifest = ExtensionManifest(
            id=name,
            name=name.replace("-", " ").title(),
            version="0.1.0",
            description=f"A pawbot extension: {name}",
            author="",
            tools=[
                {
                    "name": "hello",
                    "description": f"Example tool from {name}",
                    "function": "tools.example.hello",
                    "risk_level": "low",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message": {
                                "type": "string",
                                "description": "Message to echo",
                            }
                        },
                        "required": ["message"],
                    },
                }
            ],
            prompts=["prompts/system.md"],
        )

        (dest / "extension.json").write_text(
            manifest.model_dump_json(indent=2, exclude_defaults=True),
            encoding="utf-8",
        )

        # tools/example.py
        tools_dir = dest / "tools"
        tools_dir.mkdir(exist_ok=True)
        (tools_dir / "__init__.py").write_text("", encoding="utf-8")
        (tools_dir / "example.py").write_text(
            f'''"""Example tool for the {name} extension."""


def hello(message: str) -> str:
    """Echo a message back.

    Args:
        message: The message to echo.

    Returns:
        The echoed message.
    """
    return f"[{name}] {{message}}"
''',
            encoding="utf-8",
        )

        # prompts/system.md
        prompts_dir = dest / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        (prompts_dir / "system.md").write_text(
            f"# {name.replace('-', ' ').title()}\n\n"
            f"This extension provides tools for {name}.\n",
            encoding="utf-8",
        )

        logger.info("Created extension scaffold at {}", dest)
        return dest

    # ── Helpers ───────────────────────────────────────────────────────────

    def _install_python_deps(self, deps: list[str]) -> None:
        """Install Python dependencies."""
        logger.info("Installing {} Python dependencies...", len(deps))
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-q", *deps],
            )
        except subprocess.CalledProcessError as e:
            logger.warning("Failed to install some dependencies: {}", e)
