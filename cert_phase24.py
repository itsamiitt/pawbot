"""Phase 2 — Import Verification + Phase 4 — Functional Unit Tests."""
import sys, io, traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

results = []
def check(name, passed, detail=""):
    icon = "[PASS]" if passed else "[FAIL]"
    results.append((name, passed, detail))
    print(f"  {icon}  {name}  {detail}")

print("=" * 60)
print("PHASE 2 -- IMPORT VERIFICATION")
print("=" * 60)

imports = [
    ("pawbot.errors: PawbotError, ConfigError, ProviderError",
     "from pawbot.errors import PawbotError, ConfigError, ProviderError"),
    ("pawbot.utils.paths: PAWBOT_HOME, CONFIG_PATH, LOGS_PATH",
     "from pawbot.utils.paths import PAWBOT_HOME, CONFIG_PATH, LOGS_PATH"),
    ("pawbot.utils.secrets: mask_secret, is_placeholder",
     "from pawbot.utils.secrets import mask_secret, is_placeholder"),
    ("pawbot.utils.fs: atomic_write_json, safe_read_json, write_json_with_backup",
     "from pawbot.utils.fs import atomic_write_json, atomic_write_text, safe_read_json, write_json_with_backup"),
    ("pawbot.utils.retry: call_with_retry",
     "from pawbot.utils.retry import call_with_retry"),
    ("pawbot.utils.logging_setup: setup_logging",
     "from pawbot.utils.logging_setup import setup_logging"),
    ("pawbot.config.loader: load_config",
     "from pawbot.config.loader import load_config"),
    ("pawbot.providers.router: ModelRouter",
     "from pawbot.providers.router import ModelRouter"),
    ("pawbot.cli.commands: app",
     "from pawbot.cli.commands import app"),
]

for name, stmt in imports:
    try:
        exec(stmt)
        check(name, True)
    except Exception as e:
        check(name, False, str(e)[:120])

print()
print("=" * 60)
print("PHASE 4 -- FUNCTIONAL UNIT TESTS")
print("=" * 60)

# Test 1: atomic_write_json
import json, tempfile
from pathlib import Path
try:
    from pawbot.utils.fs import atomic_write_json
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / 'test.json'
        atomic_write_json(target, {'result': 'ok', 'n': 42})
        data = json.loads(target.read_text())
        assert data == {'result': 'ok', 'n': 42}, f'Wrong data: {data}'
        tmp_files = list(Path(d).glob('*.tmp'))
        assert not tmp_files, f'Temp files left: {tmp_files}'
        check("T1: atomic_write_json creates file, no temp files", True)
except Exception as e:
    check("T1: atomic_write_json creates file", False, str(e)[:120])

# Test 2: atomic_write_json — no temp file on failure
try:
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as d:
        with patch('os.replace', side_effect=OSError('simulated')):
            try:
                atomic_write_json(Path(d) / 'test.json', {})
            except OSError:
                pass
        leftover = list(Path(d).glob('*.tmp'))
        assert not leftover, f'Temp files not cleaned: {leftover}'
        check("T2: atomic_write_json no temp on failure", True)
except Exception as e:
    check("T2: atomic_write_json no temp on failure", False, str(e)[:120])

# Test 3: safe_read_json — corrupted file returns default
try:
    from pawbot.utils.fs import safe_read_json
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / 'data.json'
        f.write_text('{{CORRUPTED JSON GARBAGE')
        result = safe_read_json(f, default={'recovered': True})
        assert result == {'recovered': True}, f'Got: {result}'
        check("T3: safe_read_json returns default for corrupted file", True)
except Exception as e:
    check("T3: safe_read_json corrupted file", False, str(e)[:120])

# Test 4: safe_read_json — backup recovery
try:
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / 'data.json'
        bak = f.with_suffix('.json.bak')
        f.write_text('{{CORRUPTED')
        bak.write_text(json.dumps({'from_backup': True}))
        result = safe_read_json(f, default={})
        assert result == {'from_backup': True}, f'Got: {result}'
        assert f.exists(), 'Main file not restored'
        check("T4: safe_read_json recovers from .bak file", True)
except Exception as e:
    check("T4: safe_read_json backup recovery", False, str(e)[:120])

