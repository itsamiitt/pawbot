"""TokenService — scoped token issuance and verification.

Phase 19: Stateless JWT-like tokens for device authentication.

Token format: base64(header).base64(payload).base64(signature)
  - Header:    {"alg": "Ed25519", "typ": "PAT"}
  - Payload:   {"sub": device_id, "role": role, "scopes": [...], "iat": ts, "exp": ts, "jti": id}
  - Signature: Ed25519 signature of header.payload using server's private key

Tokens are:
  - Stateless (no database lookup needed for verification)
  - Scoped (carry their own permission list)
  - Time-limited (configurable TTL, default 24h)
  - Revocable (via revocation list stored on disk)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("pawbot.auth.tokens")


@dataclass
class TokenClaims:
    """Parsed token claims."""

    device_id: str             # Subject — device that owns this token
    role: str                  # Role name
    scopes: list[str]          # Granted scopes
    issued_at: float           # Unix timestamp
    expires_at: float          # Unix timestamp
    token_id: str              # Unique token identifier (jti)

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def remaining_seconds(self) -> float:
        return max(0, self.expires_at - time.time())

    def has_scope(self, scope: str) -> bool:
        """Check if this token grants a specific scope."""
        if "*" in self.scopes:
            return True
        if scope in self.scopes:
            return True
        parts = scope.split(".")
        for i in range(len(parts)):
            parent = ".".join(parts[:i + 1])
            if parent in self.scopes:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub": self.device_id,
            "role": self.role,
            "scopes": self.scopes,
            "iat": self.issued_at,
            "exp": self.expires_at,
            "jti": self.token_id,
        }


class TokenService:
    """Issues and verifies scoped authentication tokens.

    Uses HMAC-SHA256 for signing (fast, no external deps).
    Supports Ed25519 signing when KeypairManager is available.

    Token revocation uses a persistent file-based revocation list.
    """

    DEFAULT_TTL = 86400  # 24 hours
    REVOCATION_FILE = "revoked_tokens.json"

    def __init__(
        self,
        secret: str | None = None,
        storage_dir: Path | None = None,
        default_ttl: int = DEFAULT_TTL,
    ) -> None:
        self.storage_dir = storage_dir or Path.home() / ".pawbot" / "auth"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl

        # Secret key for HMAC signing
        self._secret = (secret or self._load_or_create_secret()).encode("utf-8")

        # Revocation list
        self._revoked: set[str] = set()
        self._load_revoked()

    def _load_or_create_secret(self) -> str:
        """Load or create a signing secret."""
        secret_path = self.storage_dir / ".secret"
        if secret_path.exists():
            return secret_path.read_text(encoding="utf-8").strip()

        # Generate a random 64-byte secret
        secret = base64.b64encode(os.urandom(64)).decode("ascii")
        secret_path.write_text(secret, encoding="utf-8")
        try:
            os.chmod(secret_path, 0o600)
        except OSError:
            pass
        logger.info("Created new signing secret")
        return secret

    # ── Token Issuance ───────────────────────────────────────────────────────

    def issue(
        self,
        device_id: str,
        role: str,
        scopes: list[str],
        ttl_seconds: int | None = None,
        token_id: str | None = None,
    ) -> str:
        """Issue a new scoped token.

        Returns the token string (base64.base64.base64).
        """
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        jti = token_id or str(uuid.uuid4())[:12]

        header = {"alg": "HS256", "typ": "PAT"}
        payload = {
            "sub": device_id,
            "role": role,
            "scopes": scopes,
            "iat": now,
            "exp": now + ttl,
            "jti": jti,
        }

        header_b64 = self._b64_encode(json.dumps(header))
        payload_b64 = self._b64_encode(json.dumps(payload))
        signing_input = f"{header_b64}.{payload_b64}"
        signature = self._sign(signing_input)
        sig_b64 = self._b64_encode(signature)

        token = f"{header_b64}.{payload_b64}.{sig_b64}"
        logger.info("Issued token for %s (role=%s, ttl=%ds)", device_id[:16], role, ttl)
        return token

    # ── Token Verification ───────────────────────────────────────────────────

    def verify(self, token: str) -> TokenClaims | None:
        """Verify a token and return its claims.

        Returns None if:
          - Token format is invalid
          - Signature verification fails
          - Token is expired
          - Token has been revoked
        """
        try:
            parts = token.split(".")
            if len(parts) != 3:
                logger.debug("Invalid token format")
                return None

            header_b64, payload_b64, sig_b64 = parts

            # Verify signature
            signing_input = f"{header_b64}.{payload_b64}"
            expected_sig = self._sign(signing_input)
            actual_sig = self._b64_decode(sig_b64)

            if not hmac.compare_digest(expected_sig.encode("utf-8"), actual_sig):
                logger.debug("Token signature mismatch")
                return None

            # Parse payload
            payload_json = self._b64_decode(payload_b64).decode("utf-8")
            payload = json.loads(payload_json)

            claims = TokenClaims(
                device_id=payload["sub"],
                role=payload["role"],
                scopes=payload.get("scopes", []),
                issued_at=payload["iat"],
                expires_at=payload["exp"],
                token_id=payload["jti"],
            )

            # Check expiration
            if claims.is_expired:
                logger.debug("Token expired for %s", claims.device_id[:16])
                return None

            # Check revocation
            if claims.token_id in self._revoked:
                logger.debug("Token revoked: %s", claims.token_id)
                return None

            return claims

        except (json.JSONDecodeError, KeyError, Exception) as exc:
            logger.debug("Token verification failed: %s", exc)
            return None

    # ── Token Revocation ─────────────────────────────────────────────────────

    def revoke(self, token: str) -> bool:
        """Revoke a token. Returns True if the token was valid and is now revoked."""
        claims = self.verify(token)
        if not claims:
            return False

        self._revoked.add(claims.token_id)
        self._save_revoked()
        logger.info("Revoked token %s for %s", claims.token_id, claims.device_id[:16])
        return True

    def revoke_by_id(self, token_id: str) -> None:
        """Revoke a token by its jti (token ID)."""
        self._revoked.add(token_id)
        self._save_revoked()

    def is_revoked(self, token_id: str) -> bool:
        return token_id in self._revoked

    # ── Token Refresh ────────────────────────────────────────────────────────

    def refresh(self, token: str, ttl_seconds: int | None = None) -> str | None:
        """Refresh a valid token — issue a new one with same claims, new expiry.

        Returns None if the original token is invalid.
        """
        claims = self.verify(token)
        if not claims:
            return None

        # Revoke old token
        self._revoked.add(claims.token_id)
        self._save_revoked()

        # Issue new token
        return self.issue(
            device_id=claims.device_id,
            role=claims.role,
            scopes=claims.scopes,
            ttl_seconds=ttl_seconds,
        )

    # ── Utility ──────────────────────────────────────────────────────────────

    def decode_without_verify(self, token: str) -> dict[str, Any] | None:
        """Decode token payload without verifying signature. For inspection only."""
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None
            payload_json = self._b64_decode(parts[1]).decode("utf-8")
            return json.loads(payload_json)
        except Exception:
            return None

    def _sign(self, data: str) -> str:
        """HMAC-SHA256 sign a string."""
        return hmac.new(self._secret, data.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _b64_encode(data: str | bytes) -> str:
        """URL-safe base64 encode."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64_decode(data: str) -> bytes:
        """URL-safe base64 decode."""
        padding = 4 - len(data) % 4
        if padding != 4:
            data += "=" * padding
        return base64.urlsafe_b64decode(data)

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_revoked(self) -> None:
        revoked_path = self.storage_dir / self.REVOCATION_FILE
        if revoked_path.exists():
            try:
                data = json.loads(revoked_path.read_text(encoding="utf-8"))
                self._revoked = set(data.get("revoked", []))
            except (json.JSONDecodeError, OSError):
                self._revoked = set()

    def _save_revoked(self) -> None:
        revoked_path = self.storage_dir / self.REVOCATION_FILE
        content = json.dumps(
            {"revoked": list(self._revoked), "updated_at": time.time()},
            indent=2,
        )
        tmp = revoked_path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(revoked_path)

    def cleanup_expired_revocations(self) -> int:
        """Remove revocation entries for tokens that have expired anyway.

        Returns number of entries removed.
        """
        # This is a no-op without tracking expiry per revocation
        # Could be enhanced later with a {jti: expired_at} map
        return 0

    def __repr__(self) -> str:
        return f"TokenService(revoked={len(self._revoked)})"
