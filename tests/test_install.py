"""Install verification tests."""
import subprocess
import sys

import pytest


class TestInstall:
    def test_pyproject_toml_valid(self):
        import tomllib
        from pathlib import Path
        toml_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(toml_path, "rb") as f:
            d = tomllib.load(f)
        proj = d["project"]
        assert proj["name"] == "pawbot-ai"
        assert proj.get("version")
        assert ">=3.11" in proj.get("requires-python", "")
        assert "pawbot" in proj.get("scripts", {})

    def test_errors_importable(self):
        from pawbot.errors import PawbotError, ConfigError, ProviderError
        assert PawbotError
        assert ConfigError
        assert ProviderError

    def test_utils_importable(self):
        from pawbot.utils.fs import atomic_write_json, safe_read_json
        from pawbot.utils.secrets import is_placeholder, mask_secret
        from pawbot.utils.retry import call_with_retry
        from pawbot.utils.logging_setup import setup_logging
        assert callable(atomic_write_json)
        assert callable(safe_read_json)
        assert callable(is_placeholder)
        assert callable(mask_secret)
        assert callable(call_with_retry)
        assert callable(setup_logging)

    def test_config_loader_importable(self):
        from pawbot.config.loader import load_config
        assert callable(load_config)

    def test_router_importable(self):
        from pawbot.providers.router import ModelRouter
        assert ModelRouter

    def test_cli_importable(self):
        from pawbot.cli.commands import app
        assert app
