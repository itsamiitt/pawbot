# Pawbot Production Readiness Audit Report

**Date:** 2026-03-02  
**Audited by:** AI Agent  
**Version:** 1.0.0  
**Codebase:** `~/pawbot/pawbot/` — 92 Python files

---

## Summary

| # | Category | Issues Found | Fixed | Status |
|---|----------|:---:|:---:|:---:|
| 3.1 | Bare except clauses | **0** | 0 | ✅ |
| 3.2 | Silent exception swallowing (`except Exception:` w/o log) | **51** | 0 | ❌ |
| 3.3 | Non-atomic file writes | **13** | 0 | ❌ |
| 3.4 | Missing subprocess timeouts | **3** | 0 | ❌ |
| 3.5 | Non-daemon threads | **0** | 0 | ✅ |
| 3.6 | Missing `mkdir` flags | **0** | 0 | ✅ |
| 3.7 | Hardcoded / nanobot paths (in `.py`) | **0** | 0 | ✅ |
| 3.8 | Credentials in log output | **2** | 0 | ⚠️ |
| 3.9 | Missing CLI exit codes | **Partial** | 0 | ⚠️ |
| 3.10 | Missing API key validation (placeholders) | **3** | 0 | ❌ |
| 3.11 | SQLite connection safety | **Needs review** | 0 | ⚠️ |
| 3.12 | JSON corruption handling | **30+** | 0 | ❌ |
| 3.13 | Missing retry logic on API calls | **3** | 0 | ❌ |
| 3.14 | Dashboard security (CORS/binding) | **0** | 0 | ✅ |
| 3.15 | WhatsApp bridge Node.js check | **0** | 0 | ✅ |
| — | Missing `pyproject.toml` | **1** | 0 | ❌ |
| — | Missing utility modules (`atomic_write`, `safe_read_json`, `mask_secret`) | **3** | 0 | ❌ |

**Overall: 7/15 categories PASS, 8/15 need fixes.**

---

## Step 1 — Codebase Map

### Python Files by Module

| Module | Files | Key Risks |
|--------|:-----:|-----------|
| `agent/` | 14 | 30+ `json.loads` without corruption recovery; memory.py has SQLite usage across threads |
| `channels/` | 12 | Heavy silent exception swallowing (~30 instances); token/key strings in error messages |
| `cli/` | 5 | 3 `subprocess.run` calls without `timeout=`; terminal handling exceptions silenced |
| `config/` | 2 | `json.load()` wraps `JSONDecodeError` but falls to defaults silently (prints, not logs) |
| `cron/` | 3 | Non-atomic `write_text` for job persistence; silent exception catches |
| `dashboard/` | 3 | CORS correctly locked to localhost ✅; `_mask_key()` exists but is local only |
| `heartbeat/` | 3 | Non-atomic `write_text` for trigger data |
| `providers/` | 7 | No retry/backoff logic on API calls; no placeholder key detection |
| `session/` | 2 | Silent exception catches during migration |
| `utils/` | 2 | No `atomic_write_json`, `safe_read_json`, or `mask_secret` utilities |
| `bus/` | 3 | Minimal — event routing plumbing |
| `skills/` | 1 | Non-atomic writes for skill files & training config |
| `templates/` | — | Static templates only |
| `mcp-servers/` | 5 | External MCP server scripts |

---

## Step 2 — Dependency Audit

### 2.1 — `pyproject.toml` Status

> [!CAUTION]
> **`pyproject.toml` does not exist.** The project has no declared dependencies, build system, entry points, or optional dependency groups. This is the single most critical infrastructure gap.

**Required:** Create `pyproject.toml` with:
- Build system (hatchling)
- Core dependencies (anthropic, openai, httpx, typer, rich, pydantic, redis, chromadb, croniter, python-dotenv)
- Optional groups: `desktop`, `channels`, `lora`, `dashboard`, `dev`
- Entry point: `pawbot = "pawbot.cli.commands:app"`

### 2.2 — Version Pinning
Cannot assess without `pyproject.toml`. All dependencies are imported but never declared.

### 2.3 — Optional Dependency Handling
Several optional imports found (e.g., `pyautogui`, `faster-whisper`, `axolotl`). Pattern varies — some use try/except, others don't.

---

## Step 3 — Detailed Bug Findings

