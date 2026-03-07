"""Skill manifest schema — defines the formal skill.json format (Phase 9.1).

This Pydantic model defines the standard metadata for installable skill
packages. It coexists with the existing `Skill` dataclass in `agent/skills.py`
which handles runtime (agent-created) skills.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillTool(BaseModel):
    """Tool exported by a skill package."""

    name: str
    description: str = ""
    function: str = ""            # Module path, e.g. "tools.search_products"
    risk_level: str = "low"       # low, caution, high, critical
    timeout: int = 60             # Seconds before timeout
    parameters: dict[str, Any] = Field(default_factory=dict)  # JSON Schema


class SkillManifest(BaseModel):
    """Skill package manifest (skill.json for installable packages).

    Example skill.json:
    {
        "name": "shopify-tools",
        "version": "1.0.0",
        "description": "Shopify admin tools for PawBot",
        "author": "PawBot Team",
        "tools": [
            {
                "name": "search_products",
                "description": "Search Shopify products by title or SKU",
                "function": "tools.search_products",
                "risk_level": "low"
            }
        ],
        "requires_api_key": true,
        "api_key_env_var": "SHOPIFY_API_KEY"
    }
    """

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
