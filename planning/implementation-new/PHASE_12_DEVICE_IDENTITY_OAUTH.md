# Phase 12 — Device Pairing, Identity & OAuth

> **Goal:** Add device-level identity management, multi-device pairing, and OAuth provider authentication — enabling secure remote access and multi-user setups.  
> **Duration:** 10-14 days  
> **Risk Level:** High (security-critical, auth infrastructure)  
> **Depends On:** Phase 6 (security hardening), Phase 0 (config schema)

---

## Why This Phase Exists

OpenClaw has a complete identity & device management system:
- `identity/device.json` — unique device identity with auth credentials
- `devices/paired.json` — multi-device pairing registry
- `auth.profiles` — multiple OAuth providers (`openai-codex:default`, `google:default`)
- Exec approval system with socket-based tokens
- Gateway token authentication

PawBot has **basic JWT auth on the dashboard** but no device identity, no pairing, no OAuth providers, no exec approvals.

---

## 12.1 — Device Identity System

**Create:** `pawbot/identity/device.py`

```python
"""Device identity — unique identification for each PawBot installation."""

from __future__ import annotations

import json
import platform
import secrets
import socket
import uuid
from pathlib import Path
from typing import Any

from loguru import logger


IDENTITY_DIR = Path.home() / ".pawbot" / "identity"
DEVICE_FILE = IDENTITY_DIR / "device.json"


class DeviceIdentity:
    """Manages the unique identity of this PawBot installation."""

    def __init__(self):
        IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
        self._data = self._load_or_create()

    def _load_or_create(self) -> dict[str, Any]:
        """Load existing identity or create a new one."""
        if DEVICE_FILE.exists():
            try:
                data = json.loads(DEVICE_FILE.read_text())
                logger.debug("Device identity loaded: {}", data.get("device_id", "?")[:8])
                return data
            except Exception:
                logger.warning("Corrupt device identity, regenerating")

        data = self._generate()
        DEVICE_FILE.write_text(json.dumps(data, indent=2))
        logger.info("New device identity created: {}", data["device_id"][:8])
        return data

    def _generate(self) -> dict[str, Any]:
        """Generate a new device identity."""
        return {
            "version": 1,
            "device_id": str(uuid.uuid4()),
            "display_name": socket.gethostname(),
            "created_at": __import__("time").time(),
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "auth": {
                "device_secret": secrets.token_urlsafe(32),
                "api_token": secrets.token_urlsafe(48),
            },
        }

    @property
    def device_id(self) -> str:
        return self._data["device_id"]

    @property
    def display_name(self) -> str:
        return self._data.get("display_name", "unknown")

    @property
    def api_token(self) -> str:
        return self._data.get("auth", {}).get("api_token", "")

    @property
    def device_secret(self) -> str:
        return self._data.get("auth", {}).get("device_secret", "")

    def to_public(self) -> dict[str, Any]:
        """Return public info (no secrets)."""
        return {
            "device_id": self.device_id,
            "display_name": self.display_name,
            "platform": self._data.get("platform", {}),
        }

    def rotate_token(self) -> str:
        """Generate a new API token (invalidates old one)."""
        new_token = secrets.token_urlsafe(48)
        self._data["auth"]["api_token"] = new_token
        DEVICE_FILE.write_text(json.dumps(self._data, indent=2))
        logger.info("API token rotated")
        return new_token
```

---

## 12.2 — Device Pairing System

**Create:** `pawbot/identity/pairing.py`

