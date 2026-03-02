"""Secret masking. Use before logging anything that looks like a key."""

_PLACEHOLDERS = frozenset({
    "sk-or-v1-xxx", "YOUR_API_KEY", "REPLACE_ME", "xxx",
    "your-key-here", "sk-or-xxx", "BSA-xxx",
    "YOUR_BOT_TOKEN", "YOUR_USER_ID", "PLACEHOLDER",
})


def mask_secret(value: str, show_chars: int = 8) -> str:
    """Return first `show_chars` chars + bullets. Safe to log."""
    if not value or len(value) <= show_chars:
        return "••••••••"
    return value[:show_chars] + "••••••••"


def is_placeholder(value: str | None) -> bool:
    """Return True if value is a known placeholder, not a real secret."""
    if not value or not value.strip():
        return True
    v = value.strip()
    return v in _PLACEHOLDERS or any(p in v for p in _PLACEHOLDERS)
