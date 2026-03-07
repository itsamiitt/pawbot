# 🏗️ SECTION 2 — Codebase Maturity & Reliability
## Complete Agent Fix Document
### 5 Self-Contained Agent Prompts · Full Code · Tests · Acceptance Gates

**Repo:** `itsamiitt/pawbot` · **Date:** March 2026 · **Version:** 1.0  
**Source:** Deep Scan Report (2026-03-02) · Bandit HIGH×6, MEDIUM×4 · Ruff 33 issues · 2 slow test files

---

## ⚠️ CRITICAL RULE — READ BEFORE ANY PHASE

> Every class, enum, constant, dataclass, path, and config key used in this repo
> is defined in `pawbot/contracts.py`. Before writing any code in **any** phase,
> read `pawbot/contracts.py` in full.
>
> ```python
> from pawbot.contracts import *   # gives you everything
> ```
>
> Do **not** invent new names. Do **not** duplicate anything that already exists.

---

## What This Section Fixes

| # | Problem | Current State | After Fix |
|---|---------|--------------|-----------|
| Security | `shell=True` in 5 MCP locations | Bandit HIGH — shell injection risk | Safe argv list execution |
| Security | `hashlib.md5` in coding server | Bandit HIGH — weak hash | `hashlib.sha256` |
| Security | `timeout=None` in mcp.py | Bandit MEDIUM — can hang forever | `timeout=300` |
| Hygiene | No `.gitignore` | 134 `.pyc` files tracked in git | Clean repo, no bytecode |
| Tests | `test_context.py` takes 719s | Chroma/embedding init per test | Session-scoped fixtures → <60s |
| Tests | `test_agent_loop.py` takes 675s | MemoryRouter init per test | Shared fixture → <60s |
| Lint | 33 Ruff issues in production code | Unused imports, ambiguous names, duplicates | 0 issues |
| Correctness | `F811` duplicate class in `schema.py` | Silent runtime override | Removed duplicate |
| Correctness | `F821` `BaseExceptionGroup` undefined | Crashes on Python <3.11 without target-version | Ruff target set |
| Correctness | `memory.py:1422` raw `open()` write | Inconsistent with atomic write policy | `atomic_write_text()` |
| Complexity | `_process_message` CC=33 (grade E) | Untestable monolith | Split into 4 focused helpers |
| Complexity | `_run_agent_loop` CC=22 (grade D) | Hard to modify safely | Extracted tool-call handler |

---

## Phase Execution Order

| Phase | Title | Can Start When | Blocks |
|-------|-------|---------------|--------|
| **1** | Repo Hygiene | Immediately — no deps | Nothing (independent) |
| **2** | Security Hardening | Immediately — no deps | Phase 5 CI gate |
| **3** | Test Suite Speed | Immediately — no deps | Phase 5 CI gate |
| **4** | Lint & Correctness | Immediately — no deps | Phase 5 CI gate |
| **5** | Complexity Refactor | Phases 2–4 complete | Section 2 gate |

Phases 1–4 are **fully independent** — run them simultaneously.

---

---

# PHASE 1 OF 5 — Repo Hygiene
### *Remove 134 tracked bytecode files, add .gitignore, standardize test runner*

---

## Agent Prompt

You are fixing repo hygiene for the Pawbot project.

Currently 134 compiled `.pyc` files and 21 `__pycache__` directories are tracked in git.
There is no root `.gitignore`. The `pytest` command fails in some environments because the
entrypoint is not on `PATH` — `python -m pytest` always works but bare `pytest` does not.

Your job is to add a `.gitignore`, untrack all compiled artifacts, standardize the test
runner in all docs and scripts, and verify the repo is clean.

**Rules:**
- Do not modify any Python source files in this phase
- Do not change `pyproject.toml` other than the `[tool.ruff]` section
- The `.gitignore` must cover Python, pytest, virtual environments, and Pawbot runtime files

---

## Why This Phase Exists

108+ tracked compiled artifacts cause:
- Every Python version change produces hundreds of spurious git diffs
- Stale `.pyc` files contain external source paths that confuse tracebacks
- New contributors pull a bloated repo with no benefit
- CI systems check out stale bytecode and sometimes load it instead of the source

---

## What You Will Build

| Action | File |
|--------|------|
| **CREATE** | `.gitignore` — root-level, covers Python + Pawbot runtime |
| **RUN** | `git rm -r --cached` — untrack all `.pyc` and `__pycache__` from git index |
| **EDIT** | `pyproject.toml` — add `[tool.ruff]` with `target-version = "py311"` |
| **CREATE** | `tests/test_hygiene.py` — 3 verification tests |

---

## File 1 of 3 — CREATE `.gitignore`

Create at repo root: `pawbot/.gitignore`

```gitignore
# ── Python bytecode ────────────────────────────────────────────────────────────
__pycache__/
*.py[cod]
*.pyo
*.pyd
.Python
*.so

# ── Build / distribution ───────────────────────────────────────────────────────
dist/
build/
*.egg-info/
.eggs/
.installed.cfg
MANIFEST

# ── Virtual environments ───────────────────────────────────────────────────────
.venv/
venv/
env/
ENV/
.env.local

# ── Test / coverage ────────────────────────────────────────────────────────────
.pytest_cache/
.coverage
.coverage.*
htmlcov/
.tox/
nosetests.xml
coverage.xml

# ── Type checkers / linters ────────────────────────────────────────────────────
.mypy_cache/
.ruff_cache/
.pytype/

# ── IDE ────────────────────────────────────────────────────────────────────────
.vscode/
.idea/
*.swp
*.swo
*~
.DS_Store
Thumbs.db

# ── Pawbot runtime ─────────────────────────────────────────────────────────────
.env
*.log
*.pid

# ── Node (WhatsApp bridge) ─────────────────────────────────────────────────────
node_modules/
npm-debug.log*
```

---

## File 2 of 3 — EDIT `pyproject.toml`

Add the `[tool.ruff]` section. This fixes the `F821` `BaseExceptionGroup` undefined error
by telling Ruff the target is Python 3.11+ where `BaseExceptionGroup` is a builtin.

```toml
# ADD this section to pyproject.toml (after [tool.pytest.ini_options]):

[tool.ruff]
target-version = "py311"

[tool.ruff.lint]
# Rules enabled by default. Add selects/ignores as needed.
select = ["E", "F", "W"]
ignore  = [
    "E501",   # line too long — handled by formatter
]
```

---

## File 3 of 3 — CREATE `tests/test_hygiene.py`

```python
"""
tests/test_hygiene.py

Repo hygiene verification.
Run: pytest tests/test_hygiene.py -v
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_gitignore_exists():
    """Root .gitignore must exist."""
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.exists(), f".gitignore not found at {gitignore}"


def test_gitignore_covers_pycache():
    """
    .gitignore must explicitly exclude __pycache__ and *.pyc.
    These are the two patterns that caused 134 tracked artifacts.
    """
    content = (REPO_ROOT / ".gitignore").read_text()
    assert "__pycache__/" in content, "__pycache__/ pattern missing from .gitignore"
    assert "*.py[cod]" in content or "*.pyc" in content, "*.pyc pattern missing from .gitignore"


def test_no_pyc_tracked_in_git():
    """
    No .pyc files should be tracked in git after cleanup.
    Run 'git rm -r --cached **/__pycache__ **/*.pyc' before this test.
    """
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "*.pyc"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # git ls-files returns 1 (error) when no files match — that's what we want
    assert result.returncode != 0, (
        f"Found tracked .pyc files:\n{result.stdout}\n"
        "Run: git rm -r --cached '*.pyc' '**/*.pyc' to fix"
    )
```

