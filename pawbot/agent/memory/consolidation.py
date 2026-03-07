"""MemoryStore — two-layer memory with LLM consolidation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from pawbot.agent.memory._compat import _SAVE_MEMORY_TOOL, coerce_int as _coerce_int
from pawbot.agent.memory.router import MemoryRouter
from pawbot.utils.paths import ensure_dir

if TYPE_CHECKING:
    from pawbot.providers.base import LLMProvider
    from pawbot.session.manager import Session



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
    from pawbot.utils.fs import atomic_write_text

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

    atomic_write_text(Path(migrated_flag), "")


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

    def __init__(
        self,
        workspace: Path,
        *,
        session_id: str | None = None,
        memory_config: dict[str, Any] | None = None,
    ):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"
        self.router = MemoryRouter(session_id or f"workspace:{self.memory_dir}", memory_config or {})
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
