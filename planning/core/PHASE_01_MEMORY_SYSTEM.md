# PHASE 1 — MEMORY SYSTEM OVERHAUL
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)  
> **Implementation Days:** Day 1 (Feature 1.1), Day 2 (Feature 1.2), Day 11 (Feature 1.3), Day 12 (Feature 1.4), Week 5–8 (Feature 1.5)  
> **Primary File:** `~/nanobot/agent/memory.py`  
> **Test File:** `~/nanobot/tests/test_memory.py`

---

## BEFORE YOU START — READ THESE FILES

```bash
cat ~/nanobot/agent/memory.py          # understand current public interface
cat ~/nanobot/agent/loop.py            # see how loop.py calls memory
cat ~/nanobot/agent/context.py         # see how context.py calls memory
cat ~/nanobot/pyproject.toml           # know current dependencies
```

**Do NOT change** any method name or signature that `loop.py` or `context.py` currently calls.  
Identify those call sites before writing a single line of new code.

---

## FEATURE 1.1 — THREE-DATABASE MEMORY BACKEND

### What You Are Building

Replace the flat-file MEMORY.md / HISTORY.md system with three specialized backends behind a single unified interface. The existing public API must remain unchanged.

### New File Structure After This Feature

```
~/nanobot/agent/memory.py          ← modified (keep existing API, replace internals)
~/.nanobot/memory/facts.db         ← auto-created on first run
~/.nanobot/memory/chroma/          ← auto-created on first run
```

### Step 1 — Add Dependencies to pyproject.toml

```toml
# Add to [project.dependencies] in ~/nanobot/pyproject.toml
"redis>=5.0.0",
"chromadb>=0.4.0",
"sentence-transformers>=2.0.0",
```

Install: `pip install redis chromadb sentence-transformers`

### Step 2 — Abstract Base Class

**Class name:** `MemoryProvider`  
**Location:** `agent/memory.py` (top of file, before any implementation classes)

```python
from abc import ABC, abstractmethod
from typing import Optional

class MemoryProvider(ABC):
    @abstractmethod
    def save(self, type: str, content: dict) -> str:
        """Save a memory. Returns its generated ID (UUID4 string)."""

    @abstractmethod
    def load(self, query: str, limit: int = 10) -> list[dict]:
        """Retrieve relevant memories. Returns list of memory dicts."""

    @abstractmethod
    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Semantic search over stored memories."""

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        """Delete memory by ID. Returns True if deleted."""

    @abstractmethod
    def update(self, memory_id: str, content: dict) -> bool:
        """Update existing memory. Returns True if updated."""

    @abstractmethod
    def list_all(self, type: str) -> list[dict]:
        """List all memories of a given type."""

    @abstractmethod
    def decay_pass(self) -> int:
        """Run decay pass. Returns count of archived memories."""
```

### Step 3 — RedisWorkingMemory

**Class name:** `RedisWorkingMemory`  
**Inherits:** `MemoryProvider`  
**Config key read from:** `config["memory"]["backends"]["redis"]`

```python
import redis
import json
import uuid
import time
import logging

logger = logging.getLogger("nanobot")

class RedisWorkingMemory(MemoryProvider):
    KEY_PREFIX = "session"
    DEFAULT_TTL = 3600  # seconds
    MAX_MESSAGES = 20

    def __init__(self, session_id: str, config: dict):
        redis_cfg = config.get("memory", {}).get("backends", {}).get("redis", {})
        host = redis_cfg.get("host", "localhost")
        port = redis_cfg.get("port", 6379)
        self.ttl = redis_cfg.get("ttl", self.DEFAULT_TTL)
        self.session_id = session_id
        self._fallback = {}  # in-memory dict if Redis unavailable
        self._use_fallback = False

        try:
            self.client = redis.Redis(host=host, port=port, decode_responses=True)
            self.client.ping()
        except Exception as e:
            logger.warning(f"Redis unavailable ({e}), using in-memory fallback")
            self._use_fallback = True

    def _key(self, k: str) -> str:
        return f"{self.KEY_PREFIX}:{self.session_id}:{k}"
```

Key patterns used:
- Structured data: `session:{session_id}:{key}` via `HSET`
- Message lists: `session:{session_id}:messages` via `LPUSH` / `LTRIM`

After every write: call `self.client.expire(self._key(k), self.ttl)` to reset TTL.