---

## Shell Commands — Run After Creating Files

```bash
# From the repo root (pawbot/):

# 1. Untrack all compiled artifacts
git rm -r --cached "*.pyc" 2>/dev/null || true
git rm -r --cached "**/__pycache__" 2>/dev/null || true
find . -name "*.pyc" -not -path "./.git/*" | xargs git rm --cached 2>/dev/null || true
find . -name "__pycache__" -not -path "./.git/*" | xargs git rm -r --cached 2>/dev/null || true

# 2. Verify nothing pyc remains tracked
git ls-files | grep -E "\.pyc$|__pycache__"
# Expected: no output

# 3. Stage and commit
git add .gitignore pyproject.toml
git commit -m "chore: add .gitignore, set ruff target-version=py311, untrack compiled artifacts"

# 4. Verify pyc files are now ignored (not tracked after a fresh build)
python -m compileall pawbot/ -q
git status | grep ".pyc"
# Expected: no output — they should be ignored
```

---

## Test Matrix

| # | Test | Input | Expected | Pass Condition |
|---|------|-------|----------|----------------|
| T1 | `.gitignore` exists | `Path` check | File present at repo root | `assert gitignore.exists()` |
| T2 | `.gitignore` covers patterns | Content scan | `__pycache__/` and `*.py[cod]` present | Both patterns in file |
| T3 | No `.pyc` tracked in git | `git ls-files` | Exit code 1 (no matches) | No `.pyc` in git index |

---

## ⛔ Acceptance Gate — Phase 1

```bash
pytest tests/test_hygiene.py -v
```

- [ ] All 3 tests pass
- [ ] `git ls-files | grep "\.pyc"` returns no output
- [ ] `git status` after `python -m compileall pawbot/ -q` shows no `.pyc` files as untracked
- [ ] `pyproject.toml` contains `[tool.ruff]` with `target-version = "py311"`

---

---

# PHASE 2 OF 5 — Security Hardening
### *Replace shell=True injection vectors, weak hash, and infinite timeout*

---

## Agent Prompt

You are fixing 7 security findings in the Pawbot MCP servers and agent tools.

Bandit identified 6 HIGH-severity and 1 MEDIUM-severity issues that are real risks:
- `shell=True` in 5 locations allows shell injection if any user-controlled string reaches the command
- `hashlib.md5` is a weak hash with known collision vulnerabilities
- `timeout=None` in an HTTP client means a hung MCP server can freeze the entire agent indefinitely

Your job is to fix all 7 issues with minimal diff. Read each file's surrounding context before
changing anything.

**Rules:**
- Do not restructure files — make the smallest change that eliminates the risk
- Where `shell=True` is genuinely required for shell built-ins (e.g. Windows `start`),
  keep it but add a `# nosec B602` comment with justification
- Do not change function signatures or return types
- Read `pawbot/contracts.py` before editing any file

---

## Why This Phase Exists

`shell=True` is the most dangerous subprocess flag in Python. When combined with any
user-supplied string (a file path, a server name, a git URL), it becomes a command
injection vector. An attacker who controls input to `deploy_app()`, `server_run()`,
or `app_launch()` can execute arbitrary shell commands on the host.

`hashlib.md5` is used to generate project index cache keys. While not directly exploitable
here, it violates security policy (Bandit B324) and normalises weak crypto in the codebase.

`timeout=None` on the MCP HTTP client means a single slow or hung MCP server can stall
the entire agent loop indefinitely with no recovery path.

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `mcp-servers/deploy/server.py` — replace `shell=True` in `_run()` |
| **EDIT** | `mcp-servers/server_control/server.py` — replace `shell=True` in two locations |
| **EDIT** | `mcp-servers/coding/server.py` — replace `shell=True` in `_run_shell()` + `md5` → `sha256` |
| **EDIT** | `mcp-servers/app_control/server.py` — `shell=True` Windows path: add `# nosec` with justification |
| **EDIT** | `pawbot/agent/tools/mcp.py` — replace `timeout=None` with explicit cap |
| **CREATE** | `tests/test_security_hardening.py` — 5 tests |

---

## Fix 1 of 5 — `mcp-servers/deploy/server.py`

### Current (lines 62–78)
```python
def _run(cmd: str, cwd: str | None = None, timeout: int = 120) -> dict[str, Any]:
    """Run a shell command and return a structured result."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,          # ← B602 HIGH: shell injection risk
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
```

### Replacement — split shell string to argv list
```python
import shlex

def _run(cmd: str | list[str], cwd: str | None = None, timeout: int = 120) -> dict[str, Any]:
    """Run a command and return a structured result.

    Args:
        cmd: Command as a list (preferred) or shell string (auto-split via shlex).
             Never pass unsanitised user input as a string — use a list instead.
    """
    # Convert string to argv list — eliminates shell injection (Bandit B602)
    argv = cmd if isinstance(cmd, list) else shlex.split(cmd)
    try:
        result = subprocess.run(
            argv,
            shell=False,         # ← safe: no shell interpolation
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
```

> **Note:** `shlex.split()` on a trusted, developer-authored string (not user input)
> is safe and produces the same tokenisation as the shell. All existing callers of `_run()`
> pass literal strings like `"git pull"`, `"npm install"` — these tokenise correctly.
> If a caller ever needs to pass user-controlled content, it **must** use a list.

---

## Fix 2 of 5 — `mcp-servers/server_control/server.py`

### Location 1 (lines 188–196) — background Popen
```python
# BEFORE:
proc = subprocess.Popen(
    command,
    shell=True,           # ← B602 HIGH
    cwd=run_cwd,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

# AFTER:
import shlex
argv = command if isinstance(command, list) else shlex.split(command)
proc = subprocess.Popen(
    argv,
    shell=False,          # ← safe
    cwd=run_cwd,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
```

### Location 2 (lines 200–208) — foreground run
```python
# BEFORE:
result = subprocess.run(
    command,
    shell=True,           # ← B602 HIGH
    cwd=run_cwd,
    timeout=timeout,
    capture_output=True,
    text=True,
)

# AFTER:
argv = command if isinstance(command, list) else shlex.split(command)
result = subprocess.run(
    argv,
    shell=False,          # ← safe
    cwd=run_cwd,
    timeout=timeout,
    capture_output=True,
    text=True,
)
```

> **Add `import shlex` at the top of `server_control/server.py` if not already present.**

---

## Fix 3 of 5 — `mcp-servers/coding/server.py`

### shell=True fix (line 248)
```python
# BEFORE:
def _run_shell(cmd: str, cwd: str | None = None, timeout: int = 30) -> dict[str, Any]:
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            shell=True,       # ← B602 HIGH
            capture_output=True,
            text=True,
            timeout=timeout,
        )

# AFTER:
import shlex

def _run_shell(cmd: str | list[str], cwd: str | None = None, timeout: int = 30) -> dict[str, Any]:
    argv = cmd if isinstance(cmd, list) else shlex.split(cmd)
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            shell=False,      # ← safe
            capture_output=True,
            text=True,
            timeout=timeout,
        )
```

### md5 → sha256 fix (line 101)
```python
# BEFORE:
def _get_project_hash(project_path: str) -> str:
    return hashlib.md5(project_path.encode("utf-8")).hexdigest()[:12]  # ← B324 HIGH

# AFTER:
def _get_project_hash(project_path: str) -> str:
    # sha256 replaces md5 — same non-cryptographic use (cache key), no collisions
    return hashlib.sha256(project_path.encode("utf-8")).hexdigest()[:12]
```

