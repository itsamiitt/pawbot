"""OAuth provider authentication — manage multiple auth profiles (Phase 12.3).

Supports multiple authentication modes per provider:
  - api_key: Static API key (OpenAI, Anthropic, OpenRouter, etc.)
  - oauth: OAuth 2.0 access/refresh tokens with expiry tracking
  - service_account: Google-style service account credentials

Profiles are stored at ~/.pawbot/identity/auth_profiles.json.
Secrets are stored on disk — ensure file permissions are restricted.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger


AUTH_FILE = Path.home() / ".pawbot" / "identity" / "auth_profiles.json"


class OAuthProfile:
    """A single OAuth/API authentication profile."""

    def __init__(self, provider: str, mode: str, data: dict[str, Any]):
        self.provider = provider         # e.g. "openai", "google", "anthropic"
        self.mode = mode                 # "oauth", "api_key", "service_account"
        self.data = data

    @property
    def is_valid(self) -> bool:
        """Check if the profile has valid credentials."""
        if self.mode == "api_key":
            return bool(self.data.get("api_key"))
        if self.mode == "oauth":
            return bool(self.data.get("access_token"))
        if self.mode == "service_account":
            return bool(self.data.get("credentials_path"))
        return False

    @property
    def is_expired(self) -> bool:
        """Check if OAuth token has expired."""
        if self.mode != "oauth":
            return False
        expires_at = self.data.get("expires_at", 0)
        return time.time() > expires_at

    def to_dict(self) -> dict[str, Any]:
        """Public representation (no secrets)."""
        return {
            "provider": self.provider,
            "mode": self.mode,
            "valid": self.is_valid,
            "expired": self.is_expired,
        }


class AuthManager:
    """Manage multiple OAuth/API key profiles for different providers.

    Profiles are keyed as "provider:profile_name" (e.g. "openai:default").
    """

    SUPPORTED_PROVIDERS: dict[str, dict[str, Any]] = {
        "openai": {"modes": ["api_key", "oauth"], "key_prefix": "sk-"},
        "anthropic": {"modes": ["api_key"], "key_prefix": "sk-ant-"},
        "google": {"modes": ["api_key", "oauth", "service_account"], "key_prefix": "AI"},
        "openrouter": {"modes": ["api_key"], "key_prefix": "sk-or-"},
        "deepseek": {"modes": ["api_key"], "key_prefix": ""},
        "ollama": {"modes": ["none"], "key_prefix": ""},
    }

    def __init__(self, auth_file: Path | None = None):
        self._file = auth_file or AUTH_FILE
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._profiles: dict[str, OAuthProfile] = {}
        self._load()

    def _load(self) -> None:
        """Load auth profiles from disk."""
        if not self._file.exists():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            for key, profile_data in data.get("profiles", {}).items():
                self._profiles[key] = OAuthProfile(
                    provider=profile_data.get("provider", key.split(":")[0]),
                    mode=profile_data.get("mode", "api_key"),
                    data=profile_data.get("data", {}),
                )
            logger.debug("Loaded {} auth profiles", len(self._profiles))
        except Exception:
            logger.warning("Could not load auth profiles")

    def _save(self) -> None:
        """Save auth profiles to disk."""
        data = {
            "version": 1,
            "profiles": {
                key: {
                    "provider": p.provider,
                    "mode": p.mode,
                    "data": p.data,
                }
                for key, p in self._profiles.items()
            },
        }
        self._file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ── API Key Management ────────────────────────────────────────────────

    def set_api_key(
        self, provider: str, api_key: str, profile_name: str = "default"
    ) -> None:
        """Set an API key for a provider."""
        key = f"{provider}:{profile_name}"

        # Validate key format (warning only)
        expected_prefix = self.SUPPORTED_PROVIDERS.get(provider, {}).get(
            "key_prefix", ""
        )
        if expected_prefix and not api_key.startswith(expected_prefix):
            logger.warning(
                "API key for '{}' doesn't start with expected prefix '{}' — saving anyway",
                provider,
                expected_prefix,
            )

        self._profiles[key] = OAuthProfile(
            provider=provider,
            mode="api_key",
            data={"api_key": api_key, "set_at": time.time()},
        )
        self._save()
        logger.info("API key set for {}:{}", provider, profile_name)

    # ── OAuth Token Management ────────────────────────────────────────────

    def set_oauth_token(
        self,
        provider: str,
        access_token: str,
        refresh_token: str = "",
        expires_in: int = 3600,
        profile_name: str = "default",
    ) -> None:
        """Set OAuth tokens for a provider."""
        key = f"{provider}:{profile_name}"
        self._profiles[key] = OAuthProfile(
            provider=provider,
            mode="oauth",
            data={
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": time.time() + expires_in,
                "set_at": time.time(),
            },
        )
        self._save()
        logger.info("OAuth tokens set for {}:{}", provider, profile_name)

    # ── Credential Retrieval ──────────────────────────────────────────────

    def get_credentials(
        self, provider: str, profile_name: str = "default"
    ) -> dict[str, Any] | None:
        """Get credentials for a provider."""
        key = f"{provider}:{profile_name}"
        profile = self._profiles.get(key)
        if profile is None or not profile.is_valid:
            return None
        return profile.data

    def get_api_key(
        self, provider: str, profile_name: str = "default"
    ) -> str | None:
        """Get the API key (or access token) for a provider."""
        creds = self.get_credentials(provider, profile_name)
        if creds:
            return creds.get("api_key") or creds.get("access_token")
        return None

    # ── Profile Management ────────────────────────────────────────────────

    def list_profiles(self) -> list[dict[str, Any]]:
        """List all auth profiles (without secrets)."""
        return [
            {"key": key, **profile.to_dict()}
            for key, profile in self._profiles.items()
        ]

    def remove_profile(
        self, provider: str, profile_name: str = "default"
    ) -> bool:
        """Remove an auth profile."""
        key = f"{provider}:{profile_name}"
        if key in self._profiles:
            del self._profiles[key]
            self._save()
            return True
        return False

    def has_profile(
        self, provider: str, profile_name: str = "default"
    ) -> bool:
        """Check if a profile exists."""
        return f"{provider}:{profile_name}" in self._profiles
