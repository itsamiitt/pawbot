"""Extension registry — in-memory + persisted state with allow/deny policy (Phase E2).

The registry tracks all discovered extensions with their status, capabilities,
and policy state.  It persists to ``~/.pawbot/extensions.json`` so that
enable/disable state survives restarts.

Allow/deny policy supports glob patterns on:
  - Extension id/name
  - Declared capabilities
"""

from __future__ import annotations

import fnmatch
import json
import time
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from pawbot.extensions.schema import (
    ExtensionKind,
    ExtensionManifest,
    ExtensionOrigin,
)


EXTENSIONS_DIR = Path.home() / ".pawbot" / "extensions"
REGISTRY_FILE = EXTENSIONS_DIR / "extensions.json"


# ── Extension Record ─────────────────────────────────────────────────────────


class ExtensionStatus:
    """Extension load status."""

    LOADED = "loaded"
    DISABLED = "disabled"
    ERROR = "error"
    POLICY_DENIED = "policy_denied"


class ExtensionRecord(BaseModel):
    """A single extension's registry entry."""

    id: str
    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    kind: ExtensionKind = ExtensionKind.GENERAL
    origin: ExtensionOrigin = ExtensionOrigin.LOCAL
    source: str = ""  # Install source (path, URL, pip package, etc.)

    enabled: bool = True
    status: str = ExtensionStatus.LOADED
    error: str = ""

    # What this extension provides (populated at load time)
    tool_names: list[str] = Field(default_factory=list)
    hook_names: list[str] = Field(default_factory=list)
    channel_ids: list[str] = Field(default_factory=list)
    prompt_count: int = 0
    mcp_server_count: int = 0
    command_names: list[str] = Field(default_factory=list)
    service_ids: list[str] = Field(default_factory=list)

    capabilities: list[str] = Field(default_factory=list)

    installed_at: float = Field(default_factory=time.time)
    loaded_at: float = 0.0

    @classmethod
    def from_manifest(
        cls,
        manifest: ExtensionManifest,
        source: str = "",
    ) -> ExtensionRecord:
        """Create a record from a manifest."""
        return cls(
            id=manifest.id,
            name=manifest.name,
            version=manifest.version,
            description=manifest.description,
            kind=manifest.kind,
            origin=manifest.origin,
            source=source,
            tool_names=[t.name for t in manifest.tools],
            hook_names=list(manifest.hooks),
            channel_ids=list(manifest.channels),
            prompt_count=len(manifest.prompts),
            mcp_server_count=len(manifest.mcp_servers),
            command_names=[c.name for c in manifest.commands],
            service_ids=[s.id for s in manifest.services],
            capabilities=list(manifest.capabilities),
        )


# ── Policy Engine ────────────────────────────────────────────────────────────


class PolicyConfig(BaseModel):
    """Allow/deny policy for extensions."""

    allow: list[str] = Field(default_factory=list)  # Glob patterns
    deny: list[str] = Field(default_factory=list)  # Glob patterns
    capability_allow: list[str] = Field(default_factory=list)  # Capability globs
    capability_deny: list[str] = Field(default_factory=list)  # Capability globs


class PolicyEngine:
    """Evaluate allow/deny policy for extensions.

    Deny always wins over allow.  Empty allow lists mean "allow all".
    Patterns use glob syntax (fnmatch).
    """

    def __init__(self, config: PolicyConfig | None = None):
        self._config = config or PolicyConfig()

    @property
    def config(self) -> PolicyConfig:
        return self._config

    def is_allowed(self, record: ExtensionRecord) -> tuple[bool, str]:
        """Check if an extension is allowed by policy.

        Returns:
            (allowed: bool, reason: str)
        """
        ext_id = record.id

        # Check deny list first (deny wins)
        for pattern in self._config.deny:
            if fnmatch.fnmatch(ext_id, pattern):
                return False, f"extension '{ext_id}' matches deny pattern '{pattern}'"

        # Check capability deny
        for cap in record.capabilities:
            for pattern in self._config.capability_deny:
                if fnmatch.fnmatch(cap, pattern):
                    return (
                        False,
                        f"capability '{cap}' of '{ext_id}' matches deny pattern '{pattern}'",
                    )

        # Check allow list (empty = allow all)
        if self._config.allow:
            if not any(fnmatch.fnmatch(ext_id, p) for p in self._config.allow):
                return (
                    False,
                    f"extension '{ext_id}' not in allow list",
                )

        # Check capability allow (empty = allow all)
        if self._config.capability_allow and record.capabilities:
            if not any(
                fnmatch.fnmatch(cap, p)
                for cap in record.capabilities
                for p in self._config.capability_allow
            ):
                return (
                    False,
                    f"no capabilities of '{ext_id}' match allow list",
                )

        return True, ""