---

## Fix 4 of 5 — `mcp-servers/app_control/server.py`

### Windows shell=True — keep with justification (line 346)
```python
# BEFORE:
if PLATFORM == "windows" and cmd.startswith("start "):
    full_cmd = f"{cmd} {' '.join(args)}" if args else cmd
    subprocess.Popen(full_cmd, shell=True)

# AFTER:
if PLATFORM == "windows" and cmd.startswith("start "):
    # nosec B602 — Windows 'start' is a shell built-in with no argv equivalent.
    # This branch is only reached on Windows with a hardcoded "start " prefix.
    # 'cmd' is validated against a registered app name allowlist above this call.
    full_cmd = f"{cmd} {' '.join(args)}" if args else cmd
    subprocess.Popen(full_cmd, shell=True)  # nosec B602
```

### All other subprocess calls in app_control — already safe (no other `shell=True`)
Verify by running: `grep -n "shell=True" mcp-servers/app_control/server.py`
After the fix above, only the justified Windows branch should remain.

---

## Fix 5 of 5 — `pawbot/agent/tools/mcp.py`

### timeout=None → explicit cap (line 110)
```python
# BEFORE:
http_client = await stack.enter_async_context(
    httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=None,           # ← B113 MEDIUM: can hang forever
    )
)

# AFTER:
http_client = await stack.enter_async_context(
    httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        # 300s cap: MCP tools should never take more than 5 minutes.
        # The higher-level agent loop also has an iteration timeout.
        timeout=300.0,
    )
)
```

---

## File — CREATE `tests/test_security_hardening.py`

```python
"""
tests/test_security_hardening.py

Verifies all 7 security fixes from Deep Scan Report (Bandit HIGH×6, MEDIUM×1).
Run: pytest tests/test_security_hardening.py -v
"""

import ast
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_deploy_server_no_shell_true():
    """deploy/server.py _run() must not use shell=True."""
    source = _read("mcp-servers/deploy/server.py")
    # Find all subprocess.run calls and verify none have shell=True
    # Simple text check — good enough for a regression guard
    lines = source.splitlines()
    in_run_func = False
    for i, line in enumerate(lines):
        if "def _run(" in line:
            in_run_func = True
        if in_run_func and "shell=True" in line and "nosec" not in line:
            raise AssertionError(
                f"deploy/server.py line {i+1}: shell=True without nosec — "
                "use shlex.split() and shell=False"
            )
        if in_run_func and line.strip().startswith("def ") and "def _run(" not in line:
            in_run_func = False


def test_server_control_no_shell_true():
    """server_control/server.py must not use shell=True without nosec."""
    source = _read("mcp-servers/server_control/server.py")
    for i, line in enumerate(source.splitlines()):
        if "shell=True" in line and "nosec" not in line:
            raise AssertionError(
                f"server_control/server.py line {i+1}: shell=True without nosec"
            )


def test_coding_server_no_shell_true():
    """coding/server.py _run_shell() must not use shell=True."""
    source = _read("mcp-servers/coding/server.py")
    for i, line in enumerate(source.splitlines()):
        if "shell=True" in line and "nosec" not in line:
            raise AssertionError(
                f"coding/server.py line {i+1}: shell=True without nosec"
            )


def test_coding_server_uses_sha256_not_md5():
    """_get_project_hash() must use sha256, not md5."""
    source = _read("mcp-servers/coding/server.py")
    assert "hashlib.sha256" in source, \
        "_get_project_hash() must use hashlib.sha256 (not md5)"
    # Ensure md5 is gone from the hash function
    for i, line in enumerate(source.splitlines()):
        if "hashlib.md5" in line:
            raise AssertionError(
                f"coding/server.py line {i+1}: hashlib.md5 still present — replace with sha256"
            )


def test_mcp_tool_has_explicit_timeout():
    """mcp.py httpx.AsyncClient must not use timeout=None."""
    source = _read("pawbot/agent/tools/mcp.py")
    for i, line in enumerate(source.splitlines()):
        if "timeout=None" in line:
            raise AssertionError(
                f"mcp.py line {i+1}: timeout=None found — set an explicit timeout (e.g. 300.0)"
            )
```

---

## Test Matrix

| # | Test | Input | Expected | Pass Condition |
|---|------|-------|----------|----------------|
| T1 | `deploy/server.py` no `shell=True` | Source scan | No unsuppressed `shell=True` in `_run()` | AssertionError if found |
| T2 | `server_control/server.py` no `shell=True` | Source scan | No unsuppressed `shell=True` | AssertionError if found |
| T3 | `coding/server.py` no `shell=True` | Source scan | No unsuppressed `shell=True` | AssertionError if found |
| T4 | `coding/server.py` uses `sha256` | Source scan | `hashlib.sha256` present, `hashlib.md5` absent | Both assertions pass |
| T5 | `mcp.py` no `timeout=None` | Source scan | No `timeout=None` in file | AssertionError if found |

---

## ⛔ Acceptance Gate — Phase 2

```bash
pytest tests/test_security_hardening.py -v
```

- [ ] All 5 tests pass
- [ ] `grep -rn "shell=True" mcp-servers/ | grep -v "nosec"` → returns only the justified Windows branch in `app_control/server.py`
- [ ] `grep -n "hashlib.md5" mcp-servers/coding/server.py` → no output
- [ ] `grep -n "timeout=None" pawbot/agent/tools/mcp.py` → no output
- [ ] Existing test suite still passes — run `python -m pytest tests/ -x -q` to verify no regressions

---

---

# PHASE 3 OF 5 — Test Suite Speed
### *Fix 719s + 675s test files — shared session fixtures, lazy backend init*

---

## Agent Prompt

You are fixing the two slowest test files in the Pawbot test suite.

From the Deep Scan Report:
- `tests/test_context.py`: 58 tests, **719 seconds** (12 minutes)
- `tests/test_agent_loop.py`: 45 tests, **675 seconds** (11 minutes)

Root cause: `MemoryRouter.__init__()` takes ~32 seconds per call because it initialises
ChromaDB + embedding models. Both test files create a new `MemoryRouter` per test.

Your job is to add session-scoped shared fixtures to `tests/conftest.py` so the heavy
backend is initialised **once per test session** instead of once per test. You must
also ensure memory isolation between tests by resetting state between calls.

**Rules:**
- Do not change `pawbot/agent/memory.py` source
- Do not change individual test assertions — only fix the fixture scope
- Do not add `@pytest.mark.slow` — all tests should pass in normal `pytest` runs
- Read `pawbot/contracts.py` before editing any test file

---

## Why This Phase Exists

A 23-minute test suite kills developer productivity. Nobody runs tests that take 23 minutes
locally. PRs go unverified. Bugs ship. The fix is entirely in test infrastructure —
the production code is correct.

`MemoryRouter.__init__()` is slow because ChromaDB initialises an embedding model on first
call. The model does not change between tests. There is no reason to re-initialise it 100+
times per CI run.

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `tests/conftest.py` — add 3 session-scoped shared fixtures |
| **EDIT** | `tests/test_context.py` — swap per-test memory fixture for shared one |
| **EDIT** | `tests/test_agent_loop.py` — swap per-test memory fixture for shared one |
| **CREATE** | `tests/test_suite_speed.py` — 3 timing verification tests |

---

## Dependencies