The `save()` method must:
1. Generate `memory_id = str(uuid.uuid4())`
2. Store with `HSET self._key(memory_id) content json.dumps(content)`
3. If `type == "message"`: also `LPUSH` to messages list and `LTRIM` to last `MAX_MESSAGES`
4. Return `memory_id`

### Step 4 — SQLiteFactStore

**Class name:** `SQLiteFactStore`  
**Inherits:** `MemoryProvider`  
**Database path:** `~/.nanobot/memory/facts.db`  
**Config key:** `config["memory"]["backends"]["sqlite"]["path"]`

```python
import sqlite3
import json
import uuid
import time
import os

class SQLiteFactStore(MemoryProvider):
    DB_PATH = os.path.expanduser("~/.nanobot/memory/facts.db")

    def __init__(self, config: dict):
        sqlite_cfg = config.get("memory", {}).get("backends", {}).get("sqlite", {})
        self.db_path = os.path.expanduser(sqlite_cfg.get("path", self.DB_PATH))
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
```

**Tables to create in `_init_db()`** — use `CREATE TABLE IF NOT EXISTS`:

```sql
-- Primary memory storage
CREATE TABLE IF NOT EXISTS facts (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    content      TEXT NOT NULL,       -- JSON blob
    salience     REAL DEFAULT 1.0,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL,
    last_accessed INTEGER NOT NULL,
    tags         TEXT DEFAULT '[]',   -- JSON array
    source       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_facts_type ON facts(type);
CREATE INDEX IF NOT EXISTS idx_facts_salience ON facts(salience);
CREATE INDEX IF NOT EXISTS idx_facts_created ON facts(created_at);
CREATE INDEX IF NOT EXISTS idx_facts_accessed ON facts(last_accessed);

-- Links between memories
CREATE TABLE IF NOT EXISTS memory_links (
    id         TEXT PRIMARY KEY,
    from_id    TEXT NOT NULL,
    to_id      TEXT NOT NULL,
    link_type  TEXT NOT NULL,   -- see LINK_TYPES in MASTER_REFERENCE.md
    strength   REAL DEFAULT 1.0,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(from_id) REFERENCES facts(id),
    FOREIGN KEY(to_id)   REFERENCES facts(id)
);

-- Learned failure lessons
CREATE TABLE IF NOT EXISTS reflections (
    id           TEXT PRIMARY KEY,
    failure_type TEXT NOT NULL,
    lesson       TEXT NOT NULL,
    rule         TEXT NOT NULL,
    applies_to   TEXT DEFAULT '[]',  -- JSON array of task types
    confidence   REAL DEFAULT 0.8,
    created_at   INTEGER NOT NULL,
    times_used   INTEGER DEFAULT 0
);

-- Proven task sequences
CREATE TABLE IF NOT EXISTS procedures (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    triggers      TEXT NOT NULL,      -- JSON array of trigger phrases
    steps         TEXT NOT NULL,      -- JSON array of step strings
    preconditions TEXT DEFAULT '[]',  -- JSON array
    avg_tokens    INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    last_used     INTEGER DEFAULT 0
);

-- Decayed memory archive (never hard delete)
CREATE TABLE IF NOT EXISTS archived_memories (
    id             TEXT PRIMARY KEY,
    original_table TEXT NOT NULL,
    content        TEXT NOT NULL,  -- original row as JSON
    archived_at    INTEGER NOT NULL,
    final_salience REAL
);

-- Subagent discovery inbox (Feature 1.5)
CREATE TABLE IF NOT EXISTS subagent_inbox (
    id            TEXT PRIMARY KEY,
    subagent_id   TEXT NOT NULL,
    content       TEXT NOT NULL,   -- JSON
    confidence    REAL NOT NULL,
    proposed_type TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    reviewed      INTEGER DEFAULT 0,  -- 0=false, 1=true
    accepted      INTEGER DEFAULT 0
);
```

**Critical rule:** Every SQL statement must use `?` parameterized placeholders.  
**Never do:** `f"SELECT * FROM facts WHERE type = '{type}'"` — this is a SQL injection risk.  
**Always do:** `cursor.execute("SELECT * FROM facts WHERE type = ?", (type,))`

The `save()` method for `SQLiteFactStore`:
```python
def save(self, type: str, content: dict) -> str:
    memory_id = str(uuid.uuid4())
    now = int(time.time())
    with sqlite3.connect(self.db_path) as conn:
        conn.execute(
            "INSERT INTO facts (id, type, content, salience, created_at, updated_at, last_accessed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (memory_id, type, json.dumps(content), 1.0, now, now, now)
        )
    return memory_id
```

