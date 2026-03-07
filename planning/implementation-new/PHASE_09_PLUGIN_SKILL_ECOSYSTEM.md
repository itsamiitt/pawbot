# Phase 9 — Plugin & Skill Ecosystem

> **Goal:** Build a modular plugin/skill system with installable packages, per-agent tool allow-lists, and a skill marketplace foundation.  
> **Duration:** 10-14 days  
> **Risk Level:** Medium (new subsystem, minimal impact on existing code)  
> **Depends On:** Phase 0 (clean imports), Phase 1 (tool registry)

---

## Why This Phase Exists

OpenClaw has a mature plugin/skill ecosystem:
- `plugins.enabled: true` with per-channel plugins (WhatsApp, Google Chat, Slack)
- `skills.install.nodeManager: "npm"` — npm-based skill installation
- Per-agent `tools.allow` lists (e.g. `shopify_admin_graphql`, `shopify_product_seo_update`)
- Dedicated skill entries with API keys (`nano-banana-pro`, `coding-agent`)

PawBot has a basic `SkillLoader` class but **no plugin system, no installer, no per-agent tool allow-lists**.

---

## 9.1 — Skill Package Format

### Define the standard skill package structure

```
~/.pawbot/skills/
├── installed.json                  # Registry of installed skills
└── <skill-name>/
    ├── skill.json                  # Manifest (metadata, tools, dependencies)
    ├── __init__.py                 # Skill entry point
    ├── tools.py                    # Tool definitions
    ├── prompts/                    # System prompt fragments
    │   └── default.md
    ├── requirements.txt            # Python dependencies (optional)
    └── README.md
```

**Create:** `pawbot/skills/manifest.py`

```python
"""Skill manifest schema — defines the skill.json format."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillTool(BaseModel):
    """Tool exported by a skill."""
    name: str
    description: str
    function: str          # Module path, e.g. "tools.search_products"
    risk_level: str = "low"  # low, caution, high, critical
    timeout: int = 60


class SkillManifest(BaseModel):
    """Skill package manifest (skill.json)."""
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    license: str = "MIT"
    
    # What this skill provides
    tools: list[SkillTool] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)  # Paths to prompt fragments
    
    # Dependencies
    python_dependencies: list[str] = Field(default_factory=list)
    requires_api_key: bool = False
    api_key_env_var: str = ""        # e.g. "SHOPIFY_API_KEY"
    
    # Permissions
    requires_network: bool = False
    requires_filesystem: bool = False
    requires_browser: bool = False
    
    # Compatibility
    min_pawbot_version: str = "1.0.0"
    supported_providers: list[str] = Field(default_factory=list)  # Empty = all

    # Configuration
    config_schema: dict[str, Any] = Field(default_factory=dict)
```

---

## 9.2 — Skill Installer

**Create:** `pawbot/skills/installer.py`

