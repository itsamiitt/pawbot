# PROMPT — PAWBOT FINAL PRODUCTION CERTIFICATION

You are a senior QA engineer and release manager. The Pawbot codebase has just completed a production remediation pass. The developer claims all 9 fix categories are done. Your job is **not** to fix anything — your job is to **verify every single claim is actually true**, run the full test suite, execute every E2E command, and either issue a production certificate or produce a precise list of what still needs fixing before release.

You trust nothing. You verify everything. Claim ≠ done.

Read this entire file before running a single command.

---

## WHAT THE DEVELOPER CLAIMS IS DONE

| Fix | Claim |
|-----|-------|
| Fix 1 | `pyproject.toml` created with build system, deps, CLI entry point |
| Fix 2 | 6 utility modules created: `errors.py`, `paths.py`, `secrets.py`, `fs.py`, `retry.py`, `logging_setup.py` |
| Fix 3 | 9 atomic write locations fixed |
| Fix 4 | 3 critical JSON read paths use `safe_read_json()` |
| Fix 5 | All 3 provider methods in `router.py` wrapped with `call_with_retry()` |
| Fix 6 | 104 silent exception blocks fixed across 27 files |
| Fix 7 | 3 subprocess timeouts added to `cli/commands.py` |
| Fix 8 | `_validate_key()` added to `router.py` with placeholder detection |
| Fix 9 | `print()` replaced with `logger.warning()` / `logger.error()` in `config/loader.py` |

Your job is to verify each claim independently, run the test suite, run E2E tests, and produce the final certification report.

---

## PHASE 1 — STRUCTURAL VERIFICATION (automated checks)

Run every command in this section. Record the output exactly. Do not interpret — just collect.

### 1.1 File existence checks

```bash
echo "=== FILE EXISTENCE ==="
files=(
    "~/pawbot/pyproject.toml"
    "~/pawbot/pawbot/errors.py"
    "~/pawbot/pawbot/utils/paths.py"
    "~/pawbot/pawbot/utils/secrets.py"
    "~/pawbot/pawbot/utils/fs.py"
    "~/pawbot/pawbot/utils/retry.py"
    "~/pawbot/pawbot/utils/logging_setup.py"
    "~/pawbot/tests/__init__.py"
    "~/pawbot/tests/conftest.py"
    "~/pawbot/tests/test_utils.py"
    "~/pawbot/tests/test_cli.py"
    "~/pawbot/tests/test_security.py"
    "~/pawbot/tests/test_install.py"
)

for f in "${files[@]}"; do
    expanded="${f/#\~/$HOME}"
    [ -f "$expanded" ] && echo "✅  EXISTS   $f" || echo "❌  MISSING  $f"
done
```

### 1.2 `pyproject.toml` content verification

```bash
echo ""
echo "=== PYPROJECT.TOML VERIFICATION ==="

cd ~/pawbot

# Valid TOML
python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print('TOML parseable: ✅')" 2>&1

# Has required fields
python3 -c "
import tomllib
d = tomllib.load(open('pyproject.toml','rb'))
proj = d.get('project', {})
checks = [
    ('name',            proj.get('name') == 'pawbot-ai'),
    ('version',         bool(proj.get('version'))),
    ('requires-python', '>=3.11' in proj.get('requires-python','')),
    ('pawbot script',   'pawbot' in d.get('project',{}).get('scripts',{})),
    ('hatchling',       'hatchling' in str(d.get('build-system',{}).get('requires',''))),
    ('dependencies',    len(proj.get('dependencies',[])) >= 5),
    ('optional-deps',   'dashboard' in d.get('project',{}).get('optional-dependencies',{})),
    ('dev extras',      'dev' in d.get('project',{}).get('optional-dependencies',{})),
]
for name, result in checks:
    print(f\"{'✅' if result else '❌'}  {name}\")
"
```

### 1.3 Bare and silent exception checks

```bash
echo ""
echo "=== EXCEPTION HANDLING ==="
cd ~/pawbot

# Check 1: No bare except:
BARE=$(grep -rn "except:" pawbot/ --include="*.py" | wc -l | tr -d ' ')
[ "$BARE" -eq 0 ] && echo "✅  Bare except: 0" || echo "❌  Bare except: $BARE found"
grep -rn "except:" pawbot/ --include="*.py" 2>/dev/null

# Check 2: No silent exception swallowing (except without 'as e')
SILENT=$(grep -rn "except Exception:" pawbot/ --include="*.py" | grep -v " as e" | wc -l | tr -d ' ')
[ "$SILENT" -eq 0 ] && echo "✅  Silent except: 0" || echo "❌  Silent except: $SILENT found"
grep -rn "except Exception:" pawbot/ --include="*.py" | grep -v " as e" 2>/dev/null | head -20
```