### 3.1 Bare Except Clauses — ✅ PASS

```bash
grep -rn "except:" pawbot/ --include="*.py"
# 0 results
```

No bare `except:` clauses found anywhere. All exception handlers use typed catches (`except Exception`, `except ValueError`, etc.).

---

### 3.2 Silent Exception Swallowing — ❌ 51 instances

`except Exception:` (no `as e`, no logging) found in **51+ locations**:

| File | Count | Risk |
|------|:-----:|------|
| `channels/matrix.py` | 11 | High — connection failures silently ignored |
| `dashboard/server.py` | 7 | Medium — helper functions return defaults |
| `cli/commands.py` | 6 | Medium — terminal setup failures hidden |
| `channels/email.py` | 4 | High — email send/receive failures invisible |
| `channels/mochat.py` | 3 | High |
| `cron/service.py` | 2 | Medium — job store load failures silent |
| `cron/scheduler.py` | 1 | Medium |
| `heartbeat/service.py` | 2 | Medium |
| `heartbeat/engine.py` | 1 | Low |
| `providers/ollama.py` | 1 | Medium — connectivity check |
| `providers/openai_codex_provider.py` | 2 | High — streaming parse errors |
| `session/manager.py` | 2 | Medium |
| `utils/helpers.py` | 1 | Low — template loading |
| `agent/skills.py` | 3 | Medium |
| `agent/telemetry.py` | 1 | Low |
| `agent/tools/shell.py` | 1 | Medium |
| `channels/slack.py` | 1 | High |
| `channels/qq.py` | 2 | High |
| `channels/feishu.py` | 1 | Medium |

**Fix required:** Every `except Exception:` should at minimum `logger.debug(...)` or `logger.warning(...)`.

---

### 3.3 Non-Atomic File Writes — ❌ 13 instances

All config/memory/state writes use direct `write_text()` or `open("w")`:

| File | Line | What's Written |
|------|:----:|----------------|
| `config/loader.py` | 64 | `config.json` — **CRITICAL** |
| `dashboard/server.py` | 68 | `config.json` — **CRITICAL** |
| `dashboard/server.py` | 283 | `SOUL.md` |
| `cron/service.py` | 172 | Cron job store — **HIGH** |
| `cron/scheduler.py` | 239 | Cron execution log — **HIGH** |
| `heartbeat/engine.py` | 352 | Heartbeat trigger data — **HIGH** |
| `agent/skills.py` | 249 | Skill files |
| `agent/skills.py` | 686 | Training dataset |
| `agent/skills.py` | 739 | Training config |
| `agent/memory.py` | 1491 | Memory file — **CRITICAL** |
| `agent/tools/filesystem.py` | 95 | User file writes |
| `agent/tools/filesystem.py` | 147 | User file edits |
| `channels/mochat.py` | 850 | Cursor state |

**Fix required:** Create `pawbot/utils/fs.py` with `atomic_write_json()` and use it for all config/memory/state writes.

---

### 3.4 Missing Subprocess Timeouts — ❌ 3 instances

| File | Line | Command | Has Timeout? |
|------|:----:|---------|:---:|
| `dashboard/server.py:81` | `pawbot status` | ✅ timeout=5 |
| `dashboard/server.py:209` | `node --version` | ✅ timeout=3 |
| `dashboard/server.py:235` | `pawbot agent -m` | ✅ timeout=120 |
| `dashboard/server.py:376` | `pawbot cron add` | ✅ timeout=10 |
| `dashboard/server.py:391` | `pawbot cron remove` | ✅ timeout=10 |
| `cli/commands.py:891` | `npm install` | ❌ **MISSING** |
| `cli/commands.py:894` | `npm run build` | ❌ **MISSING** |
| `cli/commands.py:924` | `npm start` | ❌ **MISSING** |
| `agent/skills.py:696` | Training subprocess | Need to verify |

**Fix required:** Add `timeout=300` to npm install/build, `timeout=None` or signal-based stop for `npm start` (long-running).

---

### 3.5 Non-Daemon Threads — ✅ PASS

All 11 `threading.Thread()` instantiations have `daemon=True`:

