# Deep Scan Report (Read-Only)

Date: 2026-03-02  
Project: `C:\Users\Administrator\Downloads\nanobot\pawbot`  
Mode: No source-code changes

## 1. Scope and commands run

This scan used runtime, lint, security, dead-code, complexity, and hygiene checks:

1. `python -m pytest --collect-only`, targeted test runs, and duration profiling
2. `python -m compileall -q pawbot mcp-servers tests`
3. `ruff check pawbot mcp-servers tests`
4. `bandit -q -r pawbot mcp-servers -ll`
5. `vulture pawbot mcp-servers --min-confidence 80`
6. `radon cc pawbot mcp-servers -s -n D`
7. repo hygiene checks for tracked `.pyc` / `__pycache__`

## 2. Executive summary

1. Tests: 547 tests discovered.
2. No failing tests were found in file-level runs, but two files are extremely slow:
   - `tests/test_context.py`: 58 passed in 719.57s
   - `tests/test_agent_loop.py`: 45 passed in 675.38s
3. Main time waste confirmed: `MemoryStore` initialization is heavy (about 32s per init) due to backend and embedding setup.
4. Security scan (Bandit): 103 findings total, including 6 high and 4 medium.
5. Production lint (Ruff): 33 issues.
6. Test lint (Ruff): 52 issues.
7. Repo hygiene: 108 tracked compiled artifacts (`.pyc`/`__pycache__`), no root `.gitignore`.

## 3. Security findings (Bandit, medium/high)

### High severity

1. `B602` at `mcp-servers/app_control/server.py:346` (`shell=True`)
2. `B324` at `mcp-servers/coding/server.py:101` (`hashlib.md5`)
3. `B602` at `mcp-servers/coding/server.py:250` (`shell=True`)
4. `B602` at `mcp-servers/deploy/server.py:66` (`shell=True`)
5. `B602` at `mcp-servers/server_control/server.py:191` (`shell=True`)
6. `B602` at `mcp-servers/server_control/server.py:204` (`shell=True`)

### Medium severity

1. `B104` at `pawbot/agent/telemetry.py:488` (`0.0.0.0`)
2. `B113` at `pawbot/agent/tools/mcp.py:110` (`timeout=None`)
3. `B310` at `pawbot/cli/commands.py:1529` (`urllib.request.urlopen`)
4. `B104` at `pawbot/config/schema.py:347` (`0.0.0.0`)

Bandit totals: `HIGH 6`, `MEDIUM 4`, `LOW 93`, `TOTAL 103`.

## 4. Production lint findings (Ruff) - complete list

Ruff totals for production code: `33`

Code counts:

1. `F401`: 17
2. `E701`: 5
3. `E741`: 5
4. `F541`: 2
5. `F841`: 2
6. `F811`: 1
7. `F821`: 1

Complete per-file list:

1. `mcp-servers/app_control/server.py`: `F401@21`
2. `mcp-servers/browser/server.py`: `F401@18`
3. `pawbot/agent/context.py`: `F401@27`, `F401@28`, `F841@792`
4. `pawbot/agent/loop.py`: `F401@13`, `F821@576`
5. `pawbot/agent/security.py`: `F401@18`, `F401@20`, `F401@20`
6. `pawbot/agent/subagent.py`: `F401@676`
7. `pawbot/bus/router.py`: `F401@10`
8. `pawbot/channels/dingtalk.py`: `E701@207`, `E701@208`, `E701@209`, `E701@319`, `E701@320`
9. `pawbot/channels/manager.py`: `F401@10`
10. `pawbot/channels/matrix.py`: `F401@17`
11. `pawbot/channels/slack.py`: `F401@5`
12. `pawbot/channels/whatsapp.py`: `F401@13`, `F401@21`
13. `pawbot/cli/commands.py`: `F541@220`, `F541@259`
14. `pawbot/cli/formatter.py`: `F401@12`
15. `pawbot/config/schema.py`: `F811@187`
16. `pawbot/cron/service.py`: `F841@252`
17. `pawbot/dashboard/server.py`: `F401@8`, `E741@122`, `E741@242`, `E741@514`, `E741@516`
18. `pawbot/providers/openai_codex_provider.py`: `E741@231`

## 5. Test lint findings (Ruff) - complete list

Ruff totals for tests: `52`

Code counts:

1. `F401`: 33
2. `E402`: 9
3. `F841`: 8
4. `E741`: 2

Complete per-file list:

1. `tests/test_agent_loop.py`: `F401@11`, `F401@14`, `F841@230`
2. `tests/test_app_control.py`: `F401@17`, `F401@22`, `F401@23`, `F841@478`
3. `tests/test_browser_mcp.py`: `F401@21`, `F401@22`, `F401@22`, `F401@22`, `F841@456`
4. `tests/test_channels.py`: `F401@16`, `F401@17`, `F401@19`, `F401@20`, `F401@21`, `F401@21`, `F401@23`, `E402@35`, `E402@36`, `E402@37`, `E402@38`, `F841@477`
5. `tests/test_cli.py`: `F401@16`, `F401@21`, `E402@28`, `E402@35`
6. `tests/test_context.py`: `F401@15`, `F401@16`, `F401@17`, `F841@179`, `F841@507`
7. `tests/test_deploy_mcp.py`: `F401@10`
8. `tests/test_model_router.py`: `F401@9`, `F401@10`
9. `tests/test_observability.py`: `F401@18`, `F401@21`, `F401@21`, `E402@31`, `F841@158`, `F841@444`, `E741@457`
10. `tests/test_security.py`: `F401@14`, `F401@16`, `F401@17`, `F401@19`, `E402@28`, `E741@131`
11. `tests/test_subagents.py`: `F401@18`, `F401@19`, `E402@28`

