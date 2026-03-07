"""
pawbot/contracts.py

Central contract module — the single source of truth for all shared types,
constants, enums, dataclasses, and utility functions used across Pawbot.

Every other module imports from here. Never redefine anything that exists here.

Usage:
    from pawbot.contracts import *   # gives you everything
    from pawbot.contracts import InboundMessage, ChannelType, config, get_logger
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

# ── Section 0.1: Path Constants ──────────────────────────────────────────────
# Canonical paths live in utils.paths (Path objects). For backward compat these
# are re-exported as str values.
from pawbot.utils.paths import (  # noqa: E402
    PAWBOT_HOME as _PAWBOT_HOME,
    CONFIG_PATH as _CONFIG_PATH,
    WORKSPACE_PATH as _WORKSPACE_PATH,
    SOUL_PATH as _SOUL_PATH,
    SKILLS_PATH as _SKILLS_PATH,
    CHROMA_PATH as _CHROMA_PATH,
)

PAWBOT_HOME = str(_PAWBOT_HOME)
SQLITE_DB = str(_PAWBOT_HOME / "pawbot.db")
CHROMA_DIR = str(_CHROMA_PATH)
CONFIG_FILE = str(_CONFIG_PATH)
SOUL_MD = str(_SOUL_PATH)
CUSTOM_SKILLS_DIR = str(_SKILLS_PATH / "custom")
WORKSPACE_DIR = str(_WORKSPACE_PATH)

# ── Section 0.3: Enums ──────────────────────────────────────────────────────


class ChannelType(str, Enum):
    """Channel types for message routing."""
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    DISCORD = "discord"
    SLACK = "slack"
    EMAIL = "email"
    API = "api"
    CLI = "cli"
    FEISHU = "feishu"
    DINGTALK = "dingtalk"
    MOCHAT = "mochat"
    QQ = "qq"
    MATRIX = "matrix"
    SYSTEM = "system"


class ProviderName(str, Enum):
    """LLM provider identifiers."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    DEEPSEEK = "deepseek"
    GROQ = "groq"


class TaskType(str, Enum):
    """Task type identifiers for model routing."""
    GENERAL = "general"
    CODING_TASK = "coding_task"
    MEMORY_TASK = "memory_task"
    MEMORY_SAVE = "memory_save"
    MEMORY_SEARCH = "memory_search"
    ARCHITECTURE = "architecture"
    REASONING = "reasoning"
    DEPLOYMENT = "deployment"


# ── Section 0.4: Dataclasses ────────────────────────────────────────────────

# Re-export existing InboundMessage and OutboundMessage
from pawbot.bus.events import InboundMessage, OutboundMessage  # noqa: E402, F401


@dataclass
class LLMRequest:
    """Request dataclass for model router calls."""
    messages: list[dict[str, Any]]
    model: str = "llama3.1:8b"
    provider: ProviderName = ProviderName.OLLAMA
    max_tokens: int = 4096
    temperature: float = 0.1
    task_type: TaskType = TaskType.GENERAL


@dataclass
class LLMResponse:
    """Response dataclass from model router calls."""
    content: str = ""
    model: str = ""
    provider: str = ""
    tokens_used: int = 0
    finish_reason: str = "stop"


# ── Section 0.11: Utility Functions ─────────────────────────────────────────

# Priority keywords for message routing
PRIORITY_KEYWORDS = {"urgent", "emergency", "critical", "asap", "help", "broken", "down"}


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Uses standard logging module for compatibility."""
    return logging.getLogger(name)


def now() -> int:
    """Return current Unix timestamp as int."""
    return int(time.time())


def new_id() -> str:
    """Return a new UUID4 string."""
    return str(uuid.uuid4())


def config():
    """
    Return the loaded Pawbot Config object.
    Lazy-loads on first call and caches the result.
    Supports dot-notation-like access via .get() method.
    """
    if not hasattr(config, "_cached"):
        from pawbot.config.loader import load_config
        config._cached = _ConfigWrapper(load_config())
    return config._cached


class _ConfigWrapper:
    """
    Wraps the Config pydantic model to support dot-notation key access
    via .get(key, default) as used throughout the Section 1 document.

    Example: config().get("providers.anthropic.api_key", "")
    """

    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, key: str, default: Any = None) -> Any:
        """
        Access config values using dot-notation keys.
        Example: config().get("providers.ollama.base_url", "http://localhost:11434")
        """
        parts = key.split(".")
        obj = self._cfg
        for part in parts:
            if obj is None:
                return default
            if isinstance(obj, dict):
                obj = obj.get(part, None)
            elif isinstance(obj, list):
                try:
                    obj = obj[int(part)]
                except (ValueError, IndexError):
                    return default
            elif hasattr(obj, part):
                obj = getattr(obj, part)
            elif hasattr(obj.__class__, "model_fields"):
                matched = None
                for field_name, field_info in obj.__class__.model_fields.items():
                    if part == field_name or part == getattr(field_info, "alias", None):
                        matched = field_name
                        break
                if matched is None:
                    return default
                obj = getattr(obj, matched)
            else:
                return default
        if obj is None:
            return default
        return obj

    def __getattr__(self, name: str):
        """Proxy attribute access to the underlying config."""
        return getattr(self._cfg, name)


# ── __all__ ─────────────────────────────────────────────────────────────────

__all__ = [
    # Path constants
    "PAWBOT_HOME", "SQLITE_DB", "CHROMA_DIR", "CONFIG_FILE",
    "SOUL_MD", "CUSTOM_SKILLS_DIR", "WORKSPACE_DIR",
    # Enums
    "ChannelType", "ProviderName", "TaskType",
    # Dataclasses
    "InboundMessage", "OutboundMessage", "LLMRequest", "LLMResponse",
    # Utilities
    "get_logger", "now", "new_id", "config", "PRIORITY_KEYWORDS",
]
