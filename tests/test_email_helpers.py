"""
tests/test_email_helpers.py

Tests for extracted EmailChannel helper methods.
Run: pytest tests/test_email_helpers.py -v
"""

from unittest.mock import MagicMock, patch

from pawbot.channels.email import EmailChannel


def _make_email_channel() -> EmailChannel:
    """Create a minimal EmailChannel instance without calling __init__."""
    channel = object.__new__(EmailChannel)
    channel.config = MagicMock()
    channel.config.imap_host = "imap.example.com"
    channel.config.imap_port = 993
    channel.config.imap_use_ssl = True
    channel.config.imap_username = "bot@example.com"
    channel.config.imap_password = "secret"
    channel.config.imap_mailbox = "INBOX"
    channel._processed_uids = set()
    channel._MAX_PROCESSED_UIDS = 100000
    return channel


def _make_raw_email(
    sender: str = "alice@example.com",
    subject: str = "Test subject",
    body: str = "Hello from Alice",
) -> bytes:
    """Build a minimal RFC-2822 message as raw bytes."""
    return (
        f"From: {sender}\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"Message-ID: <test-id-001@example.com>\r\n"
        f"\r\n"
        f"{body}\r\n"
    ).encode("utf-8")


def test_parse_raw_email_returns_structured_dict():
    """_parse_raw_email should return sender/subject/body/uid for valid input."""
    ch = _make_email_channel()
    raw = _make_raw_email("alice@example.com", "Hello", "Test body")

    result = ch._parse_raw_email(raw, uid="UID001", dedupe=False)

    assert result is not None
    assert result["sender"] == "alice@example.com"
    assert result["subject"] == "Hello"
    assert result["uid"] == "UID001"
    assert "Test body" in result["body"]


def test_parse_raw_email_skips_on_dedupe():
    """Known UID should be skipped when dedupe=True."""
    ch = _make_email_channel()
    ch._processed_uids.add("UID002")
    raw = _make_raw_email()

    result = ch._parse_raw_email(raw, uid="UID002", dedupe=True)

    assert result is None


def test_parse_raw_email_skips_empty_sender():
    """Messages with no From address should be ignored."""
    ch = _make_email_channel()
    raw = b"Subject: No Sender\r\nContent-Type: text/plain\r\n\r\nBody\r\n"

    result = ch._parse_raw_email(raw, uid="UID003", dedupe=False)

    assert result is None


def test_parse_raw_email_no_dedupe_allows_repeat():
    """Known UID should still parse when dedupe=False."""
    ch = _make_email_channel()
    ch._processed_uids.add("UID004")
    raw = _make_raw_email("bob@example.com", "Repeat", "content")

    result = ch._parse_raw_email(raw, uid="UID004", dedupe=False)

    assert result is not None
    assert result["sender"] == "bob@example.com"
    assert result["uid"] == "UID004"


def test_open_imap_uses_ssl_when_configured():
    """_open_imap_connection should use IMAP4_SSL when SSL is enabled."""
    ch = _make_email_channel()

    with patch("imaplib.IMAP4_SSL") as mock_ssl, patch("imaplib.IMAP4") as mock_plain:
        mock_client = MagicMock()
        mock_ssl.return_value = mock_client

        ch._open_imap_connection()

    mock_ssl.assert_called_once_with("imap.example.com", 993)
    mock_client.login.assert_called_once_with("bot@example.com", "secret")
    mock_plain.assert_not_called()

