"""Configuration loading utilities."""

import logging
from pathlib import Path

from pawbot.config.schema import Config

logger = logging.getLogger("pawbot.config")

# ── Built-in MCP servers that ship with pawbot ──────────────────────────────
# These are auto-registered if not already in the user's config, ensuring
# features like screen control, browser automation, etc. always work.

_BUILTIN_MCP_SERVERS = {
    "app_control": {
        "path": "~/.pawbot/mcp-servers/app_control/server.py",
        "tool_timeout": 30,
        "enabled": True,
    },
    "browser": {
        "path": "~/.pawbot/mcp-servers/browser/server.py",
        "tool_timeout": 60,
        "enabled": True,
    },
    "coding": {
        "path": "~/.pawbot/mcp-servers/coding/server.py",
        "tool_timeout": 30,
        "enabled": True,
    },
    "deploy": {
        "path": "~/.pawbot/mcp-servers/deploy/server.py",
        "tool_timeout": 60,
        "enabled": True,
    },
    "server_control": {
        "path": "~/.pawbot/mcp-servers/server_control/server.py",
        "tool_timeout": 30,
        "enabled": True,
    },
}


def get_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.home() / ".pawbot" / "config.json"


def get_data_dir() -> Path:
    """Get the pawbot data directory."""
    from pawbot.utils.helpers import get_data_path
    return get_data_path()


def _inject_builtin_mcp_servers(config: Config) -> Config:
    """Auto-register built-in MCP servers if not already configured.

    This ensures that screen control, browser, coding, deploy, and server
    management tools are always available without requiring manual config.
    Only adds servers whose script files actually exist on disk.
    """
    import os

    existing = config.tools.mcp_servers
    added: list[str] = []

    for name, defaults in _BUILTIN_MCP_SERVERS.items():
        if name in existing:
            continue  # User already configured this server

        script_path = os.path.expanduser(defaults["path"])
        if not os.path.isfile(script_path):
            continue  # Script not installed — skip silently

        from pawbot.config.schema import MCPServerConfig
        existing[name] = MCPServerConfig(**defaults)
        added.append(name)

    if added:
        logger.info("Auto-registered built-in MCP servers: %s", ", ".join(added))

    return config


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    from pawbot.utils.fs import safe_read_json

    path = config_path or get_config_path()
    data = safe_read_json(path, default={})

    if not data:
        logger.warning("config.json is missing or empty — using defaults. Run: pawbot onboard")
        config = Config()
        return _inject_builtin_mcp_servers(config)

    try:
        data = _migrate_config(data)
        config = Config.model_validate(data)
        return _inject_builtin_mcp_servers(config)
    except ValueError as e:
        logger.error(f"config.json validation failed: {e}. Returning defaults.")
        return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True)
    tools_cfg = data.get("tools", {})
    if isinstance(tools_cfg, dict):
        mcp_cfg = tools_cfg.get("mcpServers", tools_cfg.get("mcp_servers"))
        if isinstance(mcp_cfg, dict) and mcp_cfg:
            # Keep canonical top-level key for MCP server registrations.
            data["mcp_servers"] = mcp_cfg

    from pawbot.utils.fs import write_json_with_backup
    write_json_with_backup(path, data)


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    agents = data.get("agents")
    if isinstance(agents, list):
        data["agents"] = {"list": agents}
    elif not isinstance(agents, dict):
        data["agents"] = {}
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    if not isinstance(tools, dict):
        tools = {}

    exec_cfg = tools.get("exec", {})
    if not isinstance(exec_cfg, dict):
        exec_cfg = {}

    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    root_mcp = data.pop("mcp_servers", None)
    if root_mcp is None:
        root_mcp = data.pop("mcpServers", None)
    tools_mcp = tools.get("mcp_servers", tools.get("mcpServers"))
    root_mcp = root_mcp if isinstance(root_mcp, dict) else {}
    tools_mcp = tools_mcp if isinstance(tools_mcp, dict) else {}
    if root_mcp or tools_mcp:
        tools["mcp_servers"] = {**tools_mcp, **root_mcp}

    data["tools"] = tools
    return data
