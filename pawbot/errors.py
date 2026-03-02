"""Pawbot custom exception hierarchy."""


class PawbotError(Exception):
    """Base exception for all Pawbot errors."""
    pass


class ConfigError(PawbotError):
    """Configuration is missing, invalid, or has placeholder values."""
    pass


class ProviderError(PawbotError):
    """LLM provider call failed after all retries."""
    pass
