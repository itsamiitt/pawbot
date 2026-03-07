"""
tests/test_slack_helpers.py

Tests for the refactored Slack socket event helpers.
Run: pytest tests/test_slack_helpers.py -v
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from pawbot.channels.slack import SlackChannel


def _make_slack_channel(bot_user_id: str = "BOT123") -> SlackChannel:
    """Create a minimal SlackChannel with mocked dependencies."""
    channel              = object.__new__(SlackChannel)
    channel.config       = MagicMock()
    channel.config.allow_from = []
    channel.bus          = MagicMock()
    channel._bot_user_id = bot_user_id
    channel._running     = False
    return channel


def test_skip_non_events_api_type():
    """Events that are not 'message' or 'app_mention' must be skipped."""
    ch     = _make_slack_channel()
    event  = {"type": "reaction_added", "user": "U123"}
    assert ch._should_process_slack_event(event, "reaction_added") is False


def test_skip_messages_with_subtype():
    """Any subtype (bot_message, message_changed) must be skipped."""
    ch    = _make_slack_channel()
    event = {"type": "message", "subtype": "bot_message", "user": "U123"}
    assert ch._should_process_slack_event(event, "message") is False


def test_skip_bot_own_messages():
    """Messages from the bot itself must be skipped."""
    ch    = _make_slack_channel(bot_user_id="BOT123")
    event = {"type": "message", "user": "BOT123", "text": "I am bot"}
    assert ch._should_process_slack_event(event, "message") is False


def test_skip_duplicate_mention_in_message_event():
    """
    Slack sends both 'message' and 'app_mention' for @mentions.
    The 'message' copy must be skipped to avoid double-processing.
    """
    ch    = _make_slack_channel(bot_user_id="BOT123")
    event = {"type": "message", "user": "U456", "text": "Hey <@BOT123> help"}
    assert ch._should_process_slack_event(event, "message") is False


def test_allow_normal_app_mention():
    """A normal app_mention must pass all guards."""
    ch    = _make_slack_channel(bot_user_id="BOT123")
    event = {"type": "app_mention", "user": "U456", "text": "<@BOT123> do something"}
    assert ch._should_process_slack_event(event, "app_mention") is True


def test_allow_plain_message_without_mention():
    """A plain DM-style message (no @mention, no subtype) must pass."""
    ch    = _make_slack_channel(bot_user_id="BOT123")
    event = {"type": "message", "user": "U789", "text": "just a normal message"}
    assert ch._should_process_slack_event(event, "message") is True


def test_extract_slack_message_returns_fields():
    """_extract_slack_message must return all four fields correctly."""
    ch    = _make_slack_channel()
    event = {
        "user":      "U789",
        "channel":   "C001",
        "text":      "  hello  ",
        "thread_ts": "1234567890.000100",
    }
    sender_id, chat_id, text, thread_ts = ch._extract_slack_message(event)
    assert sender_id  == "U789"
    assert chat_id    == "C001"
    assert text       == "hello"   # stripped
    assert thread_ts  == "1234567890.000100"


def test_extract_slack_message_missing_thread_ts():
    """_extract_slack_message must return None for thread_ts if not present."""
    ch    = _make_slack_channel()
    event = {"user": "U111", "channel": "C002", "text": "hi"}
    _, _, _, thread_ts = ch._extract_slack_message(event)
    assert thread_ts is None
