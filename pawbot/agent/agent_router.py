"""
pawbot/agent/agent_router.py

Multi-Agent Router — resolves which agent configuration handles a message.

Routing logic:
    1. Find agents whose channels[] list contains the incoming channel type
    2. Among those, find agents whose contacts[] list contains from_user, or has "*"
    3. Return the first match
    4. If no match: return the agent with default: true
    5. If no default: return the first agent (safety fallback)

IMPORTS FROM: pawbot/contracts.py — ChannelType, config(), get_logger()
SINGLETON:    agent_router — import this everywhere
CALLED BY:    channel adapters, gateway server, session manager
"""

import os
from typing import Any
from pawbot.contracts import ChannelType, config, get_logger

logger = get_logger(__name__)


class AgentRouter:
    """
    Routes inbound messages to the correct agent configuration.
    Configuration comes from config().get("agents") — loaded fresh on each call
    so hot-reload of config.json is supported without restart.
    """

    def resolve(self, channel: ChannelType, from_user: str) -> dict:
        """
        Resolve which agent config should handle this message.

        Args:
            channel:   ChannelType enum from contracts.py (e.g. ChannelType.TELEGRAM)
            from_user: User identifier string (e.g. Telegram user_id "12345678")

        Returns:
            Agent config dict from config().get("agents").
            Always returns a dict — never None.
            Fallback order: matched agent → default agent → first agent
        """
        try:
            agents_list = config().get("agents.list", None)
            if agents_list is None:
                agents_list = config().get("agents", [])
        except Exception:
            agents_list = []

        agents_list = [
            item for item in (self._as_dict(agent) for agent in agents_list)
            if isinstance(item, dict)
        ]

        if not agents_list:
            logger.warning("AgentRouter: no agents configured in config.json")
            return self._default_agent_config()

        channel_str = channel.value if isinstance(channel, ChannelType) else str(channel)

        for agent in agents_list:
            if not isinstance(agent, dict):
                continue
            if agent.get("enabled", True) is False:
                continue

            agent_channels = agent.get("channels", [])
            agent_contacts = agent.get("contacts", ["*"])

            channel_match = ("*" in agent_channels or channel_str in agent_channels)
            contact_match = ("*" in agent_contacts or from_user in agent_contacts)

            if channel_match and contact_match:
                logger.debug(f"AgentRouter: routed {channel_str}/{from_user} → {agent.get('id', 'unknown')}")
                return agent

        # No specific match — fall back to default agent
        default = next(
            (
                a for a in agents_list
                if isinstance(a, dict) and a.get("default") and a.get("enabled", True) is not False
            ),
            None,
        )
        if default:
            logger.debug(f"AgentRouter: no match — using default agent {default.get('id', 'unknown')}")
            return default

        # Last resort: first agent
        logger.warning("AgentRouter: no default agent set — using first agent")
        first_enabled = next((a for a in agents_list if a.get("enabled", True) is not False), None)
        return first_enabled or (agents_list[0] if agents_list else self._default_agent_config())

    @staticmethod
    def _as_dict(agent: Any) -> dict | None:
        """Normalize config entries whether stored as dicts or pydantic models."""
        if isinstance(agent, dict):
            return agent
        if hasattr(agent, "model_dump"):
            return agent.model_dump(by_alias=False)
        return None

    def get_session_id(self, agent_config: dict, from_user: str, channel: ChannelType) -> str:
        """
        Build a namespaced session_id so agents never share session state.

        Format:  {session_prefix}{channel}_{from_user}
        Example: "personal_telegram_12345678"
                 "work_email_boss@company.com"
        """
        prefix = agent_config.get("session_prefix", "")
        if not prefix and not agent_config.get("default", False):
            prefix = f"{agent_config.get('id', 'agent')}_"
        channel_str = channel.value if isinstance(channel, ChannelType) else str(channel)
        return f"{prefix}{channel_str}_{from_user}"

    def get_soul_path(self, agent_config: dict) -> str:
        """
        Return the absolute path to this agent's SOUL.md file.
        Falls back to the default SOUL_MD path from contracts.py if not configured.
        """
        from pawbot.contracts import SOUL_MD

        configured = agent_config.get("soul_file", "")
        if configured:
            return os.path.expanduser(configured)
        return os.path.expanduser(SOUL_MD)

    def _default_agent_config(self) -> dict:
        """Return a minimal safe default when no agents are configured."""
        from pawbot.contracts import SOUL_MD, CUSTOM_SKILLS_DIR
        return {
            "id":             "default",
            "name":           "Pawbot",
            "soul_file":      SOUL_MD,
            "skills_dir":     CUSTOM_SKILLS_DIR,
            "channels":       ["*"],
            "contacts":       ["*"],
            "default":        True,
            "session_prefix": "",
        }


# ── Singleton ──────────────────────────────────────────────────────────────────
agent_router = AgentRouter()
