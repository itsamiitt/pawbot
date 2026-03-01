"""Configuration module for pawbot."""

from pawbot.config.loader import get_config_path, load_config
from pawbot.config.schema import Config

__all__ = ["Config", "load_config", "get_config_path"]
