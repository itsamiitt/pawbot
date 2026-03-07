"""Shared helpers for the memory sub-package.

Kept internal (prefixed with underscore) — nothing here is part of the public API.
"""

from __future__ import annotations

import json
import time
from difflib import SequenceMatcher
from typing import Any


# ── Constants ────────────────────────────────────────────────────────────────

MEMORY_TYPES = [
    "fact",
    "preference",
    "decision",
    "episode",
    "reflection",
    "procedure",
    "task",
    "risk",
]

LINK_TYPES = ["caused_by", "supports", "contradicts", "extends", "resolves", "depends_on"]

MEMORY_TYPE_CONFIG = {
    "fact": {"half_life_days": 365, "min_salience": 0.1},
    "preference": {"half_life_days": 90, "min_salience": 0.2},
    "decision": {"half_life_days": 180, "min_salience": 0.3},
    "episode": {"half_life_days": 180, "min_salience": 0.2},
    "reflection": {"half_life_days": 999, "min_salience": 0.0},
    "procedure": {"half_life_days": 365, "min_salience": 0.1},
    "task": {"half_life_days": 30, "min_salience": 0.4},
    "risk": {"half_life_days": 365, "min_salience": 0.3},
}

_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph (2-5 sentences) summarizing key events/decisions/topics. "
                        "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                    },
                    "memory_update": {
                        "type": "string",
                        "description": "Full updated long-term memory as markdown. Include all existing "
                        "facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]


# ── Coercion / text helpers ──────────────────────────────────────────────────


def to_config_dict(config: dict[str, Any] | Any | None) -> dict[str, Any]:
    if config is None:
        return {}
    if isinstance(config, dict):
        return config
    if hasattr(config, "model_dump"):
        try:
            data = config.model_dump(by_alias=False)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    if hasattr(config, "dict"):
        try:
            data = config.dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def memory_text(content: Any) -> str:
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    if isinstance(content, str):
        return content
    return str(content)


def coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def relevance_score(query: str, text: str) -> float:
    """Compute a simple relevance score between *query* and *text*."""
    if not query:
        return 1.0
    ratio = SequenceMatcher(None, query.lower(), text.lower()).ratio()
    if query.lower() in text.lower():
        ratio = max(ratio, 0.95)
    return ratio
