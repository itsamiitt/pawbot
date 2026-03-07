"""Output secret scanning tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from pawbot.agent.loop import AgentLoop
from pawbot.agent.output_sanitizer import redact_secrets, scan_output
from pawbot.bus.events import InboundMessage
from pawbot.session.manager import Session


def _make_agent_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock()

    bus = MagicMock()
    bus.publish_outbound = AsyncMock()

    return AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
    )


def test_scan_output_detects_known_secret_formats():
    text = "OpenAI sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890 leaked"
    leaks = scan_output(text)
    assert leaks
    assert leaks[0][0] == "OpenAI Key"


def test_redact_secrets_replaces_secret_value():
    text = "Slack token xoxb-1234567890-abcdefghijklmnop"
    redacted = redact_secrets(text)
    assert "xoxb-" not in redacted
    assert "[REDACTED:Slack Token]" in redacted


def test_build_response_redacts_secret_before_persisting(tmp_path):
    loop = _make_agent_loop(tmp_path)
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="chat", content="hello")
    session = Session(key="cli:chat")
    secret = "OpenAI key sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
    all_msgs = [{"role": "assistant", "content": secret}]

    response = loop._build_response(
        msg=msg,
        final_content=secret,
        all_msgs=all_msgs,
        session=session,
        history_len=0,
    )

    assert "[REDACTED:OpenAI Key]" in response.content
    assert "[REDACTED:OpenAI Key]" in session.messages[-1]["content"]
