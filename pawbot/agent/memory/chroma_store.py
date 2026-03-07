"""ChromaDB-backed episode store for semantic search."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from loguru import logger

from pawbot.agent.memory._compat import (
    coerce_float,
    coerce_int,
    memory_text,
    relevance_score,
    to_config_dict,
)
from pawbot.agent.memory.provider import MemoryProvider

try:
    import chromadb  # type: ignore[import-not-found]
    from chromadb.utils import embedding_functions
except Exception:
    chromadb = None
    embedding_functions = None


class ChromaEpisodeStore(MemoryProvider):
    COLLECTION_NAME = "pawbot_episodes"
    _fallback_collections: dict[str, dict[str, dict[str, Any]]] = {}

    def __init__(self, config: dict[str, Any] | Any):
        cfg = to_config_dict(config)
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

    @property
    def is_available(self) -> bool:
        """Whether ChromaDB vector search is active (not using fallback)."""
        return not self._use_fallback and self.collection is not None

    def save(self, type: str, content: dict[str, Any]) -> str:
        payload = dict(content)
        memory_id = payload.pop("_memory_id", str(uuid.uuid4()))
        now = int(time.time())
        text = memory_text(payload)
        metadata = {
            "type": type,
            "timestamp": now,
            "goal_id": payload.get("goal_id", ""),
            "session_id": payload.get("session_id", ""),
            "salience": coerce_float(payload.get("salience", 1.0), 1.0),
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
        rel = relevance_score(query, doc)
        return {
            "id": memory_id,
            "type": meta.get("type", "episode"),
            "content": {"text": doc},
            "salience": coerce_float(meta.get("salience", 1.0), 1.0),
            "created_at": coerce_int(meta.get("timestamp", int(time.time())), int(time.time())),
            "updated_at": coerce_int(meta.get("timestamp", int(time.time())), int(time.time())),
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
                    item["relevance_score"] = max(0.0, 1.0 - coerce_float(distances[i], 1.0))
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
            doc = memory_text(content)
            meta = self._fallback[memory_id].get("metadata", {})
            if "salience" in content:
                meta["salience"] = coerce_float(content.get("salience"), 1.0)
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
                meta["salience"] = coerce_float(content.get("salience"), 1.0)
            self.collection.update(ids=[memory_id], documents=[memory_text(content)], metadatas=[meta])
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
