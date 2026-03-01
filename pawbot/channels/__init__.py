"""Chat channels module with plugin architecture."""

from pawbot.channels.base import BaseChannel, ChannelMessage, RateLimiter
from pawbot.channels.manager import ChannelManager

__all__ = [
    "BaseChannel",
    "ChannelManager",
    "ChannelMessage",
    "RateLimiter",
]