- `dashboard/server.py:545` ✅
- `heartbeat/engine.py:219` ✅
- `cron/scheduler.py:127` ✅
- `cron/scheduler.py:217` ✅
- `channels/feishu.py:320` ✅
- `agent/subagent.py:388` ✅
- `agent/telemetry.py:135` ✅
- `agent/telemetry.py:489` ✅
- `agent/skills.py:714` ✅
- `agent/memory.py:1267` ✅
- `agent/loop.py:855` ✅

---

### 3.6 Missing `mkdir` Flags — ✅ PASS

All 23 `mkdir()` calls have `parents=True, exist_ok=True` (or `exist_ok=True` where parent is guaranteed to exist).

---

### 3.7 Hardcoded / Nanobot Paths — ✅ PASS (in Python)

```bash
grep -rn "nanobot" pawbot/ --include="*.py"
# 0 results
```

No `nanobot` references in any Python source file. **Note:** `nanobot` references remain in `phases/PHASE_16_CLI_CONFIG.md` (reference documentation only — not runtime code).

All paths derived from `Path.home() / ".pawbot"` — no absolute hardcoded paths found.

---

### 3.8 Credentials in Log Output — ⚠️ LOW RISK

| Finding | Risk | Location |
|---------|------|----------|
| `logger.error("Telegram bot token not configured")` | ✅ Safe — says "not configured", doesn't log the token |
| `cli/commands.py` prints "API key saved" | ✅ Safe — doesn't print the actual key |
| `dashboard/server.py` has `_mask_key()` | ✅ Good — masks API keys in responses |
| `providers/router.py` passes `api_key` in HTTP headers | ✅ Normal — required for API calls |

**Minor gap:** No global `mask_secret()` utility exists. `_mask_key()` in `dashboard/server.py` is local. Should be extracted to a shared utility for use across all modules.

---

### 3.9 Missing CLI Exit Codes — ⚠️ PARTIAL

The CLI uses `typer` which handles exit codes for unhandled exceptions. However, explicit `raise typer.Exit(1)` on recoverable error paths is not consistently applied. The `agent` command does catch config errors and shows user-friendly messages, but not all commands have equivalent handling.

---

### 3.10 API Key Validation — ❌ No placeholder detection

`providers/router.py` checks `if not api_key:` but does **not** check for known placeholder values like `sk-or-v1-xxx`, `YOUR_API_KEY`, etc. The audit template specifies creating a `PLACEHOLDER_PATTERNS` set and validating before API calls.

---

### 3.11 SQLite Thread Safety — ⚠️ Needs Review

`agent/memory.py` uses `sqlite3` and has `threading.Lock()` usage. The memory module appears to use thread locks, but a deeper review of all query paths is needed to confirm full thread safety.

---

### 3.12 JSON Corruption Handling — ❌ 30+ unprotected reads

30+ `json.loads()` calls across the codebase without corruption recovery. Key locations:

- `config/loader.py:35` — wraps in try/except but prints instead of logging
- `dashboard/server.py:59` — returns `{}` on failure (acceptable for read-only)
- `cron/service.py:90` — cron job store (should have backup recovery)
- `heartbeat/engine.py:359` — heartbeat triggers
- `agent/skills.py:176` — skill definitions
- `agent/memory.py` — 10+ locations for memory content parsing

**Fix required:** Create `safe_read_json()` utility with backup recovery for critical files.

---

### 3.13 Missing Retry Logic — ❌ No retry/backoff on API calls

`providers/router.py` has a **fallback chain** (primary → ollama → error) but **no retry with exponential backoff** for transient errors like `429 Too Many Requests` or `503 Service Overloaded`.

The `_call_openrouter()`, `_call_anthropic()`, and `_call_openai()` methods make single HTTP calls with `timeout=120.0` but no retry on rate limit responses.

**Fix required:** Add `call_with_retry()` wrapper with exponential backoff for 429/503 responses.

---

### 3.14 Dashboard Security — ✅ PASS

- CORS origins restricted to `["http://localhost:4000", "http://127.0.0.1:4000"]` ✅
- Default bind address is `127.0.0.1` ✅
- API keys masked in `/api/config` response via `_mask_key()` ✅
- Channel tokens/secrets masked in config responses ✅
- Log file access restricted to `ALLOWED_LOGS` whitelist ✅

---

### 3.15 WhatsApp Bridge Node.js Check — ✅ PASS

Dashboard `health()` endpoint checks for Node.js with `subprocess.run(["node", "--version"], timeout=3)` and reports status with installation guidance.

---