# ── Auto-Enable Rules ────────────────────────────────────────────────────────


class AutoEnableRule(BaseModel):
    """Rule for auto-enabling extensions based on environment."""

    extension_id: str  # Glob pattern
    condition: str  # "env:VAR_NAME" or "channel:channel_id" or "always"
    value: str = ""  # Expected value (empty = just check existence)


class AutoEnableEngine:
    """Apply auto-enable rules to extensions."""

    def __init__(self, rules: list[AutoEnableRule] | None = None):
        self._rules = rules or []

    def should_enable(
        self,
        ext_id: str,
        env: dict[str, str] | None = None,
        active_channels: list[str] | None = None,
    ) -> bool | None:
        """Check if an extension should be auto-enabled.

        Returns:
            True: auto-enable
            False: auto-disable
            None: no rule matches, leave as-is
        """
        import os

        env = env or dict(os.environ)
        channels = active_channels or []

        for rule in self._rules:
            if not fnmatch.fnmatch(ext_id, rule.extension_id):
                continue

            if rule.condition == "always":
                return True

            if rule.condition.startswith("env:"):
                var = rule.condition[4:]
                val = env.get(var, "")
                if rule.value:
                    if val == rule.value:
                        return True
                elif val:
                    return True

            if rule.condition.startswith("channel:"):
                channel_id = rule.condition[8:]
                if channel_id in channels:
                    return True

        return None


# ── Extension Registry ───────────────────────────────────────────────────────


