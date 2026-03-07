"""Multi-agent runtime pool and routing helpers."""

from __future__ import annotations

import asyncio
import fnmatch
import time
from dataclasses import replace
from typing import Any

from loguru import logger

from pawbot.agent.agent_router import agent_router
from pawbot.agent.loop import AgentLoop
from pawbot.agents.heartbeat import AgentHeartbeat
from pawbot.agents.workspace_manager import WorkspaceManager
from pawbot.bus.events import InboundMessage
from pawbot.config.schema import (
    AgentDefinition,
    AgentDefaults,
    AgentToolsConfig,
    AgentsConfig,
    ChannelsConfig,
    ExecToolConfig,
)
from pawbot.contracts import ChannelType
from pawbot.session.manager import SessionManager


def resolve_agent_definition(
    agents_config: AgentsConfig,
    agent_id: str | None = None,
    *,
    include_disabled: bool = False,
) -> AgentDefinition:
    """Resolve a configured agent definition by id, default flag, or first entry."""
    definitions = list(agents_config.agents or [])
    if not definitions:
        return AgentDefinition(id="main", default=True)

    def _eligible(definition: AgentDefinition) -> bool:
        return include_disabled or definition.enabled

    if agent_id:
        for definition in definitions:
            if definition.id == agent_id and _eligible(definition):
                return definition

    for definition in definitions:
        if definition.default and _eligible(definition):
            return definition

    for definition in definitions:
        if _eligible(definition):
            return definition

    return definitions[0]


def effective_agent_settings(
    definition: AgentDefinition,
    defaults: AgentDefaults,
) -> dict[str, Any]:
    """Merge agent overrides with global defaults."""
    return {
        "workspace": definition.workspace or defaults.workspace,
        "model": definition.model or defaults.model,
        "temperature": (
            definition.temperature
            if definition.temperature >= 0
            else defaults.temperature
        ),
        "max_tokens": definition.max_tokens or defaults.max_tokens,
        "max_iterations": definition.max_tool_iterations or defaults.max_tool_iterations,
        "memory_window": definition.memory_window or defaults.memory_window,
        "reasoning_effort": definition.reasoning_effort or defaults.reasoning_effort,
    }


def apply_tool_policy(tools, *policies: AgentToolsConfig | None) -> None:
    """Filter a ToolRegistry in place according to ordered allow/deny policies."""

    def _matches(name: str, patterns: list[str]) -> bool:
        return any(pattern == "*" or fnmatch.fnmatchcase(name, pattern) for pattern in patterns)

    for policy in policies:
        if policy is None:
            continue

        if policy.allow and "*" not in policy.allow:
            for tool_name in list(tools.tool_names):
                if not _matches(tool_name, policy.allow):
                    tools.unregister(tool_name)

        if policy.deny:
            for tool_name in list(tools.tool_names):
                if _matches(tool_name, policy.deny):
                    tools.unregister(tool_name)


