# Phase 10 — Multi-Agent Workspaces & Heartbeat

> **Goal:** Enable isolated per-agent workspaces with independent heartbeats, config, and memory — matching OpenClaw's multi-agent architecture.  
> **Duration:** 7-10 days  
> **Risk Level:** High (fundamental architectural change to agent lifecycle)  
> **Depends On:** Phase 0 (config schema), Phase 2 (per-agent memory), Phase 5 (fleet)

---

## Why This Phase Exists

OpenClaw runs **4 agents simultaneously**, each with:
- Its own workspace directory (`workspace`, `workspace-worker-1`, etc.)
- Its own SQLite memory database (`main.sqlite`, `worker-1.sqlite`, etc.)
- Independent heartbeat configuration (`every: "30m"`, `target: "last"`)
- Per-agent tool allow-lists

PawBot has a single `AgentLoop` instance with a **shared workspace and shared memory**. This phase adds true multi-agent isolation.

---

## 10.1 — Agent Definition Schema

**File:** `pawbot/config/schema.py` — add:

```python
from pydantic import BaseModel, Field
from typing import Any


class HeartbeatConfig(BaseModel):
    """Periodic heartbeat configuration for an agent."""
    enabled: bool = True
    every: str = "30m"              # Duration string: "30m", "1h", "6h"
    target: str = "last"            # "last" = heartbeat last active session
    message: str = ""               # Custom heartbeat message (empty = default)
    max_silence_before_alert: str = "2h"  # Alert if agent silent for this long


class AgentDefinition(BaseModel):
    """Definition of a single agent instance."""
    id: str                          # Unique agent ID: "main", "worker-1", etc.
    default: bool = False            # Is this the default agent?
    workspace: str = ""              # Agent-specific workspace path
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    tools: AgentToolsConfig = Field(default_factory=AgentToolsConfig)
    model: str = ""                  # Model override (empty = use default)
    temperature: float = -1.0        # -1 = use default
    max_tokens: int = 0              # 0 = use default
    max_tool_iterations: int = 0     # 0 = use default
    memory_window: int = 0           # 0 = use default
    enabled: bool = True


class AgentsConfig(BaseModel):
    """Multi-agent configuration."""
    defaults: AgentDefaultsConfig = Field(default_factory=AgentDefaultsConfig)
    list: list[AgentDefinition] = Field(
        default_factory=lambda: [
            AgentDefinition(id="main", default=True),
        ]
    )
    max_concurrent: int = 8
    subagents: SubagentConfig = Field(default_factory=SubagentConfig)


class SubagentConfig(BaseModel):
    """Subagent pool configuration."""
    max_concurrent: int = 12
    timeout_seconds: int = 300
```

### Example `config.json` agents section:

```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-sonnet-4-6",
      "workspace": "~/.pawbot/workspace",
      "heartbeat": {"every": "30m", "target": "last"}
    },
    "list": [
      {
        "id": "main",
        "default": true,
        "workspace": "~/.pawbot/workspace",
        "tools": {"allow": ["*"]}
      },
      {
        "id": "researcher",
        "workspace": "~/.pawbot/workspace-researcher",
        "model": "anthropic/claude-haiku-4-5",
        "tools": {"allow": ["web_search", "web_fetch", "browse", "read_file", "write_file"]}
      },
      {
        "id": "coder",
        "workspace": "~/.pawbot/workspace-coder",
        "model": "anthropic/claude-sonnet-4-6",
        "tools": {"deny": ["exec"]}
      },
      {
        "id": "guardian",
        "workspace": "~/.pawbot/workspace-guardian",
        "model": "anthropic/claude-haiku-4-5",
        "tools": {"allow": ["read_file", "list_dir", "web_search"]}
      }
    ],
    "max_concurrent": 8,
    "subagents": {"max_concurrent": 12}
  }
}
```

---

## 10.2 — Workspace Isolation Manager

**Create:** `pawbot/agents/workspace_manager.py`

