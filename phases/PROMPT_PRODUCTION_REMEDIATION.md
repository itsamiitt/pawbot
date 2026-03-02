# PROMPT — PAWBOT PRODUCTION REMEDIATION
## Built from Audit Report · 2026-03-02 · 9 of 14 checks failing

You are a senior Python engineer. You have the exact audit report for the Pawbot codebase. 92 Python files were audited. 9 of 14 production checks failed. Your job is to fix every failing check, in the priority order defined below, verify each fix before moving to the next, and produce a ✅ on every line of the Definition of Done.

**Read this entire file and the audit report before writing a single line of code.**

---

## CURRENT STATE — WHAT PASSES, WHAT DOESN'T

### Already passing — do NOT touch these
| Check | Status |
|-------|--------|
| Bare `except:` clauses | ✅ 0 found |
| Non-daemon threads | ✅ all 11 threads have `daemon=True` |
| Missing `mkdir` flags | ✅ all 23 calls correct |
| Hardcoded / nanobot paths | ✅ 0 in Python files |
| Dashboard CORS / binding | ✅ locked to localhost |
| WhatsApp Node.js check | ✅ implemented in dashboard |

### Failing — your entire job
| Priority | Check | Exact scope |
|----------|-------|-------------|
| 🔴 1 | Missing `pyproject.toml` | Does not exist |
| 🔴 2 | Non-atomic file writes | 13 exact locations |
| 🟠 3 | JSON corruption handling | 30+ reads, 5 critical locations |
| 🟠 4 | Missing retry on API calls | `providers/router.py` — 3 methods |
| 🟡 5 | Silent exception swallowing | 51 exact locations across 18 files |
| 🟡 6 | Missing subprocess timeouts | 3 exact lines: `cli/commands.py:891,894,924` |
| 🟡 7 | Placeholder API key detection | `providers/router.py` |
| 🟢 8 | Missing utility modules | 6 files to create |
| 🟢 9 | `print()` in `config/loader.py` | Lines 39–40 |

---

## STEP 0 — READ THE CODE BEFORE TOUCHING IT

Run every command below. Do not skip any. Write down the line numbers.

```bash
# Confirm pyproject.toml is missing
ls ~/pawbot/pyproject.toml 2>/dev/null || echo "CONFIRMED MISSING"

# Confirm the 3 subprocess calls without timeout
grep -n "subprocess.run\|subprocess.Popen" ~/pawbot/pawbot/cli/commands.py | grep -v "#"

# Confirm the 13 non-atomic writes — one per file
grep -n "write_text\|open.*['\"]w['\"]" ~/pawbot/pawbot/config/loader.py
grep -n "write_text\|open.*['\"]w['\"]" ~/pawbot/pawbot/dashboard/server.py
grep -n "write_text\|open.*['\"]w['\"]" ~/pawbot/pawbot/cron/service.py
grep -n "write_text\|open.*['\"]w['\"]" ~/pawbot/pawbot/cron/scheduler.py
grep -n "write_text\|open.*['\"]w['\"]" ~/pawbot/pawbot/heartbeat/engine.py
grep -n "write_text\|open.*['\"]w['\"]" ~/pawbot/pawbot/agent/skills.py
grep -n "write_text\|open.*['\"]w['\"]" ~/pawbot/pawbot/agent/memory.py
grep -n "write_text\|open.*['\"]w['\"]" ~/pawbot/pawbot/agent/tools/filesystem.py
grep -n "write_text\|open.*['\"]w['\"]" ~/pawbot/pawbot/channels/mochat.py

# Get full list of 51 silent exceptions with line numbers
grep -rn "except Exception:" ~/pawbot/pawbot --include="*.py" | grep -v " as e" > /tmp/silent_excepts.txt
wc -l /tmp/silent_excepts.txt
cat /tmp/silent_excepts.txt

# Read the provider router to understand its structure
cat ~/pawbot/pawbot/providers/router.py

# Confirm the 2 print() lines
grep -n "print(" ~/pawbot/pawbot/config/loader.py

# See what utils/ already contains
ls ~/pawbot/pawbot/utils/
```

Do not proceed until you have run all of the above.

---

## FIX 1 — CREATE `pyproject.toml` 🔴 CRITICAL

Without this file, `pip install pawbot-ai` fails, the `pawbot` CLI command doesn't register, and nothing else matters. Fix this first.

**Discover the actual third-party imports before writing:**

```bash
python3 -c "
import ast, pathlib, sys
stdlib = sys.stdlib_module_names
found = set()
for f in pathlib.Path('~/pawbot/pawbot').expanduser().rglob('*.py'):
    try:
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                name = node.names[0].name.split('.')[0]
            elif isinstance(node, ast.ImportFrom) and node.module:
                name = node.module.split('.')[0]
            else:
                continue
            if name and name not in stdlib and name != 'pawbot' and not name.startswith('_'):
                found.add(name)
    except Exception:
        pass
for pkg in sorted(found): print(pkg)
"
```

Cross-reference the output with the confirmed list below, then create:

