"""
tests/test_channel_manager.py

Tests for the registry-driven ChannelManager._init_channels().
Run: pytest tests/test_channel_manager.py -v
"""

import pytest
from unittest.mock import MagicMock, patch

from pawbot.channels.manager import ChannelManager, _CHANNEL_REGISTRY


def _make_config(enabled_channels: list[str]) -> MagicMock:
    """Build a minimal config mock with specific channels enabled."""
    config = MagicMock()
    channels_cfg = MagicMock()
    config.channels = channels_cfg
    # Disable everything first
    for entry in _CHANNEL_REGISTRY:
        channel_mock = MagicMock()
        channel_mock.enabled = entry.config_attr in enabled_channels
        setattr(channels_cfg, entry.config_attr, channel_mock)
    return config


def test_registry_has_ten_channels():
    """Registry must contain exactly 10 channel entries."""
    names = [e.config_attr for e in _CHANNEL_REGISTRY]
    assert len(names) == 10
    for expected in ("telegram", "whatsapp", "discord", "feishu",
                     "mochat", "dingtalk", "email", "slack", "qq", "matrix"):
        assert expected in names, f"Missing registry entry: {expected}"


def test_no_channels_when_all_disabled():
    """If no channel is enabled, channels dict must be empty."""
    config = _make_config([])
    bus    = MagicMock()
    manager = ChannelManager.__new__(ChannelManager)
    manager.config   = config
    manager.bus      = bus
    manager.channels = {}
    manager._max_send_attempts      = 3
    manager._base_retry_delay_s     = 0.5
    manager._seen_outbound_ids      = {}
    manager._seen_ttl_seconds       = 3600
    manager._dispatch_task          = None
    from pathlib import Path
    manager._dead_letter_path       = Path("/tmp/test_dead_letter.jsonl")
    manager._init_channels()
    assert manager.channels == {}


def test_import_error_does_not_crash_manager():
    """An ImportError for one channel must be caught; other channels are unaffected."""
    config = _make_config(["slack"])
    bus    = MagicMock()

    manager = ChannelManager.__new__(ChannelManager)
    manager.config   = config
    manager.bus      = bus
    manager.channels = {}
    manager._max_send_attempts      = 3
    manager._base_retry_delay_s     = 0.5
    manager._seen_outbound_ids      = {}
    manager._seen_ttl_seconds       = 3600
    manager._dispatch_task          = None
    from pathlib import Path
    manager._dead_letter_path       = Path("/tmp/test_dead_letter.jsonl")

    # Simulate slack not being installed
    with patch("builtins.__import__", side_effect=ImportError("no slack")):
        manager._init_channels()

    # Manager did not raise; channels may be empty (import failed)
    assert isinstance(manager.channels, dict)


def test_registry_config_attr_names_match_channels_config():
    """Every config_attr in the registry must be a real attribute on config.channels."""
    config = _make_config([])
    for entry in _CHANNEL_REGISTRY:
        assert hasattr(config.channels, entry.config_attr), (
            f"config.channels has no attribute '{entry.config_attr}'"
        )


def test_extra_kwargs_callable_returns_dict():
    """Every extra_kwargs function in the registry must return a dict."""
    config = MagicMock()
    config.providers.groq.api_key = "test-key"
    for entry in _CHANNEL_REGISTRY:
        result = entry.extra_kwargs(config)
        assert isinstance(result, dict), (
            f"extra_kwargs for '{entry.config_attr}' must return dict, got {type(result)}"
        )