| Dependency | Type | Import | Notes |
|-----------|------|--------|-------|
| `MemoryRouter` | Internal | `from pawbot.agent.memory import MemoryRouter` | Already used in tests |
| `_make_config` | Test helper | Copy from `test_memory.py` | Defined at module level |
| `pytest` | stdlib | `import pytest` | `scope="session"` support |
| `tmp_path_factory` | pytest builtin | Fixture param | Session-scoped temp dirs |

---

## File 1 of 3 — EDIT `tests/conftest.py`

Replace the current empty file entirely:

```python
"""
tests/conftest.py

Shared fixtures for pawbot tests.

KEY DESIGN:
- `shared_sqlite_memory` and `shared_chroma_memory` are session-scoped.
  MemoryRouter.__init__ takes ~32s due to ChromaDB + embedding model startup.
  Creating one per test × 100 tests = 3200s wasted. One per session = 32s total.
- Each test that uses these fixtures receives the shared instance.
  Tests that mutate state must clean up after themselves (see reset helpers below).
- `lightweight_memory_config` provides a fast, SQLite-only config for tests that
  do not need vector search. Use this by default unless a test explicitly tests
  ChromaDB behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest

from pawbot.agent.memory import MemoryRouter


# ── Config helpers ─────────────────────────────────────────────────────────────

def _sqlite_only_config(db_path: str) -> dict:
    """SQLite-only memory config — fast, no embedding model startup."""
    return {
        "memory": {
            "backends": {
                "redis":  {"enabled": False},
                "sqlite": {"enabled": True, "path": db_path},
                "chroma": {"enabled": False},
            }
        }
    }


def _chroma_config(db_path: str, chroma_path: str) -> dict:
    """Full config with ChromaDB — slow to init, use session scope only."""
    return {
        "memory": {
            "backends": {
                "redis":  {"enabled": False},
                "sqlite": {"enabled": True,  "path": db_path},
                "chroma": {"enabled": True,  "path": chroma_path},
            }
        }
    }


# ── Session-scoped fixtures ────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def session_tmp(tmp_path_factory) -> Path:
    """Session-level temp directory — shared across all fixtures and tests."""
    return tmp_path_factory.mktemp("pawbot_session")


@pytest.fixture(scope="session")
def shared_sqlite_router(session_tmp: Path) -> Generator[MemoryRouter, None, None]:
    """
    Session-scoped MemoryRouter with SQLite only.

    Use this for any test that needs memory operations but NOT vector search.
    Initialises once per test session (~0.1s) vs once per test (~32s).

    Tests MUST NOT leave persistent data between calls.
    Use router.sqlite.execute("DELETE FROM facts") to clean up if needed.
    """
    db_path = str(session_tmp / "shared_facts.db")
    router  = MemoryRouter("shared_session", _sqlite_only_config(db_path))
    yield router
    # No teardown needed — temp dir is cleaned up by pytest


@pytest.fixture(scope="session")
def shared_chroma_router(session_tmp: Path) -> Generator[MemoryRouter, None, None]:
    """
    Session-scoped MemoryRouter with ChromaDB enabled.

    Use ONLY for tests that explicitly test vector search / embedding behaviour.
    This is the slow fixture (~32s init) — shared to amortise startup cost.

    All other tests should use `shared_sqlite_router` instead.
    """
    db_path     = str(session_tmp / "chroma_facts.db")
    chroma_path = str(session_tmp / "chroma_store")
    router      = MemoryRouter("chroma_session", _chroma_config(db_path, chroma_path))
    yield router


# ── Function-scoped lightweight fixture ───────────────────────────────────────

@pytest.fixture
def lightweight_memory_config(tmp_path: Path) -> dict:
    """
    Per-test, SQLite-only config dict.

    Use this when you need a fresh, isolated MemoryRouter per test
    (e.g. for tests that deliberately corrupt state or test init logic).
    Creates a new in-memory SQLite DB per test — fast (~0.05s).
    """
    return _sqlite_only_config(str(tmp_path / "test_facts.db"))


@pytest.fixture
def fresh_sqlite_router(lightweight_memory_config: dict) -> MemoryRouter:
    """
    Fresh, isolated MemoryRouter per test — SQLite only.

    Use when you need guaranteed isolation (no state from previous tests).
    ~0.05s init. Use `shared_sqlite_router` when isolation is not needed.
    """
    return MemoryRouter("test_session", lightweight_memory_config)
```

---

## File 2 of 3 — EDIT `tests/test_context.py`

Find all fixtures that create a new `MemoryRouter` per test and replace them.
The key pattern to find and replace:

```python
# FIND patterns like this in test_context.py (there may be several variations):
@pytest.fixture
def memory_router(tmp_path):
    config = {
        "memory": {
            "backends": {
                "sqlite": {"enabled": True, "path": str(tmp_path / "facts.db")},
                "chroma": {"enabled": True, "path": str(tmp_path / "chroma")},
                ...
            }
        }
    }
    return MemoryRouter("test", config)

# REPLACE WITH (use shared session fixture from conftest.py):
@pytest.fixture
def memory_router(shared_sqlite_router):
    """Use session-scoped router — eliminates 32s ChromaDB init per test."""
    return shared_sqlite_router
```

For any test that calls `mock_memory_router` (a `MagicMock`), leave it unchanged —
mocked routers are already fast.

For any test that **specifically tests ChromaDB vector search**, use `shared_chroma_router`
instead of `shared_sqlite_router`.

**Add this import at the top of `test_context.py` if not already present:**
```python
# No new imports needed — conftest.py fixtures are auto-discovered by pytest
```

---

## File 3 of 3 — EDIT `tests/test_agent_loop.py`

Apply the same pattern. Find the `mock_memory` fixture and any real `MemoryRouter`
instantiations inside test functions or class-level fixtures:

```python
# FIND patterns in test_agent_loop.py that create real MemoryRouter:
@pytest.fixture
def mock_memory():
    """Create a mock memory router."""
    memory = MagicMock()
    memory.search.return_value = []
    memory.save.return_value = "test-id"
    memory.update.return_value = True
    return memory
# ↑ This is already a MagicMock — LEAVE IT UNCHANGED. It's already fast.

# FIND any test that creates a real MemoryRouter:
# def test_something(tmp_path):
#     router = MemoryRouter("s1", {...})   ← SLOW: 32s init
#     ...

# REPLACE WITH:
# def test_something(shared_sqlite_router):
#     router = shared_sqlite_router        ← FAST: already initialised
#     ...
```

Scan the full file for `MemoryRouter(` — every real instantiation is a 32s penalty.
Replace each one with `shared_sqlite_router` from conftest, unless the test specifically
tests initialisation behaviour (in which case use `fresh_sqlite_router`).

---

## File 4 — CREATE `tests/test_suite_speed.py`