### 1.4 Thread daemon check

```bash
echo ""
echo "=== THREAD DAEMON CHECK ==="
cd ~/pawbot

NON_DAEMON=$(grep -rn "Thread(" pawbot/ --include="*.py" | grep -v "daemon=True" | wc -l | tr -d ' ')
[ "$NON_DAEMON" -eq 0 ] && echo "✅  All threads daemon: confirmed" || echo "❌  Non-daemon threads: $NON_DAEMON"
grep -rn "Thread(" pawbot/ --include="*.py" | grep -v "daemon=True" 2>/dev/null
```

### 1.5 Subprocess timeout check

```bash
echo ""
echo "=== SUBPROCESS TIMEOUT CHECK ==="
cd ~/pawbot

MISSING_TIMEOUT=$(grep -rn "subprocess\.run(" pawbot/ --include="*.py" | grep -v "timeout=" | wc -l | tr -d ' ')
[ "$MISSING_TIMEOUT" -eq 0 ] && echo "✅  All subprocess.run have timeout=" || echo "❌  Missing timeouts: $MISSING_TIMEOUT"
grep -rn "subprocess\.run(" pawbot/ --include="*.py" | grep -v "timeout=" 2>/dev/null

# Specific lines the developer claimed to fix (891, 894)
echo ""
echo "--- cli/commands.py npm lines ---"
grep -n "npm\|subprocess\.run\|subprocess\.Popen" pawbot/cli/commands.py 2>/dev/null | grep -i "npm\|timeout"
```

### 1.6 Nanobot reference check

```bash
echo ""
echo "=== NANOBOT REFERENCE CHECK ==="
cd ~/pawbot

COUNT=$(grep -rn "nanobot" pawbot/ --include="*.py" | wc -l | tr -d ' ')
[ "$COUNT" -eq 0 ] && echo "✅  No nanobot references in Python files" || echo "❌  nanobot references: $COUNT"
grep -rn "nanobot" pawbot/ --include="*.py" 2>/dev/null
```

### 1.7 Atomic write verification

```bash
echo ""
echo "=== ATOMIC WRITE VERIFICATION ==="
cd ~/pawbot

echo "--- Files that should NOT have raw write_text for JSON ---"
for f in \
    "pawbot/config/loader.py" \
    "pawbot/dashboard/server.py" \
    "pawbot/cron/service.py" \
    "pawbot/heartbeat/engine.py" \
    "pawbot/agent/memory.py"; do
    
    RAW=$(grep -c "\.write_text\|open.*['\"]w['\"]" "$f" 2>/dev/null || echo 0)
    ATOMIC=$(grep -c "atomic_write\|write_json_with_backup" "$f" 2>/dev/null || echo 0)
    
    if [ "$RAW" -eq 0 ] && [ "$ATOMIC" -gt 0 ]; then
        echo "✅  $f  (atomic writes: $ATOMIC, raw writes: 0)"
    elif [ "$RAW" -gt 0 ]; then
        echo "❌  $f  (raw writes still present: $RAW)"
        grep -n "\.write_text\|open.*['\"]w['\"]" "$f" | head -5
    else
        echo "⚠️  $f  (no write operations found — verify manually)"
    fi
done
```

### 1.8 Safe JSON read verification

```bash
echo ""
echo "=== SAFE JSON READ VERIFICATION ==="
cd ~/pawbot

echo "--- Files that should use safe_read_json ---"
for f in \
    "pawbot/config/loader.py" \
    "pawbot/cron/service.py" \
    "pawbot/heartbeat/engine.py"; do
    
    SAFE=$(grep -c "safe_read_json" "$f" 2>/dev/null || echo 0)
    RAW=$(grep -c "json\.loads\|json\.load(" "$f" 2>/dev/null || echo 0)
    
    echo "  $f  |  safe_read_json: $SAFE  |  raw json.load: $RAW"
    [ "$SAFE" -gt 0 ] && echo "  ✅ has safe reads" || echo "  ❌ missing safe_read_json"
done
```

