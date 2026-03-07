"""Channel policy enforcement — applies DM/group/rate-limit rules (Phase 11.2).

The PolicyEngine is the central gatekeeper for all incoming messages.
It evaluates DM policies, group policies, per-user rate limits, and
media constraints before a message reaches the agent loop.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger


class PolicyEngine:
    """Evaluates channel policies against incoming messages.

    Supports:
      - DM policies: open, allowlist, pairing, disabled
      - Group policies: open, allowlist, mention, disabled
      - Per-user rate limiting (sliding 1-minute window)
      - Media size and type filtering
      - Self-chat mode (bot messaging itself)
    """

    def __init__(self, policy_config: dict[str, Any] | None = None):
        cfg = policy_config or {}
        self.config = cfg
        self._user_message_counts: dict[str, list[float]] = {}
        self._rate_limit = cfg.get("rate_limit_per_user", 30)
        self._dm_policy = cfg.get("dm_policy", "open")
        self._group_policy = cfg.get("group_policy", "mention")
        self._allowed_users: set[str] = set(cfg.get("allowed_users", []))
        self._allowed_groups: set[str] = set(cfg.get("allowed_groups", []))
        self._self_chat = cfg.get("self_chat_mode", False)

    def check_dm(self, sender_id: str, bot_id: str = "") -> tuple[bool, str]:
        """Check if a DM should be processed.

        Returns:
            (allowed, reason)
        """
        # Self-chat check
        if sender_id and sender_id == bot_id:
            if self._self_chat:
                return True, "self-chat mode enabled"
            return False, "self-chat mode disabled"

        if self._dm_policy == "open":
            return True, ""
        elif self._dm_policy == "allowlist":
            if sender_id in self._allowed_users:
                return True, ""
            return False, f"user '{sender_id}' not in DM allowlist"
        elif self._dm_policy == "pairing":
            return self._check_pairing(sender_id)
        elif self._dm_policy == "disabled":
            return False, "DMs are disabled"
        return True, ""

    def check_group(
        self,
        group_id: str,
        sender_id: str,
        is_mention: bool = False,
    ) -> tuple[bool, str]:
        """Check if a group message should be processed.

        Returns:
            (allowed, reason)
        """
        if self._group_policy == "open":
            return True, ""
        elif self._group_policy == "allowlist":
            if group_id in self._allowed_groups:
                return True, ""
            return False, f"group '{group_id}' not in allowlist"
        elif self._group_policy == "mention":
            if is_mention:
                return True, ""
            return False, "bot not mentioned in group"
        elif self._group_policy == "disabled":
            return False, "group messages are disabled"
        return True, ""

    def check_rate_limit(self, sender_id: str) -> tuple[bool, str]:
        """Check if the user is within their rate limit (sliding 1-min window).

        Returns:
            (allowed, reason)
        """
        if self._rate_limit <= 0:
            return True, ""

        now = time.time()
        window_start = now - 60  # 1-minute window

        # Get or create user's message history
        if sender_id not in self._user_message_counts:
            self._user_message_counts[sender_id] = []

        # Clean old entries
        timestamps = self._user_message_counts[sender_id]
        self._user_message_counts[sender_id] = [
            t for t in timestamps if t > window_start
        ]

        # Check limit
        if len(self._user_message_counts[sender_id]) >= self._rate_limit:
            return False, f"rate limit exceeded ({self._rate_limit}/min)"

        # Record this message
        self._user_message_counts[sender_id].append(now)
        return True, ""

    def check_media(
        self, file_size_bytes: int, mime_type: str
    ) -> tuple[bool, str]:
        """Check if a media file is allowed.

        Returns:
            (allowed, reason)
        """
        media_config = self.config.get("media", {})
        max_mb = media_config.get("max_size_mb", 50)
        max_bytes = max_mb * 1024 * 1024
        allowed_types = media_config.get("allowed_types", [])

        if file_size_bytes > max_bytes:
            actual_mb = file_size_bytes / 1024 / 1024
            return False, f"file too large ({actual_mb:.1f}MB > {max_mb}MB)"

        if allowed_types and mime_type not in allowed_types:
            return False, f"file type '{mime_type}' not allowed"

        return True, ""

    def _check_pairing(self, sender_id: str) -> tuple[bool, str]:
        """Check if user has a paired device."""
        paired_file = Path.home() / ".pawbot" / "devices" / "paired.json"
        if not paired_file.exists():
            return False, "no paired devices"

        try:
            data = json.loads(paired_file.read_text(encoding="utf-8"))
            paired_users = [d.get("user_id") for d in data.get("devices", [])]
            if sender_id in paired_users:
                return True, ""
            return False, f"user '{sender_id}' has no paired device"
        except Exception:
            return False, "could not read paired devices"
