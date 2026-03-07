"""Workspace isolation helpers for multi-agent runtimes."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from loguru import logger

from pawbot.utils.paths import PAWBOT_HOME


class WorkspaceManager:
    """Create and inspect isolated workspaces for each agent."""

    BASE_DIR = PAWBOT_HOME

    def __init__(
        self,
        agent_id: str,
        workspace_path: str = "",
        *,
        base_dir: Path | None = None,
    ):
        self.agent_id = agent_id
        self.base_dir = base_dir or self.BASE_DIR
        self.workspace = (
            Path(workspace_path).expanduser()
            if workspace_path
            else self._default_workspace()
        )
        self.memory_dir = self.base_dir / "memory"
        self.db_path = self.memory_dir / f"{agent_id}.sqlite"

    def _default_workspace(self) -> Path:
        if self.agent_id in {"main", "default"}:
            return self.base_dir / "workspace"
        return self.base_dir / f"workspace-{self.agent_id}"

    def ensure_workspace(self) -> Path:
        """Create the workspace structure and copy shared templates if needed."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        for name in ("projects", "scratch", "downloads", "templates", "sessions"):
            (self.workspace / name).mkdir(exist_ok=True)

        default_templates = self.base_dir / "workspace" / "templates"
        agent_templates = self.workspace / "templates"
        if (
            default_templates.exists()
            and default_templates != agent_templates
            and not any(agent_templates.iterdir())
        ):
            for source in default_templates.iterdir():
                if source.is_file():
                    shutil.copy2(source, agent_templates / source.name)
            logger.debug("Copied shared templates into workspace for agent '{}'", self.agent_id)

        logger.debug("Workspace ready for agent '{}': {}", self.agent_id, self.workspace)
        return self.workspace

    def get_memory_db_path(self) -> str:
        """Return the SQLite path for this agent."""
        return str(self.db_path)

    def workspace_size_mb(self) -> float:
        """Calculate total workspace size in MB."""
        if not self.workspace.exists():
            return 0.0

        total = 0
        for path in self.workspace.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total / (1024 * 1024)

    def cleanup_scratch(self, max_age_days: int = 7) -> int:
        """Delete old scratch files and return the number removed."""
        scratch = self.workspace / "scratch"
        if not scratch.exists():
            return 0

        cutoff = time.time() - (max_age_days * 86400)
        deleted = 0
        for path in scratch.rglob("*"):
            if path.is_file():
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        deleted += 1
                except OSError:
                    continue
        return deleted

    def to_dict(self) -> dict[str, Any]:
        """Serialize workspace state for status APIs."""
        return {
            "agent_id": self.agent_id,
            "workspace": str(self.workspace),
            "db_path": str(self.db_path),
            "exists": self.workspace.exists(),
            "size_mb": round(self.workspace_size_mb(), 2),
        }