### 1.9 Retry logic verification

```bash
echo ""
echo "=== RETRY LOGIC VERIFICATION ==="
cd ~/pawbot

echo "--- providers/router.py ---"
grep -n "call_with_retry\|_validate_key\|is_placeholder" pawbot/providers/router.py 2>/dev/null

RETRY_COUNT=$(grep -c "call_with_retry" pawbot/providers/router.py 2>/dev/null || echo 0)
VALIDATE_COUNT=$(grep -c "_validate_key\|is_placeholder" pawbot/providers/router.py 2>/dev/null || echo 0)

[ "$RETRY_COUNT" -ge 3 ] && echo "✅  call_with_retry: $RETRY_COUNT call(s)" || echo "❌  call_with_retry: only $RETRY_COUNT (need 3)"
[ "$VALIDATE_COUNT" -ge 1 ] && echo "✅  placeholder detection present" || echo "❌  placeholder detection missing"
```

### 1.10 Print statement check

```bash
echo ""
echo "=== PRINT STATEMENT CHECK ==="
cd ~/pawbot

echo "--- print() calls in non-CLI, non-test Python files ---"
grep -rn "^\s*print(" pawbot/ --include="*.py" | grep -v "cli/\|test_\|#" | head -20
PRINT_COUNT=$(grep -rn "^\s*print(" pawbot/ --include="*.py" | grep -v "cli/\|test_\|#" | wc -l | tr -d ' ')
[ "$PRINT_COUNT" -eq 0 ] && echo "✅  No bare print() in library code" || echo "⚠️  $PRINT_COUNT print() calls in library code (review each)"

echo ""
echo "--- config/loader.py specifically ---"
grep -n "print(" pawbot/config/loader.py 2>/dev/null && echo "(found)" || echo "✅  No print() in config/loader.py"
```

---

## PHASE 2 — IMPORT VERIFICATION

All utility modules and the new errors module must import without errors.

```bash
echo ""
echo "=== IMPORT VERIFICATION ==="
cd ~/pawbot

modules=(
    "from pawbot.errors import PawbotError, ConfigError, ProviderError"
    "from pawbot.utils.paths import PAWBOT_HOME, CONFIG_PATH, LOGS_PATH"
    "from pawbot.utils.secrets import mask_secret, is_placeholder"
    "from pawbot.utils.fs import atomic_write_json, atomic_write_text, safe_read_json, write_json_with_backup"
    "from pawbot.utils.retry import call_with_retry"
    "from pawbot.utils.logging_setup import setup_logging"
    "from pawbot.config.loader import load_config, validate_config"
    "from pawbot.providers.router import ModelRouter"
    "from pawbot.cli.commands import app"
)

for stmt in "${modules[@]}"; do
    python3 -c "$stmt; print('✅  $stmt')" 2>&1
done
```

---

## PHASE 3 — UNIT TESTS

```bash
echo ""
echo "=== UNIT TEST SUITE ==="
cd ~/pawbot

# Install test deps if missing
pip install pytest pytest-asyncio pytest-cov --quiet 2>&1 | tail -2

# Run full suite with verbose output
pytest tests/ -v --tb=short 2>&1 | tee /tmp/pawbot_test_results.txt

echo ""
echo "--- Test Summary ---"
tail -20 /tmp/pawbot_test_results.txt

TEST_EXIT=$?
[ $TEST_EXIT -eq 0 ] && echo "✅  All tests passed" || echo "❌  Tests failed (exit $TEST_EXIT)"
```

---

## PHASE 4 — FUNCTIONAL UNIT TESTS (run in isolation)

These verify specific utility functions work correctly, independent of the test suite.

