"""Memory system for persistent agent memory.

This package was split from a single 1,598-line file into focused modules.
All public names are re-exported here so existing ``from pawbot.agent.memory import X``
imports continue to work unchanged.
"""

from __future__ import annotations

from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────
from pawbot.agent.memory._compat import (
    LINK_TYPES,
    MEMORY_TYPE_CONFIG,
    MEMORY_TYPES,
    _SAVE_MEMORY_TOOL,
    coerce_float as _coerce_float,
    coerce_int as _coerce_int,
    memory_text as _memory_text,
    to_config_dict as _to_config_dict,
)

# ── Provider ABC ─────────────────────────────────────────────────────────────
from pawbot.agent.memory.provider import MemoryProvider

# ── Backend stores ───────────────────────────────────────────────────────────
from pawbot.agent.memory.redis_store import RedisWorkingMemory
from pawbot.agent.memory.sqlite_store import SQLiteFactStore
from pawbot.agent.memory.chroma_store import ChromaEpisodeStore

# ── Router ───────────────────────────────────────────────────────────────────
from pawbot.agent.memory.router import MemoryRouter

# ── Classifier, linker, decay ────────────────────────────────────────────────
from pawbot.agent.memory.classifier import MemoryClassifier
from pawbot.agent.memory.linker import MemoryLinker
from pawbot.agent.memory.decay import MemoryDecayEngine

# ── MemoryStore + convenience functions ──────────────────────────────────────
from pawbot.agent.memory.consolidation import (
    MemoryStore,
    _get_default_router,
    _migrate_legacy_files,
    delete,
    list_all,
    load,
    memory_stats,
    save,
    search,
    update,
)

# Backward-compat alias used by memory_commands.py
get_memory_router = _get_default_router

__all__ = [
    # Constants
    "MEMORY_TYPES",
    "LINK_TYPES",
    "MEMORY_TYPE_CONFIG",
    "_SAVE_MEMORY_TOOL",
    # ABC
    "MemoryProvider",
    # Stores
    "RedisWorkingMemory",
    "SQLiteFactStore",
    "ChromaEpisodeStore",
    # Router
    "MemoryRouter",
    # Classifier / linker / decay
    "MemoryClassifier",
    "MemoryLinker",
    "MemoryDecayEngine",
    # Consolidation layer
    "MemoryStore",
    # Convenience functions
    "save",
    "load",
    "search",
    "update",
    "delete",
    "list_all",
    "memory_stats",
    "get_memory_router",
    # Internal helpers (kept for backward compat)
    "_to_config_dict",
    "_memory_text",
    "_coerce_float",
    "_coerce_int",
    "_get_default_router",
    "_migrate_legacy_files",
]
