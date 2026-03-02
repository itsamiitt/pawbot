"""Configuration loading utilities."""

import logging
from pathlib import Path

from pawbot.config.schema import Config

logger = logging.getLogger("pawbot.config")


def get_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.home() / ".pawbot" / "config.json"


def get_data_dir() -> Path:
    """Get the pawbot data directory."""
    from pawbot.utils.helpers import get_data_path
    return get_data_path()


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
        return Config()

    try:
        data = _migrate_config(data)
        return Config.model_validate(data)
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