```bash
echo ""
echo "=== FUNCTIONAL UNIT TESTS ==="
cd ~/pawbot

# Test 1: atomic_write_json — file created and valid
python3 -c "
import json, tempfile, os
from pathlib import Path
from pawbot.utils.fs import atomic_write_json

with tempfile.TemporaryDirectory() as d:
    target = Path(d) / 'test.json'
    atomic_write_json(target, {'result': 'ok', 'n': 42})
    data = json.loads(target.read_text())
    assert data == {'result': 'ok', 'n': 42}, f'Wrong data: {data}'
    # Confirm no .tmp files left
    tmp_files = list(Path(d).glob('*.tmp'))
    assert not tmp_files, f'Temp files left: {tmp_files}'
    print('✅  atomic_write_json: creates file, no temp files left')
"

# Test 2: atomic_write_json — no temp file on failure
python3 -c "
import tempfile, os
from pathlib import Path
from unittest.mock import patch
from pawbot.utils.fs import atomic_write_json

with tempfile.TemporaryDirectory() as d:
    with patch('os.replace', side_effect=OSError('simulated disk full')):
        try:
            atomic_write_json(Path(d) / 'test.json', {})
            print('❌  Should have raised OSError')
        except OSError:
            pass
    leftover = list(Path(d).glob('*.tmp'))
    assert not leftover, f'Temp files not cleaned up: {leftover}'
    print('✅  atomic_write_json: no temp file left on failure')
"

# Test 3: safe_read_json — corrupted file returns default
python3 -c "
import tempfile
from pathlib import Path
from pawbot.utils.fs import safe_read_json

with tempfile.TemporaryDirectory() as d:
    f = Path(d) / 'data.json'
    f.write_text('{{CORRUPTED JSON GARBAGE')
    result = safe_read_json(f, default={'recovered': True})
    assert result == {'recovered': True}, f'Got: {result}'
    print('✅  safe_read_json: returns default for corrupted file')
"

# Test 4: safe_read_json — backup recovery
python3 -c "
import tempfile, json
from pathlib import Path
from pawbot.utils.fs import safe_read_json

with tempfile.TemporaryDirectory() as d:
    f = Path(d) / 'data.json'
    bak = f.with_suffix('.json.bak')
    f.write_text('{{CORRUPTED')
    bak.write_text(json.dumps({'from_backup': True}))
    result = safe_read_json(f, default={})
    assert result == {'from_backup': True}, f'Got: {result}'
    # Main file should be restored
    assert f.exists(), 'Main file not restored'
    print('✅  safe_read_json: recovers from .bak file')
"

# Test 5: is_placeholder — known placeholders
python3 -c "
from pawbot.utils.secrets import is_placeholder
placeholders = ['sk-or-v1-xxx', 'YOUR_API_KEY', 'REPLACE_ME', 'xxx', '', None]
real_keys    = ['sk-or-v1-abc123def456ghi789012', 'sk-ant-api03-abc123xyz', 'BSA-realkey123456']
for v in placeholders:
    assert is_placeholder(v), f'Should be placeholder: {v!r}'
for v in real_keys:
    assert not is_placeholder(v), f'Should NOT be placeholder: {v!r}'
print('✅  is_placeholder: correctly identifies placeholders and real keys')
"

# Test 6: mask_secret — hides key body
python3 -c "
from pawbot.utils.secrets import mask_secret
key = 'sk-or-v1-abc123def456ghi789'
masked = mask_secret(key)
assert 'abc123def456ghi789' not in masked, f'Key body leaked: {masked}'
assert '••' in masked, f'No masking applied: {masked}'
print(f'✅  mask_secret: {key[:12]}... → {masked}')
"

# Test 7: call_with_retry — retries on 429
python3 -c "
from pawbot.utils.retry import call_with_retry
attempts = [0]
def flaky():
    attempts[0] += 1
    if attempts[0] < 3:
        raise Exception('429 Too Many Requests — rate limited')
    return 'success'
result = call_with_retry(flaky, max_retries=3, base_delay=0.001)
assert result == 'success', f'Got: {result}'
assert attempts[0] == 3, f'Expected 3 attempts, got {attempts[0]}'
print(f'✅  call_with_retry: succeeded on attempt {attempts[0]} after 429 errors')
"

# Test 8: call_with_retry — raises ConfigError on 401
python3 -c "
from pawbot.utils.retry import call_with_retry
from pawbot.errors import ConfigError
try:
    call_with_retry(
        lambda: (_ for _ in ()).throw(Exception('401 Unauthorized — invalid key')),
        max_retries=3, base_delay=0.001
    )
    print('❌  Should have raised ConfigError')
except ConfigError as e:
    print(f'✅  call_with_retry: raises ConfigError on 401 (not retry)')
"

# Test 9: call_with_retry — raises after exhausting retries
python3 -c "
from pawbot.utils.retry import call_with_retry
try:
    call_with_retry(
        lambda: (_ for _ in ()).throw(Exception('503 Service Unavailable')),
        max_retries=2, base_delay=0.001
    )
    print('❌  Should have raised after max retries')
except Exception as e:
    assert '503' in str(e), f'Wrong exception: {e}'
    print('✅  call_with_retry: raises after max_retries exhausted')
"

# Test 10: thread safety of atomic_write_json
python3 -c "
import json, threading, tempfile
from pathlib import Path
from pawbot.utils.fs import atomic_write_json

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
        print(f'❌  Thread safety: {len(errors)} errors: {errors[0]}')
    else:
        data = json.loads(target.read_text())
        assert 'writer' in data
        print(f'✅  Thread safety: 30 concurrent writes, no errors, valid JSON')
"
```

