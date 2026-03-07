"""Device identity — unique identification for each PawBot installation (Phase 12.1).

Each PawBot installation gets a persistent device identity stored at
~/.pawbot/identity/device.json. This includes a UUID, hostname,
platform info, and authentication credentials (device secret + API token).
"""

from __future__ import annotations

import json
import platform
import secrets
import socket
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger


IDENTITY_DIR = Path.home() / ".pawbot" / "identity"
DEVICE_FILE = IDENTITY_DIR / "device.json"


class DeviceIdentity:
    """Manages the unique identity of this PawBot installation.

    Auto-creates ~/.pawbot/identity/device.json on first use with:
      - Unique device_id (UUID4)
      - Display name (hostname)
      - Platform info (OS, architecture, Python version)
      - Auth credentials (device_secret, api_token)
    """

    def __init__(self, identity_dir: Path | None = None):
        self._dir = identity_dir or IDENTITY_DIR
        self._file = self._dir / "device.json"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._data = self._load_or_create()

    def _load_or_create(self) -> dict[str, Any]:
        """Load existing identity or create a new one."""
        if self._file.exists():
            try:
                data = json.loads(self._file.read_text(encoding="utf-8"))
                logger.debug(
                    "Device identity loaded: {}", data.get("device_id", "?")[:8]
                )
                return data
            except Exception:
                logger.warning("Corrupt device identity, regenerating")

        data = self._generate()
        self._file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("New device identity created: {}", data["device_id"][:8])
        return data

    def _generate(self) -> dict[str, Any]:
        """Generate a new device identity."""
        return {
            "version": 1,
            "device_id": str(uuid.uuid4()),
            "display_name": socket.gethostname(),
            "created_at": time.time(),
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
        """Unique device UUID."""
        return self._data["device_id"]

    @property
    def display_name(self) -> str:
        """Human-readable device name (hostname)."""
        return self._data.get("display_name", "unknown")

    @property
    def api_token(self) -> str:
        """The API token for this device."""
        return self._data.get("auth", {}).get("api_token", "")

    @property
    def device_secret(self) -> str:
        """The device secret (used for signing)."""
        return self._data.get("auth", {}).get("device_secret", "")

    @property
    def platform_info(self) -> dict[str, str]:
        """Platform information dict."""
        return self._data.get("platform", {})

    @property
    def created_at(self) -> float:
        """Unix timestamp when device identity was created."""
        return self._data.get("created_at", 0)

    def to_public(self) -> dict[str, Any]:
        """Return public info (no secrets)."""
        return {
            "device_id": self.device_id,
            "display_name": self.display_name,
            "platform": self.platform_info,
            "created_at": self.created_at,
        }

    def rotate_token(self) -> str:
        """Generate a new API token (invalidates old one)."""
        new_token = secrets.token_urlsafe(48)
        self._data.setdefault("auth", {})["api_token"] = new_token
        self._file.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("API token rotated")
        return new_token

    def rotate_secret(self) -> str:
        """Generate a new device secret (invalidates old one)."""
        new_secret = secrets.token_urlsafe(32)
        self._data.setdefault("auth", {})["device_secret"] = new_secret
        self._file.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Device secret rotated")
        return new_secret