```python
"""Workspace isolation — each agent gets its own directory and database."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from loguru import logger


class WorkspaceManager:
    """Manages per-agent workspace directories with isolation guarantees."""

    BASE_DIR = Path.home() / ".pawbot"

    def __init__(self, agent_id: str, workspace_path: str = ""):
        self.agent_id = agent_id
        self.workspace = Path(workspace_path) if workspace_path else self.BASE_DIR / f"workspace-{agent_id}"
        self.memory_dir = self.BASE_DIR / "memory"
        self.db_path = self.memory_dir / f"{agent_id}.sqlite"

    def ensure_workspace(self) -> Path:
        """Create the workspace directory structure if it doesn't exist."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Standard subdirectories
        (self.workspace / "projects").mkdir(exist_ok=True)
        (self.workspace / "scratch").mkdir(exist_ok=True)
        (self.workspace / "downloads").mkdir(exist_ok=True)
        (self.workspace / "templates").mkdir(exist_ok=True)

        # Copy templates from default workspace if this is a new workspace
        default_templates = self.BASE_DIR / "workspace" / "templates"
        agent_templates = self.workspace / "templates"
        if default_templates.exists() and not any(agent_templates.iterdir()):
            for f in default_templates.iterdir():
                if f.is_file():
                    shutil.copy2(f, agent_templates / f.name)
            logger.debug("Copied templates to workspace for agent '{}'", self.agent_id)

        logger.debug(
            "Workspace ready for agent '{}': {}",
            self.agent_id, self.workspace,
        )
        return self.workspace

    def get_memory_db_path(self) -> str:
        """Get the SQLite database path for this agent."""
        return str(self.db_path)

    def workspace_size_mb(self) -> float:
        """Calculate total workspace size in MB."""
        total = 0
        for f in self.workspace.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return total / (1024 * 1024)

    def cleanup_scratch(self, max_age_days: int = 7) -> int:
        """Remove old files from the scratch directory."""
        import time
        scratch = self.workspace / "scratch"
        if not scratch.exists():
            return 0
        cutoff = time.time() - (max_age_days * 86400)
        deleted = 0
        for f in scratch.rglob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        return deleted

    def to_dict(self) -> dict[str, Any]:
        """Serialize workspace info."""
        return {
            "agent_id": self.agent_id,
            "workspace": str(self.workspace),
            "db_path": str(self.db_path),
            "exists": self.workspace.exists(),
            "size_mb": round(self.workspace_size_mb(), 2) if self.workspace.exists() else 0,
        }
```

---

## 10.3 — Agent Pool Manager

**Create:** `pawbot/agents/pool.py`