---

## PHASE 5 — INSTALL VERIFICATION

```bash
echo ""
echo "=== INSTALL VERIFICATION ==="
cd ~/pawbot

# 1. pip install works
echo "--- pip install -e . ---"
pip install -e . --quiet 2>&1 | tail -5
PIP_EXIT=$?
[ $PIP_EXIT -eq 0 ] && echo "✅  pip install exits 0" || echo "❌  pip install failed (exit $PIP_EXIT)"

# 2. Entry point registered
echo ""
echo "--- CLI entry point ---"
which pawbot 2>/dev/null && echo "✅  pawbot binary found at: $(which pawbot)" || echo "❌  pawbot command not found in PATH"
pawbot --version 2>&1
[ $? -eq 0 ] && echo "✅  pawbot --version exits 0" || echo "❌  pawbot --version failed"

# 3. install/setup.sh syntax
echo ""
echo "--- install/setup.sh ---"
bash -n ~/pawbot/install/setup.sh 2>&1 && echo "✅  setup.sh: valid bash syntax" || echo "❌  setup.sh: bash syntax error"
head -3 ~/pawbot/install/setup.sh
```

---

## PHASE 6 — END-TO-END COMMAND TESTS

Each command must exit with the correct code and produce sensible output.

```bash
echo ""
echo "=== END-TO-END COMMAND TESTS ==="

e2e() {
    local name="$1"
    local cmd="$2"
    local want_exit="$3"
    local grep_pattern="$4"

    output=$(eval "$cmd" 2>&1)
    actual_exit=$?

    exit_ok=true
    [ "$actual_exit" != "$want_exit" ] && exit_ok=false

    grep_ok=true
    if [ -n "$grep_pattern" ]; then
        echo "$output" | grep -qi "$grep_pattern" || grep_ok=false
    fi

    if $exit_ok && $grep_ok; then
        echo "✅  $name  (exit $actual_exit)"
    else
        echo "❌  $name  (exit $actual_exit, want $want_exit)"
        echo "    Output: ${output:0:200}"
    fi
}

# Clean slate
rm -rf ~/.pawbot

# Core lifecycle
e2e "onboard creates workspace"      "pawbot onboard"               "0" ""
e2e "config.json exists after onboard" "test -f ~/.pawbot/config.json && echo ok" "0" "ok"
e2e "workspace dir exists"           "test -d ~/.pawbot/workspace && echo ok" "0" "ok"

# Doctor — should exit 1 because no real API key yet
e2e "doctor exits 1 with no real key" "pawbot doctor"               "1" ""

# Status
e2e "status exits 0"                 "pawbot status"                "0" ""

# Cron CRUD
e2e "cron list (empty)"              "pawbot cron list"             "0" ""
e2e "cron add"                       "pawbot cron add --name cert-test --message 'hello' --every 999999" "0" ""
e2e "cron list (has job)"            "pawbot cron list"             "0" "cert-test"
e2e "cron remove"                    "pawbot cron remove cert-test" "0" ""

# Skills
e2e "skills list"                    "pawbot skills list"           "0" ""

# Memory
e2e "memory stats"                   "pawbot memory stats"          "0" ""

# Guided setup (non-interactive — should not hang)
e2e "onboard silent mode"            "echo '' | pawbot onboard"     "0" ""

# Dashboard reachability (start in bg, test, kill)
echo ""
echo "--- Dashboard health check ---"
timeout 8 pawbot dashboard --no-browser --port 4999 &
DASH_PID=$!
sleep 3

HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:4999/api/health 2>/dev/null)
if [ "$HEALTH" = "200" ]; then
    echo "✅  Dashboard /api/health returns HTTP 200"
else
    echo "❌  Dashboard /api/health returned HTTP $HEALTH (expected 200)"
fi

CTYPE=$(curl -sI http://localhost:4999/install 2>/dev/null | grep -i content-type | tr -d '\r')
echo "    Content-Type at /api/health: $(curl -sI http://localhost:4999/api/health 2>/dev/null | grep -i content-type | tr -d '\r')"

kill $DASH_PID 2>/dev/null
wait $DASH_PID 2>/dev/null
```

