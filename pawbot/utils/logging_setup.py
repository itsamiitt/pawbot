"""Centralised logging setup. Call setup_logging() once per CLI entry point."""
import logging
import sys
from pathlib import Path


def setup_logging(level: str = "WARNING") -> None:
    """
    Configure Pawbot logging.
    - stderr: shows `level` and above (default WARNING — quiet in normal use)
    - file:   always writes DEBUG+ to ~/.pawbot/logs/pawbot.log
    """
    from pawbot.utils.paths import LOGS_PATH
    numeric = getattr(logging, level.upper(), logging.WARNING)
    LOGS_PATH.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    # stderr
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(numeric)
    sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(sh)
    # file
    try:
        fh = logging.FileHandler(LOGS_PATH / "pawbot.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        root.addHandler(fh)
    except OSError as e:
        logging.getLogger("pawbot.utils.logging").warning(f"Could not open log file: {e}")
    for lib in ["httpx", "httpcore", "anthropic", "openai", "urllib3", "chromadb", "redis", "uvicorn", "fastapi"]:
        logging.getLogger(lib).setLevel(logging.WARNING)
