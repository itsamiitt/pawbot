"""Centralised path constants. Import from here — never construct ~/.pawbot paths inline."""
from pathlib import Path

PAWBOT_HOME    = Path.home() / ".pawbot"
CONFIG_PATH    = PAWBOT_HOME / "config.json"
WORKSPACE_PATH = PAWBOT_HOME / "workspace"
LOGS_PATH      = PAWBOT_HOME / "logs"
SHARED_PATH    = PAWBOT_HOME / "shared"
SKILLS_PATH    = PAWBOT_HOME / "skills"
CRONS_PATH     = PAWBOT_HOME / "crons.json"
HEARTBEAT_PATH = PAWBOT_HOME / "heartbeat_triggers.json"
TRAINING_PATH  = PAWBOT_HOME / "training"
MODELS_PATH    = PAWBOT_HOME / "models"
SESSION_PATH   = PAWBOT_HOME / "sessions"
MEMORY_PATH    = PAWBOT_HOME / "memory"
FACTS_DB_PATH  = MEMORY_PATH / "facts.db"
CHROMA_PATH    = MEMORY_PATH / "chroma"
SOUL_PATH      = WORKSPACE_PATH / "SOUL.md"
SECURITY_LOG   = LOGS_PATH / "security_audit.jsonl"


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
