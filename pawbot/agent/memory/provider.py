"""Abstract base class for memory providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


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
