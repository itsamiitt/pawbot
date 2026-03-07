"""Dashboard session authentication helpers."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

from pawbot.auth.tokens import TokenService
from pawbot.utils.paths import PAWBOT_HOME

JWT_SECRET_FILE = PAWBOT_HOME / "dashboard_secret"
AUTH_FILE = PAWBOT_HOME / "dashboard_auth.json"
AUTH_STORAGE_DIR = PAWBOT_HOME / "dashboard_auth"
JWT_EXPIRY_HOURS = 24
PBKDF2_ITERATIONS = 200_000


def _read_auth_file() -> dict[str, Any]:
    try:
        return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_auth_file(data: dict[str, Any]) -> None:
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _provision_from_env() -> None:
    """Bootstrap dashboard auth from an env var when no file exists yet."""
    if AUTH_FILE.exists():
        return
    password = os.environ.get("PAWBOT_DASHBOARD_PASSWORD", "").strip()
    if password:
        set_password(password)


def _get_or_create_secret() -> str:
    """Load or create the dashboard signing secret."""
    if JWT_SECRET_FILE.exists():
        return JWT_SECRET_FILE.read_text(encoding="utf-8").strip()

    secret = secrets.token_urlsafe(48)
    JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    JWT_SECRET_FILE.write_text(secret, encoding="utf-8")
    try:
        os.chmod(JWT_SECRET_FILE, 0o600)
    except OSError:
        pass
    return secret


def _token_service() -> TokenService:
    """Create a token service bound to dashboard auth storage."""
    return TokenService(
        secret=_get_or_create_secret(),
        storage_dir=AUTH_STORAGE_DIR,
        default_ttl=JWT_EXPIRY_HOURS * 3600,
    )


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return digest.hex()


def password_configured() -> bool:
    _provision_from_env()
    return AUTH_FILE.exists()


def set_password(password: str) -> None:
    """Persist a dashboard password using PBKDF2-HMAC-SHA256."""
    salt = secrets.token_hex(16)
    _write_auth_file({
        "scheme": "pbkdf2_sha256",
        "password_salt": salt,
        "password_hash": _hash_password(password, salt),
    })


def verify_password(password: str) -> bool:
    """Verify a plaintext password against the stored dashboard hash."""
    _provision_from_env()
    if not AUTH_FILE.exists():
        return False

    data = _read_auth_file()
    password_hash = str(data.get("password_hash", ""))
    if not password_hash:
        return False

    if data.get("scheme") == "pbkdf2_sha256" and data.get("password_salt"):
        expected = _hash_password(password, str(data["password_salt"]))
    else:
        # Backward-compatible fallback for legacy SHA256-only files.
        expected = hashlib.sha256(password.encode("utf-8")).hexdigest()

    return secrets.compare_digest(password_hash, expected)


def create_token(username: str = "admin") -> str:
    """Issue a signed dashboard session token."""
    return _token_service().issue(
        device_id=username,
        role="owner",
        scopes=["*"],
        ttl_seconds=JWT_EXPIRY_HOURS * 3600,
    )


def verify_token(token: str) -> dict[str, Any] | None:
    """Validate a dashboard session token."""
    claims = _token_service().verify(token)
    return claims.to_dict() if claims else None
