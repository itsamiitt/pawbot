"""Auto-update checker — notifies when a new PawBot version is available (Phase 13.3).

Checks PyPI every 24 hours and caches the result in
~/.pawbot/update-check.json to avoid excessive network requests.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger


UPDATE_CHECK_FILE = Path.home() / ".pawbot" / "update-check.json"
CHECK_INTERVAL_HOURS = 24


class UpdateChecker:
    """Check for PawBot updates periodically."""

    PYPI_URL = "https://pypi.org/pypi/pawbot/json"

    def __init__(self, check_file: Path | None = None):
        self._file = check_file or UPDATE_CHECK_FILE
        self._last_check = self._load_last_check()

    def _load_last_check(self) -> dict[str, Any]:
        if self._file.exists():
            try:
                return json.loads(self._file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"last_check": 0, "latest_version": "", "current_version": ""}

    def _save_check(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            json.dumps(self._last_check, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def should_check(self) -> bool:
        """Check if enough time has passed since last check."""
        last = self._last_check.get("last_check", 0)
        return (time.time() - last) > (CHECK_INTERVAL_HOURS * 3600)

    async def check(self) -> dict[str, Any] | None:
        """Check PyPI for the latest version.

        Returns:
            Dict with version info if update available, None otherwise.
        """
        if not self.should_check():
            cached = self._last_check
            if cached.get("update_available"):
                return cached
            return None

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(self.PYPI_URL)
                r.raise_for_status()
                data = r.json()
                latest = data.get("info", {}).get("version", "")

            current = self._get_current_version()

            self._last_check = {
                "last_check": time.time(),
                "latest_version": latest,
                "current_version": current,
                "update_available": self._is_newer(latest, current),
            }
            self._save_check()

            if self._last_check["update_available"]:
                return self._last_check
            return None

        except Exception as e:
            logger.debug("Update check failed: {}", e)
            self._last_check["last_check"] = time.time()
            self._save_check()
            return None

    def check_sync(self) -> dict[str, Any] | None:
        """Synchronous version of check (for CLI startup).

        Returns:
            Dict with version info if update available, None otherwise.
        """
        if not self.should_check():
            cached = self._last_check
            if cached.get("update_available"):
                return cached
            return None

        try:
            import urllib.request

            req = urllib.request.Request(
                self.PYPI_URL,
                headers={"Accept": "application/json", "User-Agent": "pawbot-update-checker"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                latest = data.get("info", {}).get("version", "")

            current = self._get_current_version()

            self._last_check = {
                "last_check": time.time(),
                "latest_version": latest,
                "current_version": current,
                "update_available": self._is_newer(latest, current),
            }
            self._save_check()

            if self._last_check["update_available"]:
                return self._last_check
            return None

        except Exception as e:
            logger.debug("Update check (sync) failed: {}", e)
            self._last_check["last_check"] = time.time()
            self._save_check()
            return None

    def get_cached_status(self) -> dict[str, Any]:
        """Get the cached update status without making a network request."""
        return dict(self._last_check)

    def mark_dismissed(self) -> None:
        """Mark the current update notification as dismissed."""
        self._last_check["dismissed_version"] = self._last_check.get(
            "latest_version", ""
        )
        self._save_check()

    @staticmethod
    def _is_newer(latest: str, current: str) -> bool:
        """Compare semver versions."""
        if not latest or not current:
            return False
        try:
            from packaging.version import Version

            return Version(latest) > Version(current)
        except Exception:
            # Fallback: simple string comparison
            return latest != current and latest > current

    @staticmethod
    def _get_current_version() -> str:
        """Get the current PawBot version."""
        try:
            from pawbot import __version__

            return __version__
        except Exception:
            return "0.0.0"