**File:** `~/pawbot/pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "pawbot-ai"
version = "1.0.0"
description = "🐾 Pawbot — Ultra-Lightweight Personal AI Assistant"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.11"
authors = [{ name = "Pawbot Team", email = "hello@thecloso.com" }]
keywords = ["ai", "assistant", "agent", "llm", "chatbot", "pawbot"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Environment :: Console",
]

dependencies = [
    # LLM providers
    "anthropic>=0.25.0",
    "openai>=1.20.0",
    "httpx>=0.27.0",
    # CLI
    "typer>=0.9.0",
    "rich>=13.7.0",
    # Config validation
    "pydantic>=2.0.0",
    # Memory backends
    "redis>=5.0.0",
    "chromadb>=0.5.0",
    # Scheduling
    "croniter>=2.0.0",
    # Utilities
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
desktop = [
    "pyautogui>=0.9.54",
    "pillow>=10.3.0",
    "pytesseract>=0.3.10",
    "pynput>=1.7.6",
    "pyperclip>=1.8.2",
]
channels = [
    "faster-whisper>=1.0.0",
]
lora = [
    "axolotl>=0.4.0",
]
dashboard = [
    "fastapi>=0.110.0",
    "uvicorn>=0.29.0",
]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=5.0.0",
]
all = [
    "pawbot-ai[desktop,channels,lora,dashboard]",
]

[project.scripts]
pawbot = "pawbot.cli.commands:app"

[project.urls]
Homepage   = "https://pawbot.thecloso.com"
Repository = "https://github.com/YOUR_ORG/pawbot"
Issues     = "https://github.com/YOUR_ORG/pawbot/issues"

[tool.hatch.build.targets.wheel]
packages = ["pawbot"]

[tool.pytest.ini_options]
testpaths    = ["tests"]
asyncio_mode = "auto"
markers      = ["integration: marks tests requiring external services"]
```

**Immediately verify — all 4 must pass before proceeding:**

```bash
# 1. Valid TOML
python3 -c "import tomllib; tomllib.load(open('pyproject.toml','rb')); print('TOML valid ✓')"

# 2. Installs cleanly
pip install -e ~/pawbot --quiet
echo "pip install exit: $?"

# 3. Entry point works
pawbot --version
echo "Exit: $?"

# 4. Core import works
python3 -c "from pawbot.cli.commands import app; print('import ok ✓')"
```

---

## FIX 2 — CREATE 6 UTILITY MODULES 🟢 LOW → needed by all other fixes

Create all 6 before applying them anywhere.

### `~/pawbot/pawbot/utils/paths.py`

```python
"""Centralised path constants. Import from here — never construct ~/.pawbot paths inline."""
from pathlib import Path

PAWBOT_HOME    = Path.home() / ".pawbot"
CONFIG_PATH    = PAWBOT_HOME / "config.json"
WORKSPACE_PATH = PAWBOT_HOME / "workspace"
LOGS_PATH      = PAWBOT_HOME / "logs"
SKILLS_PATH    = PAWBOT_HOME / "skills"
CRONS_PATH     = PAWBOT_HOME / "crons.json"
HEARTBEAT_PATH = PAWBOT_HOME / "heartbeat_triggers.json"
TRAINING_PATH  = PAWBOT_HOME / "training"
MODELS_PATH    = PAWBOT_HOME / "models"
SESSION_PATH   = PAWBOT_HOME / "sessions"
```

### `~/pawbot/pawbot/utils/secrets.py`

```python
"""Secret masking. Use before logging anything that looks like a key."""

_PLACEHOLDERS = frozenset({
    "sk-or-v1-xxx", "YOUR_API_KEY", "REPLACE_ME", "xxx",
    "your-key-here", "sk-or-xxx", "BSA-xxx",
    "YOUR_BOT_TOKEN", "YOUR_USER_ID", "PLACEHOLDER",
})


def mask_secret(value: str, show_chars: int = 8) -> str:
    """Return first `show_chars` chars + bullets. Safe to log."""
    if not value or len(value) <= show_chars:
        return "••••••••"
    return value[:show_chars] + "••••••••"


def is_placeholder(value: str) -> bool:
    """Return True if value is a known placeholder, not a real secret."""
    if not value or not value.strip():
        return True
    v = value.strip()
    return v in _PLACEHOLDERS or any(p in v for p in _PLACEHOLDERS)
```

### `~/pawbot/pawbot/utils/fs.py`

```python
"""
Filesystem utilities with atomicity guarantees.
All persistent state MUST be written through these functions.
"""
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("pawbot.utils.fs")


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    """
    Write JSON atomically: write to temp file, then os.replace().
    If process dies mid-write, original file is untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_str = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def atomic_write_text(path: Path, content: str) -> None:
    """Atomic text file write — same guarantee as atomic_write_json."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_str = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def write_json_with_backup(path: Path, data: Any, indent: int = 2) -> None:
    """
    Atomically write JSON and keep a .bak copy of the previous version.
    Use for critical files: config.json, crons.json, heartbeat triggers, memory.
    """
    path = Path(path)
    bak = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        try:
            bak.write_bytes(path.read_bytes())
        except Exception as e:
            logger.warning(f"Could not create backup {bak}: {e}")
    atomic_write_json(path, data, indent=indent)


def safe_read_json(path: Path, default: Any = None, backup: bool = True) -> Any:
    """
    Read JSON with corruption recovery.

    On JSONDecodeError:
      1. Try .bak recovery (if backup=True and .bak exists).
      2. Return `default` if recovery fails or no backup.
      3. Re-raise if default is None.
    On FileNotFoundError: return `default`.
    """
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted JSON in {path}: {e}")
        if backup:
            bak = path.with_suffix(path.suffix + ".bak")
            if bak.exists():
                logger.warning(f"Attempting recovery from {bak}")
                try:
                    data = json.loads(bak.read_text(encoding="utf-8"))
                    atomic_write_json(path, data)
                    logger.info(f"Recovered {path} from backup")
                    return data
                except Exception as bak_err:
                    logger.error(f"Backup recovery failed: {bak_err}")
        if default is not None:
            logger.warning(f"Returning default for {path}")
            return default
        raise
```