## 6. Dead/unused code indicators (Vulture, production)

1. `mcp-servers/app_control/server.py:41` unused import `Image`
2. `mcp-servers/browser/server.py:25` unused imports `Browser`, `BrowserContext`, `Page`
3. `mcp-servers/browser/server.py:259` unused variable `restore_session`
4. `mcp-servers/browser/server.py:772` unused variable `full_page`
5. `pawbot/agent/memory.py:1316` unused vars `existing_mem`, `new_mem`
6. `pawbot/agent/security.py:191` unused variable `caller`
7. `pawbot/agent/subagent.py:510` unused variable `watch_task`
8. `pawbot/channels/feishu.py:21` unused import `P2ImMessageReceiveV1`
9. `pawbot/channels/matrix.py:14` unused import `ContentRepositoryConfigError`
10. `pawbot/channels/qq.py:16` and `:25` duplicate/unused `C2CMessage`
11. `pawbot/cli/commands.py:660` unused variable `signum`
12. `mcp-servers/coding/server.py:245` helper `_run_shell` appears unused

## 7. Complexity hotspots (Radon D/E only)

1. `pawbot/agent/loop.py:585` `AgentLoop._process_message` - `E (33)`
2. `pawbot/agent/loop.py:400` `AgentLoop._run_agent_loop` - `D (22)`
3. `pawbot/agent/tools/base.py:62` `Tool._validate` - `D (27)`
4. `pawbot/channels/discord.py:220` `DiscordChannel._handle_message_create` - `D (21)`
5. `pawbot/channels/email.py:226` `EmailChannel._fetch_messages` - `D (22)`
6. `pawbot/channels/feishu.py:110` `_extract_element_content` - `E (31)`
7. `pawbot/channels/feishu.py:673` `FeishuChannel._on_message` - `D (22)`
8. `pawbot/channels/manager.py:34` `ChannelManager._init_channels` - `D (21)`
9. `pawbot/channels/slack.py:107` `SlackChannel._on_socket_request` - `D (26)`
10. `pawbot/channels/telegram.py:343` `TelegramChannel._on_message` - `D (21)`
11. `pawbot/config/schema.py:416` `Config._match_provider` - `D (21)`
12. `pawbot/providers/openai_codex_provider.py:246` `_consume_sse` - `E (32)`
13. `mcp-servers/coding/server.py:524` `code_search` - `D (27)`
14. `mcp-servers/coding/server.py:745` `code_run_checks` - `D (23)`
15. `mcp-servers/server_control/server.py:553` `server_nginx` - `D (29)`

## 8. Waste-time and hygiene findings

1. 108 compiled artifacts (`.pyc` and `__pycache__`) are tracked in git.
2. No root `.gitignore` exists, so bytecode churn pollutes the repo.
3. 121 `.pyc` files contain stale external source paths, creating confusing traceback paths.
4. `pytest` command fails import in this shell, while `python -m pytest` works.
5. `requests` emits a dependency compatibility warning (`urllib3/chardet/charset_normalizer`).
6. Test runtime waste: heavy backend/model initialization for context/memory fixtures.

## 9. Detailed remediation guide (prioritized)

### Phase 1 - Repo hygiene and deterministic workflow

1. Add root `.gitignore` with:
   - `__pycache__/`
   - `*.py[cod]`
   - `.pytest_cache/`
   - virtualenv/cache/temp patterns used by the team
2. Remove tracked bytecode from git index.
3. Standardize docs/CI on `python -m pytest` (not bare `pytest`).

### Phase 2 - Remove high-risk command execution

1. Replace `shell=True` call sites with explicit argv execution where feasible.
2. For unavoidable shell use, enforce strict allowlist and validated interpolation.
3. Gate dangerous tool commands behind explicit confirmation and policy checks.

### Phase 3 - Recover test and developer time

1. Make memory backend initialization lazy.
2. In tests, inject lightweight or mocked memory/chroma/embedding backends.
3. Disable expensive embedding model startup in default test path.
4. Target:
   - `tests/test_context.py` under 60s
   - `tests/test_agent_loop.py` under 60s

### Phase 4 - Fix correctness and schema risks

1. Resolve duplicate class definition (`F811`) in `pawbot/config/schema.py`.
2. Resolve `F821` (`BaseExceptionGroup`) by setting explicit Ruff target version and python policy.

### Phase 5 - Clean lint debt

1. Run safe auto-fixes (`ruff --fix`) on production and tests.
2. Manually fix remaining:
   - `E701` multiline split issues
   - `E741` ambiguous variable names
   - unresolved `F811` and `F821`

### Phase 6 - Refactor complexity hotspots

1. Split D/E complexity methods into smaller units with clear boundaries.
2. Start with the top 5 complexity functions and add focused unit tests per extracted helper.

### Phase 7 - Security policy in CI

1. Add CI gate:
   - fail build on Bandit high findings
   - track medium findings as warnings until triaged
2. Keep a suppression policy file with rationale and expiry for any accepted risks.

## 10. Important note

This report was generated in read-only scan mode. No source `.py` files were modified by the scan itself.
