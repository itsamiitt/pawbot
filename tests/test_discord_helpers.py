"""
tests/test_discord_helpers.py

Tests for Discord attachment download helper.
Run: pytest tests/test_discord_helpers.py -v
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pawbot.channels.discord import DiscordChannel


def _make_discord_channel() -> DiscordChannel:
    """Create a minimal DiscordChannel instance without calling __init__."""
    channel = object.__new__(DiscordChannel)
    channel.config = MagicMock()
    channel.bus = MagicMock()
    channel._http = AsyncMock()
    channel.MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
    return channel


@pytest.mark.asyncio
async def test_downloads_attachment_successfully(tmp_path):
    """Valid attachment should be downloaded and tracked."""
    ch = _make_discord_channel()

    response = MagicMock()
    response.content = b"fake_image_bytes"
    response.raise_for_status = MagicMock()
    ch._http.get = AsyncMock(return_value=response)

    attachments = [
        {
            "id": "123",
            "url": "https://example.com/img.png",
            "filename": "img.png",
            "size": 100,
        }
    ]
    descs, paths = await ch._download_attachments(attachments, tmp_path)

    assert len(paths) == 1
    assert Path(paths[0]).exists()
    assert "[attachment:" in descs[0]


@pytest.mark.asyncio
async def test_too_large_attachment_skipped(tmp_path):
    """Oversized attachments should be skipped with a message."""
    ch = _make_discord_channel()
    ch._http.get = AsyncMock()
    attachments = [
        {
            "id": "456",
            "url": "https://example.com/big.zip",
            "filename": "big.zip",
            "size": 999_999_999,
        }
    ]
    descs, paths = await ch._download_attachments(attachments, tmp_path)

    assert paths == []
    assert "too large" in descs[0]
    ch._http.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_failure_handled_gracefully(tmp_path):
    """Download failures should not raise and should return failure text."""
    ch = _make_discord_channel()
    ch._http.get = AsyncMock(side_effect=Exception("network error"))
    attachments = [
        {
            "id": "789",
            "url": "https://example.com/file.pdf",
            "filename": "file.pdf",
            "size": 500,
        }
    ]
    descs, paths = await ch._download_attachments(attachments, tmp_path)

    assert paths == []
    assert "download failed" in descs[0]


@pytest.mark.asyncio
async def test_empty_attachments_returns_empty_lists(tmp_path):
    """No attachments should produce empty output lists."""
    ch = _make_discord_channel()
    descs, paths = await ch._download_attachments([], tmp_path)
    assert descs == []
    assert paths == []

