"""Extension loader — load tools, prompts, hooks, CLI from extensions (Phase E3).

Replaces ``pawbot/skills/loader.py`` with a unified loader that handles:
  - Python tool import and registration
  - Prompt fragment loading
  - Lifecycle hook registration
  - MCP server config injection
  - CLI command registration

All loaded tools flow into the same ``ToolRegistry`` the agent loop uses.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from pawbot.extensions.lifecycle import HookName, LifecycleDispatcher
from pawbot.extensions.registry import (
    ExtensionRecord,
    ExtensionRegistry,
    ExtensionStatus,
)
from pawbot.extensions.schema import (
    ExtensionManifest,
    ExtensionTool,
    ToolRuntime,
)


class ExtensionLoader:
    """Load extensions and register their tools, prompts, and hooks.

    This is the runtime bridge between the ``ExtensionRegistry`` (which
    tracks what's installed) and the agent loop (which needs callable
    tools and prompt fragments).
    """

    def __init__(
        self,
        registry: ExtensionRegistry,
        lifecycle: LifecycleDispatcher | None = None,
        config: dict[str, Any] | None = None,
    ):
        self.registry = registry
        self.lifecycle = lifecycle or LifecycleDispatcher()
        self._config = config or {}

        # Loaded state
        self._tool_registry: dict[str, Callable] = {}  # full_name -> callable
        self._manifests: dict[str, ExtensionManifest] = {}  # ext_id -> manifest
        self._api_keys: dict[str, str] = {}  # ext_id -> api_key
        self._prompt_cache: dict[str, str] = {}  # ext_id -> combined prompt text

    # ── Load All ─────────────────────────────────────────────────────────

    def load_all(self) -> int:
        """Load all enabled extensions from the registry.

        Returns count of successfully loaded extensions.
        """
        count = 0
        for record in self.registry.list_enabled():
            try:
                self._load_extension(record)
                count += 1
            except Exception as e:
                logger.warning("Failed to load extension '{}': {}", record.id, e)
                self.registry.mark_error(record.id, str(e))

        if count:
            tool_count = len(self._tool_registry)
            logger.info(
                "Loaded {} extensions ({} tools registered)", count, tool_count
            )

        return count

    def _load_extension(self, record: ExtensionRecord) -> None:
        """Load a single extension."""
        source = record.source
        if not source:
            logger.debug("Extension '{}' has no source path, skipping tool load", record.id)
            self.registry.mark_loaded(record.id)
            return

        source_dir = Path(source)

        # Try extension.json first, fall back to skill.json
        manifest_path = source_dir / "extension.json"
        if manifest_path.exists():
            import json

            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = ExtensionManifest.model_validate(data)
        else:
            skill_json = source_dir / "skill.json"
            if skill_json.exists():
                import json

                data = json.loads(skill_json.read_text(encoding="utf-8"))
                manifest = ExtensionManifest.from_skill_json(data)
            else:
                # SKILL.md-only extension (prompt-only, no tools to import)
                self._load_prompts(record.id, source_dir)
                self.registry.mark_loaded(record.id)
                self._fire_hook(HookName.ON_LOAD, record.id)
                return

        self._manifests[record.id] = manifest

        # Check API key requirement
        if manifest.requires_api_key:
            api_key = (
                self._config.get("extensions", {})
                .get(manifest.id, {})
                .get("apiKey")
                or os.environ.get(manifest.api_key_env_var, "")
            )
            if not api_key:
                logger.warning(
                    "Extension '{}' requires API key (env: {}) — skipping",
                    manifest.id,
                    manifest.api_key_env_var,
                )
                self.registry.mark_error(
                    record.id, f"Missing API key: {manifest.api_key_env_var}"
                )
                return
            self._api_keys[manifest.id] = api_key

        # Load tools (Python only — Node tools handled by OpenClaw adapter)
        for tool_def in manifest.tools:
            if tool_def.runtime != ToolRuntime.PYTHON:
                continue
            try:
                fn = self._import_tool(source_dir, tool_def)
                full_name = f"{manifest.id}.{tool_def.name}"
                self._tool_registry[full_name] = fn
                logger.debug("Registered extension tool: {}", full_name)
            except Exception as e:
                logger.warning(
                    "Failed to load tool '{}.{}': {}",
                    manifest.id,
                    tool_def.name,
                    e,
                )

        # Load prompts
        self._load_prompts(record.id, source_dir, manifest)

        self.registry.mark_loaded(record.id)
        self._fire_hook(HookName.ON_LOAD, record.id)

    # ── Tool Import ──────────────────────────────────────────────────────

    def _import_tool(self, ext_dir: Path, tool_def: ExtensionTool) -> Callable:
        """Import a tool function from a Python module in the extension."""
        parts = tool_def.function.rsplit(".", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid tool function path: {tool_def.function}")

        module_name, func_name = parts
        module_path = ext_dir / f"{module_name}.py"

        if not module_path.exists():
            raise FileNotFoundError(f"Module not found: {module_path}")

        spec = importlib.util.spec_from_file_location(
            f"pawbot_ext_{ext_dir.name}_{module_name}",
            str(module_path),
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module spec for {module_path}")

        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fn = getattr(mod, func_name, None)
        if fn is None:
            raise AttributeError(
                f"Function '{func_name}' not found in {module_path}"
            )
        return fn

    # ── Prompts ──────────────────────────────────────────────────────────

    def _load_prompts(
        self,
        ext_id: str,
        source_dir: Path,
        manifest: ExtensionManifest | None = None,
    ) -> None:
        """Load prompt fragments from an extension."""
        prompt_paths = manifest.prompts if manifest else ["SKILL.md"]
        fragments: list[str] = []

        for prompt_path in prompt_paths:
            full_path = source_dir / prompt_path
            if full_path.exists():
                try:
                    content = full_path.read_text(encoding="utf-8").strip()
                    if content:
                        fragments.append(content)
                except OSError as e:
                    logger.warning("Cannot read prompt '{}': {}", full_path, e)

        if fragments:
            self._prompt_cache[ext_id] = "\n\n".join(fragments)

    # ── Lifecycle Hooks ──────────────────────────────────────────────────

    def _fire_hook(self, hook: HookName, ext_id: str, **kwargs: Any) -> None:
        """Fire a lifecycle hook for an extension."""
        if self.lifecycle:
            self.lifecycle.dispatch(hook, extension_id=ext_id, **kwargs)

    # ── Public API ───────────────────────────────────────────────────────

    def get_tool(self, full_name: str) -> Callable | None:
        """Get a loaded tool function by full name (ext_id.tool_name)."""
        return self._tool_registry.get(full_name)

    @property
    def tool_count(self) -> int:
        """Total count of registered extension tools."""
        return len(self._tool_registry)

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Get OpenAI-format tool definitions for all loaded extension tools."""
        definitions: list[dict[str, Any]] = []
        for ext_id, manifest in self._manifests.items():
            for tool_def in manifest.tools:
                if tool_def.runtime != ToolRuntime.PYTHON:
                    continue
                full_name = f"{ext_id}.{tool_def.name}"
                if full_name not in self._tool_registry:
                    continue
                params = tool_def.parameters or {
                    "type": "object",
                    "properties": {},
                }
                definitions.append(
                    {
                        "type": "function",
                        "function": {
                            "name": full_name,
                            "description": f"[{ext_id}] {tool_def.description}",
                            "parameters": params,
                        },
                    }
                )
        return definitions

    def get_prompt_fragments(self) -> str:
        """Get combined system prompt additions from all loaded extensions."""
        fragments: list[str] = []
        for ext_id in sorted(self._prompt_cache.keys()):
            content = self._prompt_cache[ext_id]
            fragments.append(f"\n## Extension: {ext_id}\n\n{content}\n")
        return "\n".join(fragments)

    def get_api_key(self, ext_id: str) -> str | None:
        """Get the API key for a loaded extension."""
        return self._api_keys.get(ext_id)

    @property
    def loaded_manifests(self) -> dict[str, ExtensionManifest]:
        """All loaded extension manifests."""
        return dict(self._manifests)

    def unload(self, ext_id: str) -> bool:
        """Unload an extension (remove its tools and prompts)."""
        self._fire_hook(HookName.ON_UNLOAD, ext_id)

        removed = False
        # Remove tools
        to_remove = [k for k in self._tool_registry if k.startswith(f"{ext_id}.")]
        for k in to_remove:
            del self._tool_registry[k]
            removed = True

        # Remove prompts
        if ext_id in self._prompt_cache:
            del self._prompt_cache[ext_id]
            removed = True

        # Remove manifest
        self._manifests.pop(ext_id, None)
        self._api_keys.pop(ext_id, None)

        return removed

    def unload_all(self) -> None:
        """Unload all extensions."""
        for ext_id in list(self._manifests.keys()):
            self.unload(ext_id)
