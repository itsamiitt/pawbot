"""Phase 1 memory system tests."""

from __future__ import annotations

import sqlite3
import time
import types
from pathlib import Path

import pytest

import pawbot.agent.memory as memory_mod
import pawbot.agent.memory.redis_store as redis_store_mod
import pawbot.agent.memory.chroma_store as chroma_store_mod
import pawbot.agent.memory.router as router_mod
from pawbot.agent.memory import (
    ChromaEpisodeStore,
    MemoryClassifier,
    MemoryDecayEngine,
    MemoryLinker,
    MemoryRouter,
    RedisWorkingMemory,
    SQLiteFactStore,
)


class _FakeRedisClient:
    now_fn = staticmethod(time.time)

    def __init__(self, *args, **kwargs):
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.expiry: dict[str, float] = {}

    def _is_expired(self, key: str) -> bool:
        exp = self.expiry.get(key)
        if exp is None:
            return False
        if exp <= self.now_fn():
            self.hashes.pop(key, None)
            self.lists.pop(key, None)
            self.expiry.pop(key, None)
            return True
        return False

    def ping(self):
        return True

    def hset(self, key: str, mapping: dict[str, str]):
        self._is_expired(key)
        self.hashes.setdefault(key, {})
        self.hashes[key].update(mapping)

    def hgetall(self, key: str) -> dict[str, str]:
        if self._is_expired(key):
            return {}
        return dict(self.hashes.get(key, {}))

    def expire(self, key: str, ttl: int):
        self.expiry[key] = self.now_fn() + ttl

    def keys(self, pattern: str) -> list[str]:
        prefix = pattern[:-1] if pattern.endswith("*") else pattern
        keys = []
        for key in list(self.hashes.keys()) + list(self.lists.keys()):
            self._is_expired(key)
            if key.startswith(prefix):
                keys.append(key)
        return sorted(set(keys))

    def lpush(self, key: str, value: str):
        self._is_expired(key)
        self.lists.setdefault(key, [])
        self.lists[key].insert(0, value)

    def ltrim(self, key: str, start: int, end: int):
        values = self.lists.get(key, [])
        if end == -1:
            end = len(values) - 1
        self.lists[key] = values[start : end + 1]

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        if self._is_expired(key):
            return []
        values = self.lists.get(key, [])
        if end == -1:
            end = len(values) - 1
        return values[start : end + 1]

    def lrem(self, key: str, count: int, value: str):
        if key not in self.lists:
            return 0
        before = len(self.lists[key])
        self.lists[key] = [item for item in self.lists[key] if item != value]
        return before - len(self.lists[key])

    def delete(self, key: str) -> int:
        existed = 1 if key in self.hashes or key in self.lists else 0
        self.hashes.pop(key, None)
        self.lists.pop(key, None)
        self.expiry.pop(key, None)
        return existed

    def exists(self, key: str) -> bool:
        if self._is_expired(key):
            return False
        return key in self.hashes or key in self.lists


def _make_config(
    tmp_path: Path,
    *,
    redis_enabled: bool = False,
    chroma_enabled: bool = False,
    redis_ttl: int = 3600,
) -> dict:
    return {
        "memory": {
            "backends": {
                "redis": {"enabled": redis_enabled, "host": "localhost", "port": 6379, "ttl": redis_ttl},
                "sqlite": {"enabled": True, "path": str(tmp_path / "facts.db")},
                "chroma": {"enabled": chroma_enabled, "path": str(tmp_path / "chroma")},
            }
        }
    }