# Test 5: is_placeholder
try:
    from pawbot.utils.secrets import is_placeholder
    placeholders = ['sk-or-v1-xxx', 'YOUR_API_KEY', 'REPLACE_ME', 'xxx', '', None]
    real_keys = ['sk-or-v1-abc123def456ghi789012', 'sk-ant-api03-abc123xyz', 'BSA-realkey123456']
    all_ok = True
    for v in placeholders:
        if not is_placeholder(v):
            all_ok = False
            check(f"T5: is_placeholder('{v}') should be True", False)
    for v in real_keys:
        if is_placeholder(v):
            all_ok = False
            check(f"T5: is_placeholder('{v}') should be False", False)
    if all_ok:
        check("T5: is_placeholder correctly identifies placeholders and real keys", True)
except Exception as e:
    check("T5: is_placeholder", False, str(e)[:120])

# Test 6: mask_secret
try:
    from pawbot.utils.secrets import mask_secret
    key = 'sk-or-v1-abc123def456ghi789'
    masked = mask_secret(key)
    # The full key body should not appear
    assert 'abc123def456ghi789' not in masked, f'Key body leaked: {masked}'
    check("T6: mask_secret hides key body", True, f"'{key[:12]}...' -> '{masked}'")
except Exception as e:
    check("T6: mask_secret", False, str(e)[:120])

# Test 7: call_with_retry — retries on 429
try:
    from pawbot.utils.retry import call_with_retry
    attempts = [0]
    def flaky():
        attempts[0] += 1
        if attempts[0] < 3:
            raise Exception('429 Too Many Requests - rate limited')
        return 'success'
    result = call_with_retry(flaky, max_retries=3, base_delay=0.001)
    assert result == 'success', f'Got: {result}'
    assert attempts[0] == 3, f'Expected 3 attempts, got {attempts[0]}'
    check("T7: call_with_retry retries on 429", True, f"succeeded on attempt {attempts[0]}")
except Exception as e:
    check("T7: call_with_retry retries on 429", False, str(e)[:120])

# Test 8: call_with_retry — raises ConfigError on 401
try:
    from pawbot.errors import ConfigError
    def auth_fail():
        raise Exception('401 Unauthorized - invalid key')
    try:
        call_with_retry(auth_fail, max_retries=3, base_delay=0.001)
        check("T8: call_with_retry raises ConfigError on 401", False, "No exception raised")
    except ConfigError:
        check("T8: call_with_retry raises ConfigError on 401", True)
    except Exception as e:
        check("T8: call_with_retry raises ConfigError on 401", False, f"Wrong exception: {type(e).__name__}: {e}")
except Exception as e:
    check("T8: call_with_retry ConfigError", False, str(e)[:120])

# Test 9: call_with_retry — raises after exhausting retries
try:
    def always_fail():
        raise Exception('503 Service Unavailable')
    try:
        call_with_retry(always_fail, max_retries=2, base_delay=0.001)
        check("T9: call_with_retry raises after max retries", False, "No exception raised")
    except Exception as e:
        assert '503' in str(e), f'Wrong exception: {e}'
        check("T9: call_with_retry raises after max retries", True)
except Exception as e:
    check("T9: call_with_retry max retries", False, str(e)[:120])

# Test 10: Thread safety
try:
    import threading
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / 'shared.json'
        errors = []
        def write(i):
            try:
                atomic_write_json(target, {'writer': i, 'data': list(range(100))})
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=write, args=(i,), daemon=True) for i in range(30)]
        for t in threads: t.start()
        for t in threads: t.join()
        if errors:
            check("T10: Thread safety", False, f"{len(errors)} errors: {errors[0]}")
        else:
            data = json.loads(target.read_text())
            assert 'writer' in data
            check("T10: Thread safety: 30 concurrent writes", True, "no errors, valid JSON")
except Exception as e:
    check("T10: Thread safety", False, str(e)[:120])

# Summary
print()
print("=" * 60)
failures = [r for r in results if not r[1]]
print(f"PHASE 2+4 SUMMARY: {len(results) - len(failures)}/{len(results)} passed, {len(failures)} failed")
if failures:
    print("FAILURES:")
    for name, _, detail in failures:
        print(f"  [FAIL]  {name}  {detail}")
