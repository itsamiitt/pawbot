# Phase 2 — Memory System Consolidation

> **Goal:** Make the 3-backend memory system reliable at scale with migrations, fallbacks, and efficient dedup.  
> **Duration:** 7-10 days  
> **Risk Level:** Medium (data migration, but backward compatible)  
> **Depends On:** Phase 0 (typed exceptions), Phase 1 (error handling patterns)

---

## Prerequisites

```bash
pip install "alembic>=1.13.0" "cachetools>=5.4.0"
```

Add to `pyproject.toml`:
```toml
"cachetools>=5.4.0",
```

Add `alembic` as optional:
```toml
[project.optional-dependencies]
migrations = ["alembic>=1.13.0"]
```

---

## 2.1 — SQLite Schema Versioning

### Problem
`SQLiteFactStore._init_db()` (lines 41-133) creates 5+ tables on first run but has no migration path. Any schema change requires dropping and recreating the database.

### Solution
Add a simple version table and migration system (no external dependency required for basic versioning):

```python
# Create: pawbot/agent/memory/migrations.py

"""SQLite schema migration system for the facts database.

Migration functions are registered in MIGRATIONS dict.
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
            last_accessed_at REAL,
            access_count INTEGER DEFAULT 0,
            source TEXT DEFAULT 'agent',
            tags TEXT DEFAULT '[]',
            confidence REAL DEFAULT 1.0,
            contradicted_by TEXT
        );

        CREATE TABLE IF NOT EXISTS memory_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subagent_id TEXT NOT NULL,
            content TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            proposed_type TEXT DEFAULT 'fact',
            status TEXT DEFAULT 'pending',
            created_at REAL NOT NULL,
            reviewed_at REAL
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
```

### Integration with `SQLiteFactStore.__init__`:

```python
# In sqlite_store.py, replace _init_db() call with:
def _init_db(self) -> None:
    """Initialize database with migration system."""
    conn = self._connect()
    try:
        from pawbot.agent.memory.migrations import run_migrations
        applied = run_migrations(conn)
        if applied:
            logger.info("Applied {} migration(s): {}", len(applied), ", ".join(applied))
    except Exception:
        logger.exception("Migration system failed, falling back to legacy init")
        self._legacy_init_db(conn)  # Keep old method as fallback
    finally:
        conn.close()
```

---

## 2.2 — ChromaDB Graceful Degradation

### Problem
`ChromaEpisodeStore` requires `sentence-transformers` (2GB download). If unavailable, the entire memory system logs an error but provides no search fallback.

### Solution
Add FTS5-based text search as automatic fallback:

```python
# In pawbot/agent/memory/chroma_store.py, modify __init__:

class ChromaEpisodeStore(MemoryProvider):
    def __init__(self, config: dict[str, Any] | Any):
        self._available = False
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=self.DB_DIR)
            self._collection = self._client.get_or_create_collection(
                name="episodes",
                metadata={"hnsw:space": "cosine"},
            )
            self._available = True
        except ImportError:
            logger.warning(
                "chromadb not installed — episode vector search disabled. "
                "Install with: pip install chromadb"
            )
        except Exception:
            logger.exception("ChromaDB init failed — episode vector search disabled")

    @property
    def is_available(self) -> bool:
        return self._available

    def search(self, query: str, limit: int = 5, memory_type: str | None = None) -> list[dict]:
        if not self._available:
            return []  # MemoryRouter will fall back to SQLite FTS5
        # ... existing search logic
```

### Update `MemoryRouter._gather` to use FTS5 fallback:

```python
# In pawbot/agent/memory/router.py, add FTS5 fallback method:

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
        return [self.sqlite._row_to_memory(row, query) for row in cursor.fetchall()]
    except Exception:
        logger.debug("FTS5 search failed — table may not exist yet")
        return []
```

---

## 2.3 — Redis Optional with In-Memory Fallback

### Problem
`RedisWorkingMemory` requires a running Redis server. In development, this is an unnecessary dependency.

### Solution
Create an in-memory TTL cache fallback:

```python
# Create: pawbot/agent/memory/local_cache.py

"""In-memory TTL cache — drop-in replacement for Redis in development."""

from __future__ import annotations

import uuid
from typing import Any

from cachetools import TTLCache
from loguru import logger

from pawbot.agent.memory.provider import MemoryProvider


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
        memory_id = content.get("_memory_id", str(uuid.uuid4()))
        self._cache[memory_id] = {
            "id": memory_id,
            "type": type,
            "content": content,
            "session_id": self.session_id,
        }
        return memory_id

    def load(self, query: str, limit: int = 10, memory_type: str | None = None) -> list[dict[str, Any]]:
        results = []
        for key, item in self._cache.items():
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
```