### `~/pawbot/pawbot/utils/retry.py`

```python
"""Retry with exponential backoff for external API calls."""
import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger("pawbot.utils.retry")
T = TypeVar("T")


def call_with_retry(
    fn: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> T:
    """
    Call fn() with exponential backoff on transient failures.

    Retries on:    429 rate limit, 5xx server errors, network errors.
    Never retries: 401 unauthorized (raises ConfigError), 400 bad request.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            err = str(e).lower()
            if "401" in err or "unauthorized" in err or "authentication" in err:
                from pawbot.errors import ConfigError
                raise ConfigError(
                    "API key is invalid or expired.\n"
                    "Update: ~/.pawbot/config.json\n"
                    "Or run: pawbot onboard --setup"
                ) from e
            if "400" in err and "rate" not in err:
                raise
            is_transient = (
                "429" in err or "rate limit" in err or "too many" in err
                or any(f"{c}" in err for c in [500, 502, 503, 504])
                or "connection" in err or "timeout" in err or "network" in err
            )
            if is_transient and attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                kind = "rate limited" if "429" in err or "rate" in err else "server/network error"
                logger.warning(f"API {kind} — retrying in {delay:.1f}s (attempt {attempt+1}/{max_retries})")
                time.sleep(delay)
                last_error = e
                continue
            raise
    raise last_error
```

### `~/pawbot/pawbot/utils/logging.py`

```python
"""Centralised logging setup. Call setup_logging() once per CLI entry point."""
import logging
import sys
from pathlib import Path


def setup_logging(level: str = "WARNING") -> None:
    """
    Configure Pawbot logging.
    - stderr: shows `level` and above (default WARNING — quiet in normal use)
    - file:   always writes DEBUG+ to ~/.pawbot/logs/pawbot.log
    """
    from pawbot.utils.paths import LOGS_PATH
    numeric = getattr(logging, level.upper(), logging.WARNING)
    LOGS_PATH.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    # stderr
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(numeric)
    sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(sh)
    # file
    try:
        fh = logging.FileHandler(LOGS_PATH / "pawbot.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        root.addHandler(fh)
    except OSError as e:
        logging.getLogger("pawbot.utils.logging").warning(f"Could not open log file: {e}")
    for lib in ["httpx", "httpcore", "anthropic", "openai", "urllib3", "chromadb", "redis", "uvicorn", "fastapi"]:
        logging.getLogger(lib).setLevel(logging.WARNING)
```

### `~/pawbot/pawbot/utils/__init__.py` (if missing)

```python
# Pawbot utility modules
```

**Verify all 6 import cleanly:**

```bash
python3 -c "
from pawbot.utils.paths   import PAWBOT_HOME, CONFIG_PATH
from pawbot.utils.secrets import mask_secret, is_placeholder
from pawbot.utils.fs      import atomic_write_json, safe_read_json, write_json_with_backup, atomic_write_text
from pawbot.utils.retry   import call_with_retry
from pawbot.utils.logging import setup_logging
print('All 6 utility modules ✓')
print(f'  PAWBOT_HOME = {PAWBOT_HOME}')
print(f'  mask_secret = {mask_secret(\"sk-or-v1-abc123456789\")}')
print(f'  is_placeholder(\"sk-or-v1-xxx\") = {is_placeholder(\"sk-or-v1-xxx\")}')
"
```

---

## FIX 3 — APPLY `write_json_with_backup` TO 13 WRITE LOCATIONS 🔴 CRITICAL

Read the current code at each line before modifying. The line numbers below are from the audit report — confirm them with `grep -n` first.

**Template for every JSON write:**

```python
# BEFORE (any variant of):
with open(path, "w") as f:
    json.dump(data, f, indent=2)
# or:
path.write_text(json.dumps(data, indent=2))

# AFTER — for critical state files (config, memory, cron, heartbeat):
from pawbot.utils.fs import write_json_with_backup
write_json_with_backup(path, data)

# AFTER — for lower-risk files (skills, training, logs):
from pawbot.utils.fs import atomic_write_json
atomic_write_json(path, data)

# AFTER — for text files (SOUL.md):
from pawbot.utils.fs import atomic_write_text
atomic_write_text(path, content)
```

**Apply to each location, in this order:**