class AgentInstance:
    """A single isolated agent runtime."""

    def __init__(
        self,
        definition: AgentDefinition,
        defaults: AgentDefaults,
        bus,
        provider,
        *,
        cron_service=None,
        global_tools: AgentToolsConfig | None = None,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        restrict_to_workspace: bool = False,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        enable_heartbeat: bool = True,
        multi_agent_mode: bool = False,
    ):
        self.definition = definition
        self.defaults = defaults
        self.bus = bus
        self.provider = provider
        self.multi_agent_mode = multi_agent_mode
        self.runtime = effective_agent_settings(definition, defaults)
        self.workspace_mgr = WorkspaceManager(
            agent_id=definition.id,
            workspace_path=self.runtime["workspace"],
        )
        workspace = self.workspace_mgr.ensure_workspace()
        self.sessions = SessionManager(workspace)
        self.loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=workspace,
            model=self.runtime["model"],
            temperature=self.runtime["temperature"],
            max_tokens=self.runtime["max_tokens"],
            max_iterations=self.runtime["max_iterations"],
            memory_window=self.runtime["memory_window"],
            reasoning_effort=self.runtime["reasoning_effort"],
            brave_api_key=brave_api_key,
            exec_config=exec_config,
            cron_service=cron_service,
            restrict_to_workspace=restrict_to_workspace,
            session_manager=self.sessions,
            mcp_servers=mcp_servers,
            channels_config=channels_config,
            memory_db_path=self.workspace_mgr.get_memory_db_path(),
            memory_session_id=f"agent:{definition.id}",
        )
        apply_tool_policy(self.loop.tools, global_tools, definition.tools)

        self.heartbeat = None
        if enable_heartbeat and definition.heartbeat.enabled:
            self.heartbeat = AgentHeartbeat(
                agent_id=definition.id,
                interval=definition.heartbeat.every or defaults.heartbeat.every,
                target=definition.heartbeat.target or defaults.heartbeat.target,
            )

        self._running = False
        self.started_at = 0.0
        self.last_message_at = 0.0
        self.dispatch_count = 0

    async def start(self) -> None:
        """Initialize the runtime for dispatch."""
        if self._running:
            return
        await self.loop._connect_mcp()
        if self.heartbeat is not None:
            await self.heartbeat.start()
        self._running = True
        self.started_at = time.time()
        logger.info("Agent '{}' started (workspace: {})", self.definition.id, self.workspace_mgr.workspace)

    async def stop(self) -> None:
        """Stop heartbeats, save sessions, and close external resources."""
        if not self._running and self.heartbeat is None:
            return
        self._running = False
        if self.heartbeat is not None:
            await self.heartbeat.stop()
        self.sessions.save_all()
        await self.loop.close_mcp()
        self.loop.stop()
        logger.info("Agent '{}' stopped", self.definition.id)

    async def restart(self) -> None:
        """Restart this agent instance in place."""
        await self.stop()
        await self.start()

    def _build_session_override(self, msg: InboundMessage) -> str | None:
        if msg.session_key_override:
            return msg.session_key_override
        if self.definition.session_prefix:
            return f"{self.definition.session_prefix}{msg.channel}_{msg.chat_id}"
        if self.multi_agent_mode:
            return f"{self.definition.id}:{msg.session_key}"
        return None

    def _prepare_message(self, msg: InboundMessage) -> InboundMessage:
        metadata = dict(msg.metadata or {})
        metadata.setdefault("agent_id", self.definition.id)
        return replace(
            msg,
            metadata=metadata,
            session_key_override=self._build_session_override(msg),
        )

    async def dispatch(self, msg: InboundMessage) -> None:
        """Process an inbound message through this agent."""
        if not self._running:
            await self.start()
        prepared = self._prepare_message(msg)
        self.last_message_at = time.time()
        self.dispatch_count += 1
        await self.loop._dispatch(prepared)

    async def process_direct(
        self,
        content: str,
        *,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress=None,
    ) -> str:
        """Run a one-shot direct task through this agent."""
        if not self._running:
            await self.start()
        effective_session = session_key
        if self.multi_agent_mode and not session_key.startswith(f"{self.definition.id}:"):
            effective_session = f"{self.definition.id}:{session_key}"
        self.last_message_at = time.time()
        self.dispatch_count += 1
        return await self.loop.process_direct(
            content,
            session_key=effective_session,
            channel=channel,
            chat_id=chat_id,
            on_progress=on_progress,
        )

    @property
    def is_running(self) -> bool:
        return self._running

    def status(self) -> dict[str, Any]:
        """Serialize runtime state for dashboard APIs."""
        return {
            "id": self.definition.id,
            "running": self.is_running,
            "default": self.definition.default,
            "enabled": self.definition.enabled,
            "model": self.runtime["model"],
            "workspace": self.workspace_mgr.to_dict(),
            "tools_allow": list(self.definition.tools.allow),
            "tools_deny": list(self.definition.tools.deny),
            "dispatch_count": self.dispatch_count,
            "last_message_at": self.last_message_at or None,
            "heartbeat_enabled": self.heartbeat is not None,
        }

    def heartbeat_status(self) -> dict[str, Any]:
        """Expose heartbeat status even when disabled."""
        if self.heartbeat is not None:
            return self.heartbeat.stats
        return {
            "agent_id": self.definition.id,
            "running": False,
            "interval": self.definition.heartbeat.every,
            "interval_seconds": None,
            "target": self.definition.heartbeat.target,
            "beat_count": 0,
            "errors": 0,
            "last_beat": 0.0,
            "seconds_since_beat": None,
        }