```python
"""Skill installer — install, uninstall, and update skills."""

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
    """Install and manage PawBot skills."""

    def __init__(self):
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        self._registry = self._load_registry()

    def _load_registry(self) -> dict[str, Any]:
        if REGISTRY_FILE.exists():
            return json.loads(REGISTRY_FILE.read_text())
        return {"version": 1, "skills": {}}

    def _save_registry(self) -> None:
        REGISTRY_FILE.write_text(json.dumps(self._registry, indent=2))

    def install_from_directory(self, source_path: str | Path) -> SkillManifest:
        """Install a skill from a local directory."""
        source = Path(source_path)
        manifest_path = source / "skill.json"

        if not manifest_path.exists():
            raise FileNotFoundError(f"No skill.json found in {source}")

        manifest = SkillManifest.model_validate_json(manifest_path.read_text())
        dest = SKILLS_DIR / manifest.name

        # Check if already installed
        if manifest.name in self._registry["skills"]:
            existing_version = self._registry["skills"][manifest.name].get("version", "0.0.0")
            logger.info(
                "Skill '{}' already installed (v{}). Upgrading to v{}",
                manifest.name, existing_version, manifest.version,
            )
            shutil.rmtree(dest, ignore_errors=True)

        # Copy skill files
        shutil.copytree(source, dest, dirs_exist_ok=True)

        # Install Python dependencies
        if manifest.python_dependencies:
            self._install_python_deps(manifest.python_dependencies)

        # Update registry
        self._registry["skills"][manifest.name] = {
            "version": manifest.version,
            "installed_at": time.time(),
            "source": str(source),
            "tools": [t.name for t in manifest.tools],
        }
        self._save_registry()

        logger.info(
            "Skill '{}' v{} installed ({} tools)",
            manifest.name, manifest.version, len(manifest.tools),
        )
        return manifest

    def install_from_git(self, repo_url: str, branch: str = "main") -> SkillManifest:
        """Install a skill from a Git repository."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", branch, repo_url, tmpdir],
                check=True, capture_output=True,
            )
            return self.install_from_directory(tmpdir)

    def install_from_pip(self, package_name: str) -> SkillManifest:
        """Install a skill published as a pip package."""
        # Install the package
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", package_name],
        )
        # Try to find the skill manifest
        try:
            import importlib
            mod = importlib.import_module(package_name.replace("-", "_"))
            manifest_path = Path(mod.__file__).parent / "skill.json"
            if manifest_path.exists():
                manifest = SkillManifest.model_validate_json(manifest_path.read_text())
                # Copy to skills dir
                shutil.copytree(manifest_path.parent, SKILLS_DIR / manifest.name, dirs_exist_ok=True)
                self._registry["skills"][manifest.name] = {
                    "version": manifest.version,
                    "installed_at": time.time(),
                    "source": f"pip:{package_name}",
                    "tools": [t.name for t in manifest.tools],
                }
                self._save_registry()
                return manifest
        except Exception as e:
            logger.warning("Could not auto-register pip skill: {}", e)
        raise RuntimeError(f"Package '{package_name}' installed but no skill.json found")

    def uninstall(self, skill_name: str) -> bool:
        """Uninstall a skill."""
        if skill_name not in self._registry["skills"]:
            logger.warning("Skill '{}' is not installed", skill_name)
            return False

        dest = SKILLS_DIR / skill_name
        if dest.exists():
            shutil.rmtree(dest)

        del self._registry["skills"][skill_name]
        self._save_registry()
        logger.info("Skill '{}' uninstalled", skill_name)
        return True

    def list_installed(self) -> list[dict[str, Any]]:
        """List all installed skills."""
        results = []
        for name, info in self._registry.get("skills", {}).items():
            manifest_path = SKILLS_DIR / name / "skill.json"
            if manifest_path.exists():
                manifest = SkillManifest.model_validate_json(manifest_path.read_text())
                results.append({
                    "name": name,
                    "version": manifest.version,
                    "description": manifest.description,
                    "tools": [t.name for t in manifest.tools],
                    "installed_at": info.get("installed_at"),
                })
            else:
                results.append({"name": name, "version": info.get("version", "?"), "broken": True})
        return results

    def _install_python_deps(self, deps: list[str]) -> None:
        """Install Python dependencies for a skill."""
        logger.info("Installing {} Python dependencies...", len(deps))
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", *deps],
        )
```

---

## 9.3 — Skill Loader & Runtime

**Create:** `pawbot/skills/loader.py`

