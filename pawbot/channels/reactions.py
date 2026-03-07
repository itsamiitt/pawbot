"""Acknowledgment reactions — confirm message receipt with emoji (Phase 11.4).

Adds "acknowledged" reactions to messages based on policy scope,
providing visual feedback that the bot has seen and is processing a message.
"""

from __future__ import annotations


class AckReactor:
    """Adds 'acknowledged' reactions to messages based on policy.

    Scope values:
      - "none": Never react
      - "all": React to every message
      - "group-mentions": Only when mentioned in groups
      - "dms-only": Only in DMs
    """

    # "Acknowledged / processing" reaction per channel
    REACTION_MAP = {
        "telegram": "👀",
        "whatsapp": "👀",
        "slack": "eyes",
        "discord": "👀",
        "default": "👀",
    }

    # "Processing complete" reaction per channel
    DONE_REACTION_MAP = {
        "telegram": "✅",
        "whatsapp": "✅",
        "slack": "white_check_mark",
        "discord": "✅",
        "default": "✅",
    }

    def __init__(self, scope: str = "none", channel_type: str = "default"):
        self.scope = scope
        self.channel_type = channel_type
        self._ack_emoji = self.REACTION_MAP.get(
            channel_type, self.REACTION_MAP["default"]
        )
        self._done_emoji = self.DONE_REACTION_MAP.get(
            channel_type, self.DONE_REACTION_MAP["default"]
        )

    def should_ack(self, is_dm: bool, is_mention: bool) -> bool:
        """Should we add an acknowledgment reaction?"""
        if self.scope == "none":
            return False
        if self.scope == "all":
            return True
        if self.scope == "group-mentions":
            return is_mention and not is_dm
        if self.scope == "dms-only":
            return is_dm
        return False

    @property
    def ack_emoji(self) -> str:
        """The emoji to use for 'acknowledged/processing'."""
        return self._ack_emoji

    @property
    def done_emoji(self) -> str:
        """The emoji to use for 'processing complete'."""
        return self._done_emoji
