"""Scan and redact likely secrets from agent output."""

from __future__ import annotations

import re

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub Token", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("OpenAI Key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("Anthropic Key", re.compile(r"sk-ant-[A-Za-z0-9-]{20,}")),
    ("Slack Token", re.compile(r"xox[aboprs]-[A-Za-z0-9-]{10,}")),
    ("Private Key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
]


def scan_output(text: str) -> list[tuple[str, int]]:
    """Return a list of detected secret labels and start offsets."""
    found: list[tuple[str, int]] = []
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            found.append((name, match.start()))
    return found


def redact_secrets(text: str) -> str:
    """Replace detected secrets with labeled redaction markers."""
    redacted = text
    for name, pattern in SECRET_PATTERNS:
        redacted = pattern.sub(f"[REDACTED:{name}]", redacted)
    return redacted
