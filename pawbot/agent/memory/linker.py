"""Cross-memory link inference engine."""

from __future__ import annotations

import threading
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from loguru import logger

from pawbot.agent.memory._compat import LINK_TYPES, memory_text

if TYPE_CHECKING:
    from pawbot.agent.memory.router import MemoryRouter


class MemoryLinker:
    def __init__(self, router: MemoryRouter):
        self.router = router

    def link_async(self, new_memory_id: str, new_memory: dict[str, Any]) -> None:
        thread = threading.Thread(
            target=self._link_sync, args=(new_memory_id, dict(new_memory)), daemon=True
        )
        thread.start()

    def _candidate_ids(self, new_memory: dict[str, Any]) -> list[str]:
        text = memory_text(new_memory)
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