| Severity | File | Line | Function to use |
|----------|------|:----:|----------------|
| 🔴 CRITICAL | `config/loader.py` | 64 | `write_json_with_backup` |
| 🔴 CRITICAL | `dashboard/server.py` | 68 | `write_json_with_backup` |
| 🔴 CRITICAL | `agent/memory.py` | 1491 | `write_json_with_backup` |
| 🟠 HIGH | `cron/service.py` | 172 | `write_json_with_backup` |
| 🟠 HIGH | `heartbeat/engine.py` | 352 | `write_json_with_backup` |
| 🟡 MED | `cron/scheduler.py` | 239 | `atomic_write_json` |
| 🟡 MED | `agent/skills.py` | 249 | `atomic_write_json` |
| 🟡 MED | `agent/skills.py` | 686 | `atomic_write_json` |
| 🟡 MED | `agent/skills.py` | 739 | `atomic_write_json` |
| 🟡 MED | `dashboard/server.py` | 283 | `atomic_write_text` (SOUL.md) |
| 🟢 LOW | `agent/tools/filesystem.py` | 95 | `atomic_write_text` or `atomic_write_json` |
| 🟢 LOW | `agent/tools/filesystem.py` | 147 | `atomic_write_text` or `atomic_write_json` |
| 🟢 LOW | `channels/mochat.py` | 850 | `atomic_write_json` |

**Verify after all 13 are done:**

```bash
# These files must now have zero raw JSON writes
for f in config/loader.py dashboard/server.py agent/memory.py cron/service.py heartbeat/engine.py; do
    COUNT=$(grep -c "write_text\|open.*['\"]w['\"]" ~/pawbot/pawbot/$f 2>/dev/null || echo 0)
    echo "$f: $COUNT raw writes remaining"
done
```

---

## FIX 4 — APPLY `safe_read_json` TO 5 CRITICAL READ PATHS 🟠 HIGH

The audit found 30+ unprotected reads. Fix these 5 — they hold irreplaceable state.

**For each, read the exact current code first:**

```bash
sed -n '30,45p'  ~/pawbot/pawbot/config/loader.py
sed -n '85,100p' ~/pawbot/pawbot/cron/service.py
sed -n '355,370p' ~/pawbot/pawbot/heartbeat/engine.py
sed -n '170,185p' ~/pawbot/pawbot/agent/skills.py
grep -n "json\.loads\|json\.load" ~/pawbot/pawbot/agent/memory.py | head -5
```

**Apply `safe_read_json()` at each location:**

```python
# config/loader.py:35 — the audit notes it wraps JSONDecodeError but uses print() not logging
# BEFORE (approximate — read actual code first):
try:
    with open(config_path) as f:
        return json.load(f)
except json.JSONDecodeError as e:
    print(f"Warning: ...")
    return {}

# AFTER:
from pawbot.utils.fs import safe_read_json
cfg = safe_read_json(config_path, default={})
if not cfg:
    logger.warning("config.json is missing or empty. Run: pawbot onboard")
return cfg
```

```python
# cron/service.py:90
from pawbot.utils.fs import safe_read_json
jobs = safe_read_json(cron_file, default=[])
```

```python
# heartbeat/engine.py:359
from pawbot.utils.fs import safe_read_json
triggers = safe_read_json(trigger_path, default=[])
```

```python
# agent/skills.py:176
from pawbot.utils.fs import safe_read_json
skill_data = safe_read_json(skill_file, default={})
```

```python
# agent/memory.py — apply to top 3 locations that read from disk files
from pawbot.utils.fs import safe_read_json
data = safe_read_json(memory_file, default={})
```

**Verify corruption recovery works:**

```bash
# Simulate corrupted crons.json
echo "{{CORRUPTED" > ~/.pawbot/crons.json
pawbot cron list
echo "EXIT: $?"  # must be 0 — no Python traceback

# Restore
rm ~/.pawbot/crons.json
```

---

## FIX 5 — ADD RETRY LOGIC TO API PROVIDER CALLS 🟠 HIGH

**File:** `~/pawbot/pawbot/providers/router.py`

Read the full file first:

```bash
cat ~/pawbot/pawbot/providers/router.py
```

Find the three provider call methods. They will be named something like `_call_openrouter()`, `_call_anthropic()`, `_call_openai()`. Read the exact method names.

**Add to the router class:**

```python
from pawbot.utils.retry   import call_with_retry
from pawbot.utils.secrets import is_placeholder

def _validate_key(self, provider: str, key: str) -> None:
    """Raise ConfigError before making any network call if key is missing or placeholder."""
    if not key or is_placeholder(key):
        from pawbot.errors import ConfigError
        raise ConfigError(
            f"No valid API key for '{provider}'.\n"
            f"Run: pawbot onboard --setup\n"
            f"Or edit: ~/.pawbot/config.json → providers.{provider}.apiKey"
        )
```

**Wrap each provider call method:**

```python
# Example — apply same pattern to all three methods:
def _call_openrouter(self, messages: list, model: str, **kwargs):
    self._validate_key("openrouter", self.openrouter_key)
    return call_with_retry(
        fn=lambda: self.openrouter_client.chat.completions.create(
            model=model,
            messages=messages,
            **kwargs
        ),
        max_retries=3,
        base_delay=1.0,
    )
```

**Verify retry logic end-to-end:**