```python
"""Skill runtime loader — loads installed skills and registers their tools."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from pawbot.skills.manifest import SkillManifest, SkillTool


SKILLS_DIR = Path.home() / ".pawbot" / "skills"


class SkillRuntime:
    """Load and manage skill lifecycle at runtime."""

    def __init__(self, config: dict[str, Any] | None = None):
        self._skills: dict[str, SkillManifest] = {}
        self._tool_registry: dict[str, callable] = {}
        self._config = config or {}
        self._api_keys: dict[str, str] = {}

    def load_all(self) -> int:
        """Load all installed skills. Returns count loaded."""
        if not SKILLS_DIR.exists():
            return 0

        count = 0
        for skill_dir in SKILLS_DIR.iterdir():
            if not skill_dir.is_dir():
                continue
            manifest_path = skill_dir / "skill.json"
            if not manifest_path.exists():
                continue
            try:
                self._load_skill(skill_dir, manifest_path)
                count += 1
            except Exception as e:
                logger.warning("Failed to load skill '{}': {}", skill_dir.name, e)

        logger.info("Loaded {} skills ({} tools)", count, len(self._tool_registry))
        return count

    def _load_skill(self, skill_dir: Path, manifest_path: Path) -> None:
        """Load a single skill."""
        manifest = SkillManifest.model_validate_json(manifest_path.read_text())

        # Check API key requirement
        if manifest.requires_api_key:
            import os
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
                tool_name = f"{manifest.name}.{tool_def.name}"
                self._tool_registry[tool_name] = fn
                logger.debug("Registered tool: {}", tool_name)
            except Exception as e:
                logger.warning("Failed to load tool '{}': {}", tool_def.name, e)

        self._skills[manifest.name] = manifest

    def _import_tool(self, skill_dir: Path, tool_def: SkillTool) -> callable:
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
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fn = getattr(mod, func_name, None)
        if fn is None:
            raise AttributeError(f"Function '{func_name}' not found in {module_path}")
        return fn

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Get OpenAI-format tool definitions for all loaded skills."""
        definitions = []
        for skill_name, manifest in self._skills.items():
            for tool_def in manifest.tools:
                full_name = f"{skill_name}.{tool_def.name}"
                definitions.append({
                    "type": "function",
                    "function": {
                        "name": full_name,
                        "description": f"[{skill_name}] {tool_def.description}",
                        "parameters": {},  # Would be loaded from skill if defined
                    },
                })
        return definitions

    def get_prompt_fragments(self) -> str:
        """Get system prompt additions from all loaded skills."""
        fragments = []
        for skill_name, manifest in self._skills.items():
            for prompt_path in manifest.prompts:
                full_path = SKILLS_DIR / skill_name / prompt_path
                if full_path.exists():
                    fragments.append(
                        f"\n## Skill: {skill_name}\n\n{full_path.read_text().strip()}\n"
                    )
        return "\n".join(fragments)
```

---

## 9.4 — Per-Agent Tool Allow-Lists

OpenClaw has per-agent tool filtering (`tools.allow`). Add this to PawBot.

**File:** `pawbot/config/schema.py` — add:

```python
class AgentToolsConfig(BaseModel):
    """Per-agent tool permission configuration."""
    allow: list[str] = Field(
        default_factory=list,
        description=(
            "Tool names this agent is allowed to use. Empty = all tools. "
            "Supports glob patterns: 'shopify_*', 'browser_*'"
        ),
    )
    deny: list[str] = Field(
        default_factory=list,
        description="Tool names explicitly blocked for this agent.",
    )
    max_calls_per_session: int = 200  # Safety limit


class AgentConfig(BaseModel):
    """Per-agent configuration."""
    id: str = "main"
    workspace: str = ""
    tools: AgentToolsConfig = Field(default_factory=AgentToolsConfig)
    model: str = ""            # Override default model
    max_tokens: int = 0        # Override (0 = use default)
```

**Tool filtering in the agent loop:**

```python
# In AgentLoop._get_filtered_tools():

import fnmatch

def _get_filtered_tools(self) -> list[dict]:
    """Get tool definitions filtered by agent allow/deny lists."""
    all_tools = self.tools.get_definitions()
    
    if not self._agent_config.tools.allow and not self._agent_config.tools.deny:
        return all_tools
    
    filtered = []
    for tool in all_tools:
        name = tool.get("function", {}).get("name", "")
        
        # Check deny list first
        if any(fnmatch.fnmatch(name, p) for p in self._agent_config.tools.deny):
            continue
        
        # Check allow list (empty = allow all)
        if self._agent_config.tools.allow:
            if not any(fnmatch.fnmatch(name, p) for p in self._agent_config.tools.allow):
                continue
        
        filtered.append(tool)
    
    return filtered
```

---

## 9.5 — Skill CLI Commands

**Create:** `pawbot/cli/skills_commands.py` — extend:

