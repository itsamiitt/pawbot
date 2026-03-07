"""Time-based memory salience decay engine."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from loguru import logger

from pawbot.agent.memory._compat import MEMORY_TYPE_CONFIG, coerce_float, coerce_int as _coerce_int
from pawbot.agent.memory.classifier import MemoryClassifier

if TYPE_CHECKING:
    from pawbot.agent.memory.sqlite_store import SQLiteFactStore


class MemoryDecayEngine:
    JOB_NAME = "memory_decay"
    CRON_SCHEDULE = "0 3 * * *"

    def __init__(self, sqlite: SQLiteFactStore):
        self.sqlite = sqlite

    def decay_pass(self) -> int:
        archived_count = 0
        updated_count = 0
        with self.sqlite._connect() as conn:
            rows = conn.execute(
                "SELECT id, type, content, salience, created_at, last_accessed, updated_at, tags, source "
                "FROM facts"
            ).fetchall()
            for row in rows:
                memory_id = row["id"]
                type_ = row["type"]
                base_sal = coerce_float(row["salience"], 1.0)
                created_at = _coerce_int(row["created_at"], int(time.time()))
                last_accessed = _coerce_int(row["last_accessed"], created_at)

                new_sal = MemoryClassifier.calculate_salience(base_sal, type_, created_at, last_accessed)
                if MemoryClassifier.should_archive(new_sal, type_):
                    self.sqlite._archive_fact(conn, row, new_sal)
                    conn.execute("DELETE FROM facts WHERE id = ?", (memory_id,))
                    archived_count += 1
                elif abs(new_sal - base_sal) > 0.01:
                    conn.execute(
                        "UPDATE facts SET salience = ?, updated_at = ? WHERE id = ?",
                        (new_sal, int(time.time()), memory_id),
                    )
                    updated_count += 1
        logger.info(
            "Decay pass complete: archived={}, updated={}",
            archived_count,
            updated_count,
        )
        if archived_count > 100:
            logger.warning("Memory decay: {} memories archived", archived_count)
        return archived_count
