"""KeypairManager — Ed25519 key generation, signing, and verification.

Phase 19: Provides cryptographic identity for each Pawbot device.

Each device generates an Ed25519 keypair on first run:
  - Private key: stored in ~/.pawbot/identity/device.json (encrypted)
  - Public key: shared during pairing
  - Device ID: SHA-256 hash of public key (unique identifier)

Ed25519 is chosen for:
  - Fast signing/verification
  - Small key sizes (32 bytes)
  - Constant-time operations (timing-attack resistant)
  - Same algorithm used by OpenClaw
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("pawbot.auth.keypair")

# Use Python's built-in cryptography (available since 3.6)
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


def _ensure_cryptography() -> None:
    """Raise if the cryptography package is not installed."""
    if not HAS_CRYPTOGRAPHY:
        raise ImportError(
            "The 'cryptography' package is required for device authentication. "
            "Install it with: pip install cryptography"
        )


class KeypairManager:
    """Manages Ed25519 keypairs for device identity.

    Storage location: ~/.pawbot/identity/device.json

    device.json format:
    {
        "device_id": "sha256-hex-of-public-key",
        "public_key_pem": "-----BEGIN PUBLIC KEY-----...",
        "private_key_pem": "-----BEGIN PRIVATE KEY-----...",
        "created_at": 1709510400.0,
        "platform": "windows",
        "hostname": "DESKTOP-ABC123"
    }
    """

    DEFAULT_PATH = Path.home() / ".pawbot" / "identity" / "device.json"

    def __init__(self, identity_path: Path | None = None) -> None:
        self.identity_path = identity_path or self.DEFAULT_PATH
        self._private_key: Ed25519PrivateKey | None = None
        self._public_key: Ed25519PublicKey | None = None
        self._device_id: str = ""
        self._loaded = False

    # ── Key Generation ───────────────────────────────────────────────────────

    def generate(self) -> dict[str, str]:
        """Generate a new Ed25519 keypair and save to disk.

        Returns device identity dict.
        Raises FileExistsError if device.json already exists (use load() instead).
        """
        _ensure_cryptography()

        if self.identity_path.exists():
            raise FileExistsError(
                f"Device identity already exists at {self.identity_path}. "
                "Use load() to read existing keys, or delete to regenerate."
            )

        # Generate keypair
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        # Serialise
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

        # Compute device ID (SHA-256 of public key bytes)
        public_raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        device_id = hashlib.sha256(public_raw).hexdigest()

        import platform
        identity = {
            "device_id": device_id,
            "public_key_pem": public_pem,
            "private_key_pem": private_pem,
            "created_at": time.time(),
            "platform": platform.system().lower(),
            "hostname": platform.node(),
        }

        # Save to disk with restricted permissions
        self.identity_path.parent.mkdir(parents=True, exist_ok=True)
        self.identity_path.write_text(
            json.dumps(identity, indent=2), encoding="utf-8"
        )

        # Restrict file permissions (owner-only read/write)
        try:
            os.chmod(self.identity_path, 0o600)
        except OSError:
            pass  # Windows may not support unix permissions

        self._private_key = private_key
        self._public_key = public_key
        self._device_id = device_id
        self._loaded = True

        logger.info("Generated new device identity: %s", device_id[:16])
        return identity

    # ── Key Loading ──────────────────────────────────────────────────────────

    def load(self) -> dict[str, Any]:
        """Load existing device identity from disk.

        Returns the identity dict.
        Raises FileNotFoundError if no identity exists.
        """
        _ensure_cryptography()

        if not self.identity_path.exists():
            raise FileNotFoundError(
                f"No device identity at {self.identity_path}. "
                "Run 'pawbot auth init' to generate one."
            )

        data = json.loads(self.identity_path.read_text(encoding="utf-8"))

        # Deserialise keys
        self._private_key = serialization.load_pem_private_key(
            data["private_key_pem"].encode("utf-8"),
            password=None,
        )
        self._public_key = self._private_key.public_key()
        self._device_id = data["device_id"]
        self._loaded = True

        logger.debug("Loaded device identity: %s", self._device_id[:16])
        return data

    def load_or_generate(self) -> dict[str, Any]:
        """Load existing identity or generate a new one."""
        try:
            return self.load()
        except FileNotFoundError:
            return self.generate()

    # ── Signing & Verification ───────────────────────────────────────────────

    def sign(self, payload: bytes) -> bytes:
        """Sign a payload with the device's private key.

        Returns the Ed25519 signature (64 bytes).
        """
        if not self._loaded or self._private_key is None:
            self.load()
        return self._private_key.sign(payload)

    def sign_b64(self, payload: str) -> str:
        """Sign a string payload and return base64-encoded signature."""
        sig = self.sign(payload.encode("utf-8"))
        return base64.b64encode(sig).decode("ascii")

    def verify(
        self, payload: bytes, signature: bytes, public_key_pem: str | None = None
    ) -> bool:
        """Verify a signature against a payload.

        If public_key_pem is provided, uses that key; otherwise uses own public key.
        Returns True if valid, False otherwise.
        """
        _ensure_cryptography()

        try:
            if public_key_pem:
                pub_key = serialization.load_pem_public_key(
                    public_key_pem.encode("utf-8")
                )
            else:
                if not self._loaded:
                    self.load()
                pub_key = self._public_key

            pub_key.verify(signature, payload)
            return True
        except Exception:
            return False

    def verify_b64(
        self, payload: str, signature_b64: str, public_key_pem: str | None = None
    ) -> bool:
        """Verify a base64-encoded signature against a string payload."""
        try:
            sig = base64.b64decode(signature_b64)
            return self.verify(payload.encode("utf-8"), sig, public_key_pem)
        except Exception:
            return False

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def device_id(self) -> str:
        """SHA-256 hash of the public key — unique device identifier."""
        if not self._loaded:
            self.load()
        return self._device_id

    @property
    def public_key_pem(self) -> str:
        """PEM-encoded public key."""
        if not self._loaded:
            self.load()
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    @property
    def is_initialised(self) -> bool:
        """True if a device identity exists on disk."""
        return self.identity_path.exists()

    def delete(self) -> None:
        """Delete the device identity (irreversible!)."""
        if self.identity_path.exists():
            self.identity_path.unlink()
            self._loaded = False
            self._private_key = None
            self._public_key = None
            self._device_id = ""
            logger.warning("Device identity deleted")

    def __repr__(self) -> str:
        if self._loaded:
            return f"KeypairManager(device={self._device_id[:16]}...)"
        return f"KeypairManager(path={self.identity_path})"
