"""Skill runtime loader — loads installed skill packages and registers tools (Phase 9.3).

This module bridges the SkillInstaller (which manages skill packages on disk)
with the agent's ToolRegistry (which makes tools available to the LLM).
It loads all installed skill packages, imports their tool functions, and
wraps them as agent tools.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from loguru import logger

from pawbot.skills.manifest import SkillManifest, SkillTool


SKILLS_DIR = Path.home() / ".pawbot" / "skills"


class SkillRuntime:
    """Load and manage installed skill packages at runtime."""

    def __init__(self, skills_dir: Path | None = None, config: dict[str, Any] | None = None):
        self.skills_dir = skills_dir or SKILLS_DIR
        self._skills: dict[str, SkillManifest] = {}
        self._tool_registry: dict[str, Any] = {}  # full_name -> callable
        self._config = config or {}
        self._api_keys: dict[str, str] = {}

    def load_all(self) -> int:
        """Load all installed skill packages. Returns count loaded."""
        if not self.skills_dir.exists():
            return 0

        count = 0
        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            manifest_path = skill_dir / "skill.json"
            if not manifest_path.exists():
                continue
            try:
                self._load_skill(skill_dir, manifest_path)
                count += 1
            except Exception as e:
                logger.warning("Failed to load skill package '{}': {}", skill_dir.name, e)

        if count:
            logger.info("Loaded {} skill packages ({} tools)", count, len(self._tool_registry))
        return count

    @property
    def loaded_skills(self) -> dict[str, SkillManifest]:
        """Get all loaded skill manifests."""
        return dict(self._skills)

    @property
    def tool_count(self) -> int:
        """Get total count of registered skill tools."""
        return len(self._tool_registry)

    def _load_skill(self, skill_dir: Path, manifest_path: Path) -> None:
        """Load a single skill package."""
        manifest = SkillManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )

        # Check API key requirement
        if manifest.requires_api_key:
            api_key = (
                self._config.get("skills", {}).get(manifest.name, {}).get("apiKey")
                or os.environ.get(manifest.api_key_env_var, "")
            )
            if not api_key:
                logger.warning(
                    "Skill '{}' requires API key (env: {}) — skipping",
                    manifest.name, manifest.api_key_env_var,
                )
                return
            self._api_keys[manifest.name] = api_key

        # Load tool functions
        for tool_def in manifest.tools:
            try:
                fn = self._import_tool(skill_dir, tool_def)
                full_name = f"{manifest.name}.{tool_def.name}"
                self._tool_registry[full_name] = fn
                logger.debug("Registered skill tool: {}", full_name)
            except Exception as e:
                logger.warning(
                    "Failed to load tool '{}.{}': {}",
                    manifest.name, tool_def.name, e,
                )

        self._skills[manifest.name] = manifest

    def _import_tool(self, skill_dir: Path, tool_def: SkillTool) -> Any:
        """Import a tool function from a skill module."""
        # tool_def.function is like "tools.search_products"
        parts = tool_def.function.rsplit(".", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid tool function path: {tool_def.function}")

        module_name, func_name = parts
        module_path = skill_dir / f"{module_name}.py"

        if not module_path.exists():
            raise FileNotFoundError(f"Module not found: {module_path}")

        spec = importlib.util.spec_from_file_location(
            f"pawbot_skill_{skill_dir.name}_{module_name}",
            str(module_path),
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module spec for {module_path}")

        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fn = getattr(mod, func_name, None)
        if fn is None:
            raise AttributeError(f"Function '{func_name}' not found in {module_path}")
        return fn

    def get_tool(self, full_name: str) -> Any | None:
        """Get a loaded tool function by full name (skill_name.tool_name)."""
        return self._tool_registry.get(full_name)

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Get OpenAI-format tool definitions for all loaded skill tools."""
        definitions = []
        for skill_name, manifest in self._skills.items():
            for tool_def in manifest.tools:
                full_name = f"{skill_name}.{tool_def.name}"
                params = tool_def.parameters or {"type": "object", "properties": {}}
                definitions.append({
                    "type": "function",
                    "function": {
                        "name": full_name,
                        "description": f"[{skill_name}] {tool_def.description}",
                        "parameters": params,
                    },
                })
        return definitions

    def get_prompt_fragments(self) -> str:
        """Get system prompt additions from all loaded skill packages."""
        fragments = []
        for skill_name, manifest in self._skills.items():
            for prompt_path in manifest.prompts:
                full_path = self.skills_dir / skill_name / prompt_path
                if full_path.exists():
                    content = full_path.read_text(encoding="utf-8").strip()
                    fragments.append(
                        f"\n## Skill: {skill_name}\n\n{content}\n"
                    )
        return "\n".join(fragments)

    def get_api_key(self, skill_name: str) -> str | None:
        """Get the API key for a loaded skill (if it required one)."""
        return self._api_keys.get(skill_name)
