"""Soul Evolution Engine — auto-evolving personality from usage patterns.

Phase 20: The soul evolution system handles:
  - SoulEvolution       — Proposes updates to SOUL.md based on observed patterns
  - DailyJournal        — Auto-writes memory/YYYY-MM-DD.md at end of session
  - SessionContinuity   — Reads all soul files on session boot
  - CoreDistiller       — Distills daily journals into core.md
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("pawbot.soul")


# ══════════════════════════════════════════════════════════════════════════════
#  Daily Journal
# ══════════════════════════════════════════════════════════════════════════════


class DailyJournal:
    """Writes and manages daily memory journal files.

    Files are stored at: {workspace}/memory/YYYY-MM-DD.md
    Each entry records events, conversations, and actions taken.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.memory_dir = workspace / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._events: list[dict[str, Any]] = []

    @property
    def today_path(self) -> Path:
        return self.memory_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"

    def append_event(
        self,
        event_type: str,
        content: str,
        importance: float = 0.5,
        tags: list[str] | None = None,
    ) -> None:
        """Append an event to today's buffer.

        Event types: conversation, action, decision, error, learning, observation
        Importance: 0.0 (trivial) to 1.0 (critical)
        """
        event = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": event_type,
            "content": content,
            "importance": importance,
            "tags": tags or [],
        }
        self._events.append(event)

    def flush(self) -> Path:
        """Write buffered events to today's journal file.

        Appends to existing file or creates a new one.
        Returns path to the journal file.
        """
        if not self._events:
            return self.today_path

        path = self.today_path
        is_new = not path.exists()

        with open(path, "a", encoding="utf-8") as f:
            if is_new:
                f.write(f"# Daily Journal — {datetime.now().strftime('%Y-%m-%d')}\n\n")

            for event in self._events:
                importance_marker = "⭐ " if event["importance"] >= 0.8 else ""
                tags_str = f" [{', '.join(event['tags'])}]" if event["tags"] else ""
                f.write(
                    f"- **{event['time']}** [{event['type']}] "
                    f"{importance_marker}{event['content']}{tags_str}\n"
                )

            f.write("\n")

        count = len(self._events)
        self._events.clear()
        logger.info("Flushed %d events to %s", count, path.name)
        return path

    def get_today(self) -> str:
        """Read today's journal."""
        if self.today_path.exists():
            return self.today_path.read_text(encoding="utf-8")
        return ""

    def get_recent(self, days: int = 3) -> list[dict[str, str]]:
        """Get recent journal entries (today + N previous days)."""
        results: list[dict[str, str]] = []
        for i in range(days):
            date = datetime.now() - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            path = self.memory_dir / f"{date_str}.md"
            if path.exists():
                results.append({
                    "date": date_str,
                    "content": path.read_text(encoding="utf-8"),
                })
        return results

    def list_all(self) -> list[str]:
        """List all journal dates (YYYY-MM-DD format)."""
        files = sorted(self.memory_dir.glob("????-??-??.md"), reverse=True)
        return [f.stem for f in files]


# ══════════════════════════════════════════════════════════════════════════════
#  Core Distiller
# ══════════════════════════════════════════════════════════════════════════════


class CoreDistiller:
    """Distills daily journals into core.md — persistent knowledge.

    core.md is the single source of truth for long-term knowledge.
    It is updated by reviewing daily journals and extracting:
    - Recurring patterns
    - Learned preferences
    - Important facts
    - Useful procedures
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.core_path = workspace / "memory" / "core.md"
        self.memory_dir = workspace / "memory"

    def get_core(self) -> str:
        """Read current core memory."""
        if self.core_path.exists():
            return self.core_path.read_text(encoding="utf-8")
        return ""

    def update_core(self, new_content: str) -> None:
        """Replace core memory content."""
        self.core_path.parent.mkdir(parents=True, exist_ok=True)
        self.core_path.write_text(new_content, encoding="utf-8")

    def append_to_core(self, section: str, entry: str) -> None:
        """Append an entry to a specific section of core.md."""
        content = self.get_core()
        section_header = f"## {section}"

        if section_header in content:
            # Append under existing section
            lines = content.split("\n")
            insert_idx = None
            for i, line in enumerate(lines):
                if line.strip() == section_header:
                    # Find end of section (next ## or end of file)
                    for j in range(i + 1, len(lines)):
                        if lines[j].startswith("## "):
                            insert_idx = j
                            break
                    if insert_idx is None:
                        insert_idx = len(lines)
                    break

            if insert_idx is not None:
                lines.insert(insert_idx, f"- {entry}\n")
                content = "\n".join(lines)
        else:
            # Create new section
            content += f"\n{section_header}\n\n- {entry}\n"

        self.update_core(content)

    def get_unprocessed_journals(self, days_back: int = 7) -> list[Path]:
        """Get journal files that haven't been distilled yet."""
        processed_marker = self.workspace / "memory" / ".last_distilled"
        last_distilled = 0.0
        if processed_marker.exists():
            try:
                last_distilled = float(processed_marker.read_text())
            except (ValueError, OSError):
                pass

        journals: list[Path] = []
        for i in range(days_back):
            date = datetime.now() - timedelta(days=i)
            path = self.memory_dir / f"{date.strftime('%Y-%m-%d')}.md"
            if path.exists() and path.stat().st_mtime > last_distilled:
                journals.append(path)
        return journals

    def mark_distilled(self) -> None:
        """Mark that distillation has been run."""
        marker = self.workspace / "memory" / ".last_distilled"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(time.time()))