class TestMemoryProvider:
    def test_redis_working_memory_save_load(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(redis_store_mod, "redis", types.SimpleNamespace(Redis=_FakeRedisClient))
        store = RedisWorkingMemory("s1", _make_config(tmp_path, redis_enabled=True))

        memory_id = store.save("working", {"text": "hello redis"})
        rows = store.load("hello", limit=10)

        assert rows
        assert rows[0]["id"] == memory_id
        assert rows[0]["content"]["text"] == "hello redis"

    def test_redis_fallback_when_unavailable(self, monkeypatch, tmp_path: Path):
        class _BrokenRedis:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("offline")

        monkeypatch.setattr(redis_store_mod, "redis", types.SimpleNamespace(Redis=_BrokenRedis))
        store = RedisWorkingMemory("s1", _make_config(tmp_path, redis_enabled=True))

        assert store._use_fallback is True
        memory_id = store.save("working", {"text": "fallback active"})
        rows = store.load("fallback", limit=5)
        assert rows[0]["id"] == memory_id

    def test_redis_ttl_expiry(self, monkeypatch, tmp_path: Path):
        now = [1000.0]
        monkeypatch.setattr(redis_store_mod.time, "time", lambda: now[0])
        _FakeRedisClient.now_fn = staticmethod(lambda: now[0])
        monkeypatch.setattr(redis_store_mod, "redis", types.SimpleNamespace(Redis=_FakeRedisClient))
        store = RedisWorkingMemory("s1", _make_config(tmp_path, redis_enabled=True, redis_ttl=1))

        store.save("working", {"text": "short lived"})
        assert len(store.load("short", limit=10)) == 1
        now[0] += 2.0
        assert store.load("short", limit=10) == []

    def test_redis_message_list_ltrim(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(redis_store_mod, "redis", types.SimpleNamespace(Redis=_FakeRedisClient))
        store = RedisWorkingMemory("s1", _make_config(tmp_path, redis_enabled=True))

        for i in range(store.MAX_MESSAGES + 7):
            store.save("message", {"text": f"m{i}"})

        msg_ids = store.client.lrange(store._key("messages"), 0, -1)
        assert len(msg_ids) == store.MAX_MESSAGES


class TestSQLiteFactStore:
    def test_save_returns_id(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        memory_id = store.save("fact", {"text": "a fact"})
        assert isinstance(memory_id, str)
        assert len(memory_id) >= 32

    def test_load_by_query(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        store.save("fact", {"text": "python testing"})
        store.save("fact", {"text": "rust tooling"})
        rows = store.load("python", limit=10)
        assert len(rows) == 1
        assert rows[0]["content"]["text"] == "python testing"

    def test_update_memory(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        memory_id = store.save("fact", {"text": "old"})
        assert store.update(memory_id, {"text": "new"})
        row = store.load_by_id(memory_id)
        assert row is not None
        assert row["content"]["text"] == "new"

    def test_delete_memory(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        memory_id = store.save("fact", {"text": "delete me"})
        assert store.delete(memory_id) is True
        assert store.load_by_id(memory_id) is None

    def test_list_all_by_type(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        store.save("fact", {"text": "f1"})
        store.save("risk", {"text": "r1"})
        facts = store.list_all("fact")
        assert len(facts) == 1
        assert facts[0]["type"] == "fact"

    def test_parameterized_queries_no_injection(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        store.save("fact", {"text": "safe one"})
        store.save("fact", {"text": "safe two"})
        rows = store.load("' OR '1'='1", limit=20)
        assert rows == []

    def test_archived_not_hard_deleted(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        memory_id = store.save("fact", {"text": "archive me"})
        assert store.delete(memory_id)

        with sqlite3.connect(store.db_path) as conn:
            archived_count = conn.execute("SELECT COUNT(*) FROM archived_memories").fetchone()[0]
            facts_count = conn.execute("SELECT COUNT(*) FROM facts WHERE id = ?", (memory_id,)).fetchone()[0]
        assert archived_count == 1
        assert facts_count == 0


class _FakeCollection:
    def __init__(self):
        self.docs: dict[str, tuple[str, dict]] = {}

    def add(self, ids: list[str], documents: list[str], metadatas: list[dict]):
        for idx, memory_id in enumerate(ids):
            self.docs[memory_id] = (documents[idx], metadatas[idx])

    def query(self, query_texts: list[str], n_results: int):
        query = query_texts[0].lower()
        hits = []
        for memory_id, (doc, meta) in self.docs.items():
            if query in doc.lower():
                hits.append((memory_id, doc, meta, 0.0))
        hits = hits[:n_results]
        return {
            "ids": [[h[0] for h in hits]],
            "documents": [[h[1] for h in hits]],
            "metadatas": [[h[2] for h in hits]],
            "distances": [[h[3] for h in hits]],
        }

    def get(self, ids: list[str] | None = None, include: list[str] | None = None):
        if ids is None:
            ids = list(self.docs.keys())
        docs = [self.docs[i][0] for i in ids if i in self.docs]
        metas = [self.docs[i][1] for i in ids if i in self.docs]
        return {"ids": [i for i in ids if i in self.docs], "documents": docs, "metadatas": metas}

    def delete(self, ids: list[str]):
        for memory_id in ids:
            self.docs.pop(memory_id, None)

    def update(self, ids: list[str], documents: list[str], metadatas: list[dict]):
        for i, memory_id in enumerate(ids):
            if memory_id in self.docs:
                self.docs[memory_id] = (documents[i], metadatas[i])

    def count(self):
        return len(self.docs)


class _FakeChromaClient:
    def __init__(self, path: str):
        self.path = path
        self.collection = _FakeCollection()

    def get_or_create_collection(self, **kwargs):
        return self.collection


class TestChromaEpisodeStore:
    def test_save_and_semantic_search(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(chroma_store_mod, "chromadb", None)
        store = ChromaEpisodeStore(_make_config(tmp_path, chroma_enabled=True))

        memory_id = store.save("episode", {"text": "deploy failed on nginx restart"})
        rows = store.search("deploy", limit=5)
        assert any(row["id"] == memory_id for row in rows)

    def test_fallback_embedding_function(self, monkeypatch, tmp_path: Path):
        class _Embeddings:
            class OllamaEmbeddingFunction:
                def __init__(self, *args, **kwargs):
                    raise RuntimeError("ollama offline")

            class SentenceTransformerEmbeddingFunction:
                def __init__(self, *args, **kwargs):
                    pass

        monkeypatch.setattr(chroma_store_mod, "embedding_functions", _Embeddings)
        monkeypatch.setattr(chroma_store_mod, "chromadb", types.SimpleNamespace(PersistentClient=_FakeChromaClient))
        store = ChromaEpisodeStore(_make_config(tmp_path, chroma_enabled=True))
        assert store._embedding_backend == "sentence-transformers"

    def test_collection_persists_across_restart(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(chroma_store_mod, "chromadb", None)
        cfg = _make_config(tmp_path, chroma_enabled=True)
        store_1 = ChromaEpisodeStore(cfg)
        memory_id = store_1.save("episode", {"text": "persist this episode"})

        store_2 = ChromaEpisodeStore(cfg)
        rows = store_2.search("persist", limit=5)
        assert any(row["id"] == memory_id for row in rows)


class TestMemoryRouter:
    def test_routing_episode_to_chroma_and_sqlite(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(chroma_store_mod, "chromadb", None)
        router = MemoryRouter("s1", _make_config(tmp_path, chroma_enabled=True))
        memory_id = router.save("episode", {"text": "episode route check"})

        assert router.sqlite.load_by_id(memory_id) is not None
        chroma_rows = router.chroma.search("episode", limit=5)
        assert any(row["id"] == memory_id for row in chroma_rows)

    def test_routing_fact_to_sqlite(self, tmp_path: Path):
        router = MemoryRouter("s1", _make_config(tmp_path))
        memory_id = router.save("fact", {"text": "sqlite route"})
        assert router.sqlite.load_by_id(memory_id) is not None

    def test_routing_working_to_redis(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(redis_store_mod, "redis", types.SimpleNamespace(Redis=_FakeRedisClient))
        router = MemoryRouter("s1", _make_config(tmp_path, redis_enabled=True))
        memory_id = router.save("working", {"text": "redis route"})
        rows = router.redis.load("redis", limit=5)
        assert any(row["id"] == memory_id for row in rows)

    def test_merge_deduplication(self, tmp_path: Path):
        router = MemoryRouter("s1", _make_config(tmp_path))
        router.sqlite.search = lambda *args, **kwargs: [  # type: ignore[method-assign]
            {
                "id": "a",
                "type": "fact",
                "content": {"text": "the same content"},
                "salience": 0.9,
                "relevance_score": 0.9,
            }
        ]
        router.chroma = types.SimpleNamespace(  # type: ignore[assignment]
            search=lambda *args, **kwargs: [
                {
                    "id": "b",
                    "type": "episode",
                    "content": {"text": "the same content"},
                    "salience": 0.2,
                    "relevance_score": 0.8,
                }
            ],
            load=lambda *args, **kwargs: [],
            list_all=lambda *args, **kwargs: [],
            update=lambda *args, **kwargs: False,
            delete=lambda *args, **kwargs: False,
        )

        rows = router.search("same", limit=10)
        assert len(rows) == 1
        assert rows[0]["id"] == "a"

    def test_fallback_when_chroma_unavailable(self, monkeypatch, tmp_path: Path):
        class _BrokenChroma:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("chroma unavailable")

        monkeypatch.setattr(router_mod, "ChromaEpisodeStore", _BrokenChroma)
        router = MemoryRouter("s1", _make_config(tmp_path, chroma_enabled=True))
        assert router.chroma is None
        memory_id = router.save("fact", {"text": "still works"})
        assert router.sqlite.load_by_id(memory_id) is not None


class TestMemoryClassifier:
    def test_salience_decay_formula(self):
        now = int(time.time())
        one_year_ago = now - 365 * 86400
        stale_access = one_year_ago
        score = MemoryClassifier.calculate_salience(1.0, "fact", one_year_ago, stale_access)
        assert 0.45 <= score <= 0.55

    def test_recency_boost(self):
        now = int(time.time())
        score = MemoryClassifier.calculate_salience(0.7, "fact", now, now)
        assert score >= 0.8

    def test_should_archive_threshold(self):
        assert MemoryClassifier.should_archive(0.2, "task") is True
        assert MemoryClassifier.should_archive(0.5, "task") is False

    def test_reflection_never_archived(self):
        assert MemoryClassifier.should_archive(0.0, "reflection") is False


class TestMemoryDecayEngine:
    def test_decay_pass_archives_low_salience(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        memory_id = store.save("fact", {"text": "old low salience"})
        old = int(time.time()) - 400 * 86400
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE facts SET salience = ?, created_at = ?, last_accessed = ? WHERE id = ?",
                (0.05, old, old, memory_id),
            )
        engine = MemoryDecayEngine(store)
        archived = engine.decay_pass()
        assert archived == 1
        assert store.load_by_id(memory_id) is None

    def test_decay_pass_preserves_reflections(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        memory_id = store.save("reflection", {"text": "keep this", "salience": 0.0})
        old = int(time.time()) - 1000 * 86400
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE facts SET salience = ?, created_at = ?, last_accessed = ? WHERE id = ?",
                (0.0, old, old, memory_id),
            )
        engine = MemoryDecayEngine(store)
        engine.decay_pass()
        assert store.load_by_id(memory_id) is not None

    def test_decay_count_returned(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        ids = [store.save("fact", {"text": f"old-{i}"}) for i in range(2)]
        old = int(time.time()) - 500 * 86400
        with sqlite3.connect(store.db_path) as conn:
            for memory_id in ids:
                conn.execute(
                    "UPDATE facts SET salience = ?, created_at = ?, last_accessed = ? WHERE id = ?",
                    (0.01, old, old, memory_id),
                )
        archived = MemoryDecayEngine(store).decay_pass()
        assert archived == 2


class TestMemoryLinker:
    def test_link_async_does_not_block(self, tmp_path: Path, monkeypatch):
        router = MemoryRouter("s1", _make_config(tmp_path))
        linker = MemoryLinker(router)

        def _slow_link(*args, **kwargs):
            time.sleep(0.2)

        monkeypatch.setattr(linker, "_link_sync", _slow_link)
        start = time.perf_counter()
        linker.link_async("memory-1", {"text": "new"})
        elapsed = time.perf_counter() - start
        assert elapsed < 0.1

    def test_contradicts_lowers_existing_salience(self, tmp_path: Path, monkeypatch):
        router = MemoryRouter("s1", _make_config(tmp_path))
        existing_id = router.save("fact", {"text": "sky is blue", "salience": 0.8})
        new_id = router.save("fact", {"text": "sky is not blue"})
        linker = MemoryLinker(router)

        monkeypatch.setattr(linker, "_candidate_ids", lambda *_: [existing_id])
        monkeypatch.setattr(linker, "_infer_link_type", lambda *_: "contradicts")

        before = router.sqlite.load_by_id(existing_id)["salience"]
        linker._link_sync(new_id, {"text": "sky is not blue"})
        after_row = router.sqlite.load_by_id(existing_id)
        assert after_row is not None
        assert after_row["salience"] == pytest.approx(max(0.0, before - 0.15), rel=1e-5)
        assert after_row["content"]["contradicted_by"] == new_id

    def test_null_link_not_saved(self, tmp_path: Path, monkeypatch):
        router = MemoryRouter("s1", _make_config(tmp_path))
        existing_id = router.save("fact", {"text": "a"})
        new_id = router.save("fact", {"text": "b"})
        linker = MemoryLinker(router)

        monkeypatch.setattr(linker, "_candidate_ids", lambda *_: [existing_id])
        monkeypatch.setattr(linker, "_infer_link_type", lambda *_: None)
        linker._link_sync(new_id, {"text": "b"})

        with sqlite3.connect(router.sqlite.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]
        assert count == 0


class TestSubagentInbox:
    def test_high_confidence_auto_accepted(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        store.inbox_write("agent-a", {"text": "high confidence"}, 0.95, "fact")
        accepted = store.inbox_review()
        assert len(accepted) == 1
        assert store.load_by_id(accepted[0]) is not None

    def test_low_confidence_discarded(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        inbox_id = store.inbox_write("agent-a", {"text": "low confidence"}, 0.4, "fact")
        accepted = store.inbox_review()
        assert accepted == []
        with sqlite3.connect(store.db_path) as conn:
            row = conn.execute("SELECT reviewed, accepted FROM subagent_inbox WHERE id = ?", (inbox_id,)).fetchone()
        assert row == (1, 0)

    def test_medium_confidence_accepted_after_24h(self, tmp_path: Path):
        store = SQLiteFactStore(_make_config(tmp_path))
        inbox_id = store.inbox_write("agent-a", {"text": "medium confidence"}, 0.7, "fact")
        old = int(time.time()) - 25 * 3600
        with sqlite3.connect(store.db_path) as conn:
            conn.execute("UPDATE subagent_inbox SET created_at = ? WHERE id = ?", (old, inbox_id))
        accepted = store.inbox_review()
        assert len(accepted) == 1
        assert store.load_by_id(accepted[0]) is not None
