"""
Filesystem utilities with atomicity guarantees.
All persistent state MUST be written through these functions.
"""
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("pawbot.utils.fs")

_IS_WINDOWS = sys.platform == "win32"


def _replace_with_retry(src: Path, dst: Path, retries: int = 5) -> None:
    """os.replace with retry for Windows.

    On Windows, concurrent writers can cause transient WinError 5 (Access Denied)
    when two os.replace calls race on the same target. This retries with backoff.
    """
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except OSError:
            if not _IS_WINDOWS or attempt == retries - 1:
                raise
            time.sleep(0.01 * (2 ** attempt))  # 10ms, 20ms, 40ms, 80ms, 160ms


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    """
    Write JSON atomically: write to temp file, then os.replace().
    If process dies mid-write, original file is untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_str = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp, path)
    except Exception as e:  # noqa: F841
        try:
            tmp.unlink(missing_ok=True)
        except Exception as e:  # noqa: F841
            pass
        raise


def atomic_write_text(path: Path, content: str) -> None:
    """Atomic text file write — same guarantee as atomic_write_json."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_str = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp, path)
    except Exception as e:  # noqa: F841
        try:
            tmp.unlink(missing_ok=True)
        except Exception as e:  # noqa: F841
            pass
        raise


def write_json_with_backup(path: Path, data: Any, indent: int = 2) -> None:
    """
    Atomically write JSON and keep a .bak copy of the previous version.
    Use for critical files: config.json, crons.json, heartbeat triggers, memory.
    """
    path = Path(path)
    bak = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        try:
            bak.write_bytes(path.read_bytes())
        except Exception as e:
            logger.warning(f"Could not create backup {bak}: {e}")
    atomic_write_json(path, data, indent=indent)


def safe_read_json(path: Path, default: Any = None, backup: bool = True) -> Any:
    """
    Read JSON with corruption recovery.

    On JSONDecodeError:
      1. Try .bak recovery (if backup=True and .bak exists).
      2. Return `default` if recovery fails or no backup.
      3. Re-raise if default is None.
    On FileNotFoundError: return `default`.
    """
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted JSON in {path}: {e}")
        if backup:
            bak = path.with_suffix(path.suffix + ".bak")
            if bak.exists():
                logger.warning(f"Attempting recovery from {bak}")
                try:
                    data = json.loads(bak.read_text(encoding="utf-8"))
                    atomic_write_json(path, data)
                    logger.info(f"Recovered {path} from backup")
                    return data
                except Exception as bak_err:
                    logger.error(f"Backup recovery failed: {bak_err}")
        if default is not None:
            logger.warning(f"Returning default for {path}")
            return default
        raise
