"""Clean up old browser screenshots and cached data (Phase 8.5)."""

import time
from pathlib import Path

from loguru import logger


def cleanup_screenshots(
    directory: Path | None = None,
    max_age_days: int = 7,
) -> int:
    """Delete screenshots older than max_age_days. Returns count deleted."""
    if directory is None:
        directory = Path.home() / ".pawbot" / "browser" / "screenshots"
    if not directory.exists():
        return 0

    cutoff = time.time() - (max_age_days * 86400)
    deleted = 0

    for f in directory.glob("*.png"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        except OSError:
            pass

    if deleted:
        logger.info("Cleaned up {} old browser screenshots", deleted)
    return deleted
