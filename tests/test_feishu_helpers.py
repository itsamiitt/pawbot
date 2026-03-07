"""
tests/test_feishu_helpers.py

Tests for the refactored Feishu element dispatch table and _parse_message_content.
Run: pytest tests/test_feishu_helpers.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from pawbot.channels.feishu import (
    _extract_element_content,
    _extract_share_card_content,
    FeishuChannel,
)


# ── Element extraction tests ────────────────────────────────────────────────


def test_extract_markdown_element():
    """markdown/lark_md elements must return their 'content' field."""
    el = {"tag": "markdown", "content": "Hello **world**"}
    assert _extract_element_content(el) == ["Hello **world**"]


def test_extract_div_element_with_text():
    """div elements with a text dict must return the content string."""
    el = {"tag": "div", "text": {"tag": "lark_md", "content": "Div text"}}
    assert _extract_element_content(el) == ["Div text"]


def test_extract_button_with_url():
    """button elements must return text content and URL."""
    el = {"tag": "button", "text": {"content": "Click me"}, "url": "https://example.com"}
    result = _extract_element_content(el)
    assert "Click me" in result
    assert "link: https://example.com" in result


def test_extract_image_element():
    """img elements must return the alt content or [image] fallback."""
    el_with_alt = {"tag": "img", "alt": {"content": "A chart"}}
    assert _extract_element_content(el_with_alt) == ["A chart"]

    el_no_alt = {"tag": "img"}
    assert _extract_element_content(el_no_alt) == ["[image]"]


def test_extract_unknown_tag_recurses():
    """Unknown tags must recurse into child elements."""
    el = {
        "tag": "custom_container",
        "elements": [{"tag": "plain_text", "content": "nested"}],
    }
    assert _extract_element_content(el) == ["nested"]


def test_extract_non_dict_returns_empty():
    """Non-dict inputs must return empty list without raising."""
    assert _extract_element_content("not a dict") == []  # type: ignore
    assert _extract_element_content(None) == []           # type: ignore


# ── _parse_message_content tests ────────────────────────────────────────────


def _make_feishu_channel() -> FeishuChannel:
    ch = object.__new__(FeishuChannel)
    ch.config = MagicMock()
    ch.config.react_emoji = "THUMBSUP"
    ch.bus    = MagicMock()
    ch._bot_user_id = None
    ch._running     = False
    ch._client      = None
    ch._loop        = None
    # mock the download helper so tests don't need a live Feishu connection
    ch._download_and_save_media = AsyncMock(return_value=(None, "[image: failed]"))
    return ch


@pytest.mark.asyncio
async def test_parse_text_message():
    ch = _make_feishu_channel()
    content_parts, media_paths = await ch._parse_message_content(
        "text", {"text": "hello"}, "MSG001"
    )
    assert content_parts == ["hello"]
    assert media_paths == []


@pytest.mark.asyncio
async def test_parse_unknown_message_type_uses_fallback():
    ch = _make_feishu_channel()
    content_parts, media_paths = await ch._parse_message_content(
        "unknown_type", {}, "MSG002"
    )
    assert content_parts == ["[unknown_type]"]
    assert media_paths == []


@pytest.mark.asyncio
async def test_parse_image_message_calls_download():
    ch = _make_feishu_channel()
    ch._download_and_save_media = AsyncMock(return_value=("/tmp/img.jpg", "[image: img.jpg]"))
    content_parts, media_paths = await ch._parse_message_content(
        "image", {"image_key": "KEY123"}, "MSG003"
    )
    assert "/tmp/img.jpg" in media_paths
    assert any("image" in p for p in content_parts)


@pytest.mark.asyncio
async def test_parse_share_card_message():
    ch = _make_feishu_channel()
    content_parts, media_paths = await ch._parse_message_content(
        "share_chat", {"chat_id": "oc_ABCDEF"}, "MSG004"
    )
    assert media_paths == []
    assert any("shared chat" in p for p in content_parts)
