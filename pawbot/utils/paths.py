"""Centralised path constants. Import from here — never construct ~/.pawbot paths inline."""
from pathlib import Path

PAWBOT_HOME    = Path.home() / ".pawbot"
CONFIG_PATH    = PAWBOT_HOME / "config.json"
WORKSPACE_PATH = PAWBOT_HOME / "workspace"
LOGS_PATH      = PAWBOT_HOME / "logs"
SKILLS_PATH    = PAWBOT_HOME / "skills"
CRONS_PATH     = PAWBOT_HOME / "crons.json"
HEARTBEAT_PATH = PAWBOT_HOME / "heartbeat_triggers.json"
TRAINING_PATH  = PAWBOT_HOME / "training"
MODELS_PATH    = PAWBOT_HOME / "models"
SESSION_PATH   = PAWBOT_HOME / "sessions"