```python
"""Device pairing — manage trusted devices that can connect to this PawBot instance."""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from loguru import logger


DEVICES_DIR = Path.home() / ".pawbot" / "devices"
PAIRED_FILE = DEVICES_DIR / "paired.json"
PENDING_FILE = DEVICES_DIR / "pending.json"


class PairingManager:
    """Manage device pairing for remote access."""

    PAIRING_CODE_EXPIRY_SECONDS = 300  # 5 minutes

    def __init__(self):
        DEVICES_DIR.mkdir(parents=True, exist_ok=True)
        self._paired = self._load_paired()
        self._pending = self._load_pending()

    def _load_paired(self) -> dict[str, Any]:
        if PAIRED_FILE.exists():
            try:
                return json.loads(PAIRED_FILE.read_text())
            except Exception:
                pass
        return {"version": 1, "devices": []}

    def _load_pending(self) -> dict[str, Any]:
        if PENDING_FILE.exists():
            try:
                return json.loads(PENDING_FILE.read_text())
            except Exception:
                pass
        return {"version": 1, "requests": []}

    def _save_paired(self) -> None:
        PAIRED_FILE.write_text(json.dumps(self._paired, indent=2))

    def _save_pending(self) -> None:
        PENDING_FILE.write_text(json.dumps(self._pending, indent=2))

    def generate_pairing_code(self) -> dict[str, str]:
        """Generate a 6-digit pairing code for a new device."""
        code = secrets.randbelow(900000) + 100000  # 6-digit code
        code_str = str(code)
        token = secrets.token_urlsafe(32)

        self._pending["requests"].append({
            "code": code_str,
            "token": token,
            "created_at": time.time(),
            "expires_at": time.time() + self.PAIRING_CODE_EXPIRY_SECONDS,
        })
        self._save_pending()

        logger.info("Pairing code generated: {}", code_str)
        return {"code": code_str, "token": token, "expires_in": self.PAIRING_CODE_EXPIRY_SECONDS}

    def complete_pairing(
        self,
        code: str,
        device_info: dict[str, Any],
        user_id: str = "",
    ) -> dict[str, Any] | None:
        """Complete a pairing request with a valid code.
        
        Returns:
            Pairing result with access token, or None if code is invalid/expired.
        """
        # Find matching pending request
        self._cleanup_expired()
        
        matching = None
        for req in self._pending["requests"]:
            if req["code"] == code:
                matching = req
                break

        if not matching:
            logger.warning("Invalid pairing code: {}", code)
            return None

        # Remove from pending
        self._pending["requests"].remove(matching)
        self._save_pending()

        # Generate access credentials
        access_token = secrets.token_urlsafe(48)
        
        paired_device = {
            "device_id": device_info.get("device_id", ""),
            "display_name": device_info.get("display_name", "Unknown Device"),
            "user_id": user_id,
            "paired_at": time.time(),
            "access_token_hash": self._hash_token(access_token),
            "last_seen": time.time(),
            "platform": device_info.get("platform", {}),
        }

        self._paired["devices"].append(paired_device)
        self._save_paired()

        logger.info(
            "Device paired: {} ({})",
            paired_device["display_name"],
            paired_device["device_id"][:8],
        )

        return {
            "success": True,
            "access_token": access_token,
            "device_id": paired_device["device_id"],
        }

    def verify_device(self, access_token: str) -> dict[str, Any] | None:
        """Verify an access token and return the paired device info."""
        token_hash = self._hash_token(access_token)
        for device in self._paired["devices"]:
            if device.get("access_token_hash") == token_hash:
                device["last_seen"] = time.time()
                self._save_paired()
                return device
        return None

    def revoke_device(self, device_id: str) -> bool:
        """Revoke a paired device's access."""
        before = len(self._paired["devices"])
        self._paired["devices"] = [
            d for d in self._paired["devices"] if d.get("device_id") != device_id
        ]
        if len(self._paired["devices"]) < before:
            self._save_paired()
            logger.info("Device revoked: {}", device_id[:8])
            return True
        return False

    def list_paired(self) -> list[dict[str, Any]]:
        """List all paired devices (without secrets)."""
        return [
            {
                "device_id": d["device_id"],
                "display_name": d["display_name"],
                "user_id": d.get("user_id", ""),
                "paired_at": d["paired_at"],
                "last_seen": d.get("last_seen", 0),
                "platform": d.get("platform", {}),
            }
            for d in self._paired["devices"]
        ]

    def _cleanup_expired(self) -> None:
        """Remove expired pairing requests."""
        now = time.time()
        before = len(self._pending["requests"])
        self._pending["requests"] = [
            r for r in self._pending["requests"] if r.get("expires_at", 0) > now
        ]
        if len(self._pending["requests"]) < before:
            self._save_pending()

    @staticmethod
    def _hash_token(token: str) -> str:
        import hashlib
        return hashlib.sha256(token.encode()).hexdigest()
```

