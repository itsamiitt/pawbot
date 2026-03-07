"""Tests for auth module: roles, tokens, device registry, and middleware."""

import json
import time
from pathlib import Path

import pytest

from pawbot.auth.roles import ROLES, Role, get_role, validate_scopes
from pawbot.auth.tokens import TokenClaims, TokenService
from pawbot.auth.device import DeviceRegistry
from pawbot.auth.middleware import (
    AuthContext,
    extract_token,
    verify_request,
)


# ══════════════════════════════════════════════════════════════════════════════
#  Role Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestRoles:
    """Tests for role and scope definitions."""

    def test_owner_has_all_scopes(self):
        owner = ROLES["owner"]
        assert owner.has_scope("admin") is True
        assert owner.has_scope("read.memory") is True
        assert owner.has_scope("anything.at.all") is True

    def test_viewer_read_only(self):
        viewer = ROLES["viewer"]
        assert viewer.has_scope("read") is True
        assert viewer.has_scope("read.memory") is True
        assert viewer.has_scope("write") is False
        assert viewer.has_scope("admin") is False

    def test_member_permissions(self):
        member = ROLES["member"]
        assert member.has_scope("read") is True
        assert member.has_scope("write") is True
        assert member.has_scope("agent.execute") is True
        assert member.has_scope("admin") is False
        assert member.has_scope("pairing") is False

    def test_operator_admin(self):
        op = ROLES["operator"]
        assert op.has_scope("admin") is True
        assert op.has_scope("approvals") is True
        assert op.has_scope("pairing") is True
        assert op.is_admin is True

    def test_node_limited(self):
        node = ROLES["node"]
        assert node.has_scope("agent.execute") is True
        assert node.has_scope("agent.tools") is True
        assert node.has_scope("read.memory") is True
        assert node.has_scope("admin") is False
        assert node.has_scope("channels.send") is False

    def test_scope_hierarchy(self):
        """Parent scope should cover child scopes."""
        role = Role(name="test", description="", scopes=["read"])
        assert role.has_scope("read") is True
        assert role.has_scope("read.memory") is True
        assert role.has_scope("read.logs") is True
        assert role.has_scope("write") is False

    def test_get_role(self):
        assert get_role("owner") is not None
        assert get_role("nonexistent") is None

    def test_validate_scopes_valid(self):
        valid, bad = validate_scopes(["read", "write", "admin"])
        assert valid is True
        assert bad == []

    def test_validate_scopes_invalid(self):
        valid, bad = validate_scopes(["read", "nonexistent_scope"])
        assert valid is False
        assert "nonexistent_scope" in bad

    def test_validate_scopes_wildcard(self):
        valid, _ = validate_scopes(["*"])
        assert valid is True

    def test_role_to_dict(self):
        data = ROLES["owner"].to_dict()
        assert data["name"] == "owner"
        assert "*" in data["scopes"]