class AgentPool:
    """Manage a configured set of isolated agents behind one shared bus."""

    def __init__(
        self,
        config: AgentsConfig,
        bus,
        provider,
        *,
        cron_service=None,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        restrict_to_workspace: bool = False,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        enable_heartbeats: bool = True,
    ):
        self.config = config
        self.bus = bus
        self.provider = provider
        self.cron_service = cron_service
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config
        self.restrict_to_workspace = restrict_to_workspace
        self.mcp_servers = mcp_servers
        self.channels_config = channels_config
        self.enable_heartbeats = enable_heartbeats

        self._agents: dict[str, AgentInstance] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._inflight: set[asyncio.Task] = set()
        self._running = False

    def _enabled_definitions(self) -> list[AgentDefinition]:
        return [definition for definition in self.config.agents if definition.enabled]

    def _build_instance(self, definition: AgentDefinition) -> AgentInstance:
        return AgentInstance(
            definition=definition,
            defaults=self.config.defaults,
            bus=self.bus,
            provider=self.provider,
            cron_service=self.cron_service,
            global_tools=self.config.tools,
            brave_api_key=self.brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=self.restrict_to_workspace,
            mcp_servers=self.mcp_servers,
            channels_config=self.channels_config,
            enable_heartbeat=self.enable_heartbeats,
            multi_agent_mode=len(self._enabled_definitions()) > 1,
        )

    async def start_all(self) -> int:
        """Start all enabled agents up to the configured concurrency cap."""
        count = 0
        for definition in self.config.agents:
            if not definition.enabled:
                continue
            if count >= self.config.max_concurrent:
                logger.warning(
                    "AgentPool max_concurrent={} reached; '{}' not started",
                    self.config.max_concurrent,
                    definition.id,
                )
                break
            instance = self._build_instance(definition)
            await instance.start()
            self._agents[definition.id] = instance
            count += 1

        self._running = True
        if self._dispatch_task is None:
            self._dispatch_task = asyncio.create_task(self.run())
        logger.info("Agent pool started: {}/{} agents", count, len(self._enabled_definitions()))
        return count

    async def run(self) -> None:
        """Consume inbound messages and route them to the correct agent."""
        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            agent = self.resolve_message(msg)
            if agent is None:
                logger.warning("No agent available to process message for {}", msg.sender_id)
                continue

            task = asyncio.create_task(agent.dispatch(msg))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def stop_all(self) -> int:
        """Stop dispatch and all running agent instances."""
        self._running = False
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None

        for task in list(self._inflight):
            task.cancel()
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
        self._inflight.clear()

        count = 0
        for agent_id in list(self._agents):
            await self._agents[agent_id].stop()
            count += 1
        self._agents.clear()
        logger.info("Agent pool stopped: {} agents", count)
        return count

    def resolve_message(self, msg: InboundMessage) -> AgentInstance | None:
        """Resolve the target running agent for an inbound message."""
        metadata = msg.metadata or {}
        agent_id = str(metadata.get("agent_id", "")).strip()

        if not agent_id:
            try:
                channel = ChannelType(msg.channel)
            except ValueError:
                channel = ChannelType.API

            try:
                resolved = agent_router.resolve(channel, msg.sender_id)
                if isinstance(resolved, dict):
                    agent_id = str(resolved.get("id", "")).strip()
                elif hasattr(resolved, "id"):
                    agent_id = str(getattr(resolved, "id", "")).strip()
            except Exception as exc:
                logger.debug("Agent router fallback failed: {}", exc)

        if agent_id and agent_id in self._agents:
            return self._agents[agent_id]
        return self.get_default_agent()

    async def process_direct(
        self,
        content: str,
        *,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        agent_id: str | None = None,
        on_progress=None,
    ) -> str:
        """Run a one-shot task through a specific or default agent."""
        instance = (
            self.get_agent(agent_id)
            if agent_id
            else self.get_default_agent()
        )
        if instance is None:
            definition = resolve_agent_definition(self.config, agent_id)
            instance = self._build_instance(definition)
            await instance.start()
            self._agents[definition.id] = instance
        return await instance.process_direct(
            content,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            on_progress=on_progress,
        )

    async def restart_agent(self, agent_id: str) -> bool:
        """Restart a single configured agent."""
        instance = self._agents.get(agent_id)
        if instance is not None:
            await instance.restart()
            return True

        definition = resolve_agent_definition(self.config, agent_id, include_disabled=True)
        if definition.id != agent_id or not definition.enabled:
            return False
        if len(self._agents) >= self.config.max_concurrent:
            return False

        instance = self._build_instance(definition)
        await instance.start()
        self._agents[definition.id] = instance
        return True

    def get_agent(self, agent_id: str | None) -> AgentInstance | None:
        if not agent_id:
            return None
        return self._agents.get(agent_id)

    def get_default_agent(self) -> AgentInstance | None:
        definition = resolve_agent_definition(self.config)
        if definition.id in self._agents:
            return self._agents[definition.id]
        return next(iter(self._agents.values()), None)

    def status(self) -> list[dict[str, Any]]:
        """Status of configured agents, including not-yet-started entries."""
        items: list[dict[str, Any]] = []
        for definition in self.config.agents:
            instance = self._agents.get(definition.id)
            if instance is not None:
                items.append(instance.status())
                continue

            runtime = effective_agent_settings(definition, self.config.defaults)
            workspace = WorkspaceManager(definition.id, runtime["workspace"]).to_dict()
            items.append({
                "id": definition.id,
                "running": False,
                "default": definition.default,
                "enabled": definition.enabled,
                "model": runtime["model"],
                "workspace": workspace,
                "tools_allow": list(definition.tools.allow),
                "tools_deny": list(definition.tools.deny),
                "dispatch_count": 0,
                "last_message_at": None,
                "heartbeat_enabled": definition.heartbeat.enabled,
            })
        return items

    def heartbeat_status(self) -> list[dict[str, Any]]:
        """Heartbeat state for all configured agents."""
        items: list[dict[str, Any]] = []
        for definition in self.config.agents:
            instance = self._agents.get(definition.id)
            if instance is not None:
                items.append(instance.heartbeat_status())
                continue
            items.append({
                "agent_id": definition.id,
                "running": False,
                "interval": definition.heartbeat.every,
                "interval_seconds": None,
                "target": definition.heartbeat.target,
                "beat_count": 0,
                "errors": 0,
                "last_beat": 0.0,
                "seconds_since_beat": None,
            })
        return items

    def list_sessions(self) -> list[dict[str, Any]]:
        """Flatten session metadata across running agents."""
        items: list[dict[str, Any]] = []
        for instance in self._agents.values():
            for session in instance.sessions.list_sessions():
                items.append({"agent_id": instance.definition.id, **session})
        return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)