```python
"""CLI commands for skill management."""

import typer
from rich.console import Console
from rich.table import Table

skills_app = typer.Typer(help="Manage PawBot skills")
console = Console()


@skills_app.command("install")
def install_skill(
    source: str = typer.Argument(..., help="Path, Git URL, or pip package name"),
    git: bool = typer.Option(False, "--git", help="Install from Git repository"),
    pip: bool = typer.Option(False, "--pip", help="Install from pip package"),
):
    """Install a skill from a local directory, Git repo, or pip package."""
    from pawbot.skills.installer import SkillInstaller
    installer = SkillInstaller()

    try:
        if git:
            manifest = installer.install_from_git(source)
        elif pip:
            manifest = installer.install_from_pip(source)
        else:
            manifest = installer.install_from_directory(source)
        
        console.print(f"[green]✓[/green] Installed [bold]{manifest.name}[/bold] v{manifest.version}")
        if manifest.tools:
            console.print(f"  Tools: {', '.join(t.name for t in manifest.tools)}")
    except Exception as e:
        console.print(f"[red]✗ Installation failed:[/red] {e}")
        raise typer.Exit(1)


@skills_app.command("uninstall")
def uninstall_skill(name: str = typer.Argument(..., help="Skill name")):
    """Uninstall a skill."""
    from pawbot.skills.installer import SkillInstaller
    installer = SkillInstaller()
    
    if installer.uninstall(name):
        console.print(f"[green]✓[/green] Uninstalled [bold]{name}[/bold]")
    else:
        console.print(f"[yellow]Skill '{name}' is not installed[/yellow]")


@skills_app.command("list")
def list_skills():
    """List all installed skills."""
    from pawbot.skills.installer import SkillInstaller
    installer = SkillInstaller()
    skills = installer.list_installed()

    if not skills:
        console.print("[dim]No skills installed. Use 'pawbot skills install <path>' to install.[/dim]")
        return

    table = Table(title="Installed Skills")
    table.add_column("Name", style="bold")
    table.add_column("Version")
    table.add_column("Tools")
    table.add_column("Description")

    for s in skills:
        tools_str = ", ".join(s.get("tools", [])) if s.get("tools") else "—"
        table.add_row(s["name"], s.get("version", "?"), tools_str, s.get("description", ""))

    console.print(table)


@skills_app.command("info")
def skill_info(name: str = typer.Argument(..., help="Skill name")):
    """Show detailed information about a skill."""
    from pathlib import Path
    from pawbot.skills.manifest import SkillManifest

    skill_dir = Path.home() / ".pawbot" / "skills" / name
    manifest_path = skill_dir / "skill.json"

    if not manifest_path.exists():
        console.print(f"[red]Skill '{name}' not found[/red]")
        raise typer.Exit(1)

    manifest = SkillManifest.model_validate_json(manifest_path.read_text())
    console.print(f"\n[bold]{manifest.name}[/bold] v{manifest.version}")
    console.print(f"  Author: {manifest.author or '—'}")
    console.print(f"  License: {manifest.license}")
    console.print(f"  Description: {manifest.description or '—'}")
    console.print(f"\n  Tools ({len(manifest.tools)}):")
    for t in manifest.tools:
        console.print(f"    • [cyan]{t.name}[/cyan] — {t.description} [dim](risk: {t.risk_level})[/dim]")
    if manifest.requires_api_key:
        console.print(f"\n  ⚠ Requires API key: {manifest.api_key_env_var}")
    if manifest.python_dependencies:
        console.print(f"\n  Python deps: {', '.join(manifest.python_dependencies)}")
```

---

## Verification Checklist — Phase 9 Complete

- [ ] `pawbot/skills/manifest.py` — `SkillManifest` Pydantic model
- [ ] `pawbot/skills/installer.py` — install from directory, Git, or pip
- [ ] `pawbot/skills/loader.py` — runtime skill loading + tool registration
- [ ] `~/.pawbot/skills/installed.json` registry created on first install
- [ ] `pawbot skills install <path>` CLI command works
- [ ] `pawbot skills uninstall <name>` removes skill and registry entry
- [ ] `pawbot skills list` shows installed skills with tool counts
- [ ] `pawbot skills info <name>` shows detailed skill info
- [ ] Per-agent tool allow/deny lists filter tools with glob patterns
- [ ] Skills can export prompt fragments injected into system prompt
- [ ] API key requirement check prevents loading unconfigured skills
- [ ] All tests pass: `pytest tests/ -v --tb=short`