# ══════════════════════════════════════════════════════════════════════════════
#  Session Continuity
# ══════════════════════════════════════════════════════════════════════════════


class SessionContinuity:
    """Reads all soul files on session boot to restore context.

    Boot sequence:
    1. SOUL.md — identity and values
    2. USER.md — user preferences
    3. memory/core.md — persistent knowledge
    4. memory/YYYY-MM-DD.md (today + yesterday) — recent context
    5. HEARTBEAT.md — proactive tasks
    6. STRUCTURED_MEMORY.md / PINNED_MEMORY.md — if they exist
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def boot(self) -> dict[str, str]:
        """Execute session boot sequence. Returns loaded file contents."""
        loaded: dict[str, str] = {}

        # Priority-ordered files to load
        boot_files = [
            ("soul", "SOUL.md"),
            ("user", "USER.md"),
            ("core_memory", "memory/core.md"),
            ("heartbeat", "HEARTBEAT.md"),
            ("structured_memory", "STRUCTURED_MEMORY.md"),
            ("pinned_memory", "PINNED_MEMORY.md"),
        ]

        for key, rel_path in boot_files:
            path = self.workspace / rel_path
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8")
                    loaded[key] = content
                    logger.debug("Boot loaded: %s (%d bytes)", rel_path, len(content))
                except OSError as exc:
                    logger.warning("Failed to load %s: %s", rel_path, exc)

        # Load daily journals (today + yesterday)
        journal = DailyJournal(self.workspace)
        recent = journal.get_recent(days=2)
        for entry in recent:
            key = f"journal_{entry['date']}"
            loaded[key] = entry["content"]

        logger.info(
            "Session boot: loaded %d files (%d bytes total)",
            len(loaded), sum(len(v) for v in loaded.values()),
        )
        return loaded


# ══════════════════════════════════════════════════════════════════════════════
#  Soul Evolution
# ══════════════════════════════════════════════════════════════════════════════


class SoulPatch:
    """A proposed edit to a soul file."""

    def __init__(
        self,
        target_file: str,
        section: str,
        action: str,       # "add" | "modify" | "remove"
        content: str,
        reason: str,
    ) -> None:
        self.target_file = target_file
        self.section = section
        self.action = action
        self.content = content
        self.reason = reason
        self.approved = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_file": self.target_file,
            "section": self.section,
            "action": self.action,
            "content": self.content[:200],
            "reason": self.reason,
            "approved": self.approved,
        }


class SoulEvolution:
    """Auto-evolve soul files based on usage patterns.

    The evolution engine:
    1. Reviews daily journals for patterns and lessons
    2. Proposes patches to soul files (SOUL.md, AGENTS.md)
    3. Applies patches after user approval (or auto-apply for low-risk changes)
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.pending_patches: list[SoulPatch] = []

    def propose_patch(
        self,
        target_file: str,
        section: str,
        action: str,
        content: str,
        reason: str,
    ) -> SoulPatch:
        """Create a proposed soul file edit."""
        patch = SoulPatch(
            target_file=target_file,
            section=section,
            action=action,
            content=content,
            reason=reason,
        )
        self.pending_patches.append(patch)
        logger.info(
            "Soul patch proposed: %s %s/%s — %s",
            action, target_file, section, reason[:60],
        )
        return patch

    def apply_patch(self, patch: SoulPatch) -> bool:
        """Apply a soul patch to the target file.

        Returns True if successfully applied.
        """
        target_path = self.workspace / patch.target_file
        if not target_path.exists():
            logger.warning("Target file doesn't exist: %s", patch.target_file)
            return False

        try:
            content = target_path.read_text(encoding="utf-8")

            if patch.action == "add":
                section_header = f"## {patch.section}"
                if section_header in content:
                    # Append under existing section
                    idx = content.index(section_header) + len(section_header)
                    next_section = content.find("\n## ", idx)
                    if next_section == -1:
                        content += f"\n{patch.content}\n"
                    else:
                        content = (
                            content[:next_section]
                            + f"\n{patch.content}\n"
                            + content[next_section:]
                        )
                else:
                    content += f"\n## {patch.section}\n\n{patch.content}\n"

            elif patch.action == "modify":
                # This requires more sophisticated text replacement
                content += f"\n<!-- Evolution ({patch.reason}): {patch.content[:100]} -->\n"

            elif patch.action == "remove":
                # Mark section for removal (don't actually delete — log it)
                content = content.replace(
                    patch.content,
                    f"<!-- Removed: {patch.content[:50]} (reason: {patch.reason}) -->\n",
                )

            target_path.write_text(content, encoding="utf-8")
            patch.approved = True
            logger.info("Applied soul patch: %s/%s", patch.target_file, patch.section)
            return True

        except Exception as exc:
            logger.error("Failed to apply soul patch: %s", exc)
            return False

    def get_pending(self) -> list[dict[str, Any]]:
        """Get all pending (unapproved) patches."""
        return [p.to_dict() for p in self.pending_patches if not p.approved]

    def approve_all(self) -> int:
        """Apply all pending patches. Returns count applied."""
        count = 0
        for patch in self.pending_patches:
            if not patch.approved:
                if self.apply_patch(patch):
                    count += 1
        return count