# ══════════════════════════════════════════════════════════════════════════════
#  Token Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestTokenService:
    """Tests for token issuance and verification."""

    def setup_method(self):
        import tempfile
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.ts = TokenService(
            secret="test-secret-key",
            storage_dir=self.tmp_dir,
            default_ttl=3600,
        )

    def test_issue_and_verify(self):
        token = self.ts.issue("device123", "member", ["read", "write"])
        claims = self.ts.verify(token)
        assert claims is not None
        assert claims.device_id == "device123"
        assert claims.role == "member"
        assert "read" in claims.scopes

    def test_expired_token(self):
        token = self.ts.issue("device123", "member", ["read"], ttl_seconds=0)
        time.sleep(0.01)
        claims = self.ts.verify(token)
        assert claims is None

    def test_invalid_token_format(self):
        assert self.ts.verify("not.a.valid.token.at.all") is None
        assert self.ts.verify("") is None
        assert self.ts.verify("abc") is None

    def test_tampered_token(self):
        token = self.ts.issue("device123", "member", ["read"])
        # Tamper with the payload
        parts = token.split(".")
        parts[1] = parts[1][::-1]
        tampered = ".".join(parts)
        assert self.ts.verify(tampered) is None

    def test_revoke_token(self):
        token = self.ts.issue("device123", "member", ["read"])
        assert self.ts.verify(token) is not None
        assert self.ts.revoke(token) is True
        assert self.ts.verify(token) is None

    def test_revoke_by_id(self):
        token = self.ts.issue("device123", "member", ["read"], token_id="my-token-id")
        self.ts.revoke_by_id("my-token-id")
        assert self.ts.verify(token) is None

    def test_refresh_token(self):
        token = self.ts.issue("device123", "member", ["read"])
        new_token = self.ts.refresh(token)
        assert new_token is not None
        assert new_token != token
        # Old token should be revoked
        assert self.ts.verify(token) is None
        # New token should be valid
        claims = self.ts.verify(new_token)
        assert claims is not None
        assert claims.device_id == "device123"

    def test_refresh_invalid_token(self):
        assert self.ts.refresh("invalid.token.here") is None

    def test_decode_without_verify(self):
        token = self.ts.issue("device123", "member", ["read"])
        payload = self.ts.decode_without_verify(token)
        assert payload is not None
        assert payload["sub"] == "device123"

    def test_different_secrets(self):
        ts2 = TokenService(
            secret="different-secret",
            storage_dir=self.tmp_dir / "other",
        )
        token = self.ts.issue("device123", "member", ["read"])
        assert ts2.verify(token) is None

    def test_scope_checking_in_claims(self):
        token = self.ts.issue("device123", "member", ["read", "write"])
        claims = self.ts.verify(token)
        assert claims.has_scope("read") is True
        assert claims.has_scope("read.memory") is True
        assert claims.has_scope("admin") is False

    def test_wildcard_scope(self):
        token = self.ts.issue("device123", "owner", ["*"])
        claims = self.ts.verify(token)
        assert claims.has_scope("anything") is True

    def test_revocation_persistence(self):
        token = self.ts.issue("device123", "member", ["read"])
        self.ts.revoke(token)

        # Reload service from same dir
        ts2 = TokenService(secret="test-secret-key", storage_dir=self.tmp_dir)
        assert ts2.verify(token) is None


# ══════════════════════════════════════════════════════════════════════════════
#  Device Registry Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestDeviceRegistry:
    """Tests for device pairing and management."""

    def setup_method(self):
        import tempfile
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.registry = DeviceRegistry(devices_dir=self.tmp_dir)

    def test_request_and_approve(self):
        req_id = self.registry.request_pairing(
            device_id="abc123def456",
            public_key_pem="-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----",
            platform="windows",
            role="member",
        )
        assert req_id is not None
        assert self.registry.pending_count == 1

        record = self.registry.approve("abc123def456")
        assert record["role"] == "member"
        assert self.registry.paired_count == 1
        assert self.registry.pending_count == 0

    def test_request_and_reject(self):
        self.registry.request_pairing(
            device_id="abc123",
            public_key_pem="test",
            platform="linux",
        )
        assert self.registry.reject("abc123") is True
        assert self.registry.pending_count == 0
        assert self.registry.paired_count == 0

    def test_revoke_paired_device(self):
        self.registry.request_pairing(
            device_id="device1",
            public_key_pem="test",
        )
        self.registry.approve("device1")
        assert self.registry.is_paired("device1") is True
        assert self.registry.revoke("device1") is True
        assert self.registry.is_paired("device1") is False

    def test_duplicate_pairing_request(self):
        self.registry.request_pairing(
            device_id="device1", public_key_pem="test",
        )
        with pytest.raises(ValueError, match="pending"):
            self.registry.request_pairing(
                device_id="device1", public_key_pem="test",
            )

    def test_already_paired(self):
        self.registry.request_pairing(
            device_id="device1", public_key_pem="test",
        )
        self.registry.approve("device1")
        with pytest.raises(ValueError, match="already paired"):
            self.registry.request_pairing(
                device_id="device1", public_key_pem="test",
            )

    def test_invalid_role(self):
        with pytest.raises(ValueError, match="Unknown role"):
            self.registry.request_pairing(
                device_id="device1",
                public_key_pem="test",
                role="nonexistent",
            )

    def test_list_paired(self):
        self.registry.request_pairing(
            device_id="d1", public_key_pem="test", label="Phone",
        )
        self.registry.approve("d1")
        devices = self.registry.list_paired()
        assert len(devices) == 1
        assert "public_key_pem" not in devices[0]  # Redacted

    def test_list_pending(self):
        self.registry.request_pairing(
            device_id="d1", public_key_pem="test",
        )
        pending = self.registry.list_pending()
        assert len(pending) == 1
        assert "public_key_pem" not in pending[0]

    def test_has_scope(self):
        self.registry.request_pairing(
            device_id="d1", public_key_pem="test", role="operator",
        )
        self.registry.approve("d1")
        assert self.registry.has_scope("d1", "admin") is True
        assert self.registry.has_scope("d1", "read") is True
        assert self.registry.has_scope("nonexistent", "read") is False

    def test_partial_id_approve(self):
        """Can approve with partial device ID (like OpenClaw CLI)."""
        self.registry.request_pairing(
            device_id="abcdef1234567890",
            public_key_pem="test",
        )
        record = self.registry.approve("abcdef")
        assert record is not None

    def test_persistence(self):
        self.registry.request_pairing(
            device_id="d1", public_key_pem="test",
        )
        self.registry.approve("d1")

        # Reload from disk
        registry2 = DeviceRegistry(devices_dir=self.tmp_dir)
        assert registry2.is_paired("d1") is True

    def test_update_last_seen(self):
        self.registry.request_pairing(
            device_id="d1", public_key_pem="test",
        )
        self.registry.approve("d1")
        self.registry.update_last_seen("d1")
        device = self.registry.get_device("d1")
        assert device["last_seen"] > 0


