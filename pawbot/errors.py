"""Pawbot exception hierarchy.

Phase 0: Extended from 3 exceptions to a full typed hierarchy covering
all subsystems. Use these instead of bare `except Exception` catches.
"""


class PawbotError(Exception):
    """Base exception for all Pawbot errors."""
    pass


# ── Configuration ────────────────────────────────────────────────────────────

class ConfigError(PawbotError):
    """Configuration is missing, invalid, or has placeholder values."""
    pass


# ── LLM Providers ───────────────────────────────────────────────────────────

class ProviderError(PawbotError):
    """LLM provider call failed after all retries."""
    pass


class ProviderUnavailableError(ProviderError):
    """A provider is temporarily unavailable (rate-limited, network error)."""
    pass


class ProviderAuthError(ProviderError):
    """Provider authentication failed (invalid/expired API key or OAuth token)."""
    pass


# ── Memory ───────────────────────────────────────────────────────────────────

class MemoryBackendError(PawbotError):
    """Memory backend operation error (SQLite, ChromaDB, Redis)."""
    pass


class MemoryMigrationError(MemoryBackendError):
    """Database schema migration failed."""
    pass


# ── Tools ────────────────────────────────────────────────────────────────────

class ToolError(PawbotError):
    """Tool execution error."""
    pass


class ToolTimeoutError(ToolError):
    """Tool execution exceeded its time budget."""
    pass


class ToolSandboxError(ToolError):
    """Tool attempted a disallowed operation (e.g. blocked command)."""
    pass


# ── Security ─────────────────────────────────────────────────────────────────

class SecurityError(PawbotError):
    """Security check failed (injection detection, action gate, etc.)."""
    pass


class InjectionDetectedError(SecurityError):
    """Prompt injection attempt detected."""
    pass


# ── Fleet ────────────────────────────────────────────────────────────────────

class FleetError(PawbotError):
    """Fleet orchestration error."""
    pass


class WorkerUnavailableError(FleetError):
    """No suitable worker available for the task."""
    pass


# ── Channels ─────────────────────────────────────────────────────────────────

class ChannelError(PawbotError):
    """Channel communication error."""
    pass


class ChannelConnectionError(ChannelError):
    """Failed to connect to a channel backend (Telegram, WhatsApp, etc.)."""
    pass


# ── Agent Loop ───────────────────────────────────────────────────────────────

class AgentLoopError(PawbotError):
    """Agent loop execution error."""
    pass


class ContextOverflowError(AgentLoopError):
    """Conversation context exceeds model's token limit."""
    pass