---

## 12.3 — OAuth Provider Authentication

**Create:** `pawbot/identity/oauth.py`

```python
"""OAuth provider authentication — manage multiple auth profiles."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger


AUTH_FILE = Path.home() / ".pawbot" / "identity" / "auth_profiles.json"


class OAuthProfile:
    """A single OAuth authentication profile."""

    def __init__(self, provider: str, mode: str, data: dict[str, Any]):
        self.provider = provider         # e.g. "openai", "google", "anthropic"
        self.mode = mode                  # "oauth", "api_key", "service_account"
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
        return {
            "provider": self.provider,
            "mode": self.mode,
            "valid": self.is_valid,
            "expired": self.is_expired,
        }


class AuthManager:
    """Manage multiple OAuth/API key profiles for different providers."""

    SUPPORTED_PROVIDERS = {
        "openai": {"modes": ["api_key", "oauth"], "key_prefix": "sk-"},
        "anthropic": {"modes": ["api_key"], "key_prefix": "sk-ant-"},
        "google": {"modes": ["api_key", "oauth", "service_account"], "key_prefix": "AI"},
        "openrouter": {"modes": ["api_key"], "key_prefix": "sk-or-"},
        "deepseek": {"modes": ["api_key"], "key_prefix": ""},
        "ollama": {"modes": ["none"], "key_prefix": ""},
    }

    def __init__(self):
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._profiles: dict[str, OAuthProfile] = {}
        self._load()

    def _load(self) -> None:
        """Load auth profiles from disk."""
        if not AUTH_FILE.exists():
            return
        try:
            data = json.loads(AUTH_FILE.read_text())
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
        AUTH_FILE.write_text(json.dumps(data, indent=2))

    def set_api_key(self, provider: str, api_key: str, profile_name: str = "default") -> None:
        """Set an API key for a provider."""
        key = f"{provider}:{profile_name}"
        
        # Validate key format
        expected_prefix = self.SUPPORTED_PROVIDERS.get(provider, {}).get("key_prefix", "")
        if expected_prefix and not api_key.startswith(expected_prefix):
            logger.warning(
                "API key for '{}' doesn't start with expected prefix '{}' — saving anyway",
                provider, expected_prefix,
            )

        self._profiles[key] = OAuthProfile(
            provider=provider,
            mode="api_key",
            data={"api_key": api_key, "set_at": time.time()},
        )
        self._save()
        logger.info("API key set for {}:{}", provider, profile_name)

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

    def get_credentials(self, provider: str, profile_name: str = "default") -> dict[str, Any] | None:
        """Get credentials for a provider."""
        key = f"{provider}:{profile_name}"
        profile = self._profiles.get(key)
        if profile is None:
            return None
        if not profile.is_valid:
            return None
        return profile.data

    def get_api_key(self, provider: str, profile_name: str = "default") -> str | None:
        """Get the API key for a provider."""
        creds = self.get_credentials(provider, profile_name)
        if creds:
            return creds.get("api_key") or creds.get("access_token")
        return None

    def list_profiles(self) -> list[dict[str, Any]]:
        """List all auth profiles (without secrets)."""
        return [
            {"key": key, **profile.to_dict()}
            for key, profile in self._profiles.items()
        ]

    def remove_profile(self, provider: str, profile_name: str = "default") -> bool:
        """Remove an auth profile."""
        key = f"{provider}:{profile_name}"
        if key in self._profiles:
            del self._profiles[key]
            self._save()
            return True
        return False
```

---

## 12.4 — Exec Approval System