### Update `MemoryRouter.__init__` to auto-fallback:

```python
# In router.py, replace lines 33-37:
if backends_cfg.get("redis", {}).get("enabled", True):
    try:
        self.redis = RedisWorkingMemory(session_id, self.config)
    except Exception:
        logger.info("Redis unavailable — using in-memory cache for working memory")
        from pawbot.agent.memory.local_cache import LocalCacheMemory
        self.redis = LocalCacheMemory(session_id)
```

---

## 2.4 — Dedup Performance Improvement

### Problem
`MemoryRouter._dedupe()` uses `SequenceMatcher` which is O(n²) — will degrade badly with 10K+ memories.

### Solution
Replace with a fast fingerprint-based approach:

```python
# In pawbot/agent/memory/router.py, replace _dedupe method:

import hashlib

def _dedupe(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate memory results using content fingerprinting.
    
    Uses MD5 fingerprints of normalized text for O(n) deduplication,
    with a secondary similarity check only for close fingerprint matches.
    """
    deduped: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()

    for item in sorted(rows, key=self._combined_score, reverse=True):
        text = _memory_text(item.get("content", {})).lower().strip()
        if not text:
            continue

        # Fast path: exact or near-exact match via hash
        # Normalize whitespace before hashing
        normalized = " ".join(text.split())
        content_hash = hashlib.md5(normalized.encode()).hexdigest()

        if content_hash in seen_hashes:
            continue

        # Check for high similarity against existing items (limited to last 20)
        is_dup = False
        for existing in deduped[-20:]:
            existing_text = _memory_text(existing.get("content", {})).lower().strip()
            # Quick length check first (if lengths differ by >50%, skip similarity)
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
```

---

## 2.5 — Memory Compaction

### Problem
No automatic way to prevent unbounded memory growth. Over months, the facts table can grow to 100K+ entries.

### Solution

```python
# Create: pawbot/agent/memory/compactor.py

"""Memory compaction — consolidate and archive old/low-value memories."""

from __future__ import annotations

from typing import Any

from loguru import logger


class MemoryCompactor:
    """Automatic memory compaction when fact count exceeds threshold."""

    COMPACTION_THRESHOLD = 10_000
    ARCHIVE_SALIENCE = 0.1
    TARGET_AFTER_COMPACTION = 5_000

    def __init__(self, sqlite_store):
        self.store = sqlite_store

    def should_compact(self) -> bool:
        """Check if compaction is needed."""
        conn = self.store._connect()
        try:
            row = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
            count = row[0] if row else 0
            return count > self.COMPACTION_THRESHOLD
        finally:
            conn.close()

    def compact(self) -> dict[str, int]:
        """Run compaction: archive low-salience, merge duplicates.
        
        Returns stats dict with counts of archived and merged items.
        """
        stats = {"archived": 0, "merged": 0}
        conn = self.store._connect()

        try:
            # 1. Archive facts below salience threshold
            cursor = conn.execute(
                "SELECT id, type, content, salience, created_at "
                "FROM facts WHERE salience < ? "
                "ORDER BY salience ASC",
                (self.ARCHIVE_SALIENCE,),
            )
            for row in cursor.fetchall():
                conn.execute(
                    "INSERT OR IGNORE INTO archived_memories "
                    "(id, type, content, original_salience, final_salience, created_at, archived_at, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, strftime('%s','now'), 'compaction')",
                    (row[0], row[1], row[2], row[3], row[3], row[4]),
                )
                conn.execute("DELETE FROM facts WHERE id = ?", (row[0],))
                stats["archived"] += 1

            conn.commit()
            logger.info(
                "Memory compaction: archived={}, merged={}",
                stats["archived"], stats["merged"],
            )
        except Exception:
            conn.rollback()
            logger.exception("Memory compaction failed")
        finally:
            conn.close()

        return stats
```

---

## Verification Checklist — Phase 2 Complete

- [ ] `pawbot/agent/memory/migrations.py` exists with migration system
- [ ] `_schema_version` table created in facts.db on first run
- [ ] `facts_fts` FTS5 virtual table created (migration v2)
- [ ] `llm_usage` table created (migration v3)
- [ ] ChromaDB failure degrades gracefully to FTS5 search
- [ ] Redis failure degrades gracefully to `LocalCacheMemory`
- [ ] `cachetools>=5.4.0` in `pyproject.toml`
- [ ] Dedup uses hash-based O(n) approach with similarity fallback
- [ ] `MemoryCompactor` archives facts below 0.1 salience
- [ ] All tests pass: `pytest tests/ -v --tb=short`
- [ ] Agent works with: no Redis, no ChromaDB installed