```bash
python3 -c "
from pawbot.utils.retry import call_with_retry
attempts = [0]
def flaky():
    attempts[0] += 1
    if attempts[0] < 3:
        raise Exception('429 Too Many Requests')
    return 'success'
result = call_with_retry(flaky, max_retries=3, base_delay=0.001)
assert result == 'success' and attempts[0] == 3
print(f'Retry logic works ✓ — succeeded on attempt {attempts[0]}')
"
```

---

## FIX 6 — FIX 51 SILENT EXCEPTION BLOCKS 🟡 MEDIUM

Get the full list with exact locations:

```bash
grep -rn "except Exception:" ~/pawbot/pawbot --include="*.py" | grep -v " as e" | sort > /tmp/silent.txt
cat /tmp/silent.txt
```

Work file by file in this priority order (matches audit findings):

**Rule for all 51:** Add `as e` and at minimum `logger.error()` or `logger.warning()`. Never remove the `except`. Never change control flow — only add logging.

```python
# EVERY fix follows this pattern — adapt the message to the context:
# BEFORE:
except Exception:
    pass  # or: return None  or: result = default

# AFTER:
except Exception as e:
    logger.error(f"[describe what failed and where]: {e}")
    # keep the original control flow unchanged
```

**Work through files in this order:**

| File | Count | Log level to use |
|------|:-----:|-----------------|
| `channels/matrix.py` | 11 | `logger.error` — connection failures must be visible |
| `channels/email.py` | 4 | `logger.error` — send/receive failures invisible otherwise |
| `channels/mochat.py` | 3 | `logger.error` |
| `channels/slack.py` | 1 | `logger.error` |
| `channels/qq.py` | 2 | `logger.error` |
| `channels/feishu.py` | 1 | `logger.warning` |
| `providers/openai_codex_provider.py` | 2 | `logger.warning` — streaming parse errors |
| `dashboard/server.py` | 7 | `logger.debug` — helper defaults |
| `cli/commands.py` | 6 | `logger.warning` — optional feature failures |
| `cron/service.py` | 2 | `logger.warning` |
| `cron/scheduler.py` | 1 | `logger.warning` |
| `heartbeat/service.py` | 2 | `logger.warning` |
| `heartbeat/engine.py` | 1 | `logger.debug` |
| `providers/ollama.py` | 1 | `logger.warning` |
| `session/manager.py` | 2 | `logger.warning` |
| `utils/helpers.py` | 1 | `logger.debug` |
| `agent/skills.py` | 3 | `logger.warning` |
| `agent/telemetry.py` | 1 | `logger.debug` |
| `agent/tools/shell.py` | 1 | `logger.warning` |

Make sure each file has `import logging` and `logger = logging.getLogger("pawbot.<module>")` at the top before adding logger calls.

**Verify after all 51 are done:**

```bash
COUNT=$(grep -rn "except Exception:" ~/pawbot/pawbot --include="*.py" | grep -v " as e" | wc -l)
echo "Remaining silent exceptions: $COUNT"
[ "$COUNT" -eq 0 ] && echo "✅ PASS" || echo "❌ FAIL — $COUNT remaining"
```

---

## FIX 7 — ADD SUBPROCESS TIMEOUTS TO 3 LINES 🟡 MEDIUM

**File:** `~/pawbot/pawbot/cli/commands.py`
**Exact lines from audit:** 891, 894, 924

Read the actual code first:

```bash
sed -n '885,930p' ~/pawbot/pawbot/cli/commands.py
```

Apply fixes:

```python
# Line ~891 — npm install (one-shot, wait for it)
# BEFORE: subprocess.run(["npm", "install"], cwd=bridge_dir)
# AFTER:
try:
    result = subprocess.run(
        ["npm", "install"], cwd=bridge_dir,
        capture_output=True, text=True,
        timeout=300  # 5 min — first install can be slow
    )
    if result.returncode != 0:
        logger.error(f"npm install failed:\n{result.stderr}")
        raise RuntimeError(f"npm install failed: {result.stderr[:200]}")
except subprocess.TimeoutExpired:
    raise RuntimeError("npm install timed out after 5 minutes")

# Line ~894 — npm run build (one-shot)
# BEFORE: subprocess.run(["npm", "run", "build"], cwd=bridge_dir)
# AFTER:
try:
    result = subprocess.run(
        ["npm", "run", "build"], cwd=bridge_dir,
        capture_output=True, text=True,
        timeout=120  # 2 min
    )
    if result.returncode != 0:
        logger.error(f"npm build failed:\n{result.stderr}")
        raise RuntimeError(f"npm build failed: {result.stderr[:200]}")
except subprocess.TimeoutExpired:
    raise RuntimeError("npm build timed out after 2 minutes")

# Line ~924 — npm start (long-running daemon)
# For Popen (long-running), timeout does not apply — but ensure it has pipes:
proc = subprocess.Popen(
    ["npm", "start"], cwd=bridge_dir,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
# Store proc so it can be terminated on stop()
```

**Verify:**

```bash
grep -n "subprocess\.run(" ~/pawbot/pawbot/cli/commands.py
# Every subprocess.run line must now show timeout=
```

---

## FIX 8 — PLACEHOLDER API KEY DETECTION 🟡 MEDIUM

This was partially addressed in Fix 5 with `_validate_key()`. Confirm it covers all paths:

```bash
grep -n "is_placeholder\|_validate_key\|if not api_key\|if not key" ~/pawbot/pawbot/providers/router.py
```

The fallback chain (`openrouter → anthropic → openai → ollama`) must call `_validate_key()` on each provider it tries — not just the first. If any provider has a placeholder key and is selected as the active one, the error must surface before a network call is made:

```python
def _get_provider_and_key(self, model: str) -> tuple[str, str]:
    """Returns (provider_name, api_key) or raises ConfigError."""
    from pawbot.utils.secrets import is_placeholder
    for name, pcfg in self.cfg.get("providers", {}).items():
        key = pcfg.get("apiKey", "")
        if key and not is_placeholder(key):
            return name, key
    from pawbot.errors import ConfigError
    raise ConfigError(
        "No valid API key found in any configured provider.\n"
        "Run: pawbot onboard --setup"
    )
```

---

## FIX 9 — REPLACE `print()` IN `config/loader.py:39-40` 🟢 LOW

Read the lines first:

```bash
sed -n '35,45p' ~/pawbot/pawbot/config/loader.py
```

Replace both `print()` calls:

```python
# Add at top of config/loader.py if not present:
import logging
logger = logging.getLogger("pawbot.config")

# Replace line ~39:
# print("Warning: config.json not found, using defaults")
logger.warning("config.json not found — using defaults. Run: pawbot onboard")

# Replace line ~40:
# print(f"Warning: config.json has invalid JSON: {e}")
logger.error(f"config.json has invalid JSON: {e}. Returning defaults.")
```

---

## STEP 10 — CREATE TEST SUITE

**Create:** `~/pawbot/tests/`

### `tests/__init__.py`
```python
```

### `tests/conftest.py`
```python
import json, pytest
from pathlib import Path

@pytest.fixture
def tmp_pawbot_home(tmp_path):
    home = tmp_path / ".pawbot"
    (home / "workspace").mkdir(parents=True)
    (home / "logs").mkdir(parents=True)
    (home / "skills").mkdir(parents=True)
    return home

@pytest.fixture
def valid_config_path(tmp_pawbot_home):
    p = tmp_pawbot_home / "config.json"
    p.write_text(json.dumps({
        "providers": {"openrouter": {"apiKey": "sk-or-v1-realkey1234567890abc"}},
        "agents": {"defaults": {"model": "anthropic/claude-sonnet-4-5"}}
    }, indent=2))
    return p
```

### `tests/test_utils.py`
```python
import json, threading, pytest
from pathlib import Path


class TestAtomicWriteJson:
    def test_creates_file(self, tmp_path):
        from pawbot.utils.fs import atomic_write_json
        t = tmp_path / "out.json"
        atomic_write_json(t, {"k": "v"})
        assert json.loads(t.read_text()) == {"k": "v"}

    def test_creates_parent_dirs(self, tmp_path):
        from pawbot.utils.fs import atomic_write_json
        atomic_write_json(tmp_path / "a" / "b" / "c.json", {})
        assert (tmp_path / "a" / "b" / "c.json").exists()

    def test_no_tmp_file_on_success(self, tmp_path):
        from pawbot.utils.fs import atomic_write_json
        atomic_write_json(tmp_path / "f.json", {})
        assert not list(tmp_path.glob("*.tmp"))

    def test_no_tmp_file_on_failure(self, tmp_path):
        from pawbot.utils.fs import atomic_write_json
        from unittest.mock import patch
        with patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                atomic_write_json(tmp_path / "f.json", {})
        assert not list(tmp_path.glob("*.tmp"))

    def test_concurrent_writes_valid(self, tmp_path):
        from pawbot.utils.fs import atomic_write_json
        errors = []
        def write(i):
            try:
                atomic_write_json(tmp_path / "shared.json", {"i": i})
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=write, args=(i,)) for i in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert not errors
        assert "i" in json.loads((tmp_path / "shared.json").read_text())


class TestSafeReadJson:
    def test_reads_valid(self, tmp_path):
        from pawbot.utils.fs import safe_read_json
        f = tmp_path / "d.json"
        f.write_text('{"x": 1}')
        assert safe_read_json(f) == {"x": 1}

    def test_returns_default_missing(self, tmp_path):
        from pawbot.utils.fs import safe_read_json
        assert safe_read_json(tmp_path / "no.json", default=[]) == []

    def test_returns_default_corrupted(self, tmp_path):
        from pawbot.utils.fs import safe_read_json
        (tmp_path / "d.json").write_text("{{bad")
        assert safe_read_json(tmp_path / "d.json", default={"ok": True}) == {"ok": True}

    def test_recovers_from_backup(self, tmp_path):
        from pawbot.utils.fs import safe_read_json
        f = tmp_path / "d.json"
        f.write_text("{{bad")
        f.with_suffix(".json.bak").write_text('{"from_bak": true}')
        assert safe_read_json(f, default={}) == {"from_bak": True}


class TestSecrets:
    @pytest.mark.parametrize("v", ["sk-or-v1-xxx", "YOUR_API_KEY", "xxx", "", None])
    def test_placeholder_detected(self, v):
        from pawbot.utils.secrets import is_placeholder
        assert is_placeholder(v) is True

    @pytest.mark.parametrize("v", ["sk-or-v1-abc123def456ghi789", "sk-ant-api03-abc123xyz"])
    def test_real_key_passes(self, v):
        from pawbot.utils.secrets import is_placeholder
        assert is_placeholder(v) is False

    def test_mask_hides_tail(self):
        from pawbot.utils.secrets import mask_secret
        r = mask_secret("sk-or-v1-abc123def456")
        assert "abc123def456" not in r and "••" in r


class TestRetry:
    def test_succeeds_immediately(self):
        from pawbot.utils.retry import call_with_retry
        assert call_with_retry(lambda: "ok") == "ok"

    def test_retries_on_429(self):
        from pawbot.utils.retry import call_with_retry
        calls = [0]
        def f():
            calls[0] += 1
            if calls[0] < 3: raise Exception("429 Too Many Requests")
            return "done"
        assert call_with_retry(f, max_retries=3, base_delay=0.001) == "done"
        assert calls[0] == 3

    def test_401_raises_config_error(self):
        from pawbot.utils.retry import call_with_retry
        from pawbot.errors import ConfigError
        with pytest.raises(ConfigError):
            call_with_retry(
                lambda: (_ for _ in ()).throw(Exception("401 Unauthorized")),
                base_delay=0.001
            )

    def test_exhausted_retries_raises(self):
        from pawbot.utils.retry import call_with_retry
        with pytest.raises(Exception, match="503"):
            call_with_retry(
                lambda: (_ for _ in ()).throw(Exception("503 Service Unavailable")),
                max_retries=2, base_delay=0.001
            )
```