**Create:** `pawbot/identity/exec_approvals.py`

```python
"""Exec approval system — require explicit approval for dangerous tool executions."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Any

from loguru import logger


APPROVALS_FILE = Path.home() / ".pawbot" / "exec-approvals.json"


class ExecApprovalPolicy:
    """Policy for when tool executions require human approval."""
    NEVER = "never"          # Never require approval
    HIGH_RISK = "high_risk"  # Only for high/critical risk tools
    ALWAYS = "always"        # Always require approval
    AUTO_SAFE = "auto_safe"  # Auto-approve safe tools, ask for others


class ExecApprovalManager:
    """Manages approval requests for tool executions."""

    def __init__(self, policy: str = ExecApprovalPolicy.HIGH_RISK):
        self.policy = policy
        self._pending: dict[str, dict[str, Any]] = {}
        self._approved_patterns: set[str] = set()
        self._load_config()

    def _load_config(self) -> None:
        """Load approval configuration."""
        if APPROVALS_FILE.exists():
            try:
                data = json.loads(APPROVALS_FILE.read_text())
                # Load pre-approved patterns
                defaults = data.get("defaults", {})
                for pattern, action in defaults.items():
                    if action == "approve":
                        self._approved_patterns.add(pattern)
            except Exception:
                pass

    def needs_approval(self, tool_name: str, risk_level: str) -> bool:
        """Check if a tool execution needs human approval."""
        if self.policy == ExecApprovalPolicy.NEVER:
            return False
        if self.policy == ExecApprovalPolicy.ALWAYS:
            return tool_name not in self._approved_patterns
        if self.policy == ExecApprovalPolicy.HIGH_RISK:
            return risk_level in ("high", "critical")
        if self.policy == ExecApprovalPolicy.AUTO_SAFE:
            return risk_level not in ("low",)
        return False

    def request_approval(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        agent_id: str = "main",
    ) -> str:
        """Create an approval request. Returns request_id."""
        request_id = secrets.token_hex(16)
        self._pending[request_id] = {
            "tool_name": tool_name,
            "arguments": arguments,
            "agent_id": agent_id,
            "created_at": time.time(),
            "status": "pending",
        }
        logger.info(
            "Approval requested for tool '{}' (request: {})",
            tool_name, request_id[:8],
        )
        return request_id

    def approve(self, request_id: str, remember: bool = False) -> bool:
        """Approve a pending request."""
        if request_id not in self._pending:
            return False
        self._pending[request_id]["status"] = "approved"
        
        if remember:
            tool_name = self._pending[request_id]["tool_name"]
            self._approved_patterns.add(tool_name)
            self._save_config()
        
        return True

    def deny(self, request_id: str) -> bool:
        """Deny a pending request."""
        if request_id not in self._pending:
            return False
        self._pending[request_id]["status"] = "denied"
        return True

    def get_status(self, request_id: str) -> str:
        """Get status of an approval request."""
        req = self._pending.get(request_id)
        if not req:
            return "not_found"
        return req["status"]

    async def wait_for_approval(
        self, request_id: str, timeout: float = 300.0
    ) -> bool:
        """Wait for a pending approval. Returns True if approved, False if denied/timeout."""
        start = time.time()
        while time.time() - start < timeout:
            status = self.get_status(request_id)
            if status == "approved":
                return True
            if status == "denied":
                return False
            await asyncio.sleep(1)
        
        # Timeout = deny
        self.deny(request_id)
        return False

    def list_pending(self) -> list[dict[str, Any]]:
        """List all pending approvals."""
        return [
            {"request_id": rid, **req}
            for rid, req in self._pending.items()
            if req["status"] == "pending"
        ]

    def _save_config(self) -> None:
        """Save approved patterns."""
        data = {
            "version": 1,
            "defaults": {p: "approve" for p in self._approved_patterns},
            "agents": {},
        }
        APPROVALS_FILE.write_text(json.dumps(data, indent=2))
```

---

## 12.5 — Identity API Endpoints

