"""FileInbox / FileOutbox — durable file-based message passing.

Phase 18: Workers communicate through the filesystem for durability.
Each worker has its own inbox and outbox directory:

  shared/inbox/worker-1/task-abc123.json    → Commander writes task specs
  shared/outbox/worker-1/task-abc123.json   → Worker writes results

This survives process restarts and works across machines (shared FS / NFS).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("pawbot.fleet.inbox")


@dataclass
class InboxMessage:
    """A message in a worker's inbox or outbox."""

    task_id: str
    worker_id: str
    message_type: str            # "task_assignment" | "task_result" | "cancel" | "probe"
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    priority: int = 5

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "message_type": self.message_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InboxMessage:
        return cls(
            task_id=data["task_id"],
            worker_id=data["worker_id"],
            message_type=data["message_type"],
            payload=data.get("payload", {}),
            timestamp=data.get("timestamp", time.time()),
            priority=data.get("priority", 5),
        )


class FileInbox:
    """File-based inbox for receiving task assignments.

    Commander writes task specs as JSON files;
    Workers poll their inbox directory for new tasks.
    """

    def __init__(self, base_dir: Path, worker_id: str) -> None:
        self.worker_id = worker_id
        self.inbox_dir = base_dir / "inbox" / worker_id
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self._processed_dir = self.inbox_dir / ".processed"
        self._processed_dir.mkdir(exist_ok=True)

    def write(self, message: InboxMessage) -> Path:
        """Write a message to this worker's inbox.

        Returns the path to the written file.
        """
        filename = f"{message.message_type}-{message.task_id}.json"
        filepath = self.inbox_dir / filename
        content = json.dumps(message.to_dict(), indent=2, ensure_ascii=False)

        # Atomic write: write to temp file first, then rename
        tmp_path = filepath.with_suffix(".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(filepath)

        logger.debug(
            "Inbox write: %s → %s (%s)",
            message.message_type, self.worker_id, message.task_id,
        )
        return filepath

    def read_all(self) -> list[InboxMessage]:
        """Read all pending messages in the inbox.

        Returns messages sorted by priority (lower = higher priority).
        """
        messages: list[InboxMessage] = []
        for filepath in sorted(self.inbox_dir.glob("*.json")):
            if filepath.name.startswith("."):
                continue
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                messages.append(InboxMessage.from_dict(data))
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Corrupt inbox file %s: %s", filepath, exc)
        return sorted(messages, key=lambda m: m.priority)

    def read_one(self, task_id: str) -> InboxMessage | None:
        """Read a specific message by task_id."""
        for filepath in self.inbox_dir.glob(f"*-{task_id}.json"):
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                return InboxMessage.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                continue
        return None

    def acknowledge(self, task_id: str) -> bool:
        """Move a message to .processed/ after handling.

        Returns True if the message was found and acknowledged.
        """
        for filepath in self.inbox_dir.glob(f"*-{task_id}.json"):
            dest = self._processed_dir / filepath.name
            try:
                filepath.rename(dest)
                logger.debug("Inbox ack: %s (%s)", self.worker_id, task_id)
                return True
            except OSError as exc:
                logger.warning("Failed to ack %s: %s", filepath, exc)
        return False

    def delete(self, task_id: str) -> bool:
        """Delete a message entirely (no trace)."""
        for filepath in self.inbox_dir.glob(f"*-{task_id}.json"):
            try:
                filepath.unlink()
                return True
            except OSError:
                pass
        return False

    @property
    def pending_count(self) -> int:
        return sum(1 for _ in self.inbox_dir.glob("*.json") if not _.name.startswith("."))

    def clear(self) -> int:
        """Clear all pending messages. Returns count of cleared messages."""
        count = 0
        for filepath in self.inbox_dir.glob("*.json"):
            if not filepath.name.startswith("."):
                filepath.unlink(missing_ok=True)
                count += 1
        return count


class FileOutbox:
    """File-based outbox for writing task results.

    Workers write completed results as JSON files;
    Commander polls each worker's outbox for results.
    """

    def __init__(self, base_dir: Path, worker_id: str) -> None:
        self.worker_id = worker_id
        self.outbox_dir = base_dir / "outbox" / worker_id
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self._collected_dir = self.outbox_dir / ".collected"
        self._collected_dir.mkdir(exist_ok=True)

    def write(self, message: InboxMessage) -> Path:
        """Write a result message to this worker's outbox."""
        filename = f"{message.message_type}-{message.task_id}.json"
        filepath = self.outbox_dir / filename
        content = json.dumps(message.to_dict(), indent=2, ensure_ascii=False)

        tmp_path = filepath.with_suffix(".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.rename(filepath)

        logger.debug(
            "Outbox write: %s → %s (%s)",
            self.worker_id, message.message_type, message.task_id,
        )
        return filepath

    def read_all(self) -> list[InboxMessage]:
        """Read all pending results in the outbox."""
        results: list[InboxMessage] = []
        for filepath in sorted(self.outbox_dir.glob("*.json")):
            if filepath.name.startswith("."):
                continue
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                results.append(InboxMessage.from_dict(data))
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Corrupt outbox file %s: %s", filepath, exc)
        return results

    def collect(self, task_id: str) -> InboxMessage | None:
        """Read and move a result to .collected/ (one-time consumption)."""
        for filepath in self.outbox_dir.glob(f"*-{task_id}.json"):
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                msg = InboxMessage.from_dict(data)
                dest = self._collected_dir / filepath.name
                filepath.rename(dest)
                return msg
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                logger.warning("Failed to collect %s: %s", filepath, exc)
        return None

    @property
    def pending_count(self) -> int:
        return sum(1 for _ in self.outbox_dir.glob("*.json") if not _.name.startswith("."))

    def clear(self) -> int:
        """Clear all pending results."""
        count = 0
        for filepath in self.outbox_dir.glob("*.json"):
            if not filepath.name.startswith("."):
                filepath.unlink(missing_ok=True)
                count += 1
        return count


class InboxRouter:
    """Manages inboxes and outboxes for the entire fleet.

    Provides a convenient API for the FleetCommander to dispatch tasks
    and collect results across all workers.
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._inboxes: dict[str, FileInbox] = {}
        self._outboxes: dict[str, FileOutbox] = {}

    def register_worker(self, worker_id: str) -> None:
        """Register a worker's inbox and outbox."""
        self._inboxes[worker_id] = FileInbox(self.base_dir, worker_id)
        self._outboxes[worker_id] = FileOutbox(self.base_dir, worker_id)
        logger.info("Registered inbox/outbox for %s", worker_id)

    def dispatch_task(self, worker_id: str, message: InboxMessage) -> Path | None:
        """Write a task assignment to a worker's inbox."""
        inbox = self._inboxes.get(worker_id)
        if not inbox:
            logger.error("No inbox registered for worker %s", worker_id)
            return None
        return inbox.write(message)

    def collect_results(self, worker_id: str) -> list[InboxMessage]:
        """Read all pending results from a worker's outbox."""
        outbox = self._outboxes.get(worker_id)
        if not outbox:
            return []
        return outbox.read_all()

    def collect_all_results(self) -> dict[str, list[InboxMessage]]:
        """Read all pending results from all workers."""
        return {
            wid: self.collect_results(wid)
            for wid in self._outboxes
        }

    def get_inbox(self, worker_id: str) -> FileInbox | None:
        return self._inboxes.get(worker_id)

    def get_outbox(self, worker_id: str) -> FileOutbox | None:
        return self._outboxes.get(worker_id)

    @property
    def worker_ids(self) -> list[str]:
        return list(self._inboxes.keys())

    def fleet_pending(self) -> dict[str, int]:
        """Get pending message counts per worker."""
        return {
            wid: inbox.pending_count
            for wid, inbox in self._inboxes.items()
        }