### Step 5 — ChromaEpisodeStore

**Class name:** `ChromaEpisodeStore`  
**Inherits:** `MemoryProvider`  
**Collection name:** `nanobot_episodes` (exact string — do not change)  
**Persist dir:** `~/.nanobot/memory/chroma/`  
**Config key:** `config["memory"]["backends"]["chroma"]["path"]`

```python
import chromadb
from chromadb.utils import embedding_functions

class ChromaEpisodeStore(MemoryProvider):
    COLLECTION_NAME = "nanobot_episodes"

    def __init__(self, config: dict):
        chroma_cfg = config.get("memory", {}).get("backends", {}).get("chroma", {})
        persist_path = os.path.expanduser(chroma_cfg.get("path", "~/.nanobot/memory/chroma"))
        os.makedirs(persist_path, exist_ok=True)

        self.client = chromadb.PersistentClient(path=persist_path)

        # Embedding function: try Ollama first, fall back to SentenceTransformers
        try:
            self.ef = embedding_functions.OllamaEmbeddingFunction(
                url="http://localhost:11434/api/embeddings",
                model_name="nomic-embed-text"
            )
        except Exception:
            self.ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )

        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            embedding_function=self.ef,
            metadata={"hnsw:space": "cosine"}
        )
```

Each episode document stored as:
- `id`: UUID string
- `document`: text content of the episode (what gets embedded)
- `metadata`: dict with keys: `type`, `timestamp`, `goal_id`, `session_id`, `salience`

The `search()` method uses: `self.collection.query(query_texts=[query], n_results=limit)`

### Step 6 — MemoryRouter

**Class name:** `MemoryRouter`  
**Inherits:** `MemoryProvider`  
**This is the ONLY class that other modules import and use**

```python
class MemoryRouter(MemoryProvider):
    def __init__(self, session_id: str, config: dict):
        self.config = config
        backends_cfg = config.get("memory", {}).get("backends", {})

        self.redis = None
        self.sqlite = None
        self.chroma = None

        if backends_cfg.get("redis", {}).get("enabled", True):
            self.redis = RedisWorkingMemory(session_id, config)

        if backends_cfg.get("sqlite", {}).get("enabled", True):
            self.sqlite = SQLiteFactStore(config)

        if backends_cfg.get("chroma", {}).get("enabled", True):
            self.chroma = ChromaEpisodeStore(config)

        # SQLite is always the final fallback
        if self.sqlite is None:
            logger.warning("SQLite disabled — creating emergency in-memory SQLite")
            self.sqlite = SQLiteFactStore(config)
```

**Routing logic for `save()`:**
- `type == "episode"` → write to `chroma` AND `sqlite`
- `type in ["fact", "preference", "decision", "reflection", "procedure", "task", "risk"]` → write to `sqlite`
- `type == "working"` or `type == "message"` → write to `redis` (fallback: sqlite)
- Always return the ID from the primary backend

**Routing logic for `load()` and `search()`:**
1. Query all enabled backends in parallel (use `asyncio.gather` or sequential fallback)
2. Merge results into a single list
3. Deduplicate: if two results have content similarity > 0.95, keep the one with higher salience
4. Sort by combined score: `0.6 * relevance_score + 0.4 * salience`
5. Return top `limit` results

### Step 7 — Modify Existing memory.py Public Interface

Read the current `memory.py` first. Then:

1. Keep whatever public functions `loop.py` and `context.py` currently call
2. Inside those existing functions, replace the flat-file logic with `MemoryRouter` calls
3. Add migration function:

```python
def _migrate_legacy_files():
    """On first run, import MEMORY.md and HISTORY.md into new backends."""
    memory_md = os.path.expanduser("~/nanobot/workspace/MEMORY.md")
    history_md = os.path.expanduser("~/nanobot/workspace/HISTORY.md")
    migrated_flag = os.path.expanduser("~/.nanobot/memory/.migrated")

    if os.path.exists(migrated_flag):
        return  # already migrated

    if os.path.exists(memory_md):
        with open(memory_md) as f:
            content = f.read()
        # Split by lines and save each non-empty line as a fact
        for line in content.splitlines():
            if line.strip():
                router.save("fact", {"text": line.strip(), "source": "MEMORY.md"})
        logger.info(f"Migrated MEMORY.md to SQLiteFactStore")

    if os.path.exists(history_md):
        with open(history_md) as f:
            content = f.read()
        router.save("episode", {"text": content, "source": "HISTORY.md"})
        logger.info(f"Migrated HISTORY.md to ChromaEpisodeStore")

    # Mark as migrated
    open(migrated_flag, "w").close()
```