---

## PHASE 7 — CORRUPTION RECOVERY TESTS

These tests verify that the atomic write + safe read + backup recovery chain actually works under real failure conditions.

```bash
echo ""
echo "=== CORRUPTION RECOVERY TESTS ==="

# Test 1: Corrupted config.json — pawbot should not crash
echo "--- Test: corrupted config.json ---"
cp ~/.pawbot/config.json ~/.pawbot/config.json.safe 2>/dev/null
echo "{{CORRUPTED JSON" > ~/.pawbot/config.json

pawbot status 2>&1
EXIT=$?
echo "pawbot status with corrupted config: exit $EXIT"
[ $EXIT -le 1 ] && echo "✅  No unhandled crash on corrupted config" || echo "❌  Crash detected (exit $EXIT)"

# Was backup recovery attempted?
if [ -f ~/.pawbot/config.json ] && python3 -c "import json; json.load(open('$HOME/.pawbot/config.json'))" 2>/dev/null; then
    echo "✅  Config was recovered to valid JSON"
else
    echo "⚠️  Config may still be corrupted (acceptable if backup recovery is not implemented for this path)"
fi

# Restore
cp ~/.pawbot/config.json.safe ~/.pawbot/config.json 2>/dev/null
rm -f ~/.pawbot/config.json.safe

# Test 2: Corrupted crons.json — pawbot cron list should not crash
echo ""
echo "--- Test: corrupted crons.json ---"
echo "{{CORRUPTED" > ~/.pawbot/crons.json
OUTPUT=$(pawbot cron list 2>&1)
EXIT=$?
echo "pawbot cron list with corrupted crons.json: exit $EXIT"
[ $EXIT -eq 0 ] && echo "✅  cron list recovers from corrupted crons.json" || echo "❌  cron list crashed (exit $EXIT)"
echo "Output: ${OUTPUT:0:200}"
rm -f ~/.pawbot/crons.json

# Test 3: Atomic write leaves no temp files
echo ""
echo "--- Test: no .tmp files left in ~/.pawbot ---"
pawbot cron add --name temp-test --message "test" --every 99999 2>/dev/null
pawbot cron remove temp-test 2>/dev/null
TMP_FILES=$(find ~/.pawbot -name "*.tmp" 2>/dev/null | wc -l | tr -d ' ')
[ "$TMP_FILES" -eq 0 ] && echo "✅  No .tmp files in ~/.pawbot" || echo "❌  Found $TMP_FILES .tmp files"
find ~/.pawbot -name "*.tmp" 2>/dev/null
```

---

## PHASE 8 — SECURITY SPOT CHECKS

```bash
echo ""
echo "=== SECURITY SPOT CHECKS ==="
cd ~/pawbot

# Check 1: API keys don't appear in log output
echo "--- API key masking in logs ---"
OUTPUT=$(pawbot status 2>&1)
if echo "$OUTPUT" | grep -qE "sk-or-v1-[a-z0-9]{10,}|sk-ant-[a-z0-9]{10,}"; then
    echo "❌  API key visible in status output"
    echo "$OUTPUT" | grep -E "sk-"
else
    echo "✅  No raw API keys in status output"
fi

# Check 2: Dashboard binds to 127.0.0.1, not 0.0.0.0
echo ""
echo "--- Dashboard host binding ---"
grep -n "host\|bind\|0\.0\.0\.0" pawbot/dashboard/server.py 2>/dev/null | head -10

BINDS_LOCALHOST=$(grep -c "127\.0\.0\.1\|localhost" pawbot/dashboard/server.py 2>/dev/null)
BINDS_ALL=$(grep -c "0\.0\.0\.0" pawbot/dashboard/server.py 2>/dev/null)
echo "  References to 127.0.0.1/localhost: $BINDS_LOCALHOST"
echo "  References to 0.0.0.0: $BINDS_ALL"
[ "$BINDS_LOCALHOST" -gt 0 ] && echo "✅  Dashboard defaults to localhost" || echo "⚠️  No explicit localhost binding found"

# Check 3: Config API masks keys
echo ""
echo "--- Config API masking ---"
timeout 5 pawbot dashboard --no-browser --port 4998 &
DPID=$!
sleep 3
RESPONSE=$(curl -s http://localhost:4998/api/config 2>/dev/null)
kill $DPID 2>/dev/null
wait $DPID 2>/dev/null

if echo "$RESPONSE" | grep -qE "sk-or-v1-[a-zA-Z0-9]{15,}"; then
    echo "❌  API key exposed in /api/config response"
else
    echo "✅  API keys masked in /api/config"
fi

# Check 4: Log file doesn't contain raw keys
echo ""
echo "--- Log file key check ---"
LOG="$HOME/.pawbot/logs/pawbot.log"
if [ -f "$LOG" ]; then
    if grep -qE "sk-or-v1-[a-z0-9]{10,}|sk-ant-[a-z0-9]{10,}" "$LOG" 2>/dev/null; then
        echo "❌  Raw API key found in log file"
        grep -E "sk-or-|sk-ant-" "$LOG" | head -3
    else
        echo "✅  No raw API keys in log file"
    fi
else
    echo "⚠️  Log file not created yet (acceptable on first run)"
fi
```

