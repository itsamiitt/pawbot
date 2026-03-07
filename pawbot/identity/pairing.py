"""Device pairing — 6-digit code pairing for trusted devices (Phase 12.2).

Flow:
  1. Owner generates a 6-digit pairing code on the host device
  2. New device submits the code with its device info
  3. Host verifies the code and issues an access token
  4. New device uses the access token for subsequent API calls

Pairing codes expire after 5 minutes. Access tokens are stored as
SHA-256 hashes so the raw token is never persisted on disk.
"""

from __future__ import annotations

import hashlib
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
    """Manage device pairing with 6-digit codes."""

    PAIRING_CODE_EXPIRY_SECONDS = 300  # 5 minutes

    def __init__(self, devices_dir: Path | None = None):
        self._dir = devices_dir or DEVICES_DIR
        self._paired_file = self._dir / "paired.json"
        self._pending_file = self._dir / "pending.json"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._paired = self._load_file(self._paired_file, {"version": 1, "devices": []})
        self._pending = self._load_file(self._pending_file, {"version": 1, "requests": []})

    @staticmethod
    def _load_file(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return dict(default)

    def _save_paired(self) -> None:
        self._paired_file.write_text(
            json.dumps(self._paired, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _save_pending(self) -> None:
        self._pending_file.write_text(
            json.dumps(self._pending, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ── Generate Pairing Code ─────────────────────────────────────────────

    def generate_pairing_code(self) -> dict[str, Any]:
        """Generate a 6-digit pairing code for a new device.

        Returns:
            {"code": "123456", "token": "...", "expires_in": 300}
        """
        code = secrets.randbelow(900000) + 100000  # Always 6 digits
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
        return {
            "code": code_str,
            "token": token,
            "expires_in": self.PAIRING_CODE_EXPIRY_SECONDS,
        }

    # ── Complete Pairing ──────────────────────────────────────────────────

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
        self._cleanup_expired()

        # Find matching pending request
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
            paired_device["device_id"][:8] if paired_device["device_id"] else "?",
        )

        return {
            "success": True,
            "access_token": access_token,
            "device_id": paired_device["device_id"],
        }

    # ── Verify & Manage ───────────────────────────────────────────────────

    def verify_device(self, access_token: str) -> dict[str, Any] | None:
        """Verify an access token and return paired device info."""
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
            logger.info("Device revoked: {}", device_id[:8] if device_id else "?")
            return True
        return False

    def list_paired(self) -> list[dict[str, Any]]:
        """List all paired devices (without secrets)."""
        return [
            {
                "device_id": d.get("device_id", ""),
                "display_name": d.get("display_name", ""),
                "user_id": d.get("user_id", ""),
                "paired_at": d.get("paired_at", 0),
                "last_seen": d.get("last_seen", 0),
                "platform": d.get("platform", {}),
            }
            for d in self._paired["devices"]
        ]

    def pending_count(self) -> int:
        """Number of pending pairing requests."""
        self._cleanup_expired()
        return len(self._pending["requests"])

    # ── Internal ──────────────────────────────────────────────────────────

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
        """SHA-256 hash an access token for safe storage."""
        return hashlib.sha256(token.encode()).hexdigest()