### `tests/test_cli.py`
```python
import json, pytest
from typer.testing import CliRunner
from unittest.mock import patch

runner = CliRunner()

def test_version():
    from pawbot.cli.commands import app
    assert runner.invoke(app, ["--version"]).exit_code == 0

def test_onboard_creates_workspace(tmp_path):
    with patch("pawbot.utils.paths.PAWBOT_HOME", tmp_path):
        from pawbot.cli.commands import app
        r = runner.invoke(app, ["onboard"])
    assert r.exit_code == 0
    assert (tmp_path / "config.json").exists()
    assert (tmp_path / "workspace").is_dir()

def test_onboard_no_overwrite(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"existing": true}')
    with patch("pawbot.utils.paths.PAWBOT_HOME", tmp_path):
        from pawbot.cli.commands import app
        runner.invoke(app, ["onboard"])
    assert "existing" in cfg.read_text()

def test_status_exits_zero():
    from pawbot.cli.commands import app
    assert runner.invoke(app, ["status"]).exit_code == 0

def test_cron_list_exits_zero():
    from pawbot.cli.commands import app
    assert runner.invoke(app, ["cron", "list"]).exit_code == 0

def test_skills_list_exits_zero():
    from pawbot.cli.commands import app
    assert runner.invoke(app, ["skills", "list"]).exit_code == 0

def test_doctor_exits_1_on_placeholder_key(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "providers": {"openrouter": {"apiKey": "sk-or-v1-xxx"}},
        "agents": {"defaults": {"model": "test"}}
    }))
    with patch("pawbot.utils.paths.CONFIG_PATH", cfg):
        from pawbot.cli.commands import app
        assert runner.invoke(app, ["doctor"]).exit_code == 1
```

### `tests/test_security.py`
```python
def test_blocks_rm_rf():
    from pawbot.agent.security import ActionGate
    allowed, _ = ActionGate({}).check("server_run", {"command": "rm -rf /"}, "test")
    assert not allowed

def test_allows_safe_tool():
    from pawbot.agent.security import ActionGate
    allowed, _ = ActionGate({}).check("server_read_file", {"path": "/tmp/x.txt"}, "test")
    assert allowed

def test_catches_prompt_injection():
    from pawbot.agent.security import InjectionDetector
    injected, _ = InjectionDetector().scan("ignore previous instructions")
    assert injected

def test_passes_clean_text():
    from pawbot.agent.security import InjectionDetector
    injected, _ = InjectionDetector().scan("What is the weather in Mumbai?")
    assert not injected
```

### `tests/test_install.py`
```python
import os, subprocess, pytest

@pytest.mark.integration
def test_setup_sh_valid_bash():
    r = subprocess.run(["bash", "-n", "install/setup.sh"],
                       cwd=os.path.expanduser("~/pawbot"),
                       capture_output=True, text=True)
    assert r.returncode == 0, f"Bash syntax:\n{r.stderr}"

@pytest.mark.integration
def test_pyproject_valid():
    import tomllib
    with open(os.path.expanduser("~/pawbot/pyproject.toml"), "rb") as f:
        d = tomllib.load(f)
    assert d["project"]["name"] == "pawbot-ai"
    assert "pawbot" in d["project"]["scripts"]

@pytest.mark.integration
def test_no_nanobot_in_py():
    r = subprocess.run(["grep", "-rn", "nanobot", "pawbot/", "--include=*.py"],
                       cwd=os.path.expanduser("~/pawbot"),
                       capture_output=True, text=True)
    assert not r.stdout.strip(), f"Found nanobot refs:\n{r.stdout}"
```