---

## PHASE 9 — COVERAGE REPORT

```bash
echo ""
echo "=== TEST COVERAGE ==="
cd ~/pawbot

pytest tests/ \
    --cov=pawbot \
    --cov-report=term-missing \
    --cov-report=html:htmlcov \
    --cov-fail-under=70 \
    -q 2>&1 | tee /tmp/pawbot_coverage.txt

tail -20 /tmp/pawbot_coverage.txt
COVE_EXIT=$?
[ $COVE_EXIT -eq 0 ] && echo "✅  Coverage ≥ 70%" || echo "⚠️  Coverage below 70% threshold"
```

---

## PHASE 10 — GENERATE CERTIFICATION REPORT

After running all phases, compile the results into the final report.

**Create:** `~/pawbot/CERTIFICATION_REPORT.md`

```markdown
# Pawbot Production Certification Report

**Date:** YYYY-MM-DD  
**Certified by:** AI Agent — Final Verification Pass  
**Codebase:** ~/pawbot/  
**Version:** 1.0.0  

---

## Certification Decision

<!-- Fill in: CERTIFIED ✅  or  CERTIFICATION WITHHELD ❌ -->

> **[DECISION]**  
> Reason: [one sentence]

---

## Phase 1 — Structural Checks

| Check | Result | Detail |
|-------|--------|--------|
| `pyproject.toml` exists | ✅/❌ | |
| `pyproject.toml` valid TOML | ✅/❌ | |
| CLI entry point registered | ✅/❌ | |
| No bare `except:` | ✅/❌ | count: N |
| No silent `except Exception:` | ✅/❌ | count: N |
| All threads daemon | ✅/❌ | count: N |
| All `subprocess.run` have timeout | ✅/❌ | count: N |
| No nanobot references | ✅/❌ | count: N |
| Atomic writes applied | ✅/❌ | files: N |
| safe_read_json applied | ✅/❌ | files: N |
| Retry logic in router.py | ✅/❌ | methods: N |
| Placeholder detection | ✅/❌ | |
| print() removed from loader.py | ✅/❌ | |

## Phase 2 — Import Verification

| Module | Result |
|--------|--------|
| `pawbot.errors` | ✅/❌ |
| `pawbot.utils.paths` | ✅/❌ |
| `pawbot.utils.secrets` | ✅/❌ |
| `pawbot.utils.fs` | ✅/❌ |
| `pawbot.utils.retry` | ✅/❌ |
| `pawbot.utils.logging_setup` | ✅/❌ |
| `pawbot.config.loader` | ✅/❌ |
| `pawbot.providers.router` | ✅/❌ |
| `pawbot.cli.commands` | ✅/❌ |

## Phase 3 — Unit Tests

- Tests run: N
- Tests passed: N
- Tests failed: N  
- Test failures: (list if any)

## Phase 4 — Functional Unit Tests

| Test | Result |
|------|--------|
| atomic_write_json creates file | ✅/❌ |
| atomic_write_json no temp on failure | ✅/❌ |
| safe_read_json returns default on corruption | ✅/❌ |
| safe_read_json recovers from backup | ✅/❌ |
| is_placeholder detects all placeholders | ✅/❌ |
| mask_secret hides key body | ✅/❌ |
| call_with_retry retries on 429 | ✅/❌ |
| call_with_retry raises ConfigError on 401 | ✅/❌ |
| call_with_retry raises after max retries | ✅/❌ |
| Thread-safe concurrent writes | ✅/❌ |

## Phase 5 — Install Verification

| Check | Result |
|-------|--------|
| `pip install -e .` exits 0 | ✅/❌ |
| `pawbot` binary in PATH | ✅/❌ |
| `pawbot --version` exits 0 | ✅/❌ |
| `setup.sh` valid bash syntax | ✅/❌ |

## Phase 6 — E2E Commands

| Command | Expected Exit | Actual | Result |
|---------|:---:|:---:|--------|
| `pawbot onboard` | 0 | | ✅/❌ |
| config.json created | — | | ✅/❌ |
| workspace/ created | — | | ✅/❌ |
| `pawbot doctor` (no key) | 1 | | ✅/❌ |
| `pawbot status` | 0 | | ✅/❌ |
| `pawbot cron list` | 0 | | ✅/❌ |
| `pawbot cron add` | 0 | | ✅/❌ |
| `pawbot cron remove` | 0 | | ✅/❌ |
| `pawbot skills list` | 0 | | ✅/❌ |
| `pawbot memory stats` | 0 | | ✅/❌ |
| Dashboard `/api/health` | HTTP 200 | | ✅/❌ |

## Phase 7 — Corruption Recovery

| Scenario | Result | Notes |
|----------|--------|-------|
| Corrupted config.json — no crash | ✅/❌ | |
| Corrupted crons.json — no crash | ✅/❌ | |
| No .tmp files after operations | ✅/❌ | |

## Phase 8 — Security

| Check | Result |
|-------|--------|
| No raw keys in status output | ✅/❌ |
| Dashboard binds to localhost | ✅/❌ |
| `/api/config` masks keys | ✅/❌ |
| Log file has no raw keys | ✅/❌ |

## Phase 9 — Coverage

- Total coverage: N%
- Core modules (config/, utils/, cli/): N%

---

## Remaining Issues (if any)

<!-- List anything that failed, with recommended fix -->

| Issue | Severity | Fix Required |
|-------|----------|-------------|
| | | |

---

## Sign-off

**Certification status:** CERTIFIED / WITHHELD  
**Blocking issues:** N  
**Non-blocking notes:** N  
**Recommended action:** Release / Fix N issues then re-certify
```

