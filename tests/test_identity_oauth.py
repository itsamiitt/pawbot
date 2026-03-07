"""Tests for Phase 12 — Device Identity, Pairing, OAuth & Exec Approvals."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from pawbot.identity.device import DeviceIdentity
from pawbot.identity.pairing import PairingManager
from pawbot.identity.oauth import AuthManager, OAuthProfile
from pawbot.identity.exec_approvals import ExecApprovalManager, ExecApprovalPolicy


# ── DeviceIdentity Tests ─────────────────────────────────────────────────────


class TestDeviceIdentity:
    """Test device identity creation and management (Phase 12.1)."""

    def test_auto_creates_identity(self, tmp_path):
        identity_dir = tmp_path / "identity"
        dev = DeviceIdentity(identity_dir=identity_dir)

        assert dev.device_id
        assert len(dev.device_id) == 36  # UUID4 format
        assert dev.display_name  # Hostname
        assert dev.api_token
        assert dev.device_secret
        assert dev.created_at > 0

        # File should exist on disk
        assert (identity_dir / "device.json").exists()

    def test_persists_across_loads(self, tmp_path):
        identity_dir = tmp_path / "identity"
        dev1 = DeviceIdentity(identity_dir=identity_dir)
        device_id = dev1.device_id

        dev2 = DeviceIdentity(identity_dir=identity_dir)
        assert dev2.device_id == device_id  # Same identity

    def test_to_public_excludes_secrets(self, tmp_path):
        dev = DeviceIdentity(identity_dir=tmp_path / "identity")
        public = dev.to_public()

        assert "device_id" in public
        assert "display_name" in public
        assert "platform" in public
        assert "auth" not in public
        assert "device_secret" not in str(public)
        assert "api_token" not in str(public)

    def test_platform_info(self, tmp_path):
        dev = DeviceIdentity(identity_dir=tmp_path / "identity")
        info = dev.platform_info
        assert "system" in info
        assert "python" in info

    def test_rotate_token(self, tmp_path):
        dev = DeviceIdentity(identity_dir=tmp_path / "identity")
        old_token = dev.api_token
        new_token = dev.rotate_token()

        assert new_token != old_token
        assert dev.api_token == new_token

        # Verify persisted
        dev2 = DeviceIdentity(identity_dir=tmp_path / "identity")
        assert dev2.api_token == new_token

    def test_rotate_secret(self, tmp_path):
        dev = DeviceIdentity(identity_dir=tmp_path / "identity")
        old_secret = dev.device_secret
        new_secret = dev.rotate_secret()

        assert new_secret != old_secret
        assert dev.device_secret == new_secret

    def test_corrupt_file_regenerates(self, tmp_path):
        identity_dir = tmp_path / "identity"
        identity_dir.mkdir(parents=True)
        (identity_dir / "device.json").write_text("NOT JSON!", encoding="utf-8")

        dev = DeviceIdentity(identity_dir=identity_dir)
        assert dev.device_id  # Should regenerate


# ── PairingManager Tests ─────────────────────────────────────────────────────


class TestPairingManager:
    """Test device pairing with 6-digit codes (Phase 12.2)."""

    def test_generate_pairing_code(self, tmp_path):
        mgr = PairingManager(devices_dir=tmp_path / "devices")
        result = mgr.generate_pairing_code()

        assert "code" in result
        assert len(result["code"]) == 6
        assert result["code"].isdigit()
        assert int(result["code"]) >= 100000
        assert "token" in result
        assert result["expires_in"] == 300

    def test_complete_pairing(self, tmp_path):
        mgr = PairingManager(devices_dir=tmp_path / "devices")
        code_result = mgr.generate_pairing_code()

        pair_result = mgr.complete_pairing(
            code=code_result["code"],
            device_info={"device_id": "dev-123", "display_name": "Test Phone"},
            user_id="user-1",
        )

        assert pair_result is not None
        assert pair_result["success"] is True
        assert "access_token" in pair_result
        assert pair_result["device_id"] == "dev-123"

    def test_invalid_code_rejected(self, tmp_path):
        mgr = PairingManager(devices_dir=tmp_path / "devices")
        result = mgr.complete_pairing(
            code="999999",
            device_info={"device_id": "dev-456"},
        )
        assert result is None

    def test_code_used_once(self, tmp_path):
        mgr = PairingManager(devices_dir=tmp_path / "devices")
        code_result = mgr.generate_pairing_code()

        # First use succeeds
        result1 = mgr.complete_pairing(
            code=code_result["code"],
            device_info={"device_id": "dev-A"},
        )
        assert result1 is not None

        # Second use fails (code consumed)
        result2 = mgr.complete_pairing(
            code=code_result["code"],
            device_info={"device_id": "dev-B"},
        )
        assert result2 is None

    def test_expired_code_rejected(self, tmp_path):
        mgr = PairingManager(devices_dir=tmp_path / "devices")
        code_result = mgr.generate_pairing_code()

        # Manually expire the code
        mgr._pending["requests"][-1]["expires_at"] = time.time() - 10
        mgr._save_pending()

        result = mgr.complete_pairing(
            code=code_result["code"],
            device_info={"device_id": "dev-late"},
        )
        assert result is None

    def test_verify_device(self, tmp_path):
        mgr = PairingManager(devices_dir=tmp_path / "devices")
        code_result = mgr.generate_pairing_code()
        pair_result = mgr.complete_pairing(
            code=code_result["code"],
            device_info={"device_id": "dev-v"},
        )

        # Verify with the access token
        device = mgr.verify_device(pair_result["access_token"])
        assert device is not None
        assert device["device_id"] == "dev-v"

    def test_verify_invalid_token(self, tmp_path):
        mgr = PairingManager(devices_dir=tmp_path / "devices")
        assert mgr.verify_device("invalid-token") is None

    def test_revoke_device(self, tmp_path):
        mgr = PairingManager(devices_dir=tmp_path / "devices")
        code_result = mgr.generate_pairing_code()
        pair_result = mgr.complete_pairing(
            code=code_result["code"],
            device_info={"device_id": "dev-r"},
        )

        assert mgr.revoke_device("dev-r") is True
        # Token should no longer work
        assert mgr.verify_device(pair_result["access_token"]) is None

    def test_revoke_nonexistent(self, tmp_path):
        mgr = PairingManager(devices_dir=tmp_path / "devices")
        assert mgr.revoke_device("ghost-device") is False

    def test_list_paired(self, tmp_path):
        mgr = PairingManager(devices_dir=tmp_path / "devices")

        # Pair two devices
        for name in ["Phone", "Laptop"]:
            code = mgr.generate_pairing_code()
            mgr.complete_pairing(
                code=code["code"],
                device_info={"device_id": f"dev-{name}", "display_name": name},
            )

        devices = mgr.list_paired()
        assert len(devices) == 2
        names = [d["display_name"] for d in devices]
        assert "Phone" in names
        assert "Laptop" in names
        # Verify no secrets leaked
        for d in devices:
            assert "access_token_hash" not in d

    def test_pending_count(self, tmp_path):
        mgr = PairingManager(devices_dir=tmp_path / "devices")
        assert mgr.pending_count() == 0
        mgr.generate_pairing_code()
        assert mgr.pending_count() == 1

    def test_persistence(self, tmp_path):
        devices_dir = tmp_path / "devices"
        mgr1 = PairingManager(devices_dir=devices_dir)
        code = mgr1.generate_pairing_code()
        mgr1.complete_pairing(
            code=code["code"],
            device_info={"device_id": "persist-dev", "display_name": "Persistent"},
        )

        # Load fresh instance
        mgr2 = PairingManager(devices_dir=devices_dir)
        devices = mgr2.list_paired()
        assert len(devices) == 1
        assert devices[0]["device_id"] == "persist-dev"


# ── OAuth / AuthManager Tests ────────────────────────────────────────────────


class TestOAuthProfile:
    """Test OAuthProfile model."""

    def test_api_key_valid(self):
        p = OAuthProfile("openai", "api_key", {"api_key": "sk-test123"})
        assert p.is_valid is True
        assert p.is_expired is False

    def test_api_key_empty_invalid(self):
        p = OAuthProfile("openai", "api_key", {"api_key": ""})
        assert p.is_valid is False

    def test_oauth_valid(self):
        p = OAuthProfile("google", "oauth", {
            "access_token": "ya29...",
            "expires_at": time.time() + 3600,
        })
        assert p.is_valid is True
        assert p.is_expired is False

    def test_oauth_expired(self):
        p = OAuthProfile("google", "oauth", {
            "access_token": "ya29...",
            "expires_at": time.time() - 100,
        })
        assert p.is_valid is True
        assert p.is_expired is True

    def test_service_account_valid(self):
        p = OAuthProfile("google", "service_account", {
            "credentials_path": "/path/to/creds.json",
        })
        assert p.is_valid is True

    def test_to_dict_no_secrets(self):
        p = OAuthProfile("openai", "api_key", {"api_key": "sk-secret123"})
        d = p.to_dict()
        assert "sk-secret123" not in str(d)  # Secret value not in public dict
        assert d["provider"] == "openai"
        assert d["mode"] == "api_key"
        assert d["valid"] is True


class TestAuthManager:
    """Test auth profile management (Phase 12.3)."""

    def test_set_and_get_api_key(self, tmp_path):
        auth_file = tmp_path / "auth.json"
        mgr = AuthManager(auth_file=auth_file)
        mgr.set_api_key("openai", "sk-test-key-123")

        key = mgr.get_api_key("openai")
        assert key == "sk-test-key-123"

    def test_get_nonexistent_key(self, tmp_path):
        mgr = AuthManager(auth_file=tmp_path / "auth.json")
        assert mgr.get_api_key("nonexistent") is None

    def test_multiple_providers(self, tmp_path):
        mgr = AuthManager(auth_file=tmp_path / "auth.json")
        mgr.set_api_key("openai", "sk-openai")
        mgr.set_api_key("anthropic", "sk-ant-anthropic")

        assert mgr.get_api_key("openai") == "sk-openai"
        assert mgr.get_api_key("anthropic") == "sk-ant-anthropic"

    def test_multiple_profiles_per_provider(self, tmp_path):
        mgr = AuthManager(auth_file=tmp_path / "auth.json")
        mgr.set_api_key("openai", "sk-prod", profile_name="production")
        mgr.set_api_key("openai", "sk-dev", profile_name="development")

        assert mgr.get_api_key("openai", "production") == "sk-prod"
        assert mgr.get_api_key("openai", "development") == "sk-dev"

    def test_set_oauth_token(self, tmp_path):
        mgr = AuthManager(auth_file=tmp_path / "auth.json")
        mgr.set_oauth_token(
            "google", access_token="ya29.test", refresh_token="1//refresh",
            expires_in=3600,
        )

        key = mgr.get_api_key("google")
        assert key == "ya29.test"

    def test_list_profiles(self, tmp_path):
        mgr = AuthManager(auth_file=tmp_path / "auth.json")
        mgr.set_api_key("openai", "sk-test")
        mgr.set_api_key("anthropic", "sk-ant-test")

        profiles = mgr.list_profiles()
        assert len(profiles) == 2
        # No secrets in listing
        for p in profiles:
            assert "api_key" not in str(p.get("data", ""))

    def test_remove_profile(self, tmp_path):
        mgr = AuthManager(auth_file=tmp_path / "auth.json")
        mgr.set_api_key("openai", "sk-test")
        assert mgr.remove_profile("openai") is True
        assert mgr.get_api_key("openai") is None

    def test_remove_nonexistent(self, tmp_path):
        mgr = AuthManager(auth_file=tmp_path / "auth.json")
        assert mgr.remove_profile("ghost") is False

    def test_has_profile(self, tmp_path):
        mgr = AuthManager(auth_file=tmp_path / "auth.json")
        assert mgr.has_profile("openai") is False
        mgr.set_api_key("openai", "sk-test")
        assert mgr.has_profile("openai") is True

    def test_persistence(self, tmp_path):
        auth_file = tmp_path / "auth.json"
        mgr1 = AuthManager(auth_file=auth_file)
        mgr1.set_api_key("openai", "sk-persist")

        mgr2 = AuthManager(auth_file=auth_file)
        assert mgr2.get_api_key("openai") == "sk-persist"

    def test_get_credentials(self, tmp_path):
        mgr = AuthManager(auth_file=tmp_path / "auth.json")
        mgr.set_api_key("openai", "sk-test")
        creds = mgr.get_credentials("openai")
        assert creds is not None
        assert creds["api_key"] == "sk-test"


# ── ExecApprovalManager Tests ────────────────────────────────────────────────


class TestExecApprovalManager:
    """Test exec approval system (Phase 12.4)."""

    def test_never_policy_needs_no_approval(self, tmp_path):
        mgr = ExecApprovalManager(
            policy=ExecApprovalPolicy.NEVER,
            approvals_file=tmp_path / "approvals.json",
        )
        assert mgr.needs_approval("exec", "critical") is False

    def test_always_policy_needs_approval(self, tmp_path):
        mgr = ExecApprovalManager(
            policy=ExecApprovalPolicy.ALWAYS,
            approvals_file=tmp_path / "approvals.json",
        )
        assert mgr.needs_approval("exec", "low") is True

    def test_high_risk_policy(self, tmp_path):
        mgr = ExecApprovalManager(
            policy=ExecApprovalPolicy.HIGH_RISK,
            approvals_file=tmp_path / "approvals.json",
        )
        assert mgr.needs_approval("read_file", "low") is False
        assert mgr.needs_approval("exec", "high") is True
        assert mgr.needs_approval("exec", "critical") is True

    def test_auto_safe_policy(self, tmp_path):
        mgr = ExecApprovalManager(
            policy=ExecApprovalPolicy.AUTO_SAFE,
            approvals_file=tmp_path / "approvals.json",
        )
        assert mgr.needs_approval("read_file", "low") is False
        assert mgr.needs_approval("exec", "caution") is True
        assert mgr.needs_approval("browser_eval", "high") is True

    def test_request_and_approve(self, tmp_path):
        mgr = ExecApprovalManager(
            policy=ExecApprovalPolicy.ALWAYS,
            approvals_file=tmp_path / "approvals.json",
        )
        req_id = mgr.request_approval("exec", {"command": "rm -rf /"})
        assert mgr.get_status(req_id) == "pending"

        assert mgr.approve(req_id) is True
        assert mgr.get_status(req_id) == "approved"

    def test_request_and_deny(self, tmp_path):
        mgr = ExecApprovalManager(
            policy=ExecApprovalPolicy.ALWAYS,
            approvals_file=tmp_path / "approvals.json",
        )
        req_id = mgr.request_approval("exec", {"command": "dangerous"})
        assert mgr.deny(req_id) is True
        assert mgr.get_status(req_id) == "denied"

    def test_approve_nonexistent(self, tmp_path):
        mgr = ExecApprovalManager(
            approvals_file=tmp_path / "approvals.json",
        )
        assert mgr.approve("nonexistent") is False

    def test_deny_nonexistent(self, tmp_path):
        mgr = ExecApprovalManager(
            approvals_file=tmp_path / "approvals.json",
        )
        assert mgr.deny("nonexistent") is False

    def test_get_status_not_found(self, tmp_path):
        mgr = ExecApprovalManager(
            approvals_file=tmp_path / "approvals.json",
        )
        assert mgr.get_status("ghost") == "not_found"

    def test_list_pending(self, tmp_path):
        mgr = ExecApprovalManager(
            policy=ExecApprovalPolicy.ALWAYS,
            approvals_file=tmp_path / "approvals.json",
        )
        mgr.request_approval("exec", {"cmd": "a"})
        mgr.request_approval("browser_eval", {"code": "b"})

        pending = mgr.list_pending()
        assert len(pending) == 2

    def test_approve_with_remember(self, tmp_path):
        mgr = ExecApprovalManager(
            policy=ExecApprovalPolicy.ALWAYS,
            approvals_file=tmp_path / "approvals.json",
        )
        req_id = mgr.request_approval("exec", {"command": "ls"})
        mgr.approve(req_id, remember=True)

        # Now the tool should be pre-approved
        assert mgr.needs_approval("exec", "critical") is False

        # Verify persisted
        assert (tmp_path / "approvals.json").exists()

    def test_pre_approved_patterns(self, tmp_path):
        approvals_file = tmp_path / "approvals.json"
        approvals_file.write_text(json.dumps({
            "version": 1,
            "defaults": {"read_file": "approve", "list_files": "approve"},
            "agents": {},
        }))

        mgr = ExecApprovalManager(
            policy=ExecApprovalPolicy.ALWAYS,
            approvals_file=approvals_file,
        )
        assert mgr.needs_approval("read_file", "low") is False
        assert mgr.needs_approval("exec", "high") is True  # Not pre-approved

    def test_clear_resolved(self, tmp_path):
        mgr = ExecApprovalManager(
            approvals_file=tmp_path / "approvals.json",
        )
        req1 = mgr.request_approval("a", {})
        req2 = mgr.request_approval("b", {})
        mgr.approve(req1)
        mgr.deny(req2)

        removed = mgr.clear_resolved()
        assert removed == 2
        assert len(mgr.list_pending()) == 0

    def test_get_request(self, tmp_path):
        mgr = ExecApprovalManager(
            approvals_file=tmp_path / "approvals.json",
        )
        req_id = mgr.request_approval("exec", {"cmd": "test"}, agent_id="sub-1")
        req = mgr.get_request(req_id)
        assert req is not None
        assert req["tool_name"] == "exec"
        assert req["agent_id"] == "sub-1"

    @pytest.mark.asyncio
    async def test_wait_for_approval_approved(self, tmp_path):
        mgr = ExecApprovalManager(
            approvals_file=tmp_path / "approvals.json",
        )
        req_id = mgr.request_approval("exec", {})

        # Approve in background
        async def approve_later():
            await asyncio.sleep(0.2)
            mgr.approve(req_id)

        task = asyncio.create_task(approve_later())
        result = await mgr.wait_for_approval(req_id, timeout=5.0)
        assert result is True
        await task

    @pytest.mark.asyncio
    async def test_wait_for_approval_timeout(self, tmp_path):
        mgr = ExecApprovalManager(
            approvals_file=tmp_path / "approvals.json",
        )
        req_id = mgr.request_approval("exec", {})
        result = await mgr.wait_for_approval(req_id, timeout=0.3)
        assert result is False
        assert mgr.get_status(req_id) == "denied"  # Auto-denied on timeout
