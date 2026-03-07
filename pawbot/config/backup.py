"""Configuration backup system — automatic versioned backups (Phase 13.2).

Creates rotated backups before config changes:
  config.json → config.json.bak → config.json.bak.1 → ... → config.json.bak.5

Features:
  - Automatic rotation with configurable max backups (default: 5)
  - Rate-limited to avoid excessive backups (min 60s between)
  - Restore from any backup version
  - Deep-diff comparison between current config and any backup
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from loguru import logger


class ConfigBackupManager:
    """Automatic config backup with rotation."""

    MAX_BACKUPS = 5           # Keep N most recent backups
    MIN_BACKUP_INTERVAL = 60  # Don't backup more than once per minute

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self._last_backup_time: float = 0

    # ── Backup ────────────────────────────────────────────────────────────

    def backup_before_write(self) -> str | None:
        """Create a backup before writing a config change.

        Returns:
            Backup file path, or None if no backup needed.
        """
        if not self.config_path.exists():
            return None

        # Rate limit backups
        now = time.time()
        if now - self._last_backup_time < self.MIN_BACKUP_INTERVAL:
            return None

        self._rotate_backups()

        # Create new .bak
        bak = self._bak_path(0)
        shutil.copy2(self.config_path, bak)
        self._last_backup_time = now

        logger.debug("Config backed up: {}", bak)
        return str(bak)

    # ── Restore ───────────────────────────────────────────────────────────

    def restore(self, version: int = 0) -> bool:
        """Restore config from a backup.

        Args:
            version: 0 = latest .bak, 1 = .bak.1, etc.

        Returns:
            True if restored successfully.
        """
        backup = self._bak_path(version)

        if not backup.exists():
            logger.error("Backup not found: {}", backup)
            return False

        # Backup current before restoring
        self.backup_before_write()

        shutil.copy2(backup, self.config_path)
        logger.info("Config restored from {}", backup)
        return True

    # ── List Backups ──────────────────────────────────────────────────────

    def list_backups(self) -> list[dict[str, Any]]:
        """List all available backups with metadata."""
        backups = []

        for version in range(self.MAX_BACKUPS + 1):
            bak = self._bak_path(version)
            if bak.exists():
                stat = bak.stat()
                backups.append({
                    "version": version,
                    "path": str(bak),
                    "size_bytes": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "age_hours": round((time.time() - stat.st_mtime) / 3600, 1),
                })

        return backups

    # ── Diff ──────────────────────────────────────────────────────────────

    def diff(self, version: int = 0) -> dict[str, Any]:
        """Compare current config with a backup version.

        Returns:
            Dict with added, removed, and changed keys.
        """
        backup_path = self._bak_path(version)

        if not backup_path.exists() or not self.config_path.exists():
            return {"error": "Files not found", "added": [], "removed": [], "changed": []}

        try:
            current = json.loads(self.config_path.read_text(encoding="utf-8"))
            backup = json.loads(backup_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": str(e), "added": [], "removed": [], "changed": []}

        return self._deep_diff(backup, current, prefix="")

    # ── Internal ──────────────────────────────────────────────────────────

    def _bak_path(self, version: int) -> Path:
        """Get the backup file path for a given version."""
        suffix = self.config_path.suffix
        if version == 0:
            return self.config_path.with_suffix(f"{suffix}.bak")
        return self.config_path.with_suffix(f"{suffix}.bak.{version}")

    def _rotate_backups(self) -> None:
        """Rotate existing backups: .bak.4 → delete, .bak.3 → .bak.4, etc."""
        for i in range(self.MAX_BACKUPS - 1, 0, -1):
            old = self._bak_path(i)
            new = self._bak_path(i + 1)
            if old.exists():
                if new.exists():
                    new.unlink()
                old.rename(new)

        # Current .bak → .bak.1
        bak = self._bak_path(0)
        bak1 = self._bak_path(1)
        if bak.exists():
            if bak1.exists():
                bak1.unlink()
            bak.rename(bak1)

    def _deep_diff(
        self, old: dict, new: dict, prefix: str = ""
    ) -> dict[str, list[str]]:
        """Deep diff two dicts, returning added/removed/changed key paths."""
        result: dict[str, list[str]] = {"added": [], "removed": [], "changed": []}

        all_keys = set(old.keys()) | set(new.keys())
        for key in sorted(all_keys):
            full_key = f"{prefix}.{key}" if prefix else key
            if key not in old:
                result["added"].append(full_key)
            elif key not in new:
                result["removed"].append(full_key)
            elif old[key] != new[key]:
                if isinstance(old[key], dict) and isinstance(new[key], dict):
                    sub = self._deep_diff(old[key], new[key], full_key)
                    result["added"].extend(sub["added"])
                    result["removed"].extend(sub["removed"])
                    result["changed"].extend(sub["changed"])
                else:
                    result["changed"].append(full_key)

        return result