---

## SCORING — CERTIFICATION CRITERIA

Count the results from all phases:

| Threshold | Decision |
|-----------|----------|
| 0 ❌ results, 0 test failures | **CERTIFIED — ready for release** |
| 1–3 ❌ in non-critical checks (import warnings, coverage gaps) | **CONDITIONALLY CERTIFIED — minor fixes before release** |
| Any ❌ in: bare except, subprocess timeout, pyproject.toml, CLI entry point, E2E crashes, security | **CERTIFICATION WITHHELD — blocking issues must be fixed** |

**Blocking issues (any ❌ here = not ready):**

- Bare `except:` found
- `subprocess.run` without `timeout=` found  
- `pyproject.toml` missing or invalid
- `pawbot` command not found after `pip install -e .`
- Any E2E command exits with a Python traceback
- Raw API keys visible in log output or API responses
- Any unit test failure in `test_utils.py` or `test_security.py`

---

## RULES

1. **Never fix anything during this pass** — only verify and report. If you find an issue, add it to the report. Do not patch it inline.
2. **Record exact output** — copy the actual terminal output into the report. Do not summarise or interpret.
3. **Run every phase** — even if Phase 1 shows issues, continue through all 10 phases to get the complete picture.
4. **A partial pass is not a pass** — every item in the Definition of Done must be individually verified.

---

## DEFINITION OF DONE

You are finished when:

- [ ] All 10 phases have been run and outputs collected
- [ ] `CERTIFICATION_REPORT.md` is written with real results (no placeholder cells)
- [ ] Every ✅/❌ in the report corresponds to actual test output
- [ ] The certification decision (CERTIFIED / WITHHELD) is clearly stated with reasoning
- [ ] If WITHHELD: every blocking issue is listed with the exact command that surfaced it and the exact fix required