# ══════════════════════════════════════════════════════════════════════════════
#  Middleware Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestMiddleware:
    """Tests for auth middleware functions."""

    def setup_method(self):
        import tempfile
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.ts = TokenService(secret="test", storage_dir=self.tmp_dir)

    def test_extract_bearer_token(self):
        token = extract_token({"authorization": "Bearer my-token-123"})
        assert token == "my-token-123"

    def test_extract_custom_header(self):
        token = extract_token({"x-auth-token": "my-token-456"})
        assert token == "my-token-456"

    def test_extract_no_token(self):
        token = extract_token({})
        assert token is None

    def test_verify_request_unprotected(self):
        ok, ctx, err = verify_request(self.ts, {}, path="/health")
        assert ok is True

    def test_verify_request_no_token(self):
        ok, ctx, err = verify_request(self.ts, {}, path="/api/protected")
        assert ok is False
        assert "Missing" in err

    def test_verify_request_valid(self):
        token = self.ts.issue("device1", "member", ["read"])
        ok, ctx, err = verify_request(
            self.ts,
            {"authorization": f"Bearer {token}"},
            path="/api/protected",
        )
        assert ok is True
        assert ctx is not None
        assert ctx.device_id == "device1"

    def test_verify_request_expired(self):
        token = self.ts.issue("device1", "member", ["read"], ttl_seconds=0)
        time.sleep(0.01)
        ok, ctx, err = verify_request(
            self.ts,
            {"authorization": f"Bearer {token}"},
            path="/api/protected",
        )
        assert ok is False
        assert "Invalid" in err

    def test_verify_request_scope_check(self):
        token = self.ts.issue("device1", "viewer", ["read"])
        ok, ctx, err = verify_request(
            self.ts,
            {"authorization": f"Bearer {token}"},
            path="/api/admin",
            required_scopes=["admin"],
        )
        assert ok is False
        assert "scope" in err.lower()

    def test_auth_context(self):
        claims = TokenClaims(
            device_id="d1", role="member", scopes=["read", "write"],
            issued_at=0, expires_at=float("inf"), token_id="t1",
        )
        ctx = AuthContext(claims)
        assert ctx.has_scope("read") is True
        assert ctx.has_scope("admin") is False
        assert "d1" in repr(ctx)
