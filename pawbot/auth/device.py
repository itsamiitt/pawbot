"""DeviceRegistry — device pairing, approval, and revocation.

Phase 19: Manages paired devices with role-based access control.

Flow:
  1. New device calls request_pairing(device_id, public_key, platform, role)
  2. Owner runs 'pawbot auth approve <device_id>'
  3. Approved device receives scoped tokens and can access the system
  4. Owner can revoke at any time with 'pawbot auth revoke <device_id>'

Storage:
  ~/.pawbot/devices/paired.json    — Approved devices
  ~/.pawbot/devices/pending.json   — Awaiting approval
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from pawbot.auth.roles import ROLES, Role, get_role

logger = logging.getLogger("pawbot.auth.device")


class DeviceRegistry:
    """Manages device pairing and registration."""

    def __init__(self, devices_dir: Path | None = None) -> None:
        self.devices_dir = devices_dir or Path.home() / ".pawbot" / "devices"
        self.devices_dir.mkdir(parents=True, exist_ok=True)
        self._paired_path = self.devices_dir / "paired.json"
        self._pending_path = self.devices_dir / "pending.json"
        self._paired: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._load()

    # ── Pairing Flow ─────────────────────────────────────────────────────────

    def request_pairing(
        self,
        device_id: str,
        public_key_pem: str,
        platform: str = "unknown",
        client_id: str = "",
        role: str = "member",
        label: str = "",
    ) -> str:
        """Submit a pairing request. Returns a pairing request ID.

        The request is stored in pending.json until approved or rejected.
        """
        if device_id in self._paired:
            raise ValueError(f"Device {device_id[:16]} is already paired")
        if device_id in self._pending:
            raise ValueError(f"Device {device_id[:16]} already has a pending request")

        # Validate role
        if role not in ROLES:
            raise ValueError(f"Unknown role: {role}. Available: {list(ROLES.keys())}")

        request_id = str(uuid.uuid4())[:12]
        self._pending[device_id] = {
            "request_id": request_id,
            "device_id": device_id,
            "public_key_pem": public_key_pem,
            "platform": platform,
            "client_id": client_id or f"client-{device_id[:8]}",
            "role": role,
            "label": label or f"{platform}-{device_id[:8]}",
            "requested_at": time.time(),
            "status": "pending",
        }

        self._save_pending()
        logger.info(
            "Pairing request from %s (%s, role=%s)",
            device_id[:16], platform, role,
        )
        return request_id

    def approve(self, device_id: str) -> dict[str, Any]:
        """Approve a pending pairing request.

        Moves the device from pending to paired.
        Returns the paired device record.
        """
        pending = self._pending.pop(device_id, None)
        if not pending:
            # Try by shortened ID
            for did, req in list(self._pending.items()):
                if did.startswith(device_id):
                    pending = self._pending.pop(did)
                    device_id = did
                    break

        if not pending:
            raise ValueError(f"No pending request for device {device_id[:16]}")

        role_obj = get_role(pending["role"])
        paired_record = {
            "device_id": device_id,
            "public_key_pem": pending["public_key_pem"],
            "platform": pending["platform"],
            "client_id": pending["client_id"],
            "role": pending["role"],
            "scopes": role_obj.scopes if role_obj else ["read"],
            "label": pending["label"],
            "paired_at": time.time(),
            "last_seen": time.time(),
            "status": "active",
            "tokens": [],
        }

        self._paired[device_id] = paired_record
        self._save_paired()
        self._save_pending()

        logger.info(
            "Approved device %s (%s, role=%s)",
            device_id[:16], pending["platform"], pending["role"],
        )
        return paired_record

    def reject(self, device_id: str) -> bool:
        """Reject a pending pairing request."""
        removed = self._pending.pop(device_id, None)
        if not removed:
            for did in list(self._pending.keys()):
                if did.startswith(device_id):
                    removed = self._pending.pop(did)
                    break

        if removed:
            self._save_pending()
            logger.info("Rejected pairing request for %s", device_id[:16])
            return True
        return False

    def revoke(self, device_id: str) -> bool:
        """Revoke a paired device — immediately disables all access."""
        removed = self._paired.pop(device_id, None)
        if not removed:
            for did in list(self._paired.keys()):
                if did.startswith(device_id):
                    removed = self._paired.pop(did)
                    break

        if removed:
            self._save_paired()
            logger.warning("Revoked device %s", device_id[:16])
            return True
        return False

    # ── Queries ──────────────────────────────────────────────────────────────

    def is_paired(self, device_id: str) -> bool:
        """Check if a device is paired and active."""
        record = self._paired.get(device_id)
        return record is not None and record.get("status") == "active"

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        """Get a paired device record."""
        return self._paired.get(device_id)

    def get_device_role(self, device_id: str) -> Role | None:
        """Get the role object for a paired device."""
        record = self._paired.get(device_id)
        if not record:
            return None
        return get_role(record.get("role", "viewer"))

    def list_paired(self) -> list[dict[str, Any]]:
        """List all paired devices (redacted — no private keys)."""
        devices = []
        for device_id, record in self._paired.items():
            safe = {k: v for k, v in record.items() if k != "public_key_pem"}
            safe["device_id_short"] = device_id[:16]
            devices.append(safe)
        return devices

    def list_pending(self) -> list[dict[str, Any]]:
        """List all pending pairing requests."""
        return [
            {k: v for k, v in req.items() if k != "public_key_pem"}
            for req in self._pending.values()
        ]

    @property
    def paired_count(self) -> int:
        return len(self._paired)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def update_last_seen(self, device_id: str) -> None:
        """Update the last_seen timestamp for a device."""
        record = self._paired.get(device_id)
        if record:
            record["last_seen"] = time.time()
            self._save_paired()

    def has_scope(self, device_id: str, scope: str) -> bool:
        """Check if a paired device has a specific scope."""
        role = self.get_device_role(device_id)
        if not role:
            return False
        return role.has_scope(scope)

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load paired and pending devices from disk."""
        if self._paired_path.exists():
            try:
                self._paired = json.loads(
                    self._paired_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load paired.json: %s", exc)
                self._paired = {}

        if self._pending_path.exists():
            try:
                self._pending = json.loads(
                    self._pending_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load pending.json: %s", exc)
                self._pending = {}

    def _save_paired(self) -> None:
        """Atomically save paired devices."""
        content = json.dumps(self._paired, indent=2, ensure_ascii=False)
        tmp = self._paired_path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(self._paired_path)

    def _save_pending(self) -> None:
        """Atomically save pending requests."""
        content = json.dumps(self._pending, indent=2, ensure_ascii=False)
        tmp = self._pending_path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(self._pending_path)

    def __repr__(self) -> str:
        return (
            f"DeviceRegistry(paired={self.paired_count}, pending={self.pending_count})"
        )
