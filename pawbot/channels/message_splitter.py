"""Split long messages into channel-appropriate chunks (Phase 11.5).

Each channel has different message length limits. This module splits
responses at natural boundaries (paragraphs, sentences, words) to
respect those limits while maintaining readability.
"""

from __future__ import annotations


# Channel-specific message length limits (characters)
CHANNEL_LIMITS: dict[str, int] = {
    "telegram": 4096,
    "whatsapp": 65536,
    "slack": 40000,       # Slack blocks have a ~40K char limit
    "discord": 2000,
    "email": 0,           # No limit
    "cli": 0,             # No limit
    "websocket": 0,       # No limit
    "default": 4096,
}


def split_message(
    text: str,
    channel: str = "default",
    max_length: int = 0,
) -> list[str]:
    """Split a message into chunks appropriate for the channel.

    Splits at paragraph boundaries when possible, falls back to
    sentence boundaries, then word boundaries, then hard splits.

    Args:
        text: The full message text.
        channel: Channel name for looking up limits.
        max_length: Override length limit (0 = use channel default).

    Returns:
        List of message chunks (always at least one element).
    """
    limit = max_length or CHANNEL_LIMITS.get(channel, CHANNEL_LIMITS["default"])

    if limit <= 0 or len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        # Try to split at a paragraph boundary (double newline)
        split_pos = remaining[:limit].rfind("\n\n")
        if split_pos > limit * 0.3:
            chunks.append(remaining[:split_pos].rstrip())
            remaining = remaining[split_pos:].lstrip()
            continue

        # Try to split at a single newline
        split_pos = remaining[:limit].rfind("\n")
        if split_pos > limit * 0.3:
            chunks.append(remaining[:split_pos].rstrip())
            remaining = remaining[split_pos:].lstrip()
            continue

        # Try to split at a sentence boundary
        for sep in [". ", "! ", "? ", "; "]:
            split_pos = remaining[:limit].rfind(sep)
            if split_pos > limit * 0.3:
                chunks.append(remaining[: split_pos + 1].rstrip())
                remaining = remaining[split_pos + 1 :].lstrip()
                break
        else:
            # Try to split at a word boundary (space)
            split_pos = remaining[:limit].rfind(" ")
            if split_pos > limit * 0.3:
                chunks.append(remaining[:split_pos])
                remaining = remaining[split_pos + 1 :]
            else:
                # Absolute hard split (no useful boundary found)
                chunks.append(remaining[:limit])
                remaining = remaining[limit:]

    # Add continuation markers when split
    if len(chunks) > 1:
        total = len(chunks)
        for i in range(total):
            chunks[i] = f"{chunks[i]}\n\n_({i + 1}/{total})_"

    return chunks
