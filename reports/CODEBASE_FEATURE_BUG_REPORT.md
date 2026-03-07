# Pawbot Feature And Bug Report

Date: 2026-03-07
Scope: `c:\Users\Administrator\Downloads\nanobot\pawbot`

## Codebase Snapshot

- Runtime app code under `pawbot/`: 174 Python files, 31,748 Python lines.
- Bundled UI under `pawbot/`: 2 HTML files, 1,265 lines.
- Main subsystems reviewed: agent runtime, multi-agent pool, memory, channels, gateway, dashboard, canvas, delivery queue, scheduling, providers, MCP/browser integrations.

## Feature Inventory

### Core agent runtime

- `pawbot/agent/loop.py`: main conversation loop, tool orchestration, session handling, response building.
- `pawbot/agent/agent_router.py`: contact/channel-based routing into per-agent configs.
- `pawbot/agents/pool.py`: isolated multi-agent runtime instances with restart, status, and direct execution support.
- `pawbot/session/manager.py`: session persistence and restoration.

### Memory system

- `pawbot/agent/memory/sqlite_store.py`: persistent fact store, archival, links, inbox, decay.
- `pawbot/agent/memory/chroma_store.py`: vector/semantic retrieval.
- `pawbot/agent/memory/redis_store.py`: working-memory style fast store.
- `pawbot/agent/memory/router.py`: backend routing and merge logic.
- `pawbot/agent/memory/decay.py`, `linker.py`, `consolidation.py`: salience decay, relationship inference, memory compaction.

### Channels and delivery

- `pawbot/channels/*`: Telegram, WhatsApp, Discord, Slack, Matrix, Email, Feishu, DingTalk, QQ, Mochat.
- `pawbot/channels/manager.py`: channel lifecycle, outbound dispatch, duplicate suppression, dead-letter logging.
- `pawbot/delivery/queue.py`: persistent delivery queue with retries, expiry, and failed-message recovery.

### Gateway and dashboard

- `pawbot/gateway/server.py`: public API/WS gateway, one-shot REST chat, session lanes, metrics.
- `pawbot/dashboard/server.py`: dashboard backend, auth, config APIs, memory/log views, queue APIs, metrics.
- `pawbot/dashboard/auth.py`: password hashing and signed dashboard session tokens.
- `pawbot/canvas/server.py` and `pawbot/canvas/index.html`: rendered output viewer, session list, live websocket updates.

### Scheduling and automation

- `pawbot/cron/*`: cron registry and scheduler.
- `pawbot/heartbeat/*` and `pawbot/agents/heartbeat.py`: heartbeat scheduling and per-agent keepalive automation.

### Providers, tools, and integrations

- `pawbot/providers/*`: provider routing and auth-aware provider matching.
- `pawbot/tools/*` and `pawbot/agent/tools/*`: filesystem, exec, MCP, browser, web, message tools.
- `mcp-servers/*`: local MCP servers for browser, coding, deploy, app control, server control.

## Verified Bugs To Fix

### 1. High: memory link persistence is broken against the migrated SQLite schema

Evidence:

- `pawbot/agent/memory/migrations.py:79-86` creates `memory_links.id` as `INTEGER PRIMARY KEY AUTOINCREMENT`.
- `pawbot/agent/memory/sqlite_store.py:359-368` generates a UUID string and inserts it into that integer primary key column.

Reproduction:

- Created a fresh SQLite store with migrations applied.
- Saved two facts.
- Called `SQLiteFactStore.save_link(..., "supports")`.
- Result: `sqlite3.IntegrityError: datatype mismatch`.

Impact:

- Relationship graph features cannot persist links.
- Contradiction/support/depends-on logic is unreliable anywhere link creation is expected.

Fix:

- Make schema and code agree.
- Either store `id` as `TEXT PRIMARY KEY` in migrations, or let SQLite autogenerate the integer key and stop inserting UUID strings.

### 2. High: subagent inbox write/review path is incompatible with the migrated schema

Evidence:

- `pawbot/agent/memory/migrations.py:122-130` creates `subagent_inbox.id` as `INTEGER PRIMARY KEY AUTOINCREMENT` and uses `status` / `reviewed_at`.
- `pawbot/agent/memory/sqlite_store.py:393-405` inserts a UUID string into `id`.
- `pawbot/agent/memory/sqlite_store.py:411-434` queries and updates `reviewed` and `accepted` columns, which the migration schema does not create.

