"""Focused unit tests for helper methods extracted from AgentLoop."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pawbot.agent.loop import AgentLoop


def _make_loop() -> AgentLoop:
    loop = object.__new__(AgentLoop)
    loop.sessions = MagicMock()
    loop.context = MagicMock()
    loop.tools = MagicMock()
    loop.memory_window = 20
    loop.failure_count = 0
    loop.failure_log = []
    loop.current_step = 1
    loop._session_meta = {}
    return loop


def _make_msg(content: str = "hello") -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        channel="telegram",
        chat_id="chat-1",
        sender_id="user-1",
        session_key="telegram:chat-1",
        metadata={},
    )


@pytest.mark.asyncio
async def test_handle_slash_help_returns_outbound() -> None:
    loop = _make_loop()
    msg = _make_msg("/help")
    session = MagicMock()

    result = await loop._handle_slash_command(msg, session)

    assert result is not None
    assert "pawbot commands" in result.content
    assert result.channel == "telegram"


@pytest.mark.asyncio
async def test_non_slash_command_returns_none() -> None:
    loop = _make_loop()
    msg = _make_msg("what is the weather")
    session = MagicMock()

    result = await loop._handle_slash_command(msg, session)

    assert result is None


def test_setup_session_uses_explicit_key() -> None:
    loop = _make_loop()
    msg = _make_msg()
    session = MagicMock()
    loop.sessions.get_or_create.return_value = session

    key, got = loop._setup_session(msg, "custom:key")

    assert key == "custom:key"
    assert got is session
    loop.sessions.get_or_create.assert_called_once_with("custom:key")


def test_build_response_saves_turn_and_session() -> None:
    loop = _make_loop()
    msg = _make_msg()
    session = MagicMock()
    loop._save_turn = MagicMock()

    result = loop._build_response(
        msg=msg,
        final_content="done",
        all_msgs=[{"role": "assistant", "content": "done"}],
        session=session,
        history_len=3,
    )

    loop._save_turn.assert_called_once()
    loop.sessions.save.assert_called_once_with(session)
    assert result.content == "done"
    assert result.channel == msg.channel


@pytest.mark.asyncio
async def test_process_tool_calls_tracks_tools_and_trace() -> None:
    loop = _make_loop()
    loop.context.add_assistant_message = MagicMock(
        side_effect=lambda messages, *_args, **_kwargs: messages + [{"role": "assistant"}]
    )
    loop.context.add_tool_result = MagicMock(
        side_effect=lambda messages, _id, name, _result: messages + [{"role": "tool", "name": name}]
    )
    loop.tools.execute = AsyncMock(side_effect=[{"ok": True}, {"ok": True}])
    loop._record_failure = MagicMock()

    tc1 = SimpleNamespace(id="call-1", name="shell", arguments={"cmd": "echo hi"})
    tc2 = SimpleNamespace(id="call-2", name="web_search", arguments={"q": "pawbot"})
    response = SimpleNamespace(
        content="working",
        tool_calls=[tc1, tc2],
        reasoning_content=None,
        thinking_blocks=None,
    )

    tools_used: list[str] = []
    trace: list[dict] = []
    messages = await loop._process_tool_calls(response, [], tools_used, trace)

    assert tools_used == ["shell", "web_search"]
    assert len(trace) == 2
    assert "shell(" in trace[0]["action"]
    assert "web_search(" in trace[1]["action"]
    assert messages[-1]["name"] == "web_search"


@pytest.mark.asyncio
async def test_process_tool_calls_records_error_failures() -> None:
    loop = _make_loop()
    loop.context.add_assistant_message = MagicMock(return_value=[])
    loop.context.add_tool_result = MagicMock(return_value=[])
    loop.tools.execute = AsyncMock(return_value="Error: boom")
    loop._record_failure = MagicMock()

    tc = SimpleNamespace(id="call-1", name="shell", arguments={"cmd": "bad"})
    response = SimpleNamespace(
        content="running",
        tool_calls=[tc],
        reasoning_content=None,
        thinking_blocks=None,
    )

    await loop._process_tool_calls(response, [], [], [])

    loop._record_failure.assert_called_once()
