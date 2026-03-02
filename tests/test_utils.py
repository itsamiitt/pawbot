"""Unit tests for pawbot utility modules."""
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest


# ── atomic_write_json ───────────────────────────────────────────────────────

class TestAtomicWriteJson:
    def test_creates_valid_json(self, tmp_path):
        from pawbot.utils.fs import atomic_write_json
        target = tmp_path / "test.json"
        atomic_write_json(target, {"result": "ok", "n": 42})
        data = json.loads(target.read_text())
        assert data == {"result": "ok", "n": 42}

    def test_no_temp_files_left(self, tmp_path):
        from pawbot.utils.fs import atomic_write_json
        target = tmp_path / "test.json"
        atomic_write_json(target, {"a": 1})
        assert not list(tmp_path.glob("*.tmp"))

    def test_overwrites_existing(self, tmp_path):
        from pawbot.utils.fs import atomic_write_json
        target = tmp_path / "test.json"
        atomic_write_json(target, {"v": 1})
        atomic_write_json(target, {"v": 2})
        assert json.loads(target.read_text()) == {"v": 2}

    def test_thread_safety(self, tmp_path):
        from pawbot.utils.fs import atomic_write_json
        target = tmp_path / "shared.json"
        errors = []

        def write(i):
            try:
                atomic_write_json(target, {"writer": i, "data": list(range(50))})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write, args=(i,), daemon=True) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread safety errors: {errors}"
        data = json.loads(target.read_text())
        assert "writer" in data


# ── safe_read_json ──────────────────────────────────────────────────────────

class TestSafeReadJson:
    def test_reads_valid_json(self, tmp_path):
        from pawbot.utils.fs import safe_read_json
        f = tmp_path / "data.json"
        f.write_text(json.dumps({"ok": True}))
        assert safe_read_json(f) == {"ok": True}

    def test_returns_default_for_corrupted(self, tmp_path):
        from pawbot.utils.fs import safe_read_json
        f = tmp_path / "data.json"
        f.write_text("{{CORRUPTED")
        result = safe_read_json(f, default={"recovered": True})
        assert result == {"recovered": True}

    def test_returns_default_for_missing(self, tmp_path):
        from pawbot.utils.fs import safe_read_json
        result = safe_read_json(tmp_path / "nope.json", default=[])
        assert result == []

    def test_backup_recovery(self, tmp_path):
        from pawbot.utils.fs import safe_read_json
        f = tmp_path / "data.json"
        bak = f.with_suffix(".json.bak")
        f.write_text("{{CORRUPTED")
        bak.write_text(json.dumps({"from_backup": True}))
        result = safe_read_json(f, default={})
        assert result == {"from_backup": True}


# ── is_placeholder ──────────────────────────────────────────────────────────

class TestIsPlaceholder:
    @pytest.mark.parametrize("val", [
        "sk-or-v1-xxx", "YOUR_API_KEY", "REPLACE_ME", "xxx", "", None,
    ])
    def test_detects_placeholders(self, val):
        from pawbot.utils.secrets import is_placeholder
        assert is_placeholder(val), f"Should be placeholder: {val!r}"

    @pytest.mark.parametrize("val", [
        "sk-or-v1-abc123def456ghi789012",
        "sk-ant-api03-abc123xyz",
        "BSA-realkey123456",
    ])
    def test_detects_real_keys(self, val):
        from pawbot.utils.secrets import is_placeholder
        assert not is_placeholder(val), f"Should NOT be placeholder: {val!r}"


# ── mask_secret ─────────────────────────────────────────────────────────────

class TestMaskSecret:
    def test_hides_key_body(self):
        from pawbot.utils.secrets import mask_secret
        key = "sk-or-v1-abc123def456ghi789"
        masked = mask_secret(key)
        assert "abc123def456ghi789" not in masked

    def test_short_key_fully_masked(self):
        from pawbot.utils.secrets import mask_secret
        masked = mask_secret("abc")
        assert "abc" not in masked or "••" in masked


# ── call_with_retry ─────────────────────────────────────────────────────────

class TestCallWithRetry:
    def test_retries_on_429(self):
        from pawbot.utils.retry import call_with_retry
        attempts = [0]

        def flaky():
            attempts[0] += 1
            if attempts[0] < 3:
                raise Exception("429 Too Many Requests")
            return "success"

        result = call_with_retry(flaky, max_retries=3, base_delay=0.001)
        assert result == "success"
        assert attempts[0] == 3

    def test_raises_config_error_on_401(self):
        from pawbot.utils.retry import call_with_retry
        from pawbot.errors import ConfigError

        def auth_fail():
            raise Exception("401 Unauthorized - invalid key")

        with pytest.raises(ConfigError):
            call_with_retry(auth_fail, max_retries=3, base_delay=0.001)

    def test_raises_after_max_retries(self):
        from pawbot.utils.retry import call_with_retry

        def always_fail():
            raise Exception("503 Service Unavailable")

        with pytest.raises(Exception, match="503"):
            call_with_retry(always_fail, max_retries=2, base_delay=0.001)

    def test_succeeds_immediately(self):
        from pawbot.utils.retry import call_with_retry
        result = call_with_retry(lambda: "ok", max_retries=3, base_delay=0.001)
        assert result == "ok"


# ── errors module ───────────────────────────────────────────────────────────

class TestErrors:
    def test_hierarchy(self):
        from pawbot.errors import PawbotError, ConfigError, ProviderError
        assert issubclass(ConfigError, PawbotError)
        assert issubclass(ProviderError, PawbotError)

    def test_config_error_message(self):
        from pawbot.errors import ConfigError
        e = ConfigError("test message")
        assert "test message" in str(e)