**Add to:** `pawbot/dashboard/server.py`

```python
# Device identity
@app.get("/api/identity")
def get_identity():
    """Get this device's identity."""
    from pawbot.identity.device import DeviceIdentity
    return DeviceIdentity().to_public()


# Device pairing
@app.post("/api/devices/pair/generate")
def generate_pairing_code():
    """Generate a pairing code for a new device."""
    from pawbot.identity.pairing import PairingManager
    return PairingManager().generate_pairing_code()


@app.post("/api/devices/pair/complete")
def complete_pairing(body: dict):
    """Complete a pairing request with a valid code."""
    from pawbot.identity.pairing import PairingManager
    result = PairingManager().complete_pairing(
        code=body.get("code", ""),
        device_info=body.get("device_info", {}),
        user_id=body.get("user_id", ""),
    )
    if result:
        return result
    return JSONResponse(status_code=400, content={"error": "Invalid or expired pairing code"})


@app.get("/api/devices")
def list_devices():
    """List all paired devices."""
    from pawbot.identity.pairing import PairingManager
    return {"devices": PairingManager().list_paired()}


@app.delete("/api/devices/{device_id}")
def revoke_device(device_id: str):
    """Revoke a paired device."""
    from pawbot.identity.pairing import PairingManager
    if PairingManager().revoke_device(device_id):
        return {"success": True}
    return JSONResponse(status_code=404, content={"error": "Device not found"})


# Auth profiles
@app.get("/api/auth/profiles")
def list_auth_profiles():
    """List all authentication profiles."""
    from pawbot.identity.oauth import AuthManager
    return {"profiles": AuthManager().list_profiles()}


@app.post("/api/auth/api-key")
def set_api_key(body: dict):
    """Set an API key for a provider."""
    from pawbot.identity.oauth import AuthManager
    mgr = AuthManager()
    mgr.set_api_key(
        provider=body["provider"],
        api_key=body["api_key"],
        profile_name=body.get("profile", "default"),
    )
    return {"success": True}


# Exec approvals
@app.get("/api/approvals/pending")
def list_pending_approvals():
    """List all pending exec approvals."""
    from pawbot.identity.exec_approvals import ExecApprovalManager
    return {"pending": ExecApprovalManager().list_pending()}


@app.post("/api/approvals/{request_id}/approve")
def approve_exec(request_id: str, body: dict = {}):
    """Approve a pending execution."""
    from pawbot.identity.exec_approvals import ExecApprovalManager
    mgr = ExecApprovalManager()
    if mgr.approve(request_id, remember=body.get("remember", False)):
        return {"success": True}
    return JSONResponse(status_code=404, content={"error": "Request not found"})


@app.post("/api/approvals/{request_id}/deny")
def deny_exec(request_id: str):
    """Deny a pending execution."""
    from pawbot.identity.exec_approvals import ExecApprovalManager
    mgr = ExecApprovalManager()
    if mgr.deny(request_id):
        return {"success": True}
    return JSONResponse(status_code=404, content={"error": "Request not found"})
```

---

## Verification Checklist — Phase 12 Complete

- [ ] `~/.pawbot/identity/device.json` auto-created with UUID, hostname, platform info
- [ ] `DeviceIdentity.rotate_token()` generates new API token
- [ ] `PairingManager.generate_pairing_code()` returns 6-digit code
- [ ] Pairing code expires after 5 minutes
- [ ] `PairingManager.complete_pairing()` produces access token
- [ ] `PairingManager.verify_device()` validates access tokens
- [ ] `AuthManager.set_api_key()` stores encrypted API keys per provider
- [ ] `AuthManager.list_profiles()` returns all profiles without secrets
- [ ] `ExecApprovalManager.needs_approval()` checks risk level against policy
- [ ] Approval requests can be approved/denied via API
- [ ] `wait_for_approval()` blocks with 5-minute timeout
- [ ] All API endpoints functional and return correct status codes
- [ ] All tests pass: `pytest tests/ -v --tb=short`