Reproduction:

- Created a fresh SQLite store with migrations applied.
- Called `SQLiteFactStore.inbox_write(...)`.
- Result: `sqlite3.IntegrityError: datatype mismatch`.

Impact:

- The subagent review pipeline is effectively unusable on a clean database.
- Any dashboard or automation depending on inbox acceptance will fail at runtime.

Fix:

- Align the migration schema with the current code, or rewrite the code to match the migrated schema.
- Add migration coverage for inbox write and inbox review on a freshly initialized database.

### 3. High: dashboard canvas websocket bypasses authentication

Evidence:

- `pawbot/dashboard/server.py:52-70` enforces auth through `BaseHTTPMiddleware`.
- `pawbot/dashboard/server.py:73-74` mounts canvas routes on the authenticated dashboard app.
- `pawbot/canvas/server.py:175-199` accepts websocket connections directly and does not validate a session token.

Reproduction:

- Unauthenticated `GET /canvas` returns `401`.
- Unauthenticated `websocket_connect("/canvas/ws")` succeeds and streams canvas session data.

Impact:

- Private dashboard-generated canvas output can be read without logging in.
- This is a direct auth boundary bypass, not just a UI quirk.

Fix:

- Add explicit websocket auth validation using the dashboard session cookie or a signed token.
- Do not rely on `BaseHTTPMiddleware` for websocket protection.

### 4. Medium: `/api/observability` is unreachable on the dashboard

Evidence:

- `pawbot/dashboard/server.py:805-809` registers the SPA catch-all `/{path:path}`.
- `pawbot/dashboard/server.py:835-853` registers `/api/observability` after the catch-all.
- `pawbot/dashboard/server.py:832-833` also calls `start()` before that route when the file is executed directly.

Reproduction:

- Logged into the dashboard with `TestClient`.
- Requested `GET /api/observability`.
- Result: `200 text/html` with the dashboard HTML, not JSON.

Impact:

- The observability API is effectively dead from the dashboard app.
- Any client expecting JSON from that endpoint will silently receive the SPA shell.

Fix:

- Move `/api/observability` above the SPA fallback.
- Keep all route declarations above the `if __name__ == "__main__": start()` block.

### 5. Medium: dashboard memory APIs ignore the actual SQLite/Chroma memory system

Evidence:

- `pawbot/dashboard/server.py:497-554` reads only `workspace/memory/MEMORY.md`.
- The real persistent memory implementation lives in `pawbot/agent/memory/sqlite_store.py` and related router/backends.

Reproduction:

- Inserted a fact into SQLite through `SQLiteFactStore`.
- Called authenticated `GET /api/memory`.
- Result: empty `items` and `total: 0` because no `MEMORY.md` file existed.

Impact:

- The dashboard’s memory browser/export does not reflect the system of record.
- Operators can think memory is empty even when facts exist in SQLite/Chroma.

Fix:

- Back the dashboard memory APIs with `MemoryRouter` or the SQLite store instead of markdown scraping.
- If `MEMORY.md` is kept as a human-readable cache, label it as such and do not present it as the full memory dataset.

### 6. Medium: dashboard API-key validation endpoint always returns success

Evidence:

- `pawbot/dashboard/server.py:700-704` is an unconditional placeholder that returns `{"valid": True, "latency_ms": 0}`.

Reproduction:

- Authenticated to the dashboard.
- Posted an empty API key to `POST /api/config/test-key`.
- Result: `200` and `{"valid": true, "latency_ms": 0}`.

Impact:

- The dashboard can incorrectly tell an operator that an invalid or blank key is valid.
- This undermines onboarding and production debugging.

Fix:

- Either implement real provider-specific validation or remove the endpoint from the UI until it is real.

## Feature Gaps Worth Tracking

- `pawbot/dashboard/server.py:610-612` returns a hard-coded empty stub for `/api/subagents`.
- The dashboard surface area is materially behind the underlying runtime in memory and subagent observability.

## Verification Notes

- Feature mapping was done from the repo structure and core modules.
- Bugs above were verified by direct code inspection plus targeted Python reproductions.
- Broad pytest runs are still noisy in this Windows sandbox because pytest temp-directory cleanup hits `PermissionError` on workspace temp roots, so the strongest evidence here comes from direct runtime reproductions instead of a full clean suite run.
