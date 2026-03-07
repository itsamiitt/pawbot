"""Tests for Phase 10 multi-agent workspaces and heartbeats."""

from __future__ import annotations

import shutil
import uuid
from types import SimpleNamespace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from pawbot.agent.agent_router import agent_router
from pawbot.agents.heartbeat import parse_duration
from pawbot.agents.pool import AgentInstance, AgentPool
from pawbot.agents.workspace_manager import WorkspaceManager
from pawbot.bus.queue import MessageBus
from pawbot.config.schema import Config
from pawbot.contracts import ChannelType
from pawbot.dashboard import auth as dashboard_auth
from pawbot.dashboard.server import app


class DummyResponse:
    """Minimal provider response shape for AgentLoop tests."""

    content = "ok"
    tool_calls: list = []
    has_tool_calls = False
    usage = {}
    reasoning_content = None
    thinking_blocks = None


class DummyProvider:
    """Minimal provider implementation for pool lifecycle tests."""

    def get_default_model(self) -> str:
        return "dummy/model"

    async def chat(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return DummyResponse()


@pytest.fixture
def local_tmp_path() -> Path:
    """Workspace-local temp dir to avoid Windows temp permission issues in sandbox."""
    base = Path(__file__).resolve().parents[1] / "pytest_temp_phase10"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _config_for(tmp_path: Path) -> Config:
    return Config.model_validate({
        "agents": {
            "defaults": {
                "workspace": str(tmp_path / "workspace"),
                "model": "dummy/default",
                "heartbeat": {"every": "30m", "target": "last"},
            },
            "list": [
                {
                    "id": "main",
                    "default": True,
                    "workspace": str(tmp_path / "workspace"),
                },
                {
                    "id": "researcher",
                    "workspace": str(tmp_path / "workspace-researcher"),
                    "heartbeat": {"every": "1h", "target": "last"},
                    "tools": {"allow": ["read_file"]},
                },
            ],
            "maxConcurrent": 2,
        }
    })


def test_config_supports_agent_list_and_heartbeat(local_tmp_path: Path) -> None:
    """Config schema should parse per-agent workspace, tools, and heartbeat data."""
    config = _config_for(local_tmp_path)

    assert config.agents.defaults.heartbeat.every == "30m"
    assert len(config.agents.agents) == 2
    assert config.agents.agents[1].id == "researcher"
    assert config.agents.agents[1].workspace.endswith("workspace-researcher")
    assert config.agents.agents[1].heartbeat.every == "1h"
    assert config.agents.agents[1].tools.allow == ["read_file"]


def test_workspace_manager_creates_isolated_directories_and_db(local_tmp_path: Path) -> None:
    """Each agent workspace should get its own directory tree and db path."""
    default_templates = local_tmp_path / "workspace" / "templates"
    default_templates.mkdir(parents=True)
    (default_templates / "template.txt").write_text("seed", encoding="utf-8")

    mgr = WorkspaceManager("researcher", base_dir=local_tmp_path)
    workspace = mgr.ensure_workspace()

    assert workspace == local_tmp_path / "workspace-researcher"
    assert (workspace / "projects").exists()
    assert (workspace / "scratch").exists()
    assert (workspace / "downloads").exists()
    assert (workspace / "templates" / "template.txt").read_text(encoding="utf-8") == "seed"
    assert mgr.get_memory_db_path().endswith("researcher.sqlite")


def test_parse_duration_supports_expected_units() -> None:
    """Heartbeat duration parser should support the compact formats in the plan."""
    assert parse_duration("30m") == 1800
    assert parse_duration("1h") == 3600
    assert parse_duration("6h") == 21600
    assert parse_duration("1d") == 86400
    with pytest.raises(ValueError):
        parse_duration("90")


def test_agent_router_reads_agents_list_schema(monkeypatch) -> None:
    """Router should resolve agent definitions from agents.list, not only a raw list."""
    data = {
        "agents": {
            "list": [
                {"id": "researcher", "channels": ["api"], "contacts": ["alice"]},
                {"id": "main", "default": True, "channels": ["telegram"], "contacts": ["*"]},
            ]
        }
    }

    def _get(key: str, default=None):
        obj = data
        for part in key.split("."):
            if not isinstance(obj, dict):
                return default
            obj = obj.get(part)
            if obj is None:
                return default
        return obj

    monkeypatch.setattr(
        "pawbot.agent.agent_router.config",
        lambda: SimpleNamespace(get=_get),
    )

    resolved = agent_router.resolve(ChannelType.API, "alice")
    assert resolved["id"] == "researcher"


def test_agent_instance_applies_tool_allow_list(local_tmp_path: Path) -> None:
    """Per-agent tool policy should filter the registered tools."""
    config = _config_for(local_tmp_path)
    instance = AgentInstance(
        definition=config.agents.agents[1],
        defaults=config.agents.defaults,
        bus=MessageBus(),
        provider=DummyProvider(),
        enable_heartbeat=False,
    )

    assert "read_file" in instance.loop.tools.tool_names
    assert "list_dir" not in instance.loop.tools.tool_names
    assert "exec" not in instance.loop.tools.tool_names


@pytest.mark.asyncio
async def test_agent_pool_start_stop_and_restart(local_tmp_path: Path) -> None:
    """Pool should start configured agents, report status, and restart a member."""
    config = _config_for(local_tmp_path)
    pool = AgentPool(
        config.agents,
        MessageBus(),
        DummyProvider(),
        enable_heartbeats=False,
    )

    started = await pool.start_all()
    assert started == 2
    assert pool.get_default_agent() is not None
    assert pool.get_default_agent().definition.id == "main"

    status = pool.status()
    assert {item["id"] for item in status} == {"main", "researcher"}
    assert all("workspace" in item for item in status)

    assert await pool.restart_agent("researcher") is True
    assert len(pool.heartbeat_status()) == 2

    stopped = await pool.stop_all()
    assert stopped == 2


def test_dashboard_agent_endpoints_require_auth_and_use_pool(local_tmp_path: Path, monkeypatch) -> None:
    """Dashboard agent APIs should expose pool status after login."""
    monkeypatch.setattr(dashboard_auth, "AUTH_FILE", local_tmp_path / "dashboard_auth.json")
    monkeypatch.setattr(dashboard_auth, "JWT_SECRET_FILE", local_tmp_path / "dashboard_secret")
    monkeypatch.setattr(dashboard_auth, "AUTH_STORAGE_DIR", local_tmp_path / "dashboard_tokens")
    dashboard_auth.set_password("phase10-secret")

    class DummyWorkspace:
        def to_dict(self):
            return {
                "agent_id": "main",
                "workspace": str(local_tmp_path / "workspace"),
                "db_path": str(local_tmp_path / "memory" / "main.sqlite"),
                "exists": True,
                "size_mb": 0.0,
            }

    class DummyAgent:
        workspace_mgr = DummyWorkspace()

    class DummyPool:
        def status(self):
            return [{"id": "main", "running": True, "workspace": DummyWorkspace().to_dict()}]

        def get_agent(self, agent_id: str):
            return DummyAgent() if agent_id == "main" else None

        async def restart_agent(self, agent_id: str):
            return agent_id == "main"

        def heartbeat_status(self):
            return [{"agent_id": "main", "running": True, "interval": "30m"}]

    app.state.agent_pool = DummyPool()
    client = TestClient(app)

    try:
        assert client.get("/api/agents/status").status_code == 401

        login = client.post("/api/auth/login", json={"password": "phase10-secret"})
        assert login.status_code == 200

        status = client.get("/api/agents/status")
        assert status.status_code == 200
        assert status.json()["agents"][0]["id"] == "main"

        workspace = client.get("/api/agents/main/workspace")
        assert workspace.status_code == 200
        assert workspace.json()["workspace"]["agent_id"] == "main"

        heartbeat = client.get("/api/agents/heartbeats")
        assert heartbeat.status_code == 200
        assert heartbeat.json()["heartbeats"][0]["agent_id"] == "main"

        restarted = client.post("/api/agents/main/restart")
        assert restarted.status_code == 200
        assert restarted.json()["success"] is True
    finally:
        delattr(app.state, "agent_pool")
