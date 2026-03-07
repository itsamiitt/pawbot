"""SQLite-backed fact store for persistent memory."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any

from loguru import logger

from pawbot.agent.memory._compat import (
    LINK_TYPES,
    coerce_float,
    coerce_int,
    memory_text,
    relevance_score,
    to_config_dict,
)
from pawbot.agent.memory.provider import MemoryProvider


class SQLiteFactStore(MemoryProvider):
    DB_PATH = os.path.expanduser("~/.pawbot/memory/facts.db")

    def __init__(self, config: dict[str, Any] | Any):
        cfg = to_config_dict(config)
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
        """Initialize database with migration system (Phase 2)."""
        conn = self._connect()
        try:
            from pawbot.agent.memory.migrations import run_migrations
            applied = run_migrations(conn)
            if applied:
                logger.info("Applied {} migration(s): {}", len(applied), ", ".join(applied))
        except Exception:
            logger.exception("Migration system failed, falling back to legacy init")
            self._legacy_init_db(conn)
        finally:
            conn.close()

    def _legacy_init_db(self, conn: sqlite3.Connection) -> None:
        """Legacy schema init — fallback if migration system fails."""
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
        salience = coerce_float(payload.get("salience", 1.0), 1.0)
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
                    coerce_float(payload.get("confidence", 0.8), 0.8),
                    now,
                    coerce_int(payload.get("times_used", 0), 0),
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
                    coerce_int(payload.get("avg_tokens", 0), 0),
                    coerce_int(payload.get("success_count", 0), 0),
                    coerce_int(payload.get("last_used", 0), 0),
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
        text = memory_text(payload)
        relevance = relevance_score(query, text)
        try:
            tags = json.loads(row["tags"] or "[]")
        except Exception as e:  # noqa: F841
            tags = []
        return {
            "id": row["id"],
            "type": row["type"],
            "content": payload,
            "salience": coerce_float(row["salience"], 1.0),
            "created_at": coerce_int(row["created_at"], int(time.time())),
            "updated_at": coerce_int(row["updated_at"], int(time.time())),
            "last_accessed": coerce_int(row["last_accessed"], int(time.time())),
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
            self._archive_fact(conn, row, coerce_float(row["salience"], 1.0))
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
                existing = {"text": memory_text(existing)}
            existing.update(payload)
            now = int(time.time())
            salience = coerce_float(payload.get("salience", row["salience"]), 1.0)
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
                "strength": coerce_float(row["strength"], 1.0),
                "created_at": coerce_int(row["created_at"], int(time.time())),
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
            salience = max(0.0, coerce_float(row["salience"], 1.0) + delta)
            try:
                content = json.loads(row["content"])
            except Exception as e:  # noqa: F841
                content = {}
            if not isinstance(content, dict):
                content = {"text": memory_text(content)}
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
                confidence = coerce_float(row["confidence"], 0.0)
                age_hours = (now - coerce_int(row["created_at"], now)) / 3600

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
        from pawbot.agent.memory.decay import MemoryDecayEngine
        return MemoryDecayEngine(self).decay_pass()
