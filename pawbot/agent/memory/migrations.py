"""SQLite schema migration system for the facts database.

Phase 2: Migration functions are registered in MIGRATIONS dict.
Each migration runs once and is recorded in the _schema_version table.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

from loguru import logger

# Migration functions: version -> (description, migration_fn)
MIGRATIONS: dict[int, tuple[str, Callable[[sqlite3.Connection], None]]] = {}


def migration(version: int, description: str):
    """Decorator to register a migration function."""
    def decorator(fn: Callable[[sqlite3.Connection], None]):
        MIGRATIONS[version] = (description, fn)
        return fn
    return decorator


def get_current_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  description TEXT NOT NULL,"
            "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        row = conn.execute("SELECT MAX(version) FROM _schema_version").fetchone()
        return row[0] or 0
    except sqlite3.Error:
        return 0


def run_migrations(conn: sqlite3.Connection) -> list[str]:
    """Run all pending migrations. Returns list of applied migration descriptions."""
    current = get_current_version(conn)
    applied: list[str] = []

    for version in sorted(MIGRATIONS.keys()):
        if version <= current:
            continue

        description, fn = MIGRATIONS[version]
        logger.info("Applying migration v{}: {}", version, description)

        try:
            fn(conn)
            conn.execute(
                "INSERT INTO _schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
            conn.commit()
            applied.append(f"v{version}: {description}")
        except sqlite3.Error as e:
            conn.rollback()
            logger.error("Migration v{} failed: {}", version, e)
            raise

    return applied


# ── Migration definitions ────────────────────────────────────────────────────


@migration(1, "Initial schema — facts, memory_links, reflections, procedures, archived")
def _v1_initial(conn: sqlite3.Connection) -> None:
    """Creates all tables if they don't exist (safe for existing databases)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL DEFAULT 'fact',
            content TEXT NOT NULL DEFAULT '{}',
            salience REAL NOT NULL DEFAULT 1.0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0,
            source TEXT DEFAULT 'agent',
            tags TEXT DEFAULT '[]',
            confidence REAL DEFAULT 1.0,
            contradicted_by TEXT
        );

        CREATE TABLE IF NOT EXISTS memory_links (
            id TEXT PRIMARY KEY,
            from_id TEXT NOT NULL,
            to_id TEXT NOT NULL,
            link_type TEXT NOT NULL,
            strength REAL DEFAULT 0.8,
            created_at REAL NOT NULL,
            UNIQUE(from_id, to_id, link_type)
        );

        CREATE TABLE IF NOT EXISTS reflections (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL DEFAULT '{}',
            salience REAL NOT NULL DEFAULT 1.0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            confidence REAL DEFAULT 0.0,
            success_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS procedures (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL DEFAULT '{}',
            salience REAL NOT NULL DEFAULT 1.0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            success_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS archived_memories (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            original_salience REAL,
            final_salience REAL,
            created_at REAL,
            archived_at REAL NOT NULL,
            reason TEXT DEFAULT 'decay'
        );

        CREATE TABLE IF NOT EXISTS subagent_inbox (
            id TEXT PRIMARY KEY,
            subagent_id TEXT NOT NULL,
            content TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            proposed_type TEXT DEFAULT 'fact',
            created_at REAL NOT NULL,
            reviewed INTEGER DEFAULT 0,
            accepted INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_facts_type ON facts(type);
        CREATE INDEX IF NOT EXISTS idx_facts_salience ON facts(salience);
        CREATE INDEX IF NOT EXISTS idx_links_from ON memory_links(from_id);
        CREATE INDEX IF NOT EXISTS idx_links_to ON memory_links(to_id);
    """)


@migration(2, "Add FTS5 full-text search index on facts.content")
def _v2_fts_index(conn: sqlite3.Connection) -> None:
    """Add FTS5 index for text search fallback when ChromaDB is unavailable."""
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
            content,
            content='facts',
            content_rowid='rowid'
        );

        -- Triggers to keep FTS in sync
        CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
            INSERT INTO facts_fts(rowid, content) VALUES (new.rowid, new.content);
        END;

        CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
            INSERT INTO facts_fts(facts_fts, rowid, content) VALUES('delete', old.rowid, old.content);
        END;

        CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
            INSERT INTO facts_fts(facts_fts, rowid, content) VALUES('delete', old.rowid, old.content);
            INSERT INTO facts_fts(rowid, content) VALUES (new.rowid, new.content);
        END;
    """)


@migration(3, "Add llm_usage tracking table")
def _v3_llm_usage(conn: sqlite3.Connection) -> None:
    """Track LLM API calls for cost analysis."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            latency_ms REAL DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            task_type TEXT DEFAULT '',
            session_key TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_ts ON llm_usage(timestamp)")


@migration(4, "Fix memory_links PK type and subagent_inbox columns")
def _v4_fix_link_and_inbox_schema(conn: sqlite3.Connection) -> None:
    """Recreate memory_links with TEXT PK and ensure subagent_inbox has reviewed/accepted."""
    # -- Fix memory_links: recreate with TEXT PRIMARY KEY --
    # Check if table needs fixing (INTEGER PK vs TEXT PK)
    cursor = conn.execute("PRAGMA table_info(memory_links)")
    cols = {row[1]: row[2] for row in cursor.fetchall()}
    if cols.get("id", "").upper() == "INTEGER":
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_links_new (
                id TEXT PRIMARY KEY,
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                link_type TEXT NOT NULL,
                strength REAL DEFAULT 0.8,
                created_at REAL NOT NULL,
                UNIQUE(from_id, to_id, link_type)
            );
            INSERT OR IGNORE INTO memory_links_new (id, from_id, to_id, link_type, strength, created_at)
                SELECT CAST(id AS TEXT), from_id, to_id, link_type, strength, created_at
                FROM memory_links;
            DROP TABLE memory_links;
            ALTER TABLE memory_links_new RENAME TO memory_links;
            CREATE INDEX IF NOT EXISTS idx_links_from ON memory_links(from_id);
            CREATE INDEX IF NOT EXISTS idx_links_to ON memory_links(to_id);
        """)

    # -- Fix subagent_inbox: ensure reviewed/accepted columns exist --
    cursor = conn.execute("PRAGMA table_info(subagent_inbox)")
    inbox_cols = {row[1] for row in cursor.fetchall()}

    # Recreate with correct schema if id is INTEGER or columns are wrong
    needs_recreate = "reviewed" not in inbox_cols or "accepted" not in inbox_cols
    if needs_recreate:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS subagent_inbox_new (
                id TEXT PRIMARY KEY,
                subagent_id TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                proposed_type TEXT DEFAULT 'fact',
                created_at REAL NOT NULL,
                reviewed INTEGER DEFAULT 0,
                accepted INTEGER DEFAULT 0
            );
            INSERT OR IGNORE INTO subagent_inbox_new (id, subagent_id, content, confidence, proposed_type, created_at)
                SELECT CAST(id AS TEXT), subagent_id, content, confidence, proposed_type, created_at
                FROM subagent_inbox;
            DROP TABLE subagent_inbox;
            ALTER TABLE subagent_inbox_new RENAME TO subagent_inbox;
        """)

