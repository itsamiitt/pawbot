"""In-memory TTL cache — drop-in replacement for Redis in development.

Phase 2: Used as automatic fallback when Redis is unavailable.
Data is lost on process restart — this is by design for working memory.
"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

from pawbot.agent.memory.provider import MemoryProvider

try:
    from cachetools import TTLCache
except ImportError:
    # Minimal TTLCache fallback if cachetools not installed
    class TTLCache(dict):  # type: ignore[no-redef]
        def __init__(self, maxsize: int = 1000, ttl: int = 3600):
            super().__init__()
            self._maxsize = maxsize


class LocalCacheMemory(MemoryProvider):
    """In-memory working memory using TTLCache.

    Used as automatic fallback when Redis is unavailable.
    Data is lost on process restart — this is by design for working memory.
    """

    def __init__(self, session_id: str, maxsize: int = 1000, ttl: int = 3600):
        self.session_id = session_id
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        logger.debug("LocalCacheMemory initialized (session={})", session_id)

    def save(self, type: str, content: dict[str, Any]) -> str:
        payload = dict(content)
        memory_id = payload.pop("_memory_id", str(uuid.uuid4()))
        self._cache[memory_id] = {
            "id": memory_id,
            "type": type,
            "content": payload,
            "session_id": self.session_id,
        }
        return memory_id

    def load(self, query: str, limit: int = 10, memory_type: str | None = None) -> list[dict[str, Any]]:
        results = []
        for key, item in list(self._cache.items()):
            if memory_type and item.get("type") != memory_type:
                continue
            if query.lower() in str(item.get("content", "")).lower():
                results.append(item)
            if len(results) >= limit:
                break
        return results

    def search(self, query: str, limit: int = 5, memory_type: str | None = None) -> list[dict[str, Any]]:
        return self.load(query, limit, memory_type)

    def delete(self, memory_id: str) -> bool:
        return self._cache.pop(memory_id, None) is not None

    def update(self, memory_id: str, content: dict[str, Any]) -> bool:
        if memory_id in self._cache:
            self._cache[memory_id]["content"] = content
            return True
        return False

    def list_all(self, type: str) -> list[dict[str, Any]]:
        return [v for v in self._cache.values() if v.get("type") == type]

    def decay_pass(self) -> int:
        """No-op for in-memory cache — TTL handles expiry."""
        return 0