4. Add `memory_stats()` function:

```python
def memory_stats() -> dict:
    """Returns counts per type and storage sizes."""
    # Query SQLite for counts per type
    # Check ChromaDB collection count
    # Check Redis key count (if available)
    # Return dict: {"facts": N, "episodes": N, "reflections": N, ...}
```

---

## FEATURE 1.2 — MEMORY TYPES AND SALIENCE SCORING

**New class:** `MemoryClassifier`  
**Location:** `agent/memory.py` (add after MemoryRouter)  
**No new files** — all changes in `memory.py`

### Memory Type Registry

```python
MEMORY_TYPE_CONFIG = {
    "fact":       {"half_life_days": 365, "min_salience": 0.1},
    "preference": {"half_life_days":  90, "min_salience": 0.2},
    "decision":   {"half_life_days": 180, "min_salience": 0.3},
    "episode":    {"half_life_days": 180, "min_salience": 0.2},
    "reflection": {"half_life_days": 999, "min_salience": 0.0},  # never pruned
    "procedure":  {"half_life_days": 365, "min_salience": 0.1},
    "task":       {"half_life_days":  30, "min_salience": 0.4},
    "risk":       {"half_life_days": 365, "min_salience": 0.3},
}
```

### Salience Scoring Formula

```python
import math

class MemoryClassifier:
    @staticmethod
    def calculate_salience(base_salience: float, type: str,
                           created_at: int, last_accessed: int) -> float:
        """Calculate current salience using exponential decay."""
        cfg = MEMORY_TYPE_CONFIG.get(type, {"half_life_days": 180})
        half_life = cfg["half_life_days"]
        now = int(time.time())
        age_days = (now - created_at) / 86400

        decayed = base_salience * (0.5 ** (age_days / half_life))

        # Recency boost: if accessed within last 7 days
        days_since_access = (now - last_accessed) / 86400
        if days_since_access <= 7:
            decayed = min(1.0, decayed + 0.2)

        return round(decayed, 4)

    @staticmethod
    def should_archive(salience: float, type: str) -> bool:
        """Returns True if memory should be moved to archive."""
        min_sal = MEMORY_TYPE_CONFIG.get(type, {}).get("min_salience", 0.1)
        return salience < min_sal
```

### Access Tracking

Every time a memory is retrieved via `load()` or `search()`, update its `last_accessed`:

```python
# In SQLiteFactStore.load() and SQLiteFactStore.search(), after fetching rows:
now = int(time.time())
for row in results:
    conn.execute(
        "UPDATE facts SET last_accessed = ? WHERE id = ?",
        (now, row["id"])
    )
```

---

## FEATURE 1.3 — MEMORY SELF-LINKING (A-MEM Pattern)

**New class:** `MemoryLinker`  
**Location:** `agent/memory.py`  
**Depends on:** Feature 1.1 (ChromaEpisodeStore, SQLiteFactStore)

### Process Flow

This runs **asynchronously** after `save()` returns — it must NEVER block the agent response.