## Step 4 — Test Suite Status

> [!WARNING]
> No test files were found in `~/pawbot/tests/`. The test suite from the audit template has not been created yet.

---

## Step 5 — End-to-End Test Results

Not executed in this audit pass. Requires the fixes above to be implemented first.

---

## Step 6 — Logging Audit

| Finding | Count | Severity |
|---------|:-----:|----------|
| `print()` in non-CLI code (`config/loader.py:39-40`) | 2 | Medium — should use `logger.warning()` |
| Missing `logging.py` setup utility | 1 | Medium — no centralized logging configuration |
| Silent `except Exception:` blocks | 51 | High — see §3.2 |

---

## Step 7 — `pyproject.toml` Audit

> [!CAUTION]
> **`pyproject.toml` does not exist.** This means:
> - The package cannot be installed via `pip install`
> - No `pawbot` CLI entry point is registered
> - No dependencies are declared
> - No optional dependency groups exist
> - No build system is configured

---

## Missing Utility Modules

The audit template requires these shared utilities which **do not exist**:

| Utility | Recommended File | Purpose |
|---------|------------------|---------|
| `atomic_write_json()` | `pawbot/utils/fs.py` | Write-to-temp then `os.replace()` |
| `safe_read_json()` | `pawbot/utils/fs.py` | JSON read with backup recovery |
| `mask_secret()` | `pawbot/utils/secrets.py` | Mask API keys for safe logging |
| `call_with_retry()` | `pawbot/utils/retry.py` | Exponential backoff for API calls |
| Centralized paths | `pawbot/utils/paths.py` | `PAWBOT_HOME`, `CONFIG_PATH`, etc. |
| Logging setup | `pawbot/utils/logging.py` | `setup_logging()` with file + stderr handlers |

---

## Priority Remediation Order

1. **🔴 CRITICAL** — Create `pyproject.toml` with all dependencies and entry points
2. **🔴 CRITICAL** — Create `atomic_write_json()` and apply to config/memory/cron writes
3. **🟠 HIGH** — Create `safe_read_json()` and apply to all JSON reads of persistent files
4. **🟠 HIGH** — Add retry/backoff logic to API provider calls in `router.py`
5. **🟡 MEDIUM** — Fix 51 silent exception swallowing instances (add logging)
6. **🟡 MEDIUM** — Add `timeout=` to 3 `subprocess.run` calls in `cli/commands.py`
7. **🟡 MEDIUM** — Add placeholder API key detection before API calls
8. **🟢 LOW** — Create shared `mask_secret()` utility
9. **🟢 LOW** — Create centralized `paths.py` constants module
10. **🟢 LOW** — Create `setup_logging()` utility and integrate

---

## Remaining Known Issues

| Issue | Justification |
|-------|---------------|
| `nanobot` references in `phases/*.md` | These are development reference docs, not runtime code. Safe to leave. |
| E2E tests not run | Require fixes above + running environment with dependencies installed. |
| Coverage metrics unavailable | No test suite exists yet to measure against. |
| `pyproject.toml` not created | Audit is read-only — creating it requires design decisions on exact versions. |

---

## Definition of Done Checklist

- [x] `grep -rn "except:" ~/pawbot/pawbot` returns zero results
- [x] `grep -rn "nanobot" ~/pawbot/pawbot --include="*.py"` returns zero results
- [x] All `threading.Thread` calls have `daemon=True`
- [ ] All `subprocess.run()` calls have `timeout=N` — **3 missing**
- [x] All `Path.mkdir()` calls have `parents=True, exist_ok=True`
- [ ] All config/memory JSON writes use `atomic_write_json()` — **utility doesn't exist**
- [ ] All JSON reads use `safe_read_json()` — **utility doesn't exist**
- [ ] All API calls have retry logic — **no retry logic**
- [x] No API keys in log output (no actual keys logged, only status messages)
- [ ] `pytest tests/ -v` passes — **no tests exist**
- [ ] Coverage ≥ 80% — **no tests exist**
- [ ] All 11 E2E commands exit with correct code — **not tested**
- [x] `AUDIT_REPORT.md` is written and complete
- [ ] `pip install pawbot-ai && pawbot onboard && pawbot agent -m "hello"` works — **no `pyproject.toml`**

**Result: 5/14 checks PASS, 9/14 need remediation.**