class ExtensionRegistry:
    """In-memory extension registry with persisted state.

    Tracks all discovered extensions, their status, and policy.
    Persists enable/disable state to ``~/.pawbot/extensions.json``.
    """

    def __init__(
        self,
        extensions_dir: Path | None = None,
        policy: PolicyConfig | None = None,
        auto_enable_rules: list[AutoEnableRule] | None = None,
    ):
        self.extensions_dir = extensions_dir or EXTENSIONS_DIR
        self._records: dict[str, ExtensionRecord] = {}
        self._persisted_state: dict[str, Any] = {}
        self._policy = PolicyEngine(policy)
        self._auto_enable = AutoEnableEngine(auto_enable_rules)

        self._load_persisted_state()

    # ── Persisted State ──────────────────────────────────────────────────

    def _get_registry_file(self) -> Path:
        return self.extensions_dir / "extensions.json"

    def _load_persisted_state(self) -> None:
        """Load persisted enable/disable state."""
        path = self._get_registry_file()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._persisted_state = data.get("extensions", {})
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Corrupt extensions.json — ignoring: {}", e)
                self._persisted_state = {}

    def _save_persisted_state(self) -> None:
        """Save enable/disable state."""
        path = self._get_registry_file()
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": 1,
            "extensions": {
                ext_id: {
                    "enabled": rec.enabled,
                    "installed_at": rec.installed_at,
                    "source": rec.source,
                }
                for ext_id, rec in self._records.items()
            },
        }
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Registration ─────────────────────────────────────────────────────

    def register(self, manifest: ExtensionManifest, source: str = "") -> ExtensionRecord:
        """Register an extension from its manifest.

        Applies policy and auto-enable rules.  Returns the record.
        """
        record = ExtensionRecord.from_manifest(manifest, source=source)

        # Restore persisted enable/disable state
        persisted = self._persisted_state.get(record.id, {})
        if "enabled" in persisted:
            record.enabled = persisted["enabled"]
        if "installed_at" in persisted:
            record.installed_at = persisted["installed_at"]

        # Apply auto-enable rules (only if no persisted state)
        if "enabled" not in persisted:
            auto = self._auto_enable.should_enable(record.id)
            if auto is not None:
                record.enabled = auto

        # Apply policy
        if record.enabled:
            allowed, reason = self._policy.is_allowed(record)
            if not allowed:
                record.enabled = False
                record.status = ExtensionStatus.POLICY_DENIED
                record.error = reason
                logger.info("Extension '{}' denied by policy: {}", record.id, reason)

        if not record.enabled and record.status not in (
            ExtensionStatus.POLICY_DENIED,
            ExtensionStatus.ERROR,
        ):
            record.status = ExtensionStatus.DISABLED

        self._records[record.id] = record
        return record

    def unregister(self, ext_id: str) -> bool:
        """Remove an extension from the registry."""
        if ext_id in self._records:
            del self._records[ext_id]
            self._persisted_state.pop(ext_id, None)
            self._save_persisted_state()
            return True
        return False

    # ── Enable/Disable ───────────────────────────────────────────────────

    def enable(self, ext_id: str) -> bool:
        """Enable an extension.  Returns False if not found."""
        record = self._records.get(ext_id)
        if not record:
            return False

        # Check policy before enabling
        allowed, reason = self._policy.is_allowed(record)
        if not allowed:
            logger.warning("Cannot enable '{}': {}", ext_id, reason)
            return False

        record.enabled = True
        record.status = ExtensionStatus.LOADED
        record.error = ""
        self._save_persisted_state()
        return True

    def disable(self, ext_id: str) -> bool:
        """Disable an extension.  Returns False if not found."""
        record = self._records.get(ext_id)
        if not record:
            return False
        record.enabled = False
        record.status = ExtensionStatus.DISABLED
        self._save_persisted_state()
        return True

    # ── Query ────────────────────────────────────────────────────────────

    def get(self, ext_id: str) -> ExtensionRecord | None:
        """Get an extension record by id."""
        return self._records.get(ext_id)

    def list_all(self) -> list[ExtensionRecord]:
        """List all registered extensions."""
        return list(self._records.values())

    def list_enabled(self) -> list[ExtensionRecord]:
        """List all enabled extensions."""
        return [r for r in self._records.values() if r.enabled]

    def list_by_status(self, status: str) -> list[ExtensionRecord]:
        """List extensions by status."""
        return [r for r in self._records.values() if r.status == status]

    @property
    def count(self) -> int:
        return len(self._records)

    @property
    def enabled_count(self) -> int:
        return sum(1 for r in self._records.values() if r.enabled)

    def to_json(self) -> list[dict[str, Any]]:
        """Serialize all records to JSON-friendly dicts."""
        return [r.model_dump() for r in self._records.values()]

    # ── Mark loaded/error ────────────────────────────────────────────────

    def mark_loaded(self, ext_id: str) -> None:
        """Mark an extension as successfully loaded."""
        record = self._records.get(ext_id)
        if record:
            record.status = ExtensionStatus.LOADED
            record.loaded_at = time.time()
            record.error = ""

    def mark_error(self, ext_id: str, error: str) -> None:
        """Mark an extension as failed to load."""
        record = self._records.get(ext_id)
        if record:
            record.status = ExtensionStatus.ERROR
            record.error = error
            record.enabled = False

    # ── Policy ───────────────────────────────────────────────────────────

    @property
    def policy(self) -> PolicyConfig:
        return self._policy.config

    def update_policy(self, policy: PolicyConfig) -> None:
        """Update the policy and re-evaluate all extensions."""
        self._policy = PolicyEngine(policy)
        for record in self._records.values():
            if record.enabled:
                allowed, reason = self._policy.is_allowed(record)
                if not allowed:
                    record.enabled = False
                    record.status = ExtensionStatus.POLICY_DENIED
                    record.error = reason
        self._save_persisted_state()
