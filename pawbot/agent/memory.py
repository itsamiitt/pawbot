"""Memory system for persistent agent memory."""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from pawbot.utils.helpers import ensure_dir

try:
    import redis  # type: ignore[import-not-found]
except Exception:
    redis = None

try:
    import chromadb  # type: ignore[import-not-found]
    from chromadb.utils import embedding_functions
except Exception:
    chromadb = None
    embedding_functions = None

if TYPE_CHECKING:
    from pawbot.providers.base import LLMProvider
    from pawbot.session.manager import Session


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


def _to_config_dict(config: dict[str, Any] | Any | None) -> dict[str, Any]:
    if config is None:
        return {}
    if isinstance(config, dict):
        return config
    if hasattr(config, "model_dump"):
        try:
            data = config.model_dump(by_alias=False)
            if isinstance(data, dict):
                return data
        except Exception as e:  # noqa: F841
            pass
    if hasattr(config, "dict"):
        try:
            data = config.dict()
            if isinstance(data, dict):
                return data
        except Exception as e:  # noqa: F841
            pass
    return {}


def _memory_text(content: Any) -> str:
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    if isinstance(content, str):
        return content
    return str(content)


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception as e:  # noqa: F841
        return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception as e:  # noqa: F841
        return default


class MemoryProvider(ABC):
    @abstractmethod
    def save(self, type: str, content: dict[str, Any]) -> str:
        """Save a memory and return its ID."""

    @abstractmethod
    def load(self, query: str, limit: int = 10, memory_type: str | None = None) -> list[dict[str, Any]]:
        """Load memories relevant to query."""

    @abstractmethod
    def search(
        self, query: str, limit: int = 5, memory_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Semantic or lexical search."""

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        """Archive/remove memory by ID."""

    @abstractmethod
    def update(self, memory_id: str, content: dict[str, Any]) -> bool:
        """Update memory content."""

    @abstractmethod
    def list_all(self, type: str) -> list[dict[str, Any]]:
        """List all memories of a given type."""

    @abstractmethod
    def decay_pass(self) -> int:
        """Run decay and return archived count."""


class RedisWorkingMemory(MemoryProvider):
    KEY_PREFIX = "session"
    DEFAULT_TTL = 3600
    MAX_MESSAGES = 20

    def __init__(self, session_id: str, config: dict[str, Any] | Any):
        cfg = _to_config_dict(config)
        redis_cfg = cfg.get("memory", {}).get("backends", {}).get("redis", {})
        host = redis_cfg.get("host", "localhost")
        port = _coerce_int(redis_cfg.get("port", 6379), 6379)
        db = _coerce_int(redis_cfg.get("db", 0), 0)
        self.ttl = _coerce_int(redis_cfg.get("ttl", self.DEFAULT_TTL), self.DEFAULT_TTL)
        self.session_id = session_id
        self._fallback: dict[str, dict[str, Any]] = {}
        self._fallback_expiry: dict[str, float] = {}
        self._fallback_messages: list[str] = []
        self._use_fallback = False
        self.client = None

        if redis is None:
            self._use_fallback = True
            logger.warning("Redis unavailable (import error), using in-memory fallback")
            return

        try:
            self.client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
            self.client.ping()
        except Exception as exc:
            logger.warning("Redis unavailable ({}), using in-memory fallback", exc)
            self._use_fallback = True

    def _key(self, suffix: str) -> str:
        return f"{self.KEY_PREFIX}:{self.session_id}:{suffix}"

    def _prune_fallback(self) -> None:
        now = time.time()
        expired = [mid for mid, ts in self._fallback_expiry.items() if ts <= now]
        if not expired:
            return
        for memory_id in expired:
            self._fallback.pop(memory_id, None)
            self._fallback_expiry.pop(memory_id, None)
            while memory_id in self._fallback_messages:
                self._fallback_messages.remove(memory_id)

    def _touch_fallback(self, memory_id: str) -> None:
        if memory_id not in self._fallback:
            return
        now = int(time.time())
        self._fallback[memory_id]["last_accessed"] = now
        self._fallback_expiry[memory_id] = time.time() + self.ttl

    def _touch_redis(self, memory_id: str) -> None:
        if self.client is None:
            return
        now = int(time.time())
        key = self._key(memory_id)
        self.client.hset(key, mapping={"last_accessed": str(now), "updated_at": str(now)})
        self.client.expire(key, self.ttl)

    def _to_result(self, memory_id: str, payload: dict[str, Any], query: str) -> dict[str, Any]:
        text = _memory_text(payload.get("content", {}))
        rel = 1.0 if not query else SequenceMatcher(None, query.lower(), text.lower()).ratio()
        if query and query.lower() in text.lower():
            rel = max(rel, 0.95)
        return {
            "id": memory_id,
            "type": payload.get("type", "working"),
            "content": payload.get("content", {}),
            "salience": _coerce_float(payload.get("salience", 1.0), 1.0),
            "created_at": _coerce_int(payload.get("created_at", int(time.time())), int(time.time())),
            "updated_at": _coerce_int(payload.get("updated_at", int(time.time())), int(time.time())),
            "last_accessed": _coerce_int(
                payload.get("last_accessed", int(time.time())), int(time.time())
            ),
            "relevance_score": rel,
        }

    def save(self, type: str, content: dict[str, Any]) -> str:
        payload = dict(content)
        memory_id = payload.pop("_memory_id", str(uuid.uuid4()))
        now = int(time.time())
        data = {
            "type": type,
            "content": payload,
            "salience": _coerce_float(payload.get("salience", 1.0), 1.0),
            "created_at": now,
            "updated_at": now,
            "last_accessed": now,
        }

        if self._use_fallback or self.client is None:
            self._prune_fallback()
            self._fallback[memory_id] = data
            self._fallback_expiry[memory_id] = time.time() + self.ttl
            if type == "message":
                self._fallback_messages.insert(0, memory_id)
                self._fallback_messages = self._fallback_messages[: self.MAX_MESSAGES]
            logger.info("Saved working memory in fallback store: {}", memory_id)
            return memory_id

        try:
            key = self._key(memory_id)
            self.client.hset(
                key,
                mapping={
                    "type": type,
                    "content": json.dumps(payload, ensure_ascii=False),
                    "salience": str(data["salience"]),
                    "created_at": str(now),
                    "updated_at": str(now),
                    "last_accessed": str(now),
                },
            )
            self.client.expire(key, self.ttl)
            if type == "message":
                msg_key = self._key("messages")
                self.client.lpush(msg_key, memory_id)
                self.client.ltrim(msg_key, 0, self.MAX_MESSAGES - 1)
                self.client.expire(msg_key, self.ttl)
            logger.info("Saved working memory in redis: {}", memory_id)
            return memory_id
        except Exception as exc:
            logger.warning("Redis save failed ({}), switching to fallback", exc)
            self._use_fallback = True
            return self.save(type, payload | {"_memory_id": memory_id})

    def _iter_fallback(self) -> list[tuple[str, dict[str, Any]]]:
        self._prune_fallback()
        return list(self._fallback.items())

    def _iter_redis(self) -> list[tuple[str, dict[str, Any]]]:
        if self.client is None:
            return []
        items: list[tuple[str, dict[str, Any]]] = []
        for key in self.client.keys(self._key("*")):
            if key.endswith(":messages"):
                continue
            raw = self.client.hgetall(key)
            if not raw:
                continue
            memory_id = key.rsplit(":", 1)[-1]
            try:
                content = json.loads(raw.get("content", "{}"))
            except Exception as e:  # noqa: F841
                content = {"text": raw.get("content", "")}
            items.append(
                (
                    memory_id,
                    {
                        "type": raw.get("type", "working"),
                        "content": content,
                        "salience": _coerce_float(raw.get("salience", 1.0), 1.0),
                        "created_at": _coerce_int(raw.get("created_at", int(time.time())), int(time.time())),
                        "updated_at": _coerce_int(raw.get("updated_at", int(time.time())), int(time.time())),
                        "last_accessed": _coerce_int(
                            raw.get("last_accessed", int(time.time())), int(time.time())
                        ),
                    },
                )
            )
        return items

    def load(self, query: str, limit: int = 10, memory_type: str | None = None) -> list[dict[str, Any]]:
        try:
            entries = self._iter_fallback() if self._use_fallback else self._iter_redis()
        except Exception as exc:
            logger.warning("Redis load failed ({}), using fallback", exc)
            self._use_fallback = True
            entries = self._iter_fallback()

        out: list[dict[str, Any]] = []
        for memory_id, payload in entries:
            if memory_type and payload.get("type") != memory_type:
                continue
            text = _memory_text(payload.get("content", {})).lower()
            if query and query.lower() not in text:
                continue
            out.append(self._to_result(memory_id, payload, query))
            if self._use_fallback:
                self._touch_fallback(memory_id)
            else:
                self._touch_redis(memory_id)

        out.sort(
            key=lambda x: (x.get("relevance_score", 0.0), x.get("last_accessed", 0), x.get("created_at", 0)),
            reverse=True,
        )
        return out[:limit]

    def search(
        self, query: str, limit: int = 5, memory_type: str | None = None
    ) -> list[dict[str, Any]]:
        return self.load(query=query, limit=limit, memory_type=memory_type)

    def delete(self, memory_id: str) -> bool:
        if self._use_fallback or self.client is None:
            self._prune_fallback()
            existed = memory_id in self._fallback
            self._fallback.pop(memory_id, None)
            self._fallback_expiry.pop(memory_id, None)
            while memory_id in self._fallback_messages:
                self._fallback_messages.remove(memory_id)
            return existed

        try:
            deleted = self.client.delete(self._key(memory_id)) > 0
            msg_key = self._key("messages")
            if hasattr(self.client, "lrem"):
                self.client.lrem(msg_key, 0, memory_id)
            return deleted
        except Exception as e:  # noqa: F841
            logger.exception("Redis delete failed for {}", memory_id)
            return False

    def update(self, memory_id: str, content: dict[str, Any]) -> bool:
        payload = dict(content)
        now = int(time.time())

        if self._use_fallback or self.client is None:
            self._prune_fallback()
            if memory_id not in self._fallback:
                return False
            existing = self._fallback[memory_id]
            existing_content = existing.get("content", {})
            merged = dict(existing_content) if isinstance(existing_content, dict) else {}
            merged.update(payload)
            existing["content"] = merged
            existing["salience"] = _coerce_float(payload.get("salience", existing.get("salience", 1.0)), 1.0)
            existing["updated_at"] = now
            self._touch_fallback(memory_id)
            return True

        try:
            key = self._key(memory_id)
            if not self.client.exists(key):
                return False
            raw = self.client.hgetall(key)
            try:
                existing = json.loads(raw.get("content", "{}"))
            except Exception as e:  # noqa: F841
                existing = {}
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(payload)
            self.client.hset(
                key,
                mapping={
                    "content": json.dumps(merged, ensure_ascii=False),
                    "updated_at": str(now),
                    "salience": str(_coerce_float(payload.get("salience", raw.get("salience", 1.0)), 1.0)),
                },
            )
            self._touch_redis(memory_id)
            return True
        except Exception as e:  # noqa: F841
            logger.exception("Redis update failed for {}", memory_id)
            return False

    def list_all(self, type: str) -> list[dict[str, Any]]:
        return self.load(query="", limit=10_000, memory_type=type)

    def decay_pass(self) -> int:
        return 0


class SQLiteFactStore(MemoryProvider):
    DB_PATH = os.path.expanduser("~/.pawbot/memory/facts.db")

    def __init__(self, config: dict[str, Any] | Any):
        cfg = _to_config_dict(config)
        sqlite_cfg = cfg.get("memory", {}).get("backends", {}).get("sqlite", {})
        self.db_path = os.path.expanduser(sqlite_cfg.get("path", self.DB_PATH))
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id            TEXT PRIMARY KEY,
                    type          TEXT NOT NULL,
                    content       TEXT NOT NULL,
                    salience      REAL DEFAULT 1.0,
                    created_at    INTEGER NOT NULL,
                    updated_at    INTEGER NOT NULL,
                    last_accessed INTEGER NOT NULL,
                    tags          TEXT DEFAULT '[]',
                    source        TEXT DEFAULT ''
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_type ON facts(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_salience ON facts(salience)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_created ON facts(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_accessed ON facts(last_accessed)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_links (
                    id         TEXT PRIMARY KEY,
                    from_id    TEXT NOT NULL,
                    to_id      TEXT NOT NULL,
                    link_type  TEXT NOT NULL,
                    strength   REAL DEFAULT 1.0,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(from_id) REFERENCES facts(id),
                    FOREIGN KEY(to_id)   REFERENCES facts(id)
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reflections (
                    id           TEXT PRIMARY KEY,
                    failure_type TEXT NOT NULL,
                    lesson       TEXT NOT NULL,
                    rule         TEXT NOT NULL,
                    applies_to   TEXT DEFAULT '[]',
                    confidence   REAL DEFAULT 0.8,
                    created_at   INTEGER NOT NULL,
                    times_used   INTEGER DEFAULT 0
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS procedures (
                    id            TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    triggers      TEXT NOT NULL,
                    steps         TEXT NOT NULL,
                    preconditions TEXT DEFAULT '[]',
                    avg_tokens    INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    last_used     INTEGER DEFAULT 0
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS archived_memories (
                    id             TEXT PRIMARY KEY,
                    original_table TEXT NOT NULL,
                    content        TEXT NOT NULL,
                    archived_at    INTEGER NOT NULL,
                    final_salience REAL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subagent_inbox (
                    id            TEXT PRIMARY KEY,
                    subagent_id   TEXT NOT NULL,
                    content       TEXT NOT NULL,
                    confidence    REAL NOT NULL,
                    proposed_type TEXT NOT NULL,
                    created_at    INTEGER NOT NULL,
                    reviewed      INTEGER DEFAULT 0,
                    accepted      INTEGER DEFAULT 0
                )
                """
            )

    def _save_with_conn(
        self,
        conn: sqlite3.Connection,
        type: str,
        content: dict[str, Any],
        memory_id: str | None = None,
    ) -> str:
        now = int(time.time())
        payload = dict(content)
        memory_id = memory_id or payload.pop("_memory_id", str(uuid.uuid4()))
        tags = payload.get("tags", [])
        if not isinstance(tags, list):
            tags = [str(tags)]
        source = payload.get("source", "")
        salience = _coerce_float(payload.get("salience", 1.0), 1.0)
        conn.execute(
            "INSERT INTO facts (id, type, content, salience, created_at, updated_at, last_accessed, tags, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                memory_id,
                type,
                json.dumps(payload, ensure_ascii=False),
                salience,
                now,
                now,
                now,
                json.dumps(tags, ensure_ascii=False),
                str(source),
            ),
        )

        if type == "reflection":
            conn.execute(
                "INSERT OR REPLACE INTO reflections "
                "(id, failure_type, lesson, rule, applies_to, confidence, created_at, times_used) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory_id,
                    str(payload.get("failure_type", payload.get("type", "unknown"))),
                    str(payload.get("lesson", payload.get("text", ""))),
                    str(payload.get("rule", payload.get("lesson", payload.get("text", "")))),
                    json.dumps(payload.get("applies_to", []), ensure_ascii=False),
                    _coerce_float(payload.get("confidence", 0.8), 0.8),
                    now,
                    _coerce_int(payload.get("times_used", 0), 0),
                ),
            )
        elif type == "procedure":
            name = str(payload.get("name", "procedure"))
            triggers = payload.get("triggers", [])
            steps = payload.get("steps", [])
            if not isinstance(triggers, list):
                triggers = [str(triggers)]
            if not isinstance(steps, list):
                steps = [str(steps)]
            conn.execute(
                "INSERT OR REPLACE INTO procedures "
                "(id, name, triggers, steps, preconditions, avg_tokens, success_count, last_used) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory_id,
                    name,
                    json.dumps(triggers, ensure_ascii=False),
                    json.dumps(steps, ensure_ascii=False),
                    json.dumps(payload.get("preconditions", []), ensure_ascii=False),
                    _coerce_int(payload.get("avg_tokens", 0), 0),
                    _coerce_int(payload.get("success_count", 0), 0),
                    _coerce_int(payload.get("last_used", 0), 0),
                ),
            )
        logger.info("Saved sqlite memory: {} ({})", memory_id, type)
        return memory_id

    def save(self, type: str, content: dict[str, Any]) -> str:
        with self._connect() as conn:
            return self._save_with_conn(conn, type, content)

    def _row_to_memory(self, row: sqlite3.Row, query: str = "") -> dict[str, Any]:
        try:
            payload = json.loads(row["content"])
        except Exception as e:  # noqa: F841
            payload = {"text": row["content"]}
        text = _memory_text(payload)
        relevance = 1.0 if not query else SequenceMatcher(None, query.lower(), text.lower()).ratio()
        if query and query.lower() in text.lower():
            relevance = max(relevance, 0.95)
        try:
            tags = json.loads(row["tags"] or "[]")
        except Exception as e:  # noqa: F841
            tags = []
        return {
            "id": row["id"],
            "type": row["type"],
            "content": payload,
            "salience": _coerce_float(row["salience"], 1.0),
            "created_at": _coerce_int(row["created_at"], int(time.time())),
            "updated_at": _coerce_int(row["updated_at"], int(time.time())),
            "last_accessed": _coerce_int(row["last_accessed"], int(time.time())),
            "tags": tags,
            "source": row["source"] or "",
            "relevance_score": relevance,
        }

    def _touch_ids(self, conn: sqlite3.Connection, ids: list[str]) -> None:
        now = int(time.time())
        conn.executemany("UPDATE facts SET last_accessed = ? WHERE id = ?", [(now, mid) for mid in ids])

    def load(self, query: str, limit: int = 10, memory_type: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM facts"
        where = []
        params: list[Any] = []
        if memory_type:
            where.append("type = ?")
            params.append(memory_type)
        if query:
            where.append("LOWER(content) LIKE ?")
            params.append(f"%{query.lower()}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY salience DESC, created_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            out = [self._row_to_memory(row, query=query) for row in rows]
            if out:
                self._touch_ids(conn, [item["id"] for item in out])
            return out

    def search(
        self, query: str, limit: int = 5, memory_type: str | None = None
    ) -> list[dict[str, Any]]:
        return self.load(query=query, limit=limit, memory_type=memory_type)

    def load_by_id(self, memory_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE facts SET last_accessed = ? WHERE id = ?", (int(time.time()), memory_id))
            return self._row_to_memory(row)

    def _archive_fact(self, conn: sqlite3.Connection, row: sqlite3.Row, final_salience: float) -> str:
        archive_id = str(uuid.uuid4())
        payload = json.dumps({k: row[k] for k in row.keys()}, ensure_ascii=False)
        conn.execute(
            "INSERT INTO archived_memories (id, original_table, content, archived_at, final_salience) "
            "VALUES (?, ?, ?, ?, ?)",
            (archive_id, "facts", payload, int(time.time()), final_salience),
        )
        return archive_id

    def delete(self, memory_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                return False
            self._archive_fact(conn, row, _coerce_float(row["salience"], 1.0))
            conn.execute("DELETE FROM facts WHERE id = ?", (memory_id,))
            logger.info("Archived and removed memory from facts: {}", memory_id)
            return True

    def update(self, memory_id: str, content: dict[str, Any]) -> bool:
        payload = dict(content)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                return False
            try:
                existing = json.loads(row["content"])
            except Exception as e:  # noqa: F841
                existing = {}
            if not isinstance(existing, dict):
                existing = {"text": _memory_text(existing)}
            existing.update(payload)
            now = int(time.time())
            salience = _coerce_float(payload.get("salience", row["salience"]), 1.0)
            conn.execute(
                "UPDATE facts SET content = ?, salience = ?, updated_at = ? WHERE id = ?",
                (json.dumps(existing, ensure_ascii=False), salience, now, memory_id),
            )
            return True

    def list_all(self, type: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM facts WHERE type = ? ORDER BY created_at DESC", (type,)
            ).fetchall()
            out = [self._row_to_memory(row) for row in rows]
            if out:
                self._touch_ids(conn, [item["id"] for item in out])
            return out

    def get_links(self, memory_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, from_id, to_id, link_type, strength, created_at "
                "FROM memory_links WHERE from_id = ? OR to_id = ?",
                (memory_id, memory_id),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "from_id": row["from_id"],
                "to_id": row["to_id"],
                "link_type": row["link_type"],
                "strength": _coerce_float(row["strength"], 1.0),
                "created_at": _coerce_int(row["created_at"], int(time.time())),
            }
            for row in rows
        ]

    def save_link(self, from_id: str, to_id: str, link_type: str, strength: float = 0.8) -> str | None:
        if link_type not in LINK_TYPES:
            return None
        link_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO memory_links (id, from_id, to_id, link_type, strength, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (link_id, from_id, to_id, link_type, strength, int(time.time())),
            )
        return link_id

    def adjust_salience(self, memory_id: str, delta: float, contradicted_by: str | None = None) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT content, salience FROM facts WHERE id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                return False
            salience = max(0.0, _coerce_float(row["salience"], 1.0) + delta)
            try:
                content = json.loads(row["content"])
            except Exception as e:  # noqa: F841
                content = {}
            if not isinstance(content, dict):
                content = {"text": _memory_text(content)}
            if contradicted_by:
                content["contradicted_by"] = contradicted_by
            conn.execute(
                "UPDATE facts SET salience = ?, content = ?, updated_at = ? WHERE id = ?",
                (salience, json.dumps(content, ensure_ascii=False), int(time.time()), memory_id),
            )
            return True

    def inbox_write(
        self, subagent_id: str, content: dict[str, Any], confidence: float, proposed_type: str
    ) -> str:
        inbox_id = str(uuid.uuid4())
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO subagent_inbox "
                "(id, subagent_id, content, confidence, proposed_type, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (inbox_id, subagent_id, json.dumps(content, ensure_ascii=False), confidence, proposed_type, now),
            )
        return inbox_id

    def inbox_review(self) -> list[str]:
        now = int(time.time())
        accepted_ids: list[str] = []
        with self._connect() as conn:
            pending = conn.execute(
                "SELECT id, subagent_id, content, confidence, proposed_type, created_at "
                "FROM subagent_inbox WHERE reviewed = 0"
            ).fetchall()
            for row in pending:
                inbox_id = row["id"]
                confidence = _coerce_float(row["confidence"], 0.0)
                age_hours = (now - _coerce_int(row["created_at"], now)) / 3600

                should_accept = confidence > 0.9 or (0.6 <= confidence <= 0.9 and age_hours >= 24)
                should_discard = confidence < 0.6
                if should_accept:
                    try:
                        content = json.loads(row["content"])
                    except Exception as e:  # noqa: F841
                        content = {"text": row["content"]}
                    memory_id = self._save_with_conn(conn, row["proposed_type"], content)
                    accepted_ids.append(memory_id)
                    conn.execute(
                        "UPDATE subagent_inbox SET reviewed = 1, accepted = 1 WHERE id = ?", (inbox_id,)
                    )
                elif should_discard:
                    conn.execute(
                        "UPDATE subagent_inbox SET reviewed = 1, accepted = 0 WHERE id = ?", (inbox_id,)
                    )
        return accepted_ids

    def decay_pass(self) -> int:
        return MemoryDecayEngine(self).decay_pass()


class ChromaEpisodeStore(MemoryProvider):
    COLLECTION_NAME = "pawbot_episodes"
    _fallback_collections: dict[str, dict[str, dict[str, Any]]] = {}

    def __init__(self, config: dict[str, Any] | Any):
        cfg = _to_config_dict(config)
        chroma_cfg = cfg.get("memory", {}).get("backends", {}).get("chroma", {})
        self.persist_path = os.path.expanduser(chroma_cfg.get("path", "~/.pawbot/memory/chroma"))
        os.makedirs(self.persist_path, exist_ok=True)
        self.client = None
        self.collection = None
        self._use_fallback = False
        self._embedding_backend = "none"
        self._fallback = self._fallback_collections.setdefault(self.persist_path, {})

        if chromadb is None:
            self._use_fallback = True
            logger.warning("Chroma unavailable (import error), using fallback episode store")
            return

        try:
            self.client = chromadb.PersistentClient(path=self.persist_path)
            ef = None
            if embedding_functions is not None:
                try:
                    ef = embedding_functions.OllamaEmbeddingFunction(
                        url="http://localhost:11434/api/embeddings",
                        model_name="nomic-embed-text",
                    )
                    self._embedding_backend = "ollama"
                except Exception as e:  # noqa: F841
                    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                        model_name="all-MiniLM-L6-v2"
                    )
                    self._embedding_backend = "sentence-transformers"
            kwargs: dict[str, Any] = {
                "name": self.COLLECTION_NAME,
                "metadata": {"hnsw:space": "cosine"},
            }
            if ef is not None:
                kwargs["embedding_function"] = ef
            self.collection = self.client.get_or_create_collection(**kwargs)
        except Exception as exc:
            self._use_fallback = True
            logger.warning("Chroma unavailable ({}), using fallback episode store", exc)

    def save(self, type: str, content: dict[str, Any]) -> str:
        payload = dict(content)
        memory_id = payload.pop("_memory_id", str(uuid.uuid4()))
        now = int(time.time())
        text = _memory_text(payload)
        metadata = {
            "type": type,
            "timestamp": now,
            "goal_id": payload.get("goal_id", ""),
            "session_id": payload.get("session_id", ""),
            "salience": _coerce_float(payload.get("salience", 1.0), 1.0),
        }

        if self._use_fallback or self.collection is None:
            self._fallback[memory_id] = {"document": text, "metadata": metadata}
            logger.info("Saved episode in fallback store: {}", memory_id)
            return memory_id

        try:
            self.collection.add(ids=[memory_id], documents=[text], metadatas=[metadata])
            logger.info("Saved episode in chroma: {}", memory_id)
            return memory_id
        except Exception as exc:
            logger.warning("Chroma save failed ({}), switching to fallback", exc)
            self._use_fallback = True
            self._fallback[memory_id] = {"document": text, "metadata": metadata}
            return memory_id

    @staticmethod
    def _make_result(memory_id: str, doc: str, meta: dict[str, Any], query: str) -> dict[str, Any]:
        rel = 1.0 if not query else SequenceMatcher(None, query.lower(), doc.lower()).ratio()
        if query and query.lower() in doc.lower():
            rel = max(rel, 0.95)
        return {
            "id": memory_id,
            "type": meta.get("type", "episode"),
            "content": {"text": doc},
            "salience": _coerce_float(meta.get("salience", 1.0), 1.0),
            "created_at": _coerce_int(meta.get("timestamp", int(time.time())), int(time.time())),
            "updated_at": _coerce_int(meta.get("timestamp", int(time.time())), int(time.time())),
            "last_accessed": int(time.time()),
            "relevance_score": rel,
        }

    def load(self, query: str, limit: int = 10, memory_type: str | None = None) -> list[dict[str, Any]]:
        if query:
            return self.search(query=query, limit=limit, memory_type=memory_type)
        return self.list_all(memory_type or "episode")[:limit]

    def search(
        self, query: str, limit: int = 5, memory_type: str | None = None
    ) -> list[dict[str, Any]]:
        if self._use_fallback or self.collection is None:
            out = []
            for memory_id, item in self._fallback.items():
                meta = item.get("metadata", {})
                if memory_type and meta.get("type") != memory_type:
                    continue
                doc = item.get("document", "")
                if query and query.lower() not in doc.lower():
                    continue
                out.append(self._make_result(memory_id, doc, meta, query))
            out.sort(key=lambda x: x.get("relevance_score", 0.0), reverse=True)
            return out[:limit]

        try:
            result = self.collection.query(query_texts=[query], n_results=max(limit, 1))
            ids = result.get("ids", [[]])[0]
            docs = result.get("documents", [[]])[0]
            metas = result.get("metadatas", [[]])[0]
            distances = result.get("distances", [[]])[0] if "distances" in result else []
            out: list[dict[str, Any]] = []
            for i, memory_id in enumerate(ids):
                meta = metas[i] if i < len(metas) else {}
                if memory_type and meta.get("type") != memory_type:
                    continue
                doc = docs[i] if i < len(docs) else ""
                item = self._make_result(memory_id, doc, meta, query)
                if i < len(distances):
                    item["relevance_score"] = max(0.0, 1.0 - _coerce_float(distances[i], 1.0))
                out.append(item)
            out.sort(key=lambda x: x.get("relevance_score", 0.0), reverse=True)
            return out[:limit]
        except Exception as exc:
            logger.warning("Chroma search failed ({}), using fallback query path", exc)
            self._use_fallback = True
            return self.search(query=query, limit=limit, memory_type=memory_type)

    def delete(self, memory_id: str) -> bool:
        if self._use_fallback or self.collection is None:
            return self._fallback.pop(memory_id, None) is not None
        try:
            self.collection.delete(ids=[memory_id])
            return True
        except Exception as e:  # noqa: F841
            logger.exception("Chroma delete failed for {}", memory_id)
            return False

    def update(self, memory_id: str, content: dict[str, Any]) -> bool:
        if self._use_fallback or self.collection is None:
            if memory_id not in self._fallback:
                return False
            doc = _memory_text(content)
            meta = self._fallback[memory_id].get("metadata", {})
            if "salience" in content:
                meta["salience"] = _coerce_float(content.get("salience"), 1.0)
            self._fallback[memory_id] = {"document": doc, "metadata": meta}
            return True
        try:
            existing = self.collection.get(ids=[memory_id], include=["metadatas", "documents"])
            ids = existing.get("ids", [])
            if not ids:
                return False
            metas = existing.get("metadatas", [{}])
            meta = metas[0] if metas else {}
            if "salience" in content:
                meta["salience"] = _coerce_float(content.get("salience"), 1.0)
            self.collection.update(ids=[memory_id], documents=[_memory_text(content)], metadatas=[meta])
            return True
        except Exception as e:  # noqa: F841
            logger.exception("Chroma update failed for {}", memory_id)
            return False

    def list_all(self, type: str) -> list[dict[str, Any]]:
        if self._use_fallback or self.collection is None:
            out = []
            for memory_id, item in self._fallback.items():
                meta = item.get("metadata", {})
                if type and meta.get("type") != type:
                    continue
                out.append(self._make_result(memory_id, item.get("document", ""), meta, ""))
            out.sort(key=lambda x: x.get("created_at", 0), reverse=True)
            return out
        try:
            data = self.collection.get(include=["documents", "metadatas"])
            ids = data.get("ids", [])
            docs = data.get("documents", [])
            metas = data.get("metadatas", [])
            out = []
            for i, memory_id in enumerate(ids):
                meta = metas[i] if i < len(metas) else {}
                if type and meta.get("type") != type:
                    continue
                doc = docs[i] if i < len(docs) else ""
                out.append(self._make_result(memory_id, doc, meta, ""))
            out.sort(key=lambda x: x.get("created_at", 0), reverse=True)
            return out
        except Exception as e:  # noqa: F841
            logger.exception("Chroma list_all failed")
            return []

    def decay_pass(self) -> int:
        return 0


class MemoryRouter(MemoryProvider):
    def __init__(self, session_id: str, config: dict[str, Any] | Any):
        self.session_id = session_id
        self.config = _to_config_dict(config)
        backends_cfg = self.config.get("memory", {}).get("backends", {})
        self.redis: RedisWorkingMemory | None = None
        self.sqlite: SQLiteFactStore | None = None
        self.chroma: ChromaEpisodeStore | None = None

        if backends_cfg.get("redis", {}).get("enabled", True):
            try:
                self.redis = RedisWorkingMemory(session_id, self.config)
            except Exception as e:  # noqa: F841
                logger.exception("Redis backend init failed")

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
        deduped: list[dict[str, Any]] = []
        for item in sorted(rows, key=self._combined_score, reverse=True):
            text = _memory_text(item.get("content", {})).lower()
            replaced = False
            for idx, existing in enumerate(deduped):
                existing_text = _memory_text(existing.get("content", {})).lower()
                if self._sim(text, existing_text) > 0.95:
                    if _coerce_float(item.get("salience", 0.0), 0.0) > _coerce_float(
                        existing.get("salience", 0.0), 0.0
                    ):
                        deduped[idx] = item
                    replaced = True
                    break
            if not replaced:
                deduped.append(item)
        return deduped

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
        return MemoryDecayEngine(self.sqlite).decay_pass()


class MemoryClassifier:
    @staticmethod
    def calculate_salience(base_salience: float, type: str, created_at: int, last_accessed: int) -> float:
        cfg = MEMORY_TYPE_CONFIG.get(type, {"half_life_days": 180})
        half_life = _coerce_float(cfg.get("half_life_days", 180), 180.0)
        now = int(time.time())
        age_days = max(0.0, (now - created_at) / 86400)
        decayed = base_salience * (0.5 ** (age_days / half_life))
        days_since_access = max(0.0, (now - last_accessed) / 86400)
        if days_since_access <= 7:
            decayed = min(1.0, decayed + 0.2)
        return round(max(0.0, decayed), 4)

    @staticmethod
    def should_archive(salience: float, type: str) -> bool:
        if type == "reflection":
            return False
        min_sal = _coerce_float(MEMORY_TYPE_CONFIG.get(type, {}).get("min_salience", 0.1), 0.1)
        return salience < min_sal


class MemoryLinker:
    def __init__(self, router: MemoryRouter):
        self.router = router

    def link_async(self, new_memory_id: str, new_memory: dict[str, Any]) -> None:
        thread = threading.Thread(
            target=self._link_sync, args=(new_memory_id, dict(new_memory)), daemon=True
        )
        thread.start()

    def _candidate_ids(self, new_memory: dict[str, Any]) -> list[str]:
        text = _memory_text(new_memory)
        candidates: list[str] = []
        if self.router.chroma is not None:
            try:
                rows = self.router.chroma.search(text, limit=5)
                candidates.extend([row.get("id") for row in rows if row.get("id")])
            except Exception as e:  # noqa: F841
                logger.exception("Link candidate query via chroma failed")
        if not candidates and self.router.sqlite is not None:
            try:
                rows = self.router.sqlite.search(text, limit=5)
                candidates.extend([row.get("id") for row in rows if row.get("id")])
            except Exception as e:  # noqa: F841
                logger.exception("Link candidate query via sqlite failed")
        seen: set[str] = set()
        out: list[str] = []
        for item in candidates:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def _link_sync(self, new_memory_id: str, new_memory: dict[str, Any]) -> None:
        if self.router.sqlite is None:
            return
        try:
            for existing_id in self._candidate_ids(new_memory):
                if existing_id == new_memory_id:
                    continue
                existing = self.router.sqlite.load_by_id(existing_id)
                if not existing:
                    continue
                link_type = self._infer_link_type(new_memory, existing)
                if link_type is None:
                    continue
                if link_type not in LINK_TYPES:
                    continue
                self.router.sqlite.save_link(new_memory_id, existing_id, link_type, strength=0.8)
                if link_type == "contradicts":
                    self.router.sqlite.adjust_salience(existing_id, delta=-0.15, contradicted_by=new_memory_id)
        except Exception as exc:
            logger.warning("Memory linking failed: {}", exc)

    def _infer_link_type(self, new_mem: dict[str, Any], existing_mem: dict[str, Any]) -> str | None:
        # Stub for Phase 4 Ollama integration.
        return None


class MemoryDecayEngine:
    JOB_NAME = "memory_decay"
    CRON_SCHEDULE = "0 3 * * *"

    def __init__(self, sqlite: SQLiteFactStore):
        self.sqlite = sqlite

    def decay_pass(self) -> int:
        archived_count = 0
        updated_count = 0
        with self.sqlite._connect() as conn:
            rows = conn.execute(
                "SELECT id, type, content, salience, created_at, last_accessed, updated_at, tags, source "
                "FROM facts"
            ).fetchall()
            for row in rows:
                memory_id = row["id"]
                type_ = row["type"]
                base_sal = _coerce_float(row["salience"], 1.0)
                created_at = _coerce_int(row["created_at"], int(time.time()))
                last_accessed = _coerce_int(row["last_accessed"], created_at)

                new_sal = MemoryClassifier.calculate_salience(base_sal, type_, created_at, last_accessed)
                if MemoryClassifier.should_archive(new_sal, type_):
                    self.sqlite._archive_fact(conn, row, new_sal)
                    conn.execute("DELETE FROM facts WHERE id = ?", (memory_id,))
                    archived_count += 1
                elif abs(new_sal - base_sal) > 0.01:
                    conn.execute(
                        "UPDATE facts SET salience = ?, updated_at = ? WHERE id = ?",
                        (new_sal, int(time.time()), memory_id),
                    )
                    updated_count += 1
        logger.info(
            "Decay pass complete: archived={}, updated={}",
            archived_count,
            updated_count,
        )
        if archived_count > 100:
            logger.warning("Memory decay: {} memories archived", archived_count)
        return archived_count


_DEFAULT_ROUTER: MemoryRouter | None = None


def _get_default_router() -> MemoryRouter:
    global _DEFAULT_ROUTER
    if _DEFAULT_ROUTER is None:
        _DEFAULT_ROUTER = MemoryRouter("default-session", {})
    return _DEFAULT_ROUTER


def save(type: str, content: dict[str, Any]) -> str:
    return _get_default_router().save(type, content)


def load(query: str, limit: int = 10, type: str | None = None) -> list[dict[str, Any]]:
    return _get_default_router().load(query, limit=limit, memory_type=type)


def search(query: str, limit: int = 5, type: str | None = None) -> list[dict[str, Any]]:
    return _get_default_router().search(query, limit=limit, memory_type=type)


def update(memory_id: str, content: dict[str, Any]) -> bool:
    return _get_default_router().update(memory_id, content)


def delete(memory_id: str) -> bool:
    return _get_default_router().delete(memory_id)


def list_all(type: str) -> list[dict[str, Any]]:
    return _get_default_router().list_all(type)


def _migrate_legacy_files(router: MemoryRouter | None = None) -> None:
    router = router or _get_default_router()
    memory_md = os.path.expanduser("~/pawbot/workspace/MEMORY.md")
    history_md = os.path.expanduser("~/pawbot/workspace/HISTORY.md")
    migrated_flag = os.path.expanduser("~/.pawbot/memory/.migrated")
    os.makedirs(os.path.dirname(migrated_flag), exist_ok=True)
    if os.path.exists(migrated_flag):
        return

    if os.path.exists(memory_md):
        with open(memory_md, encoding="utf-8") as f:
            content = f.read()
        for line in content.splitlines():
            if line.strip():
                router.save("fact", {"text": line.strip(), "source": "MEMORY.md"})
        logger.info("Migrated MEMORY.md into SQLiteFactStore")

    if os.path.exists(history_md):
        with open(history_md, encoding="utf-8") as f:
            content = f.read()
        if content.strip():
            router.save("episode", {"text": content, "source": "HISTORY.md"})
            logger.info("Migrated HISTORY.md into ChromaEpisodeStore")

    with open(migrated_flag, "w", encoding="utf-8"):
        pass


def memory_stats(router: MemoryRouter | None = None) -> dict[str, Any]:
    router = router or _get_default_router()
    stats: dict[str, Any] = {
        "facts": 0,
        "episodes": 0,
        "reflections": 0,
        "procedures": 0,
        "archived": 0,
        "redis_keys": 0,
    }

    if router.sqlite is not None:
        with router.sqlite._connect() as conn:
            rows = conn.execute("SELECT type, COUNT(*) AS c FROM facts GROUP BY type").fetchall()
            for row in rows:
                type_name = row["type"]
                count = _coerce_int(row["c"], 0)
                key = "episodes" if type_name == "episode" else f"{type_name}s"
                stats[key] = count
                if type_name == "fact":
                    stats["facts"] = count
            stats["archived"] = _coerce_int(
                conn.execute("SELECT COUNT(*) AS c FROM archived_memories").fetchone()["c"], 0
            )

    if router.chroma is not None:
        try:
            if router.chroma._use_fallback or router.chroma.collection is None:
                stats["episodes"] = max(stats.get("episodes", 0), len(router.chroma._fallback))
            else:
                stats["episodes"] = max(stats.get("episodes", 0), _coerce_int(router.chroma.collection.count(), 0))
        except Exception as e:  # noqa: F841
            logger.exception("Failed to count episodes from chroma")

    if router.redis is not None:
        try:
            if router.redis._use_fallback or router.redis.client is None:
                stats["redis_keys"] = len(router.redis._fallback)
            else:
                stats["redis_keys"] = len(router.redis.client.keys(router.redis._key("*")))
        except Exception as e:  # noqa: F841
            logger.exception("Failed to count redis keys")

    return stats


class MemoryStore:
    """Two-layer memory files with backend routing for Phase 1 compatibility."""

    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"
        self.router = MemoryRouter(f"workspace:{self.memory_dir}", {})
        try:
            _migrate_legacy_files(self.router)
        except Exception as e:  # noqa: F841
            logger.exception("Legacy memory migration failed")

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        from pawbot.utils.fs import atomic_write_text
        atomic_write_text(self.memory_file, content)
        try:
            self.router.save("fact", {"text": content, "source": "MEMORY.md"})
        except Exception as e:  # noqa: F841
            logger.exception("Failed to persist MEMORY.md snapshot into router")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")
        try:
            self.router.save("episode", {"text": entry, "source": "HISTORY.md"})
        except Exception as e:  # noqa: F841
            logger.exception("Failed to persist history entry into router")

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    async def consolidate(
        self,
        session: Session,
        provider: LLMProvider,
        model: str,
        *,
        archive_all: bool = False,
        memory_window: int = 50,
    ) -> bool:
        """Consolidate old messages into MEMORY.md + HISTORY.md via LLM tool call."""
        if archive_all:
            old_messages = session.messages
            keep_count = 0
            logger.info("Memory consolidation (archive_all): {} messages", len(session.messages))
        else:
            keep_count = memory_window // 2
            if len(session.messages) <= keep_count:
                return True
            if len(session.messages) - session.last_consolidated <= 0:
                return True
            old_messages = session.messages[session.last_consolidated:-keep_count]
            if not old_messages:
                return True
            logger.info("Memory consolidation: {} to consolidate, {} keep", len(old_messages), keep_count)

        lines = []
        for message in old_messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )

        current_memory = self.read_long_term()
        prompt = f"""Process this conversation and call the save_memory tool with your consolidation.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{chr(10).join(lines)}"""

        try:
            response = await provider.chat(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a memory consolidation agent. Call the save_memory tool with your consolidation of the conversation.",
                    },
                    {"role": "user", "content": prompt},
                ],
                tools=_SAVE_MEMORY_TOOL,
                model=model,
            )

            if not response.has_tool_calls:
                logger.warning("Memory consolidation: LLM did not call save_memory, skipping")
                return False

            args = response.tool_calls[0].arguments
            if isinstance(args, str):
                args = json.loads(args)
            if not isinstance(args, dict):
                logger.warning("Memory consolidation: unexpected arguments type {}", type(args).__name__)
                return False

            if entry := args.get("history_entry"):
                if not isinstance(entry, str):
                    entry = json.dumps(entry, ensure_ascii=False)
                self.append_history(entry)
            if update_content := args.get("memory_update"):
                if not isinstance(update_content, str):
                    update_content = json.dumps(update_content, ensure_ascii=False)
                if update_content != current_memory:
                    self.write_long_term(update_content)

            session.last_consolidated = 0 if archive_all else len(session.messages) - keep_count
            logger.info(
                "Memory consolidation done: {} messages, last_consolidated={}",
                len(session.messages),
                session.last_consolidated,
            )
            return True
        except Exception as e:  # noqa: F841
            logger.exception("Memory consolidation failed")
            return False