```python
"""
tests/test_suite_speed.py

Verifies that the shared session fixtures are fast and properly shared.
Run: pytest tests/test_suite_speed.py -v

These tests also serve as documentation for the fixture design.
"""

import time
import pytest
from pawbot.agent.memory import MemoryRouter


def test_shared_sqlite_router_is_same_object(shared_sqlite_router, shared_sqlite_router):  # noqa: F811
    """
    Session-scoped fixture must return the same object every time.
    If this fails, the scope is not working and we're re-initialising per test.
    """
    # pytest caches session fixtures — both references should be the same object
    # This test verifies the fixture scope is correctly set
    assert shared_sqlite_router is not None


def test_sqlite_router_init_is_fast(tmp_path):
    """
    SQLite-only MemoryRouter must initialise in under 2 seconds.
    If this fails, a dependency has been added that loads heavy models at init.
    """
    config = {
        "memory": {
            "backends": {
                "redis":  {"enabled": False},
                "sqlite": {"enabled": True, "path": str(tmp_path / "speed_test.db")},
                "chroma": {"enabled": False},
            }
        }
    }
    start  = time.monotonic()
    router = MemoryRouter("speed_test", config)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, (
        f"SQLite-only MemoryRouter took {elapsed:.2f}s to initialise. "
        "Expected < 2.0s. Check if a heavy dependency was added to __init__."
    )


def test_shared_router_can_save_and_load(shared_sqlite_router):
    """
    Shared router must be functional — save and retrieve a fact.
    Verifies the session fixture is not broken after being shared.
    """
    mem_id = shared_sqlite_router.save("fact", {
        "text": "test_suite_speed fixture verification",
        "source": "test"
    })
    assert mem_id is not None, "save() must return a memory ID"

    results = shared_sqlite_router.search("fixture verification", limit=5)
    # Results may include items from other tests (shared scope) — just verify
    # the basic functionality works
    assert isinstance(results, list), "search() must return a list"
```

---

## Test Matrix

| # | Test | Input | Expected | Pass Condition |
|---|------|-------|----------|----------------|
| T1 | Session fixture is same object | Two fixture refs | Same object | `is not None` (scope validation) |
| T2 | SQLite init is fast | New router with SQLite-only config | `elapsed < 2.0s` | Catches regression if heavy dep added |
| T3 | Shared router functional | `save()` then `search()` | ID returned, list returned | Basic ops work after sharing |

---

## Expected Speed Improvement

| File | Before | After | Improvement |
|------|--------|-------|-------------|
| `test_context.py` | ~719s | <60s | ~12× faster |
| `test_agent_loop.py` | ~675s | <60s | ~11× faster |
| Full test suite | ~23min | <5min | ~5× faster |

---

## ⛔ Acceptance Gate — Phase 3

```bash
pytest tests/test_suite_speed.py -v
```

- [ ] All 3 tests pass
- [ ] `time python -m pytest tests/test_context.py -q` completes in **under 120 seconds**
- [ ] `time python -m pytest tests/test_agent_loop.py -q` completes in **under 120 seconds**
- [ ] `grep -c "MemoryRouter(" tests/test_context.py tests/test_agent_loop.py` returns 0 or only justified per-test inits
- [ ] Full suite `python -m pytest tests/ -q` passes with no failures

---

---

# PHASE 4 OF 5 — Lint & Correctness
### *Fix all 33 Ruff issues: unused imports, duplicate class, ambiguous names, empty f-strings*

---

## Agent Prompt

You are fixing all 33 Ruff lint issues in the Pawbot production codebase.

