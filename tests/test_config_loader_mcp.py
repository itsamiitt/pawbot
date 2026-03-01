"""Tests for MCP server config migration and persistence."""

from __future__ import annotations

import json
from pathlib import Path

from pawbot.config.loader import load_config, save_config
from pawbot.config.schema import Config, MCPServerConfig


def test_load_config_accepts_top_level_mcp_servers(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "mcp_servers": {
                    "server_control": {
                        "path": "~/.pawbot/mcp-servers/server_control/server.py",
                        "enabled": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert "server_control" in config.tools.mcp_servers
    assert config.tools.mcp_servers["server_control"].path.endswith("server_control/server.py")


def test_save_config_writes_top_level_mcp_servers(tmp_path: Path):
    config = Config()
    config.tools.mcp_servers["server_control"] = MCPServerConfig(
        path="~/.pawbot/mcp-servers/server_control/server.py",
        enabled=True,
    )
    config_path = tmp_path / "saved_config.json"
    save_config(config, config_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "mcp_servers" in saved
    assert "server_control" in saved["mcp_servers"]