```python
"""Agent pool — manages lifecycle of multiple agent instances."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from pawbot.agents.workspace_manager import WorkspaceManager
from pawbot.agent.loop import AgentLoop
from pawbot.config.schema import AgentDefinition, AgentsConfig


class AgentInstance:
    """A single running agent with its own loop, workspace, and memory."""

    def __init__(
        self,
        definition: AgentDefinition,
        defaults: dict[str, Any],
        bus,
        provider,
        cron_service=None,
    ):
        self.definition = definition
        self.workspace_mgr = WorkspaceManager(
            agent_id=definition.id,
            workspace_path=definition.workspace or defaults.get("workspace", ""),
        )
        self.workspace_mgr.ensure_workspace()

        # Build agent loop with per-agent config
        model = definition.model or defaults.get("model", "")
        temperature = definition.temperature if definition.temperature >= 0 else defaults.get("temperature", 0.1)
        max_tokens = definition.max_tokens or defaults.get("max_tokens", 4096)

        self.loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=str(self.workspace_mgr.workspace),
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            max_iterations=definition.max_tool_iterations or defaults.get("max_tool_iterations", 25),
            memory_window=definition.memory_window or defaults.get("memory_window", 20),
            memory_db_path=self.workspace_mgr.get_memory_db_path(),
            cron_service=cron_service,
        )
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the agent loop."""
        self._task = asyncio.create_task(self.loop.run())
        logger.info("Agent '{}' started (workspace: {})", self.definition.id, self.workspace_mgr.workspace)

    async def stop(self) -> None:
        """Stop the agent loop gracefully."""
        if self._task and not self._task.done():
            self.loop._running = False
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Agent '{}' stopped", self.definition.id)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()


class AgentPool:
    """Manages a pool of agent instances based on configuration."""

    def __init__(self, config: AgentsConfig, bus, provider, cron_service=None):
        self.config = config
        self.bus = bus
        self.provider = provider
        self.cron_service = cron_service
        self._agents: dict[str, AgentInstance] = {}

    async def start_all(self) -> int:
        """Start all enabled agents. Returns count started."""
        defaults = self.config.defaults.model_dump() if self.config.defaults else {}
        count = 0

        for definition in self.config.list:
            if not definition.enabled:
                logger.debug("Agent '{}' is disabled, skipping", definition.id)
                continue

            if len(self._agents) >= self.config.max_concurrent:
                logger.warning(
                    "Max concurrent agents ({}) reached, cannot start '{}'",
                    self.config.max_concurrent, definition.id,
                )
                break

            instance = AgentInstance(
                definition=definition,
                defaults=defaults,
                bus=self.bus,
                provider=self.provider,
                cron_service=self.cron_service,
            )
            await instance.start()
            self._agents[definition.id] = instance
            count += 1

        logger.info("Agent pool started: {}/{} agents", count, len(self.config.list))
        return count

    async def stop_all(self) -> int:
        """Stop all running agents. Returns count stopped."""
        count = 0
        for agent_id, instance in list(self._agents.items()):
            await instance.stop()
            count += 1
        self._agents.clear()
        logger.info("Agent pool stopped: {} agents", count)
        return count

    async def restart_agent(self, agent_id: str) -> bool:
        """Restart a specific agent."""
        instance = self._agents.get(agent_id)
        if not instance:
            return False
        await instance.stop()
        await instance.start()
        logger.info("Agent '{}' restarted", agent_id)
        return True

    def get_agent(self, agent_id: str) -> AgentInstance | None:
        return self._agents.get(agent_id)

    def get_default_agent(self) -> AgentInstance | None:
        for instance in self._agents.values():
            if instance.definition.default:
                return instance
        # Fallback to first
        return next(iter(self._agents.values()), None)

    def status(self) -> list[dict[str, Any]]:
        """Get status of all agents."""
        results = []
        for agent_id, instance in self._agents.items():
            results.append({
                "id": agent_id,
                "running": instance.is_running,
                "default": instance.definition.default,
                "model": instance.definition.model or "default",
                "workspace": instance.workspace_mgr.to_dict(),
                "tools_allow": instance.definition.tools.allow,
                "tools_deny": instance.definition.tools.deny,
            })
        return results
```

---

## 10.4 — Heartbeat System

**Create:** `pawbot/agents/heartbeat.py`