**Run the suite:**

```bash
cd ~/pawbot
pip install pytest pytest-asyncio pytest-cov --quiet
pytest tests/ -v --tb=short 2>&1 | tee ~/pawbot/test_results.txt
echo "pytest exit: $?"
```

All tests must pass. If any fail, fix the source code — never the test.

---

## STEP 11 — FINAL VERIFICATION

```bash
cd ~/pawbot

echo "==============================="
echo "  PAWBOT PRODUCTION CHECKLIST  "
echo "==============================="

chk() {
    local label="$1"
    local cmd="$2"
    local want="$3"
    got=$(eval "$cmd" 2>&1 | tr -d ' \n')
    [ "$got" = "$want" ] && echo "✅  $label" || echo "❌  $label  (got: $got, want: $want)"
}

chk "No bare except"               "grep -rn 'except:' pawbot/ --include='*.py' | wc -l"        "0"
chk "No silent except"             "grep -rn 'except Exception:' pawbot/ --include='*.py' | grep -v ' as e' | wc -l" "0"
chk "No nanobot in .py files"      "grep -rn 'nanobot' pawbot/ --include='*.py' | wc -l"         "0"
chk "All threads daemon"           "grep -rn 'Thread(' pawbot/ --include='*.py' | grep -v 'daemon=True' | wc -l" "0"
chk "All subprocess.run timeout"   "grep -rn 'subprocess\.run(' pawbot/ --include='*.py' | grep -v 'timeout=' | wc -l" "0"
chk "pyproject.toml valid TOML"    "python3 -c \"import tomllib,builtins; tomllib.load(builtins.open('pyproject.toml','rb')); print(0)\"" "0"
chk "pawbot CLI works"             "pawbot --version > /dev/null 2>&1; echo \$?"                 "0"
chk "paths.py importable"          "python3 -c 'from pawbot.utils.paths import CONFIG_PATH; print(0)'"  "0"
chk "secrets.py importable"        "python3 -c 'from pawbot.utils.secrets import mask_secret; print(0)'"  "0"
chk "fs.py importable"             "python3 -c 'from pawbot.utils.fs import atomic_write_json; print(0)'"  "0"
chk "retry.py importable"          "python3 -c 'from pawbot.utils.retry import call_with_retry; print(0)'"  "0"
chk "logging.py importable"        "python3 -c 'from pawbot.utils.logging import setup_logging; print(0)'"  "0"

echo ""
echo "--- E2E ---"
rm -rf ~/.pawbot
pawbot onboard > /dev/null 2>&1
chk "onboard creates config"       "[ -f ~/.pawbot/config.json ] && echo 0 || echo 1"            "0"
chk "onboard creates workspace"    "[ -d ~/.pawbot/workspace ] && echo 0 || echo 1"              "0"
chk "doctor exits 1 on bad key"    "pawbot doctor > /dev/null 2>&1; echo \$?"                    "1"
chk "status exits 0"               "pawbot status > /dev/null 2>&1; echo \$?"                    "0"
chk "cron list exits 0"            "pawbot cron list > /dev/null 2>&1; echo \$?"                 "0"

echo ""
echo "--- TESTS ---"
pytest tests/ -q 2>&1 | tail -3

echo ""
echo "==============================="
```

**Every line must show ✅ before you are done.**

---

## RULES

1. **Work in order** — Fixes 1 through 9 in sequence. Never skip ahead.
2. **Read before editing** — run `grep -n` or `sed -n` on each file before modifying it.
3. **Verify each fix** — run the confirmation command after each fix.
4. **Never touch passing checks** — do not modify code listed under "Already passing."
5. **Never edit tests to make them pass** — fix the source code instead.
6. **Silent exception fixes are additive only** — add `as e` + `logger.x()`. Never remove `except` or alter control flow.
7. **`pyproject.toml` uses minimum pins** — `>=X.Y.Z` not `==X.Y.Z`.

---

## DEFINITION OF DONE — 15 CHECKS, ALL MUST PASS

- [ ] `pyproject.toml` exists and `tomllib.load()` succeeds
- [ ] `pip install -e .` exits 0 and `pawbot --version` prints a version
- [ ] `grep -rn "except:" pawbot/` → 0 results
- [ ] `grep -rn "except Exception:" pawbot/ | grep -v " as e"` → 0 results
- [ ] `grep -rn "nanobot" pawbot/ --include="*.py"` → 0 results
- [ ] All `threading.Thread()` calls have `daemon=True`
- [ ] All `subprocess.run()` calls have `timeout=`
- [ ] All 13 write locations use `atomic_write_json()`, `atomic_write_text()`, or `write_json_with_backup()`
- [ ] All 5 critical JSON read locations use `safe_read_json()`
- [ ] All 3 provider call methods wrapped in `call_with_retry()`
- [ ] `is_placeholder()` called on all provider paths before network requests
- [ ] `pawbot/utils/` contains: `paths.py`, `secrets.py`, `fs.py`, `retry.py`, `logging.py`
- [ ] `pytest tests/ -v` → 0 failures
- [ ] `pawbot onboard` exits 0 and creates `~/.pawbot/config.json` and `workspace/`
- [ ] `pawbot doctor` exits 1 when API key is a placeholder
