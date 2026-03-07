"""Multi-backend memory router with dedup and link expansion."""

from __future__ import annotations

import copy
import hashlib
import uuid
from difflib import SequenceMatcher
from typing import Any

from loguru import logger

from pawbot.agent.memory._compat import (
    MEMORY_TYPES,
    coerce_float as _coerce_float,
    memory_text as _memory_text,
    to_config_dict,
)
from pawbot.agent.memory.chroma_store import ChromaEpisodeStore
from pawbot.agent.memory.provider import MemoryProvider
from pawbot.agent.memory.redis_store import RedisWorkingMemory
from pawbot.agent.memory.sqlite_store import SQLiteFactStore


class MemoryRouter(MemoryProvider):
    def __init__(self, session_id: str, config: dict[str, Any] | Any):
        self.session_id = session_id
        self.config = to_config_dict(config)
        backends_cfg = self.config.get("memory", {}).get("backends", {})
        self.redis: RedisWorkingMemory | None = None
        self.sqlite: SQLiteFactStore | None = None
        self.chroma: ChromaEpisodeStore | None = None

        if backends_cfg.get("redis", {}).get("enabled", True):
            try:
                self.redis = RedisWorkingMemory(session_id, self.config)
            except Exception:
                logger.info("Redis unavailable — using in-memory cache for working memory")
                from pawbot.agent.memory.local_cache import LocalCacheMemory
                self.redis = LocalCacheMemory(session_id)

        if backends_cfg.get("sqlite", {}).get("enabled", True):
            try:
                self.sqlite = SQLiteFactStore(self.config)
            except Exception as e:  # noqa: F841
                logger.exception("SQLite backend init failed")

        if backends_cfg.get("chroma", {}).get("enabled", True):
            try:
                self.chroma = ChromaEpisodeStore(self.config)
            except Exception as e:  # noqa: F841
                logger.exception("Chroma backend init failed")

        if self.sqlite is None:
            logger.warning("SQLite disabled/unavailable, creating emergency SQLiteFactStore")
            self.sqlite = SQLiteFactStore({})

        from pawbot.agent.memory.linker import MemoryLinker
        self.linker = MemoryLinker(self)

    @staticmethod
    def _combined_score(item: dict[str, Any]) -> float:
        relevance = _coerce_float(item.get("relevance_score", 0.0), 0.0)
        salience = _coerce_float(item.get("salience", 1.0), 1.0)
        weight = _coerce_float(item.get("relevance_weight", 1.0), 1.0)
        return (0.6 * relevance + 0.4 * salience) * weight

    @staticmethod
    def _sim(a: str, b: str) -> float:
        if not a and not b:
            return 1.0
        return SequenceMatcher(None, a, b).ratio()

    def _dedupe(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate memory results using content fingerprinting.

        Uses MD5 fingerprints of normalized text for O(n) deduplication,
        with a secondary similarity check only for the last 20 items.
        """
        deduped: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()

        for item in sorted(rows, key=self._combined_score, reverse=True):
            text = _memory_text(item.get("content", {})).lower().strip()
            if not text:
                continue

            # Fast path: exact or near-exact match via hash
            normalized = " ".join(text.split())
            content_hash = hashlib.md5(normalized.encode()).hexdigest()

            if content_hash in seen_hashes:
                continue

            # Similarity check against recent items (limited to last 20)
            is_dup = False
            for existing in deduped[-20:]:
                existing_text = _memory_text(existing.get("content", {})).lower().strip()
                # Quick length check first
                if abs(len(text) - len(existing_text)) > max(len(text), len(existing_text)) * 0.5:
                    continue
                if self._sim(text, existing_text) > 0.95:
                    is_dup = True
                    # Replace if higher salience
                    if _coerce_float(item.get("salience", 0), 0) > _coerce_float(existing.get("salience", 0), 0):
                        idx = deduped.index(existing)
                        deduped[idx] = item
                    break

            if not is_dup:
                seen_hashes.add(content_hash)
                deduped.append(item)

        return deduped

    def _fts_search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Full-text search fallback using SQLite FTS5 (when ChromaDB unavailable)."""
        if self.sqlite is None:
            return []
        try:
            conn = self.sqlite._connect()
            cursor = conn.execute(
                "SELECT f.* FROM facts f "
                "JOIN facts_fts fts ON f.rowid = fts.rowid "
                "WHERE facts_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?",
                (query, limit),
            )
            results = [self.sqlite._row_to_memory(row, query) for row in cursor.fetchall()]
            conn.close()
            return results
        except Exception:
            logger.debug("FTS5 search failed — table may not exist yet")
            return []

    def _expand_linked(self, direct: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.sqlite is None:
            return []
        linked_rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in direct:
            memory_id = item.get("id")
            if not memory_id:
                continue
            for link in self.sqlite.get_links(memory_id):
                linked_id = link["to_id"] if link["from_id"] == memory_id else link["from_id"]
                if linked_id in seen:
                    continue
                linked = self.sqlite.load_by_id(linked_id)
                if not linked:
                    continue
                linked["relevance_weight"] = 0.7
                linked_rows.append(linked)
                seen.add(linked_id)
        return linked_rows

    def save(self, type: str, content: dict[str, Any]) -> str:
        payload = dict(content)
        memory_id = str(uuid.uuid4())
        payload["_memory_id"] = memory_id
        primary_id = memory_id

        persistent_types = {"fact", "preference", "decision", "reflection", "procedure", "task", "risk"}

        if type == "episode":
            if self.chroma is not None:
                try:
                    primary_id = self.chroma.save(type, copy.deepcopy(payload))
                except Exception as e:  # noqa: F841
                    logger.exception("Episode save to chroma failed")
            if self.sqlite is not None:
                try:
                    self.sqlite.save(type, copy.deepcopy(payload))
                except Exception as e:  # noqa: F841
                    logger.exception("Episode save to sqlite failed")
        elif type in persistent_types:
            if self.sqlite is not None:
                primary_id = self.sqlite.save(type, copy.deepcopy(payload))
        elif type in {"working", "message"}:
            if self.redis is not None:
                try:
                    primary_id = self.redis.save(type, copy.deepcopy(payload))
                except Exception as e:  # noqa: F841
                    logger.exception("Working memory save to redis failed")
                    if self.sqlite is not None:
                        primary_id = self.sqlite.save(type, copy.deepcopy(payload))
            elif self.sqlite is not None:
                primary_id = self.sqlite.save(type, copy.deepcopy(payload))
        else:
            if self.sqlite is not None:
                primary_id = self.sqlite.save(type, copy.deepcopy(payload))

        if type in set(MEMORY_TYPES):
            try:
                payload.pop("_memory_id", None)
                self.linker.link_async(primary_id, payload)
            except Exception as e:  # noqa: F841
                logger.exception("Memory linker dispatch failed")

        return primary_id

    def _gather(
        self, method: str, query: str, limit: int, memory_type: str | None = None
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for backend in (self.redis, self.sqlite, self.chroma):
            if backend is None:
                continue
            fn = getattr(backend, method)
            try:
                rows.extend(fn(query=query, limit=limit, memory_type=memory_type))
            except TypeError:
                rows.extend(fn(query=query, limit=limit))
            except Exception as e:  # noqa: F841
                logger.exception("Backend {} {} failed", backend.__class__.__name__, method)
        return rows

    def load(self, query: str, limit: int = 10, memory_type: str | None = None) -> list[dict[str, Any]]:
        rows = self._gather("load", query, max(limit * 2, 10), memory_type=memory_type)
        rows.extend(self._expand_linked(rows))
        rows = self._dedupe(rows)
        rows.sort(key=self._combined_score, reverse=True)
        return rows[:limit]

    def search(
        self, query: str, limit: int = 5, memory_type: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self._gather("search", query, max(limit * 2, 10), memory_type=memory_type)
        rows = self._dedupe(rows)
        rows.sort(key=self._combined_score, reverse=True)
        return rows[:limit]

    def delete(self, memory_id: str) -> bool:
        ok = False
        for backend in (self.redis, self.chroma, self.sqlite):
            if backend is None:
                continue
            try:
                ok = backend.delete(memory_id) or ok
            except Exception as e:  # noqa: F841
                logger.exception("Backend {} delete failed", backend.__class__.__name__)
        return ok

    def update(self, memory_id: str, content: dict[str, Any]) -> bool:
        ok = False
        for backend in (self.redis, self.chroma, self.sqlite):
            if backend is None:
                continue
            try:
                ok = backend.update(memory_id, content) or ok
            except Exception as e:  # noqa: F841
                logger.exception("Backend {} update failed", backend.__class__.__name__)
        return ok

    def list_all(self, type: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for backend in (self.redis, self.sqlite, self.chroma):
            if backend is None:
                continue
            try:
                rows.extend(backend.list_all(type))
            except Exception as e:  # noqa: F841
                logger.exception("Backend {} list_all failed", backend.__class__.__name__)
        rows = self._dedupe(rows)
        rows.sort(key=self._combined_score, reverse=True)
        return rows

    def decay_pass(self) -> int:
        if self.sqlite is None:
            return 0
        from pawbot.agent.memory.decay import MemoryDecayEngine
        return MemoryDecayEngine(self.sqlite).decay_pass()