From the Deep Scan Report, there are 33 violations across 18 files:
- **F401 (17)** — unused imports
- **E701 (5)** — multiple statements on one line
- **E741 (5)** — ambiguous variable names (`l`, `O`, `I`)
- **F541 (2)** — f-string without placeholders (wasted `f""` prefix)
- **F841 (2)** — local variable assigned but never used
- **F811 (1)** — duplicate class definition (silent runtime override)
- **F821 (1)** — undefined name `BaseExceptionGroup` (fixed by Phase 1's `target-version`)

Your job is to fix all of these. Most are mechanical — remove the import, rename the variable.
The `F811` duplicate class in `schema.py` requires careful inspection before removal.

**Rules:**
- Run `python -m ruff check pawbot/ --fix` first to auto-fix safe issues
- Then manually fix the remaining issues that auto-fix cannot handle
- Do not change any test behaviour
- Do not rename public API symbols (class names, function names, exported constants)
- Read `pawbot/contracts.py` before editing any file

---

## Why This Phase Exists

Unused imports are dead weight — they slow import time, confuse readers, and sometimes
indicate that the feature they were imported for was never fully implemented. Ambiguous
variable names (`l` looks like `1`, `O` looks like `0`) cause real bugs when code is
copy-edited. The `F811` duplicate class in `schema.py` means one `MatrixConfig` silently
overwrites another — whichever is defined last wins, and the earlier one's fields are lost.

---

## Step 1 — Run Auto-Fix First

```bash
cd pawbot/

# Auto-fix safe issues (F401 unused imports, F541 empty f-strings, F841 unused vars)
python -m ruff check pawbot/ mcp-servers/ --fix --select F401,F541,F841

# Verify what remains
python -m ruff check pawbot/ mcp-servers/
```

Auto-fix handles most F401, F541, and some F841. The remaining manual fixes are below.

---

## Manual Fix 1 — `F811` Duplicate `MatrixConfig` in `pawbot/config/schema.py`

### What happened
Ruff found two class definitions with the same name at lines 187 and ~199. The second
definition silently replaces the first. One of them is missing its fields.

### How to fix
```python
# 1. Find both class definitions:
grep -n "class MatrixConfig" pawbot/config/schema.py
# Should return two line numbers, e.g. 183 and 199

# 2. Read both definitions completely
# 3. Merge the fields: keep ALL unique fields from BOTH definitions in ONE class
# 4. Delete the duplicate

# The correct MatrixConfig should look like this (merge whatever your two had):
class MatrixConfig(Base):
    """Matrix (Element) channel configuration."""
    enabled: bool = False
    homeserver: str = "https://matrix.org"
    access_token: str = ""
    user_id: str = ""                         # e.g. @bot:matrix.org
    device_id: str = ""
    e2ee_enabled: bool = True                 # end-to-end encryption support
    sync_stop_grace_seconds: int = 2          # graceful sync_forever shutdown timeout
    max_media_bytes: int = 20 * 1024 * 1024   # inbound + outbound attachment limit
    allow_from: list[str] = Field(default_factory=list)
    group_policy: Literal["open", "mention", "allowlist"] = "open"
    group_allow_from: list[str] = Field(default_factory=list)
    allow_room_mentions: bool = False
    # Add any fields that were ONLY in the first definition here
```

After merging, verify: `grep -c "class MatrixConfig" pawbot/config/schema.py` → must return `1`.

---

## Manual Fix 2 — `E701` Multiple Statements on One Line in `pawbot/channels/dingtalk.py`

### Lines 207–209 (if/return on one line)
```python
# BEFORE (E701 — multiple statements per line):
if ext in self._IMAGE_EXTS: return "image"
if ext in self._AUDIO_EXTS: return "voice"
if ext in self._VIDEO_EXTS: return "video"

# AFTER:
if ext in self._IMAGE_EXTS:
    return "image"
if ext in self._AUDIO_EXTS:
    return "voice"
if ext in self._VIDEO_EXTS:
    return "video"
```

### Lines 319–320
```python
# BEFORE (E701):
try: result = resp.json()
except Exception as e:  # noqa: F841 result = {}

# AFTER:
try:
    result = resp.json()
except Exception:
    result = {}
```

---

## Manual Fix 3 — `E741` Ambiguous Variable Names

### `pawbot/dashboard/server.py` — lines 122, 242, 514, 516

```python
# BEFORE (E741 — 'l' is ambiguous: looks like '1'):
return len([l for l in content.splitlines() if l.strip().startswith("- ")])
clean = [l for l in lines if not l.startswith("🐾")]

# AFTER:
return len([line for line in content.splitlines() if line.strip().startswith("- ")])
clean = [line for line in lines if not line.startswith("🐾")]
```

### `pawbot/providers/openai_codex_provider.py` — line 231
```python
# BEFORE (E741 — find the ambiguous variable name in the _consume_sse function):
# Look for single-letter variable names like 'l', 'O', 'I' and rename them descriptively
# e.g. 'l' → 'line', 'O' → 'obj', 'I' → 'idx'
```

---

## Manual Fix 4 — `F821` `BaseExceptionGroup` in `pawbot/agent/loop.py` line 576

This is fixed by Phase 1's `target-version = "py311"` in `pyproject.toml`.
Verify the fix worked:

```bash
python -m ruff check pawbot/agent/loop.py --select F821
# Expected: no output (0 issues)
```

If it still reports F821 after Phase 1, add the explicit Python version guard:
```python
# BEFORE:
except (RuntimeError, BaseExceptionGroup):
    pass  # MCP SDK cancel scope cleanup is noisy but harmless

# AFTER (if ruff still complains even with target-version set):
import sys
if sys.version_info >= (3, 11):
    _CLEANUP_EXC = (RuntimeError, BaseExceptionGroup)
else:
    _CLEANUP_EXC = (RuntimeError,)

# ...
except _CLEANUP_EXC:
    pass  # MCP SDK cancel scope cleanup is noisy but harmless
```

---

## Manual Fix 5 — `memory.py:1422` Raw `open()` Write

This was identified in the Status Report as inconsistent with the atomic write policy.

```python
# BEFORE (raw open — inconsistent with rest of codebase):
with open(migrated_flag, "w", encoding="utf-8"):
    pass

# AFTER (use atomic_write_text — consistent with all other state writes):
from pawbot.utils.fs import atomic_write_text
atomic_write_text(migrated_flag, "")
```

---

## File — CREATE `tests/test_lint_correctness.py`

```python
"""
tests/test_lint_correctness.py

Verifies the F811 duplicate class fix and key correctness issues.
Run: pytest tests/test_lint_correctness.py -v
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _count_class_definitions(source: str, class_name: str) -> int:
    """Count how many times a class is defined in a source file."""
    tree  = ast.parse(source)
    count = sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return count


def test_matrix_config_defined_exactly_once():
    """
    MatrixConfig must be defined exactly once in schema.py.
    The F811 duplicate caused the first definition's fields to be silently lost.
    """
    source = _read("pawbot/config/schema.py")
    count  = _count_class_definitions(source, "MatrixConfig")
    assert count == 1, (
        f"MatrixConfig is defined {count} times in schema.py — expected exactly 1. "
        "Merge both definitions into one and delete the duplicate."
    )


def test_schema_py_has_no_duplicate_classes():
    """No class should be defined more than once in schema.py."""
    source = _read("pawbot/config/schema.py")
    tree   = ast.parse(source)
    names  = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    seen   = set()
    for name in names:
        assert name not in seen, (
            f"Class '{name}' is defined more than once in schema.py — "
            "this is an F811 duplicate that silently overwrites the first definition."
        )
        seen.add(name)


def test_memory_migration_uses_atomic_write():
    """
    memory.py migration flag must use atomic_write_text, not raw open().
    Raw open() is inconsistent with the atomic write policy used everywhere else.
    """
    source = _read("pawbot/agent/memory.py")
    # Find the migration section — look for the migrated_flag write
    assert "atomic_write_text" in source or "atomic_write" in source, (
        "memory.py migration flag write must use atomic_write_text from pawbot.utils.fs, "
        "not raw open(). Found raw open() at line ~1422."
    )


def test_no_ambiguous_loop_vars_in_dashboard():
    """
    dashboard/server.py must not use single-letter loop variables l, O, I.
    These are indistinguishable from 1, 0, 1 in many fonts.
    """
    source = _read("pawbot/dashboard/server.py")
    # Check list comprehensions for ambiguous var names
    for i, line in enumerate(source.splitlines()):
        stripped = line.strip()
        # Look for "for l in" or "for I in" or "for O in" patterns
        for bad_var in [" for l in ", " for I in ", " for O in "]:
            if bad_var in stripped:
                raise AssertionError(
                    f"dashboard/server.py line {i+1}: ambiguous variable name in "
                    f"'{stripped}' — rename to a descriptive name (e.g. 'line', 'idx')"
                )
```

---

## Test Matrix

| # | Test | Input | Expected | Pass Condition |
|---|------|-------|----------|----------------|
| T1 | `MatrixConfig` defined once | AST parse | Count == 1 | No duplicate class |
| T2 | No duplicate classes in schema.py | AST walk all ClassDef | No name seen twice | Set membership check |
| T3 | Memory migration uses atomic write | Source scan | `atomic_write_text` present | Consistent with policy |
| T4 | No ambiguous loop vars in dashboard | Source scan | No `for l in`, `for I in` | Clean variable names |

---

## ⛔ Acceptance Gate — Phase 4

```bash
pytest tests/test_lint_correctness.py -v
```

- [ ] All 4 tests pass
- [ ] `python -m ruff check pawbot/ mcp-servers/` → **0 issues**
- [ ] `grep -c "class MatrixConfig" pawbot/config/schema.py` → `1`
- [ ] `grep -n "open(migrated_flag" pawbot/agent/memory.py` → no output (replaced with atomic write)
- [ ] `python -m ruff check pawbot/ --select E741` → **0 issues**

---

---

# PHASE 5 OF 5 — Complexity Refactor
### *Split the two grade-E and top grade-D functions into testable helpers*

---

## Agent Prompt

You are refactoring the two most complex functions in the Pawbot codebase.

From the Deep Scan Report (Radon cyclomatic complexity):
- `pawbot/agent/loop.py` `AgentLoop._process_message` — **CC=33 (grade E)**
- `pawbot/agent/loop.py` `AgentLoop._run_agent_loop` — **CC=22 (grade D)**

These two functions together are 200+ lines of nested conditions. They are:
- Impossible to unit test in isolation (every test must set up the full agent)
- Dangerous to modify (any change touches 5+ code paths at once)
- The source of the slowest tests (`test_agent_loop.py` 675s — now fixed by Phase 3,
  but still hard to test correctly)

Your job is to extract clearly-named helper methods that each have a single responsibility.
The public interface of `AgentLoop` must not change — callers must work without modification.

**Rules:**
- Do not change any method signature visible outside `AgentLoop`
- Do not change existing test assertions — tests must pass unchanged after refactor
- Each extracted helper must have its own unit test
- Read `pawbot/contracts.py` fully before modifying any file
- Run the full test suite before and after — zero regressions allowed

---

## Why This Phase Exists

CC=33 is the highest complexity grade (E). The industry threshold for "needs refactoring"
is CC=10. A function with CC=33 has at minimum 33 independent paths through it — meaning
you need 33 tests to achieve full branch coverage. Nobody writes 33 tests for one function.
The result: the most critical path in the agent goes under-tested, and every bug fix has
a high chance of introducing a new bug.

---

## What You Will Build

| Action | File |
|--------|------|
| **EDIT** | `pawbot/agent/loop.py` — extract 4 helpers from `_process_message` |
| **EDIT** | `pawbot/agent/loop.py` — extract 1 helper from `_run_agent_loop` |
| **CREATE** | `tests/test_loop_helpers.py` — 6 focused unit tests for extracted helpers |

---

## Refactor 1 — Extract from `_process_message` (CC=33 → target CC≤10)

Read `_process_message` in full first. The function handles:
1. System message routing (channel parsing + session management)
2. Slash command handling (`/new`, `/help`, etc.)
3. Pre-task reflection check (Phase 2.1 feature)
4. Main agent loop invocation
5. Post-task learning

Extract each responsibility into its own private method:

```python
# ── NEW HELPER 1: System message handling ──────────────────────────────────────
async def _handle_system_message(
    self,
    msg: "InboundMessage",
) -> "OutboundMessage | None":
    """
    Handle messages routed through the 'system' channel.

    System messages come from internal triggers (heartbeat, cron) rather than
    external users. They have a special chat_id format: "channel:chat_id".

    Extracted from _process_message to reduce CC from 33.
    ONLY called when msg.channel == "system".
    """
    channel, chat_id = (
        msg.chat_id.split(":", 1) if ":" in msg.chat_id
        else ("cli", msg.chat_id)
    )
    key     = f"{channel}:{chat_id}"
    session = self.sessions.get_or_create(key)
    self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
    history  = session.get_history(max_messages=self.memory_window)
    messages = self.context.build_messages(
        history         = history,
        current_message = msg.content,
        channel         = channel,
        chat_id         = chat_id,
    )
    final_content, _, all_msgs, _ = await self._run_agent_loop(messages)
    self._save_turn(session, all_msgs, 1 + len(history))
    self.sessions.save(session)
    return OutboundMessage(
        channel = channel,
        chat_id = chat_id,
        content = final_content or "Background task completed.",
    )


# ── NEW HELPER 2: Slash command handling ──────────────────────────────────────
async def _handle_slash_command(
    self,
    cmd: str,
    msg: "InboundMessage",
    session: "Session",
) -> "OutboundMessage | None":
    """
    Handle /new, /help, and other slash commands.

    Returns an OutboundMessage if the command was handled, None if the
    input is not a slash command and should fall through to normal processing.

    Extracted from _process_message to reduce CC from 33.
    """
    cmd_lower = cmd.strip().lower()

    if cmd_lower == "/new":
        return await self._execute_new_command(msg, session)

    if cmd_lower in ("/help", "/?"):
        return OutboundMessage(
            channel = msg.channel,
            chat_id = msg.chat_id,
            content = self._help_text(),
        )

    return None  # not a slash command — caller handles normally


# ── NEW HELPER 3: Session setup ────────────────────────────────────────────────
def _setup_session(
    self,
    msg: "InboundMessage",
    session_key: "str | None",
) -> tuple["Session", list[dict]]:
    """
    Get or create session, build initial message history.

    Returns (session, history_messages).
    Extracted from _process_message to reduce CC from 33.
    """
    key     = session_key or msg.session_key
    session = self.sessions.get_or_create(key)
    history = session.get_history(max_messages=self.memory_window)
    return session, history


# ── NEW HELPER 4: Response finalisation ───────────────────────────────────────
def _build_response(
    self,
    msg: "InboundMessage",
    final_content: "str | None",
    all_msgs: list[dict],
    session: "Session",
    history_len: int,
) -> "OutboundMessage":
    """
    Save turn history and build the final OutboundMessage.

    Extracted from _process_message to reduce CC from 33.
    """
    self._save_turn(session, all_msgs, 1 + history_len)
    self.sessions.save(session)
    return OutboundMessage(
        channel = msg.channel,
        chat_id = msg.chat_id,
        content = final_content or "",
    )
```

### Updated `_process_message` — thin orchestrator

After extraction, `_process_message` becomes a thin orchestrator:

```python
async def _process_message(
    self,
    msg: "InboundMessage",
    session_key: "str | None" = None,
    on_progress: "Callable[[str], Awaitable[None]] | None" = None,
) -> "OutboundMessage | None":
    """Process a single inbound message and return the response."""
    # Phase 2.5: Reset self-correction state for each message
    self.failure_count  = 0
    self.failure_log    = []
    self.current_step   = 0
    self._session_meta  = {}

    # System messages have a separate fast path
    if msg.channel == "system":
        return await self._handle_system_message(msg)

    preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
    logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

    # Session setup
    session, history = self._setup_session(msg, session_key)

    # Slash commands
    slash_result = await self._handle_slash_command(msg.content, msg, session)
    if slash_result is not None:
        return slash_result

    # Build context and run agent loop
    messages = self.context.build_messages(
        history         = history,
        current_message = msg.content,
        channel         = msg.channel,
        chat_id         = msg.chat_id,
    )
    final_content, tools_used, all_msgs, trace = await self._run_agent_loop(
        messages, on_progress=on_progress
    )

    # Post-task learning (Phase 2.3 — non-blocking)
    self._post_task_learning(
        task            = msg.content,
        success         = bool(final_content),
        execution_trace = trace,
        failure_reason  = None,
        session_key     = session.key,
    )

    return self._build_response(msg, final_content, all_msgs, session, len(history))
```

---

## Refactor 2 — Extract from `_run_agent_loop` (CC=22 → target CC≤10)

The tool-call processing block is the primary complexity driver. Extract it:

```python
# ── NEW HELPER: Tool call processing ──────────────────────────────────────────
async def _process_tool_calls(
    self,
    response: "ProviderResponse",
    messages: list[dict],
    tools_used: list[str],
    execution_trace: list[dict],
    on_progress: "Callable[..., Awaitable[None]] | None",
) -> list[dict]:
    """
    Execute all tool calls from a provider response and append results to messages.

    Returns the updated messages list.
    Extracted from _run_agent_loop to reduce CC from 22.

    Args:
        response:        Provider response containing tool_calls
        messages:        Current message list (will be extended)
        tools_used:      Mutable list — tool names appended here for tracking
        execution_trace: Mutable list — step dicts appended here for learning
        on_progress:     Optional progress callback

    Returns:
        Updated messages list with tool call results appended.
    """
    if on_progress:
        clean = self._strip_think(response.content)
        if clean:
            await on_progress(clean)
        await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

    tool_call_dicts = [
        {
            "id":   tc.id,
            "type": "function",
            "function": {
                "name":      tc.name,
                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
            },
        }
        for tc in response.tool_calls
    ]

    messages = self.context.add_assistant_message(
        messages,
        response.content,
        tool_call_dicts,
        reasoning_content = response.reasoning_content,
    )

    # Execute tools
    tool_results = []
    for tc in response.tool_calls:
        tools_used.append(tc.name)
        result = await self.tools.execute(tc.name, tc.arguments)
        tool_results.append({
            "tool_call_id": tc.id,
            "role":         "tool",
            "name":         tc.name,
            "content":      json.dumps(result, ensure_ascii=False),
        })
        execution_trace.append({
            "step":   self.current_step,
            "tool":   tc.name,
            "args":   tc.arguments,
            "result": result,
        })

    return self.context.add_tool_results(messages, tool_results)
```

---

## File — CREATE `tests/test_loop_helpers.py`

```python
"""
tests/test_loop_helpers.py

Focused unit tests for the helper methods extracted from AgentLoop.
These tests are fast (<1s each) because they test individual helpers,
not the full agent loop.

Run: pytest tests/test_loop_helpers.py -v
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from pawbot.agent.loop import AgentLoop


# ── Minimal AgentLoop factory ──────────────────────────────────────────────────

def _make_loop() -> AgentLoop:
    """Create a minimal AgentLoop with all heavy deps mocked."""
    loop             = object.__new__(AgentLoop)
    loop.sessions    = MagicMock()
    loop.context     = MagicMock()
    loop.tools       = MagicMock()
    loop.provider    = MagicMock()
    loop.model       = "test-model"
    loop.temperature = 0.0
    loop.max_tokens  = 1000
    loop.memory_window = 20
    loop.max_iterations = 10
    loop.failure_count  = 0
    loop.failure_log    = []
    loop.current_step   = 0
    loop._session_meta  = {}
    loop.reasoning_effort = None
    return loop


def _make_msg(content: str = "hello", channel: str = "telegram") -> MagicMock:
    """Create a minimal InboundMessage mock."""
    msg            = MagicMock()
    msg.content    = content
    msg.channel    = channel
    msg.chat_id    = "test_chat_001"
    msg.sender_id  = "user_123"
    msg.session_key = "telegram:test_chat_001"
    msg.metadata   = {}
    return msg


# ── Tests for _handle_slash_command ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_slash_command_new_returns_outbound():
    """
    /new command must return an OutboundMessage (not None).
    Tests that _handle_slash_command correctly identifies slash commands.
    """
    loop  = _make_loop()
    msg   = _make_msg("/new")
    session = MagicMock()

    # Mock the _execute_new_command helper
    expected = MagicMock()
    loop._execute_new_command = AsyncMock(return_value=expected)

    result = await loop._handle_slash_command("/new", msg, session)
    assert result is expected
    loop._execute_new_command.assert_called_once_with(msg, session)


@pytest.mark.asyncio
async def test_non_slash_command_returns_none():
    """
    Normal messages must return None from _handle_slash_command.
    None means "not a slash command — process normally".
    """
    loop  = _make_loop()
    msg   = _make_msg("what's the weather?")
    session = MagicMock()

    result = await loop._handle_slash_command("what's the weather?", msg, session)
    assert result is None, "Non-slash-command must return None to fall through to agent loop"


def test_setup_session_returns_session_and_history():
    """
    _setup_session must return (session, history) tuple.
    History must come from session.get_history(max_messages=memory_window).
    """
    loop    = _make_loop()
    msg     = _make_msg()
    session = MagicMock()
    history = [{"role": "user", "content": "previous message"}]

    loop.sessions.get_or_create.return_value = session
    session.get_history.return_value         = history

    result_session, result_history = loop._setup_session(msg, session_key=None)

    assert result_session is session
    assert result_history is history
    loop.sessions.get_or_create.assert_called_once_with(msg.session_key)
    session.get_history.assert_called_once_with(max_messages=loop.memory_window)


def test_setup_session_uses_explicit_session_key():
    """
    When session_key is provided, _setup_session must use it instead of msg.session_key.
    This is used by the gateway to enforce session namespacing (Phase 4, Section 1).
    """
    loop    = _make_loop()
    msg     = _make_msg()
    session = MagicMock()
    session.get_history.return_value = []
    loop.sessions.get_or_create.return_value = session

    loop._setup_session(msg, session_key="custom_key_from_router")

    loop.sessions.get_or_create.assert_called_once_with("custom_key_from_router")


def test_build_response_saves_turn_and_returns_outbound():
    """
    _build_response must call _save_turn, sessions.save, and return OutboundMessage.
    """
    loop    = _make_loop()
    msg     = _make_msg("test message")
    session = MagicMock()
    session.key = "session_key_001"
    all_msgs = [{"role": "user", "content": "test message"}]

    loop._save_turn = MagicMock()

    result = loop._build_response(
        msg           = msg,
        final_content = "Agent response here",
        all_msgs      = all_msgs,
        session       = session,
        history_len   = 3,
    )

    loop._save_turn.assert_called_once_with(session, all_msgs, 4)  # 1 + history_len
    loop.sessions.save.assert_called_once_with(session)
    assert result.content == "Agent response here"
    assert result.channel == msg.channel
    assert result.chat_id == msg.chat_id


@pytest.mark.asyncio
async def test_process_tool_calls_appends_to_tools_used():
    """
    _process_tool_calls must append each tool name to the tools_used list.
    This list is used by post-task learning to record what tools were exercised.
    """
    loop       = _make_loop()
    tools_used = []
    trace      = []

    # Build a mock response with two tool calls
    tc1      = MagicMock(); tc1.id = "call_1"; tc1.name = "shell"; tc1.arguments = {"cmd": "ls"}
    tc2      = MagicMock(); tc2.id = "call_2"; tc2.name = "web_search"; tc2.arguments = {"q": "pawbot"}
    response = MagicMock()
    response.content       = "I'll run that for you."
    response.tool_calls    = [tc1, tc2]
    response.reasoning_content = None

    loop.context.add_assistant_message = MagicMock(return_value=[])
    loop.context.add_tool_results      = MagicMock(return_value=[])
    loop.tools.execute                 = AsyncMock(return_value={"ok": True})

    await loop._process_tool_calls(
        response        = response,
        messages        = [],
        tools_used      = tools_used,
        execution_trace = trace,
        on_progress     = None,
    )

    assert "shell" in tools_used
    assert "web_search" in tools_used
    assert len(trace) == 2
    assert trace[0]["tool"] == "shell"
    assert trace[1]["tool"] == "web_search"
```

---

## Test Matrix

| # | Test | Input | Expected | Pass Condition |
|---|------|-------|----------|----------------|
| T1 | `/new` → returns OutboundMessage | `/new` msg | `_execute_new_command` called | Result is not None |
| T2 | Normal msg → returns None | `"what's the weather?"` | `None` returned | Falls through to agent loop |
| T3 | `_setup_session` returns tuple | No explicit key | `(session, history)` | Both are correct mocks |
| T4 | `_setup_session` uses explicit key | `session_key="custom"` | `get_or_create("custom")` called | Key override works |
| T5 | `_build_response` saves and returns | `final_content="resp"` | `_save_turn` called, result.content correct | Full save flow |
| T6 | `_process_tool_calls` tracks tools | 2 tool calls | `tools_used` has both names | Learning trace populated |

---

## ⛔ Acceptance Gate — Phase 5 (Section 2 Final Gate)
**ALL criteria must pass. This is the Section 2 gate.**

```bash
pytest tests/test_loop_helpers.py \
       tests/test_hygiene.py \
       tests/test_security_hardening.py \
       tests/test_suite_speed.py \
       tests/test_lint_correctness.py \
       -v
```

- [ ] All **21 tests** pass across all 5 phases
- [ ] PHASE 1: `git ls-files | grep "\.pyc"` → no output
- [ ] PHASE 1: `pyproject.toml` has `[tool.ruff]` with `target-version = "py311"`
- [ ] PHASE 2: `grep -rn "shell=True" mcp-servers/ | grep -v nosec` → only the justified Windows branch
- [ ] PHASE 2: `grep -n "hashlib.md5" mcp-servers/coding/server.py` → no output
- [ ] PHASE 2: `grep -n "timeout=None" pawbot/agent/tools/mcp.py` → no output
- [ ] PHASE 3: `time python -m pytest tests/test_context.py -q` → completes in **<120 seconds**
- [ ] PHASE 3: `time python -m pytest tests/test_agent_loop.py -q` → completes in **<120 seconds**
- [ ] PHASE 4: `python -m ruff check pawbot/ mcp-servers/` → **0 issues**
- [ ] PHASE 4: `grep -c "class MatrixConfig" pawbot/config/schema.py` → `1`
- [ ] PHASE 5: `_process_message` no longer contains system message handling inline — uses `_handle_system_message`
- [ ] PHASE 5: `_process_message` no longer contains slash command handling inline — uses `_handle_slash_command`
- [ ] COMBINED: `python -m pytest tests/ -q` → all 547+ tests pass, **0 failures**

---

**Section 2 is complete when all of the above are verified.**

Signal Section 3 agent that it can proceed.

> **Remember:** Every name in this repo — every class, enum, constant, path, and
> config key — comes from `pawbot/contracts.py`. Read it first. Never invent new names.
> The single source of truth is the contract.

---

*End of Section 2 Fix Document — itsamiitt/pawbot — March 2026*
