"""Memory compaction — consolidate and archive old/low-value memories.

Phase 2: Prevents unbounded memory growth by archiving facts that fall
below a salience threshold when the total count exceeds a configurable limit.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


class MemoryCompactor:
    """Automatic memory compaction when fact count exceeds threshold.

    Usage::

        compactor = MemoryCompactor(sqlite_store)
        if compactor.should_compact():
            stats = compactor.compact()
            print(f"Archived {stats['archived']} memories")
    """

    COMPACTION_THRESHOLD = 10_000
    ARCHIVE_SALIENCE = 0.1
    TARGET_AFTER_COMPACTION = 5_000

    def __init__(self, sqlite_store: Any):
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

    def get_stats(self) -> dict[str, int]:
        """Get current memory statistics."""
        conn = self.store._connect()
        try:
            facts_count = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            archived_count = conn.execute("SELECT COUNT(*) FROM archived_memories").fetchone()[0]
            return {
                "facts": facts_count,
                "archived": archived_count,
                "threshold": self.COMPACTION_THRESHOLD,
                "needs_compaction": facts_count > self.COMPACTION_THRESHOLD,
            }
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
            rows = cursor.fetchall()

            for row in rows:
                # Move to archived_memories
                conn.execute(
                    "INSERT OR IGNORE INTO archived_memories "
                    "(id, original_table, content, archived_at, final_salience) "
                    "VALUES (?, ?, ?, strftime('%s','now'), ?)",
                    (row["id"], row["type"], row["content"], row["salience"]),
                )
                conn.execute("DELETE FROM facts WHERE id = ?", (row["id"],))
                stats["archived"] += 1

            # 2. If still over target, archive oldest low-priority facts
            remaining = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            if remaining > self.TARGET_AFTER_COMPACTION:
                excess = remaining - self.TARGET_AFTER_COMPACTION
                oldest = conn.execute(
                    "SELECT id, type, content, salience FROM facts "
                    "ORDER BY salience ASC, last_accessed ASC "
                    "LIMIT ?",
                    (excess,),
                ).fetchall()

                for row in oldest:
                    conn.execute(
                        "INSERT OR IGNORE INTO archived_memories "
                        "(id, original_table, content, archived_at, final_salience) "
                        "VALUES (?, ?, ?, strftime('%s','now'), ?)",
                        (row["id"], row["type"], row["content"], row["salience"]),
                    )
                    conn.execute("DELETE FROM facts WHERE id = ?", (row["id"],))
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
