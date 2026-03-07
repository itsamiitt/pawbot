"""Skill installer — install, uninstall, and update skills (Phase 9.2).

Supports three installation sources:
  1. Local directory (copy files to ~/.pawbot/skills/<name>)
  2. Git repository (shallow clone + install)
  3. pip package (pip install + auto-discover skill.json)

Maintains a registry at ~/.pawbot/skills/installed.json tracking
all installed skill packages with metadata.
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

from pawbot.skills.manifest import SkillManifest


SKILLS_DIR = Path.home() / ".pawbot" / "skills"
REGISTRY_FILE = SKILLS_DIR / "installed.json"


class SkillInstaller:
    """Install and manage PawBot skill packages."""

    def __init__(self, skills_dir: Path | None = None):
        self.skills_dir = skills_dir or SKILLS_DIR
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._registry = self._load_registry()

    # ── Registry ──────────────────────────────────────────────────────────

    def _load_registry(self) -> dict[str, Any]:
        registry_file = self.skills_dir / "installed.json"
        if registry_file.exists():
            try:
                return json.loads(registry_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt installed.json — resetting registry")
        return {"version": 1, "skills": {}}

    def _save_registry(self) -> None:
        registry_file = self.skills_dir / "installed.json"
        registry_file.write_text(
            json.dumps(self._registry, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Install Methods ───────────────────────────────────────────────────

    def install_from_directory(self, source_path: str | Path) -> SkillManifest:
        """Install a skill from a local directory."""
        source = Path(source_path).resolve()
        manifest_path = source / "skill.json"

        if not manifest_path.exists():
            raise FileNotFoundError(f"No skill.json found in {source}")

        manifest = SkillManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        dest = self.skills_dir / manifest.name

        # Upgrade if already installed
        if manifest.name in self._registry.get("skills", {}):
            existing_ver = self._registry["skills"][manifest.name].get("version", "0.0.0")
            logger.info(
                "Skill '{}' already installed (v{}). Upgrading to v{}",
                manifest.name, existing_ver, manifest.version,
            )
            shutil.rmtree(dest, ignore_errors=True)

        # Copy skill files
        shutil.copytree(source, dest, dirs_exist_ok=True)

        # Install Python dependencies if any
        if manifest.python_dependencies:
            self._install_python_deps(manifest.python_dependencies)

        # Update registry
        self._registry.setdefault("skills", {})[manifest.name] = {
            "version": manifest.version,
            "installed_at": time.time(),
            "source": str(source),
            "source_type": "directory",
            "tools": [t.name for t in manifest.tools],
        }
        self._save_registry()

        logger.info(
            "Skill '{}' v{} installed ({} tools)",
            manifest.name, manifest.version, len(manifest.tools),
        )
        return manifest

    def install_from_git(self, repo_url: str, branch: str = "main") -> SkillManifest:
        """Install a skill from a Git repository (shallow clone)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", "--branch", branch, repo_url, tmpdir],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError:
                raise RuntimeError("Git is not installed. Install git to use --git installs.")
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Git clone failed: {e.stderr.strip()}")

            manifest = self.install_from_directory(tmpdir)

            # Update source type in registry
            self._registry["skills"][manifest.name]["source"] = repo_url
            self._registry["skills"][manifest.name]["source_type"] = "git"
            self._registry["skills"][manifest.name]["branch"] = branch
            self._save_registry()

            return manifest

    def install_from_pip(self, package_name: str) -> SkillManifest:
        """Install a skill published as a pip package."""
        # Install the package
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-q", package_name],
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"pip install failed for '{package_name}': {e}")

        # Try to find the skill manifest in the installed package
        try:
            import importlib

            mod = importlib.import_module(package_name.replace("-", "_"))
            manifest_path = Path(mod.__file__).parent / "skill.json"
            if manifest_path.exists():
                manifest = SkillManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
                dest = self.skills_dir / manifest.name
                shutil.copytree(manifest_path.parent, dest, dirs_exist_ok=True)

                self._registry.setdefault("skills", {})[manifest.name] = {
                    "version": manifest.version,
                    "installed_at": time.time(),
                    "source": f"pip:{package_name}",
                    "source_type": "pip",
                    "tools": [t.name for t in manifest.tools],
                }
                self._save_registry()
                return manifest
        except ImportError:
            pass
        except Exception as e:
            logger.warning("Could not auto-register pip skill: {}", e)

        raise RuntimeError(f"Package '{package_name}' installed but no skill.json found")

    # ── Uninstall ─────────────────────────────────────────────────────────

    def uninstall(self, skill_name: str) -> bool:
        """Uninstall a skill."""
        if skill_name not in self._registry.get("skills", {}):
            logger.warning("Skill '{}' is not installed", skill_name)
            return False

        dest = self.skills_dir / skill_name
        if dest.exists():
            shutil.rmtree(dest)

        del self._registry["skills"][skill_name]
        self._save_registry()
        logger.info("Skill '{}' uninstalled", skill_name)
        return True

    # ── Query ─────────────────────────────────────────────────────────────

    def list_installed(self) -> list[dict[str, Any]]:
        """List all installed skill packages."""
        results = []
        for name, info in self._registry.get("skills", {}).items():
            manifest_path = self.skills_dir / name / "skill.json"
            if manifest_path.exists():
                try:
                    manifest = SkillManifest.model_validate_json(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    results.append({
                        "name": name,
                        "version": manifest.version,
                        "description": manifest.description,
                        "author": manifest.author,
                        "tools": [t.name for t in manifest.tools],
                        "source_type": info.get("source_type", "unknown"),
                        "installed_at": info.get("installed_at"),
                    })
                except Exception:
                    results.append({
                        "name": name,
                        "version": info.get("version", "?"),
                        "broken": True,
                    })
            else:
                results.append({
                    "name": name,
                    "version": info.get("version", "?"),
                    "broken": True,
                })
        return results

    def is_installed(self, skill_name: str) -> bool:
        """Check if a skill is installed."""
        return skill_name in self._registry.get("skills", {})

    def get_manifest(self, skill_name: str) -> SkillManifest | None:
        """Load the manifest for an installed skill."""
        manifest_path = self.skills_dir / skill_name / "skill.json"
        if manifest_path.exists():
            return SkillManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        return None

    # ── Helpers ────────────────────────────────────────────────────────────

    def _install_python_deps(self, deps: list[str]) -> None:
        """Install Python dependencies for a skill."""
        logger.info("Installing {} Python dependencies...", len(deps))
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-q", *deps],
            )
        except subprocess.CalledProcessError as e:
            logger.warning("Failed to install some dependencies: {}", e)
