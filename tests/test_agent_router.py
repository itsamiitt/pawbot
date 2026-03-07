"""
tests/test_agent_router.py
Run: pytest tests/test_agent_router.py -v
"""

import pytest
from unittest.mock import patch, MagicMock
from pawbot.agent.agent_router import AgentRouter
from pawbot.contracts import ChannelType

MOCK_AGENTS_LIST = [
    {
        "id":             "personal",
        "name":           "Personal",
        "soul_file":      "~/SOUL_PERSONAL.md",
        "channels":       ["telegram", "whatsapp"],
        "contacts":       ["*"],
        "default":        True,
        "session_prefix": "personal_"
    },
    {
        "id":             "work",
        "name":           "Work",
        "soul_file":      "~/SOUL_WORK.md",
        "channels":       ["email"],
        "contacts":       ["boss@company.com", "team@company.com"],
        "default":        False,
        "session_prefix": "work_"
    }
]


def make_router():
    return AgentRouter()


def _mock_config():
    """Create a mock config wrapper that returns MOCK_AGENTS_LIST for agents."""
    wrapper = MagicMock()
    def _get(key, default=None):
        if key == "agents":
            return MOCK_AGENTS_LIST
        return default
    wrapper.get = _get
    return wrapper


def test_telegram_routes_to_personal():
    """Telegram messages should route to personal agent."""
    router = make_router()
    with patch("pawbot.agent.agent_router.config", return_value=_mock_config()):
        result = router.resolve(ChannelType.TELEGRAM, "user_12345")
    assert result["id"] == "personal"


def test_email_routes_to_work_for_known_contact():
    """Email from known contact routes to work agent."""
    router = make_router()
    with patch("pawbot.agent.agent_router.config", return_value=_mock_config()):
        result = router.resolve(ChannelType.EMAIL, "boss@company.com")
    assert result["id"] == "work"


def test_unknown_channel_falls_back_to_default():
    """Unknown channel should fall back to default agent."""
    router = make_router()
    with patch("pawbot.agent.agent_router.config", return_value=_mock_config()):
        result = router.resolve(ChannelType.API, "some_user")
    assert result["id"] == "personal", "API channel not in any agent — should use default"


def test_session_id_namespaced_per_agent():
    """Session IDs must include agent prefix to prevent cross-agent memory bleed."""
    router       = make_router()
    personal_cfg = MOCK_AGENTS_LIST[0]
    session_id   = router.get_session_id(personal_cfg, "12345", ChannelType.TELEGRAM)
    assert session_id == "personal_telegram_12345"
    assert session_id.startswith("personal_"), "Must include agent session_prefix"
