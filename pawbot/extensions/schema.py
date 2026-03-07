"""Unified extension manifest schema — extension.json (Phase E2).

The ExtensionManifest is a superset of:
  - pawbot's      skill.json   (SkillManifest)
  - OpenClaw's    openclaw.plugin.json  (PluginManifest)
  - Both systems' SKILL.md     (prompt-only skills)

Backward compatibility:
  - Existing `skill.json` files are accepted via `from_skill_json()`.
  - Existing `SKILL.md`  files are accepted via `from_skill_md()`.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────


class ToolRuntime(str, Enum):
    """Runtime environment for tool execution."""
    PYTHON = "python"
    NODE = "node"
    SHELL = "shell"


class RiskLevel(str, Enum):
    """Tool risk classification."""
    LOW = "low"
    CAUTION = "caution"
    HIGH = "high"
    CRITICAL = "critical"


class ExtensionOrigin(str, Enum):
    """Where an extension was discovered from."""
    BUNDLED = "bundled"
    LOCAL = "local"
    GIT = "git"
    PIP = "pip"
    OPENCLAW = "openclaw"
    WORKSPACE = "workspace"


class ExtensionKind(str, Enum):
    """Special extension kinds for exclusive slot assignment."""
    MEMORY = "memory"
    GENERAL = "general"


# ── Sub-models ───────────────────────────────────────────────────────────────


class ExtensionTool(BaseModel):
    """Tool exported by an extension."""

    name: str
    description: str = ""
    function: str = ""  # Module path, e.g. "tools.search_products"
    risk_level: RiskLevel = RiskLevel.LOW
    timeout: int = 60
    parameters: dict[str, Any] = Field(default_factory=dict)  # JSON Schema
    runtime: ToolRuntime = ToolRuntime.PYTHON


class ExtensionDependencies(BaseModel):
    """Extension dependencies by type."""

    python: list[str] = Field(default_factory=list)  # pip packages
    system: list[str] = Field(default_factory=list)  # system binaries (e.g. curl)
    node: list[str] = Field(default_factory=list)  # npm packages (for openclaw compat)


class ExtensionPermissions(BaseModel):
    """Permissions declared by an extension."""

    network: bool = False
    filesystem: bool = False
    browser: bool = False
    exec: bool = False


class ExtensionCompatibility(BaseModel):
    """Compatibility constraints."""

    min_pawbot_version: str = "1.0.0"
    supported_providers: list[str] = Field(default_factory=list)
    runtime: ToolRuntime = ToolRuntime.PYTHON


class ExtensionCommand(BaseModel):
    """A slash command provided by an extension."""

    name: str  # Without leading slash
    description: str = ""
    accepts_args: bool = False
    require_auth: bool = True
    function: str = ""  # Module path to handler


class ExtensionService(BaseModel):
    """A background service provided by an extension."""

    id: str
    function: str = ""  # Module path to start/stop functions


class ExtensionMCPServer(BaseModel):
    """An MCP server provided by an extension."""

    name: str
    path: str  # Path to server script
    tool_timeout: int = 30
    enabled: bool = True


# ── Main Manifest ────────────────────────────────────────────────────────────


class ExtensionManifest(BaseModel):
    """Unified extension manifest (extension.json).

    Superset of pawbot's skill.json and OpenClaw's openclaw.plugin.json.

    Example extension.json:
    {
        "id": "weather",
        "name": "Weather Tools",
        "version": "1.0.0",
        "description": "Get weather forecasts",
        "tools": [
            {
                "name": "get_weather",
                "description": "Fetch current weather",
                "function": "tools.weather.get",
                "risk_level": "low"
            }
        ]
    }
    """

    # ── Identity ─────────────────────────────────────────────────────────
    id: str  # Unique extension identifier
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    license: str = "MIT"
    kind: ExtensionKind = ExtensionKind.GENERAL

    # ── What this extension provides ─────────────────────────────────────
    tools: list[ExtensionTool] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)  # Paths to prompt fragments
    channels: list[str] = Field(default_factory=list)  # Channel IDs provided
    providers: list[str] = Field(default_factory=list)  # LLM provider IDs
    skills: list[str] = Field(default_factory=list)  # Bundled SKILL.md names
    mcp_servers: list[ExtensionMCPServer] = Field(default_factory=list)
    cli_commands: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)  # Lifecycle hook names
    commands: list[ExtensionCommand] = Field(default_factory=list)
    services: list[ExtensionService] = Field(default_factory=list)

    # ── Dependencies ─────────────────────────────────────────────────────
    dependencies: ExtensionDependencies = Field(
        default_factory=ExtensionDependencies
    )

    # ── API key / env ────────────────────────────────────────────────────
    requires_api_key: bool = False
    api_key_env_var: str = ""
    env: dict[str, str] = Field(default_factory=dict)

    # ── Permissions ──────────────────────────────────────────────────────
    permissions: ExtensionPermissions = Field(
        default_factory=ExtensionPermissions
    )

    # ── Capabilities (free-form tags for allow/deny matching) ────────────
    capabilities: list[str] = Field(default_factory=list)

    # ── Compatibility ────────────────────────────────────────────────────
    compatibility: ExtensionCompatibility = Field(
        default_factory=ExtensionCompatibility
    )

    # ── Origin tracking (set by discovery, not in manifest files) ────────
    origin: ExtensionOrigin = ExtensionOrigin.LOCAL

    # ── Config schema (JSON Schema for runtime config) ───────────────────
    config_schema: dict[str, Any] = Field(default_factory=dict)
    ui_hints: dict[str, Any] = Field(default_factory=dict)

    # ── Legacy compatibility fields ──────────────────────────────────────
    # These are accepted for backward compatibility with skill.json but
    # mapped into the unified schema.
    python_dependencies: list[str] = Field(default_factory=list, exclude=True)
    requires_network: bool = Field(default=False, exclude=True)
    requires_filesystem: bool = Field(default=False, exclude=True)
    requires_browser: bool = Field(default=False, exclude=True)
    min_pawbot_version: str = Field(default="1.0.0", exclude=True)
    supported_providers: list[str] = Field(default_factory=list, exclude=True)

    def model_post_init(self, __context: Any) -> None:
        """Migrate legacy skill.json fields to unified schema."""
        # Merge legacy python_dependencies into dependencies.python
        if self.python_dependencies:
            existing = set(self.dependencies.python)
            for dep in self.python_dependencies:
                if dep not in existing:
                    self.dependencies.python.append(dep)

        # Merge legacy permission booleans
        if self.requires_network:
            self.permissions.network = True
        if self.requires_filesystem:
            self.permissions.filesystem = True
        if self.requires_browser:
            self.permissions.browser = True

        # Merge legacy compatibility fields
        if self.min_pawbot_version != "1.0.0":
            self.compatibility.min_pawbot_version = self.min_pawbot_version
        if self.supported_providers:
            self.compatibility.supported_providers = list(self.supported_providers)

        # Auto-set name from id if missing
        if not self.name:
            self.name = self.id

    # ── Factory methods ──────────────────────────────────────────────────

    @classmethod
    def from_skill_json(cls, data: dict[str, Any]) -> ExtensionManifest:
        """Create an ExtensionManifest from a legacy skill.json dict.

        Maps skill.json fields to the unified schema:
          - skill.name → extension.id
          - skill.tools → extension.tools
          - skill.python_dependencies → extension.dependencies.python
          - skill.requires_* → extension.permissions.*
        """
        # skill.json uses "name" where extension.json uses "id"
        ext_data = dict(data)
        if "id" not in ext_data and "name" in ext_data:
            ext_data["id"] = ext_data["name"]
        ext_data["origin"] = ExtensionOrigin.LOCAL.value
        return cls.model_validate(ext_data)

    @classmethod
    def from_skill_md(
        cls, name: str, description: str, metadata: dict[str, Any] | None = None
    ) -> ExtensionManifest:
        """Create an ExtensionManifest from a SKILL.md frontmatter.

        SKILL.md files are prompt-only skills (no tools, no Python code).
        They inject prompt instructions into the system prompt.
        """
        meta = metadata or {}
        pawbot_meta = meta.get("pawbot", meta.get("openclaw", {}))

        deps = ExtensionDependencies()
        requires = pawbot_meta.get("requires", {})
        if requires.get("bins"):
            deps.system = list(requires["bins"])

        return cls(
            id=name,
            name=name,
            description=description,
            prompts=["SKILL.md"],
            origin=ExtensionOrigin.BUNDLED,
            dependencies=deps,
            capabilities=["prompt-only"],
        )

    @classmethod
    def from_openclaw_plugin(
        cls, plugin_data: dict[str, Any], package_data: dict[str, Any] | None = None
    ) -> ExtensionManifest:
        """Create an ExtensionManifest from an openclaw.plugin.json + package.json.

        Maps OpenClaw plugin fields:
          - plugin.id → extension.id
          - plugin.channels → extension.channels
          - plugin.providers → extension.providers
          - plugin.skills → extension.skills
          - plugin.configSchema → extension.config_schema
          - package.version → extension.version
          - package.description → extension.description
        """
        pkg = package_data or {}

        kind = ExtensionKind.GENERAL
        raw_kind = plugin_data.get("kind")
        if raw_kind == "memory":
            kind = ExtensionKind.MEMORY

        return cls(
            id=plugin_data.get("id", pkg.get("name", "unknown")),
            name=plugin_data.get("name", pkg.get("name", "")),
            version=plugin_data.get("version", pkg.get("version", "0.0.0")),
            description=plugin_data.get(
                "description", pkg.get("description", "")
            ),
            kind=kind,
            channels=plugin_data.get("channels", []),
            providers=plugin_data.get("providers", []),
            skills=plugin_data.get("skills", []),
            config_schema=plugin_data.get("configSchema", {}),
            ui_hints=plugin_data.get("uiHints", {}),
            origin=ExtensionOrigin.OPENCLAW,
            compatibility=ExtensionCompatibility(runtime=ToolRuntime.NODE),
        )
