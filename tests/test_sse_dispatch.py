"""Tests for Codex SSE event dispatch handlers."""

from __future__ import annotations

import pytest

from pawbot.providers.openai_codex_provider import (
    _SSE_HANDLERS,
    _SSEState,
    _sse_error,
    _sse_fn_args_delta,
    _sse_fn_args_done,
    _sse_output_item_added,
    _sse_output_item_done,
    _sse_output_text_delta,
    _sse_response_completed,
)


def test_text_delta_appends_to_content() -> None:
    state = _SSEState()
    _sse_output_text_delta({"delta": "Hello "}, state)
    _sse_output_text_delta({"delta": "world"}, state)
    assert state.content == "Hello world"


def test_output_item_added_buffers_tool_call() -> None:
    state = _SSEState()
    _sse_output_item_added(
        {
            "item": {
                "type": "function_call",
                "call_id": "call_abc",
                "id": "fc_1",
                "name": "shell",
            }
        },
        state,
    )
    assert "call_abc" in state.tool_call_buffers
    assert state.tool_call_buffers["call_abc"]["name"] == "shell"


def test_fn_args_delta_appends_fragment() -> None:
    state = _SSEState()
    state.tool_call_buffers["call_xyz"] = {
        "id": "fc_2",
        "name": "web_search",
        "arguments": "",
    }
    _sse_fn_args_delta({"call_id": "call_xyz", "delta": '{"q":'}, state)
    _sse_fn_args_delta({"call_id": "call_xyz", "delta": '"pawbot"}'}, state)
    assert state.tool_call_buffers["call_xyz"]["arguments"] == '{"q":"pawbot"}'


def test_fn_args_done_overwrites_buffer() -> None:
    state = _SSEState()
    state.tool_call_buffers["call_done"] = {
        "id": "fc_3",
        "name": "read_file",
        "arguments": '{"pa',
    }
    _sse_fn_args_done({"call_id": "call_done", "arguments": '{"path": "/tmp/x.txt"}'}, state)
    assert state.tool_call_buffers["call_done"]["arguments"] == '{"path": "/tmp/x.txt"}'


def test_output_item_done_flushes_tool_call() -> None:
    state = _SSEState()
    state.tool_call_buffers["call_flush"] = {
        "id": "fc_4",
        "name": "shell",
        "arguments": '{"cmd":"ls /"}',
    }
    _sse_output_item_done(
        {
            "item": {"type": "function_call", "call_id": "call_flush", "id": "fc_4"},
        },
        state,
    )
    assert len(state.tool_calls) == 1
    assert state.tool_calls[0].name == "shell"
    assert state.tool_calls[0].arguments == {"cmd": "ls /"}


def test_response_completed_sets_finish_reason() -> None:
    done = _SSEState()
    _sse_response_completed({"response": {"status": "completed"}}, done)
    assert done.finish_reason == "stop"

    incomplete = _SSEState()
    _sse_response_completed({"response": {"status": "incomplete"}}, incomplete)
    assert incomplete.finish_reason == "length"


def test_error_event_raises() -> None:
    with pytest.raises(RuntimeError, match="Codex response failed"):
        _sse_error({"type": "error"}, _SSEState())


def test_dispatch_table_is_complete() -> None:
    required = {
        "response.output_item.added",
        "response.output_text.delta",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.output_item.done",
        "response.completed",
        "error",
        "response.failed",
    }
    missing = required - set(_SSE_HANDLERS.keys())
    assert not missing
    assert all(callable(handler) for handler in _SSE_HANDLERS.values())
