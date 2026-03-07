"""Redis-backed working memory store."""

from __future__ import annotations

import json
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
    import redis  # type: ignore[import-not-found]
except Exception:
    redis = None


class RedisWorkingMemory(MemoryProvider):
    KEY_PREFIX = "session"
    DEFAULT_TTL = 3600
    MAX_MESSAGES = 20

    def __init__(self, session_id: str, config: dict[str, Any] | Any):
        cfg = to_config_dict(config)
        redis_cfg = cfg.get("memory", {}).get("backends", {}).get("redis", {})
        host = redis_cfg.get("host", "localhost")
        port = coerce_int(redis_cfg.get("port", 6379), 6379)
        db = coerce_int(redis_cfg.get("db", 0), 0)
        self.ttl = coerce_int(redis_cfg.get("ttl", self.DEFAULT_TTL), self.DEFAULT_TTL)
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
        text = memory_text(payload.get("content", {}))
        rel = relevance_score(query, text)
        return {
            "id": memory_id,
            "type": payload.get("type", "working"),
            "content": payload.get("content", {}),
            "salience": coerce_float(payload.get("salience", 1.0), 1.0),
            "created_at": coerce_int(payload.get("created_at", int(time.time())), int(time.time())),
            "updated_at": coerce_int(payload.get("updated_at", int(time.time())), int(time.time())),
            "last_accessed": coerce_int(
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
            "salience": coerce_float(payload.get("salience", 1.0), 1.0),
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
                        "salience": coerce_float(raw.get("salience", 1.0), 1.0),
                        "created_at": coerce_int(raw.get("created_at", int(time.time())), int(time.time())),
                        "updated_at": coerce_int(raw.get("updated_at", int(time.time())), int(time.time())),
                        "last_accessed": coerce_int(
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
            text = memory_text(payload.get("content", {})).lower()
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
            existing["salience"] = coerce_float(payload.get("salience", existing.get("salience", 1.0)), 1.0)
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
                    "salience": str(coerce_float(payload.get("salience", raw.get("salience", 1.0)), 1.0)),
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