```python
"""Agent heartbeat — periodic check-in and health monitoring."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from loguru import logger


def _parse_duration(duration: str) -> int:
    """Parse a duration string to seconds. E.g. '30m' -> 1800, '2h' -> 7200."""
    m = re.match(r"^(\d+)\s*(s|m|h|d)$", duration.strip().lower())
    if not m:
        raise ValueError(f"Invalid duration: '{duration}'. Use format: 30m, 1h, 6h, 1d")
    value, unit = int(m.group(1)), m.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers[unit]


class AgentHeartbeat:
    """Periodic heartbeat for an agent — checks health and triggers actions."""

    def __init__(
        self,
        agent_id: str,
        interval: str = "30m",
        target: str = "last",
        on_heartbeat: callable | None = None,
    ):
        self.agent_id = agent_id
        self.interval_seconds = _parse_duration(interval)
        self.target = target
        self._on_heartbeat = on_heartbeat
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_beat: float = 0
        self._beat_count: int = 0
        self._errors: int = 0

    async def start(self) -> None:
        """Start the heartbeat loop."""
        self._running = True
        self._task = asyncio.create_task(self._beat_loop())
        logger.info(
            "Heartbeat started for agent '{}' (every {}s)",
            self.agent_id, self.interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the heartbeat loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _beat_loop(self) -> None:
        """Main heartbeat loop."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                if not self._running:
                    break

                self._beat_count += 1
                self._last_beat = time.time()

                # Execute heartbeat action
                if self._on_heartbeat:
                    try:
                        await self._on_heartbeat(self.agent_id, self.target)
                    except Exception as e:
                        self._errors += 1
                        logger.warning("Heartbeat action failed for '{}': {}", self.agent_id, e)

                logger.debug(
                    "Heartbeat #{} for agent '{}' (target: {})",
                    self._beat_count, self.agent_id, self.target,
                )

            except asyncio.CancelledError:
                break
            except Exception:
                self._errors += 1
                logger.exception("Heartbeat loop error for '{}'", self.agent_id)
                await asyncio.sleep(60)  # Back off on error

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "running": self._running,
            "interval_seconds": self.interval_seconds,
            "beat_count": self._beat_count,
            "errors": self._errors,
            "last_beat": self._last_beat,
            "seconds_since_beat": round(time.time() - self._last_beat, 1) if self._last_beat else None,
        }
```

---

## 10.5 — Agent Status API

**Add to:** `pawbot/dashboard/server.py`

```python
@app.get("/api/agents/status")
def agents_status():
    """Get status of all agent instances."""
    # Requires access to the running AgentPool instance
    if not hasattr(app.state, "agent_pool"):
        return {"agents": [], "error": "Agent pool not initialized"}
    return {"agents": app.state.agent_pool.status()}


@app.get("/api/agents/{agent_id}/workspace")
def agent_workspace(agent_id: str):
    """Get workspace info for a specific agent."""
    if not hasattr(app.state, "agent_pool"):
        return {"error": "Agent pool not initialized"}
    instance = app.state.agent_pool.get_agent(agent_id)
    if not instance:
        return {"error": f"Agent '{agent_id}' not found"}
    return {"workspace": instance.workspace_mgr.to_dict()}


@app.post("/api/agents/{agent_id}/restart")
async def restart_agent(agent_id: str):
    """Restart a specific agent."""
    if not hasattr(app.state, "agent_pool"):
        return {"error": "Agent pool not initialized"}
    ok = await app.state.agent_pool.restart_agent(agent_id)
    if ok:
        return {"success": True, "message": f"Agent '{agent_id}' restarted"}
    return {"error": f"Agent '{agent_id}' not found"}


@app.get("/api/agents/heartbeats")
def agents_heartbeats():
    """Get heartbeat status for all agents."""
    # This would be wired to the heartbeat instances
    return {"heartbeats": []}
```

---

## Verification Checklist — Phase 10 Complete

- [ ] `AgentDefinition` schema supports per-agent id, workspace, model, tools, heartbeat
- [ ] `WorkspaceManager` creates isolated directories per agent
- [ ] Each agent gets its own SQLite database file (`{agent_id}.sqlite`)
- [ ] `AgentPool` starts/stops multiple agent instances concurrently
- [ ] `max_concurrent` limit enforced
- [ ] Default agent identified via `default: true` flag
- [ ] `AgentHeartbeat` runs periodic check-ins with configurable interval
- [ ] Duration parser handles `30m`, `1h`, `6h`, `1d` formats
- [ ] `/api/agents/status` shows all agents with workspace info
- [ ] `/api/agents/{id}/restart` restarts a specific agent
- [ ] Agent pool integrates with graceful shutdown
- [ ] All tests pass: `pytest tests/ -v --tb=short`