```python
import asyncio
import threading

class MemoryLinker:
    def __init__(self, router: MemoryRouter):
        self.router = router

    def link_async(self, new_memory_id: str, new_memory: dict):
        """Fire-and-forget: link new memory to related existing ones."""
        thread = threading.Thread(
            target=self._link_sync,
            args=(new_memory_id, new_memory),
            daemon=True
        )
        thread.start()

    def _link_sync(self, new_memory_id: str, new_memory: dict):
        try:
            # 1. Find top-5 semantically similar memories
            text = new_memory.get("text", json.dumps(new_memory))
            similar = self.router.chroma.collection.query(
                query_texts=[text], n_results=5
            )

            for existing_id in similar.get("ids", [[]])[0]:
                if existing_id == new_memory_id:
                    continue
                existing = self.router.sqlite.load_by_id(existing_id)
                if not existing:
                    continue

                # 2. Infer relationship type
                link_type = self._infer_link_type(new_memory, existing)
                if link_type is None:
                    continue

                # 3. Save link to memory_links table
                link_id = str(uuid.uuid4())
                now = int(time.time())
                with sqlite3.connect(self.router.sqlite.db_path) as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO memory_links "
                        "(id, from_id, to_id, link_type, strength, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (link_id, new_memory_id, existing_id, link_type, 0.8, now)
                    )

                # 4. If contradicts: lower salience of existing memory
                if link_type == "contradicts":
                    with sqlite3.connect(self.router.sqlite.db_path) as conn:
                        conn.execute(
                            "UPDATE facts SET salience = MAX(0, salience - 0.15), "
                            "content = json_set(content, '$.contradicted_by', ?) "
                            "WHERE id = ?",
                            (new_memory_id, existing_id)
                        )

        except Exception as e:
            logger.warning(f"Memory linking failed: {e}")

    def _infer_link_type(self, new_mem: dict, existing_mem: dict) -> Optional[str]:
        """
        Uses local Ollama model to classify relationship.
        Returns one of LINK_TYPES or None.
        """
        # See MASTER_REFERENCE.md for LINK_TYPES canonical list
        # Prompt the local model:
        # "Given memory A and memory B, classify their relationship as one of:
        #  caused_by, supports, contradicts, extends, resolves, depends_on, or none.
        #  Respond with ONLY the relationship type or 'none'. No explanation."
        # Parse response — if not in LINK_TYPES, return None
        pass  # implement using OllamaProvider from Phase 4
```

**Link traversal for retrieval** — add to `MemoryRouter.load()`:

After getting initial results, for each result:
```python
# Load first-degree linked memories
links = sqlite.get_links(memory_id)
for link in links:
    linked = sqlite.load_by_id(link["to_id"])
    if linked:
        linked["relevance_weight"] = 0.7  # weighted lower than direct results
        results.append(linked)
```

---

## FEATURE 1.4 — MEMORY DECAY ENGINE

**New class:** `MemoryDecayEngine`  
**Location:** `agent/memory.py`  
**Registration:** Register as nightly cron job (see Phase 11 for cron details)

```python
class MemoryDecayEngine:
    JOB_NAME = "memory_decay"
    CRON_SCHEDULE = "0 3 * * *"  # 3am every night

    def __init__(self, sqlite: SQLiteFactStore):
        self.sqlite = sqlite

    def decay_pass(self) -> int:
        """
        Run full decay pass. Returns count of archived memories.
        This is the method registered with CronScheduler.
        """
        archived_count = 0
        updated_count = 0

        with sqlite3.connect(self.sqlite.db_path) as conn:
            rows = conn.execute(
                "SELECT id, type, content, salience, created_at, last_accessed "
                "FROM facts"
            ).fetchall()

            for row in rows:
                mem_id, type_, content, base_sal, created_at, last_accessed = row
                new_sal = MemoryClassifier.calculate_salience(
                    base_sal, type_, created_at, last_accessed
                )

                if MemoryClassifier.should_archive(new_sal, type_):
                    # Move to archive — never hard delete
                    archive_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO archived_memories "
                        "(id, original_table, content, archived_at, final_salience) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (archive_id, "facts", content, int(time.time()), new_sal)
                    )
                    conn.execute("DELETE FROM facts WHERE id = ?", (mem_id,))
                    archived_count += 1
                elif abs(new_sal - base_sal) > 0.01:
                    conn.execute(
                        "UPDATE facts SET salience = ? WHERE id = ?",
                        (new_sal, mem_id)
                    )
                    updated_count += 1

        summary = (f"Decay pass complete: {len(rows) - archived_count} active, "
                   f"{archived_count} archived, {len(rows)} total")
        logger.info(summary)

        # Only notify user if decay was dramatic
        if archived_count > 100:
            logger.warning(f"Memory decay: {archived_count} memories archived")

        return archived_count
```

**Integration with CronScheduler** (Phase 11 will implement CronScheduler):

```python
# In your initialization code (e.g., nanobot startup):
decay_engine = MemoryDecayEngine(sqlite_store)
# This call happens in Phase 11:
# cron_scheduler.register(
#     name=MemoryDecayEngine.JOB_NAME,
#     schedule=MemoryDecayEngine.CRON_SCHEDULE,
#     fn=decay_engine.decay_pass
# )
```

---

## FEATURE 1.5 — SUBAGENT MEMORY INBOX

**Location:** `agent/memory.py`  
**Depends on:** Feature 1.1 (SQLiteFactStore), Phase 12 (SubagentRunner)

The `subagent_inbox` table is already created in Feature 1.1's `_init_db()`.

Add these methods to `SQLiteFactStore`:

```python
def inbox_write(self, subagent_id: str, content: dict,
                confidence: float, proposed_type: str) -> str:
    """Subagents call this to propose new memories."""
    inbox_id = str(uuid.uuid4())
    now = int(time.time())
    with sqlite3.connect(self.db_path) as conn:
        conn.execute(
            "INSERT INTO subagent_inbox "
            "(id, subagent_id, content, confidence, proposed_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (inbox_id, subagent_id, json.dumps(content), confidence, proposed_type, now)
        )
    return inbox_id

def inbox_review(self) -> list[dict]:
    """
    Orchestrator calls this after each subgoal.
    Auto-accepts confidence > 0.9.
    Auto-accepts after 24hrs if confidence 0.6-0.9.
    Discards confidence < 0.6.
    Returns list of accepted memory IDs.
    """
    now = int(time.time())
    accepted_ids = []

    with sqlite3.connect(self.db_path) as conn:
        pending = conn.execute(
            "SELECT id, subagent_id, content, confidence, proposed_type, created_at "
            "FROM subagent_inbox WHERE reviewed = 0"
        ).fetchall()

        for row in pending:
            inbox_id, subagent_id, content, confidence, proposed_type, created_at = row
            age_hours = (now - created_at) / 3600

            should_accept = (
                confidence > 0.9 or
                (0.6 <= confidence <= 0.9 and age_hours >= 24)
            )
            should_discard = confidence < 0.6

            if should_accept:
                # Promote to main memory
                mem_id = self.save(proposed_type, json.loads(content))
                accepted_ids.append(mem_id)
                conn.execute(
                    "UPDATE subagent_inbox SET reviewed = 1, accepted = 1 WHERE id = ?",
                    (inbox_id,)
                )
            elif should_discard:
                conn.execute(
                    "UPDATE subagent_inbox SET reviewed = 1, accepted = 0 WHERE id = ?",
                    (inbox_id,)
                )

    return accepted_ids
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_memory.py`

Minimum tests required:

```python
# test_memory.py structure
class TestMemoryProvider:
    def test_redis_working_memory_save_load()
    def test_redis_fallback_when_unavailable()
    def test_redis_ttl_expiry()
    def test_redis_message_list_ltrim()

class TestSQLiteFactStore:
    def test_save_returns_id()
    def test_load_by_query()
    def test_update_memory()
    def test_delete_memory()
    def test_list_all_by_type()
    def test_parameterized_queries_no_injection()  # test with ' OR '1'='1
    def test_archived_not_hard_deleted()

class TestChromaEpisodeStore:
    def test_save_and_semantic_search()
    def test_fallback_embedding_function()
    def test_collection_persists_across_restart()

class TestMemoryRouter:
    def test_routing_episode_to_chroma_and_sqlite()
    def test_routing_fact_to_sqlite()
    def test_routing_working_to_redis()
    def test_merge_deduplication()
    def test_fallback_when_chroma_unavailable()

class TestMemoryClassifier:
    def test_salience_decay_formula()
    def test_recency_boost()
    def test_should_archive_threshold()
    def test_reflection_never_archived()  # min_salience=0.0

class TestMemoryDecayEngine:
    def test_decay_pass_archives_low_salience()
    def test_decay_pass_preserves_reflections()
    def test_decay_count_returned()

class TestMemoryLinker:
    def test_link_async_does_not_block()
    def test_contradicts_lowers_existing_salience()
    def test_null_link_not_saved()

class TestSubagentInbox:
    def test_high_confidence_auto_accepted()
    def test_low_confidence_discarded()
    def test_medium_confidence_accepted_after_24h()
```

Run with: `pytest tests/test_memory.py -v --tb=short`

---

## CROSS-REFERENCES

- **Phase 2** (loop.py) calls: `memory.search(query, type="reflection")`, `memory.search(query, type="procedure")`, `memory.save("episode", {...})`, `memory.save("reflection", {...})`
- **Phase 3** (context.py) calls: `memory.load(query, limit=3)`, `memory.search(query, type="episode")`, `memory_stats()`
- **Phase 11** (cron) calls: `decay_engine.decay_pass()`
- **Phase 12** (subagent) calls: `sqlite.inbox_write(...)`, `sqlite.inbox_review()`
- **Phase 14** (security) calls: `memory.search(...)` to sanitize results before injecting into context
- **Phase 16** (CLI) calls: `memory_stats()`, `memory.list_all(type)`, `memory.search(query)`, `memory.delete(id)`

All canonical names are in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
