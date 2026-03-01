# PROMPT — DESIGN & BUILD PAWBOT LOCAL DASHBOARD

You are an expert full-stack engineer and UI designer. Your job is to build a **local web dashboard** for Pawbot — a personal AI assistant framework. The dashboard runs at `http://localhost:4000` and gives the owner full control over every subsystem: the agent, memory, channels, cron jobs, subagents, skills, config, and logs. 

This is a **private, owner-only tool**. There is no auth layer. No marketing copy. No empty states with cute illustrations. Every pixel serves a function. The aesthetic is **precision engineering** — dark background, sharp edges, monospaced data, surgical colour use. Think a Bloomberg terminal crossed with a modern IDE.

Read this entire file before writing a single line of code.

---

## WHAT YOU ARE BUILDING

One Python file + one HTML/CSS/JS file. That's it. No framework build step, no node_modules, no Docker required. It must start with:

```bash
pawbot dashboard
# opens http://localhost:4000 automatically
```

| File | Purpose |
|------|---------|
| `~/pawbot/pawbot/dashboard/server.py` | FastAPI backend — reads config, memory, logs, channels, cron, skills |
| `~/pawbot/pawbot/dashboard/ui.html` | Single-file frontend — all HTML + CSS + JS in one file |
| `~/pawbot/pawbot/cli/commands.py` | Add `pawbot dashboard` command |

---

## STEP 1 — READ THESE FILES BEFORE WRITING ANY CODE

```bash
cat ~/pawbot/pyproject.toml
cat ~/pawbot/pawbot/cli/commands.py
cat ~/pawbot/pawbot/config/loader.py
cat ~/.pawbot/config.json
ls ~/.pawbot/workspace/
ls ~/.pawbot/logs/ 2>/dev/null || echo "no logs yet"
```

Understand the exact data shapes before building any API endpoints or UI components.

---

## STEP 2 — AESTHETIC DIRECTION

### The look

**Dark theme. Absolute black base (`#080808`). Not dark grey — black.**

```
Background:     #080808
Surface cards:  #111111
Borders:        #222222 (1px, sharp — never rounded more than 4px)
Text primary:   #F0F0F0
Text secondary: #666666
Text dim:       #333333
Accent:         #00FF88  (electric green — Pawbot's brand colour)
Accent dim:     #00FF8820 (green tint for backgrounds)
Warning:        #FFB800
Error:          #FF4444
Success:        #00FF88
```

**Typography:**
- Headers / labels: `'IBM Plex Mono', monospace` — every label feels like terminal output
- Body / values: `'IBM Plex Sans', sans-serif`
- Load both from Google Fonts

**Layout:**
- Fixed left sidebar (220px wide) — navigation only, no clutter
- Main content area fills the rest
- No rounded cards — `border-radius: 4px` maximum, everywhere
- 1px borders everywhere — never shadows
- Tables, not cards, for list data
- Status dots: 8px circles, green/red/yellow, no labels needed beside them

**Motion:**
- Sidebar nav item hover: `background` slides in from left (200ms)
- Page transitions: instant — no animations between sections
- Status dot for agent online: slow pulse (3s), green
- Log lines: fade in from bottom when new lines arrive (150ms)
- Table row hover: `background: #181818` (100ms)
- All other interactions: 0ms — data tools must feel instant

**What to NEVER do:**
- No gradients anywhere
- No drop shadows
- No rounded corners beyond 4px
- No empty-state illustrations
- No onboarding tooltips
- No animated loading spinners — use a 1px green progress bar at the very top of the page
- No colour besides the 7 defined above

---

## STEP 3 — DASHBOARD SECTIONS (ALL 9)

Build every section. The left sidebar shows all 9. Default view on load is **Section 1: Overview**.

---

### SECTION 1 — OVERVIEW

**Route:** `/` (default)

The command centre. Everything at a glance.

**Layout:** 3-column stat bar at top, then two columns below.

**Top stat bar (4 stats, equal width):**
```
[ Agent Status ]  [ Memory Items ]  [ Active Channels ]  [ Cron Jobs ]
   ● ONLINE            2,847               2/3                 5 active
```

**Left column (60% width):**

`LIVE AGENT LOG` — real-time stream of the agent's activity. Reads from `~/.pawbot/logs/agent.log`. New lines appear at bottom, scroll up automatically. Max 200 lines visible. Each line has:
- Timestamp in dim green (`#00FF8840`)  
- Log level badge: `[TOOL]` `[MEM]` `[LLM]` `[ERR]` in respective colours
- Message text

**Right column (40% width):**

`QUICK CHAT` — a minimal inline chat panel. Input at bottom, conversation scrolls up. Calls `POST /api/chat`. Not a full chat UI — just for quick test messages to the agent without opening a terminal.

```
┌─ QUICK CHAT ──────────────────────┐
│                                   │
│  [assistant] Hello! How can I...  │
│                                   │
│  > _______________________ [Send] │
└───────────────────────────────────┘
```

Below quick chat:

`SYSTEM HEALTH` — compact version of `pawbot doctor` output. 5–7 rows, each with status dot + check name. Refresh button top-right. Calls `GET /api/health`.

---

### SECTION 2 — AGENT

**Route:** `/agent`

Full agent control panel.

**Subpanels:**

**Current session** — shows session ID, start time, total tokens used this session, model in use, current system prompt (collapsed, expandable).

**Model selector** — dropdown of all configured providers + their models. Changing it calls `POST /api/agent/model` and restarts the agent context. Shows current model prominently.

**System prompt editor** — `<textarea>` pre-filled with current `SOUL.md` content. `[Save & Reload]` button calls `POST /api/agent/soul`. Monospaced font. Resizable vertically.

**Token usage** — bar chart (pure CSS, no chart library): today's token usage by model. Each model gets one row. Bars fill left-to-right. Numbers on right.

```
claude-sonnet-4-5  ████████████████░░░░  48,230 / 100,000
gpt-4o             ██░░░░░░░░░░░░░░░░░░   5,100 / 100,000
```

**Agent controls:**
```
[ ● Stop Agent ]  [ ↺ Restart Agent ]  [ ✕ Clear Context ]
```
All three are dangerous — show a 1-line inline confirmation before executing.

---

### SECTION 3 — MEMORY

**Route:** `/memory`

Full memory browser and editor.

**Top toolbar:**
```
Search: [___________________]   Type: [ All ▼ ]   Sort: [ Newest ▼ ]   [ + Add Memory ]
```

**Memory table** — one row per memory item. Columns:

| # | Type | Content (truncated 80 chars) | Salience | Created | Actions |
|---|------|------------------------------|----------|---------|---------|
| 1 | fact | The user prefers dark mode… | 0.92 | 2d ago | [Edit] [Archive] |

- Type badge: coloured pill (`fact` = blue, `preference` = purple, `decision` = orange, `episode` = green, `procedure` = yellow, `task` = red, `risk` = pink, `reflection` = grey)
- Clicking a row expands it inline to show full content
- `[Edit]` opens an inline editor directly in the row — no modal
- `[Archive]` turns the row grey with a strikethrough, calls `POST /api/memory/{id}/archive`
- Salience shown as a number + a thin coloured bar behind it (green=high, yellow=medium, red=low)

**Bottom bar:**
```
Showing 1–50 of 2,847     [ ← Prev ]  Page 1 of 57  [ Next → ]     [ Run Decay Pass ]  [ Export All ]
```

`[Run Decay Pass]` calls `POST /api/memory/decay` and shows a progress bar.

`[Export All]` downloads a `.jsonl` file of all memories.

**Memory stats sidebar (right 200px):**
```
BY TYPE
fact          1,203
preference      445
episode         398
procedure       287
decision        214
task            183
risk             89
reflection       28

TOTAL       2,847
ARCHIVED      312
```

---

### SECTION 4 — CHANNELS

**Route:** `/channels`

Manage Telegram, WhatsApp, and Email connections.

**For each channel — a panel:**

```
┌─ TELEGRAM ──────────────────────────────── ● CONNECTED ─┐
│                                                          │
│  Bot:        @mypawbot_bot                               │
│  Token:      7123456789:AAH••••••••••••  [Reveal] [Edit] │
│  Allow From: 123456789, 987654321        [Edit]          │
│  Gateway:    ● Running  PID 12345                        │
│                                                          │
│  Messages today:  47   This week:  312   Total:  8,421   │
│                                                          │
│  RECENT MESSAGES                                         │
│  14:23  → user  "what's the weather?"                    │
│  14:23  ← bot   "It's 28°C in Mumbai…"                   │
│  09:11  → user  "remind me about standup"                │
│                                                          │
│  [ ↺ Restart Gateway ]  [ ■ Stop Gateway ]  [ Test Bot ] │
└──────────────────────────────────────────────────────────┘
```

```
┌─ WHATSAPP ──────────────────────────────── ● OFFLINE ───┐
│                                                          │
│  Bridge:     Not running                                 │
│  Node.js:    v20.11.0  ✓                                 │
│                                                          │
│  [ ▶ Start Bridge ]  [ Scan QR Code ]                    │
│                                                          │
│  When you click [Scan QR Code], a QR code appears        │
│  inline here. Scan it with WhatsApp on your phone.       │
└──────────────────────────────────────────────────────────┘
```

`[Test Bot]` sends a test message to the first `allowFrom` number/ID and shows the response inline.

Token and API key fields: show masked by default (`••••••`). `[Reveal]` shows for 10 seconds then re-masks.

---

### SECTION 5 — CRON

**Route:** `/cron`

Full cron job manager.

**Top bar:**
```
[ + New Job ]
```

**Job table:**

| Name | Schedule | Next Run | Last Run | Runs | Status | Actions |
|------|----------|----------|----------|------|--------|---------|
| morning | 0 9 * * * | 09:00 tomorrow | 09:00 today | 142 | ● Active | [Run Now] [Edit] [Pause] [Delete] |
| weekly | 0 8 * * 1 | Mon 08:00 | Mon 08:00 | 31 | ● Active | ... |

`[Run Now]` fires the job immediately — shows spinner in that row, then success/fail.

`[Edit]` expands the row to an inline editor:
```
  Name:    [morning________________]
  Message: [Good morning! What's on____________________________]
  Cron:    [0 9 * * *_____]   → Human: Every day at 9:00 AM
           [Save]  [Cancel]
```

The cron expression auto-translates to plain English as the user types it.

`[Pause]` toggles the job off — row goes dim, status shows `◌ Paused`.

**`[+ New Job]`** expands a form at the top of the table:
```
  Name:     [_________________]
  Message:  [_____________________________________]
  Schedule: ● Cron  [0 9 * * *]  → Every day at 9:00 AM
            ○ Every  [___] seconds
            [Create Job]
```

---

### SECTION 6 — SUBAGENTS

**Route:** `/subagents`

Live subagent monitor.

**Active subagents** (empty state = "No active subagents" in dim text, nothing else):

```
┌─ researcher-1 ─────────────────────────── ● RUNNING 00:42 ─┐
│  Task:    "Research recent AI papers on memory systems"     │
│  Role:    researcher                                        │
│  Model:   claude-sonnet-4-5                                 │
│  Budget:  ████████░░░░░░░░  4,230 / 8,000 tokens           │
│  Steps:   12 / 15 iterations                               │
│                                                            │
│  Latest:  "Found 3 relevant papers on episodic memory…"    │
│                                                            │
│  [ ■ Cancel ]                                              │
└────────────────────────────────────────────────────────────┘
```

**Inbox** — discoveries waiting for review (from completed subagents):

```
INBOX  (3 pending)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  researcher-1  │  fact  │  "LLMs with episodic memory show…"  │  conf: 0.87
  [ ✓ Accept ]  [ ✕ Reject ]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  researcher-1  │  fact  │  "The attention sink phenomenon…"   │  conf: 0.72
  [ ✓ Accept ]  [ ✕ Reject ]
```

`[Accept All]` / `[Reject All]` buttons at top of inbox.

**Spawn subagent** — form at bottom of page:
```
  Task:   [_____________________________________________]
  Role:   [ researcher ▼ ]   Budget: [ 8000 ] tokens   [ ▶ Spawn ]
```

**Completed subagents** — collapsible table of last 20 runs with task, role, tokens used, duration, success/fail.

---

### SECTION 7 — SKILLS

**Route:** `/skills`

Skill library browser and editor.

**Top bar:**
```
Search: [_________________]                    [ + Create Skill ]
```

**Skill cards** — grid layout (3 columns). Each card:

```
┌─────────────────────────────────┐
│  deploy-to-vps          v1.3    │
│  ─────────────────────────────  │
│  Deploy application to VPS      │
│  ─────────────────────────────  │
│  Triggers: deploy, vps, server  │
│  Tools:    deploy_app, server…  │
│  Uses:     47    Avg: 2,340 tok │
│                                 │
│  [Edit]  [Delete]               │
└─────────────────────────────────┘
```

`[Edit]` opens a full-page skill editor (replaces the grid):

```
EDITING: deploy-to-vps
─────────────────────────────────────────────────────────

  Name:         [deploy-to-vps___________________]  v1.3
  Description:  [_________________________________]
  Triggers:     [deploy, vps, server______________]  (comma separated)

  SYSTEM PROMPT
  ┌─────────────────────────────────────────────────────┐
  │ You are executing the deploy-to-vps skill.          │
  │ Follow these steps precisely:                       │
  │                                                     │
  │                                                     │
  └─────────────────────────────────────────────────────┘

  STEPS
  1. [Build application_____________________________]  [✕]
  2. [Upload to server______________________________]  [✕]
  3. [Run deployment script_________________________]  [✕]
  4. [Verify service status_________________________]  [✕]
  [+ Add step]

  TOOLS USED: [deploy_app, server_run, service_control_____]

  [ Save Changes ]  [ Cancel ]  [ Delete Skill ]
```

`[+ Create Skill]` opens the same editor but empty.

**Stats row below the grid:**
```
Total skills: 12    Most used: deploy-to-vps (47)    Total skill runs: 312
```

---

### SECTION 8 — CONFIG

**Route:** `/config`

Full config editor. This is the most powerful section.

**Layout:** Two-column. Left = tree navigation. Right = editor for selected section.

**Left tree:**
```
CONFIG
├── providers
│   ├── openrouter      ✓
│   └── anthropic       ✗ (no key)
├── agents
│   └── defaults
├── channels
│   ├── telegram        ✓
│   └── whatsapp        ✗
├── tools
│   └── web
├── security            (from PHASE_14)
├── observability       (from PHASE_15)
├── subagents           (from PHASE_12)
├── skills              (from PHASE_13)
└── heartbeat           (from PHASE_11)
```

Clicking a node shows its fields on the right.

**Right editor:**

For each config key, show a **typed form field** — not a raw JSON textarea.

Examples:

```
PROVIDERS › OPENROUTER
──────────────────────────────────────────────

  API Key     [sk-or-v1-••••••••••••••]  [Reveal] [Test]
  
  Status      ● Connected  (tested 2 min ago)

  [Save Changes]
```

```
AGENTS › DEFAULTS
──────────────────────────────────────────────

  Model       [ anthropic/claude-sonnet-4-5 ▼ ]
              ↳ Options pulled from configured providers

  Max tokens  [ 8192  ]

  Temperature [ 0.7   ]   (0.0 – 1.0)

  [Save Changes]
```

```
SECURITY
──────────────────────────────────────────────

  Block root execution        [ ● ON  ○ OFF ]
  Require confirmation        [ ● ON  ○ OFF ]
  Injection detection         [ ● ON  ○ OFF ]
  Min memory salience         [ 0.2  ]   (0.0 – 1.0)
  Max memory tokens per item  [ 300  ]   (50 – 2000)
  Audit log path              [ ~/.pawbot/logs/security_audit.jsonl ]

  [Save Changes]
```

**`[Test]` button on API keys:** fires `POST /api/config/test-key` → shows `✓ Valid` or `✗ Invalid (401)` inline next to the field.

**Raw JSON fallback:** a `[View Raw JSON]` toggle at the top of the right panel — switches to a syntax-highlighted JSON editor (`<textarea>`) for power users. `[Apply Raw]` validates and saves. Syntax errors shown inline.

**Danger zone** (at very bottom of the page, separated by a red line):
```
────────────────────── DANGER ZONE ──────────────────────────────
  [ Reset Config to Defaults ]   [ Wipe All Memory ]   [ Factory Reset ]
  These actions cannot be undone. Type "CONFIRM" to proceed.
```

---

### SECTION 9 — LOGS

**Route:** `/logs`

All logs in one place. Professional log viewer.

**Top bar:**
```
File: [ agent.log ▼ ]   Level: [ ALL ▼ ]   Search: [____________]   [ ↓ Tail ]  [ ⤓ Download ]
```

File dropdown: `agent.log`, `gateway.log`, `security_audit.jsonl`, `traces.jsonl`

**Log viewport:**
```
14:23:01.445  [TOOL]  server_run: cd /app && npm run build
14:23:03.112  [MEM]   saved: "User prefers vite over webpack"  id=a3f9
14:23:03.890  [LLM]   claude-sonnet-4-5  →  483 tokens  (in: 312, out: 171)
14:23:04.221  [TOOL]  server_run completed in 2.3s  exit=0
14:23:04.500  [INFO]  task complete
```

Colour rules per level:
- `[TOOL]` — cyan `#00CCFF`
- `[MEM]` — purple `#CC88FF`
- `[LLM]` — green `#00FF88`
- `[ERR]` — red `#FF4444`
- `[WARN]` — yellow `#FFB800`
- `[INFO]` — dim `#666666`
- Timestamps — very dim `#333333`

`[ ↓ Tail ]` toggle: when ON, auto-scrolls to bottom as new lines arrive (polling `/api/logs/stream` every second). When OFF, allows free scroll.

`[ ⤓ Download ]` — downloads the full log file.

**Trace viewer** — appears when `traces.jsonl` is selected. Shows spans in a waterfall:

```
agent.process          ████████████████████████████  240ms
  ├─ memory.search     ██                             18ms
  ├─ model.call        ████████████████               160ms
  └─ tool.server_run   ████                           42ms
```

Clicking any span shows its full attributes in a panel on the right.

---

## STEP 4 — BACKEND API (FastAPI)

**File:** `~/pawbot/pawbot/dashboard/server.py`

```python
#!/usr/bin/env python3
"""
Pawbot Dashboard Backend
Serves the UI and exposes REST API endpoints for all dashboard sections.
Start with: pawbot dashboard
"""

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import json, subprocess, asyncio, time, os

# Import Pawbot internals
from pawbot.config.loader import load_config, save_config
from pawbot.agent.memory import MemoryRouter
# Import other modules as they exist — add graceful fallbacks

app = FastAPI(title="Pawbot Dashboard", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PAWBOT_HOME = Path("~/.pawbot").expanduser()
CONFIG_PATH = PAWBOT_HOME / "config.json"

# ── Static file serving ────────────────────────────────────────────────────────

UI_FILE = Path(__file__).parent / "ui.html"

@app.get("/", response_class=HTMLResponse)
async def root():
    return UI_FILE.read_text()

# All non-API routes return the SPA (single page app handles routing)
@app.get("/{path:path}", response_class=HTMLResponse)
async def spa_fallback(path: str):
    if not path.startswith("api/"):
        return UI_FILE.read_text()

# ── Overview ──────────────────────────────────────────────────────────────────

@app.get("/api/overview")
async def overview():
    cfg = load_config()
    memory = _count_memories()
    channels = _channel_status(cfg)
    cron = _count_cron_jobs()
    return {
        "agent_status": _agent_status(),
        "memory_count": memory,
        "active_channels": channels,
        "cron_jobs": cron,
    }

@app.get("/api/health")
async def health():
    """Run all doctor checks. Returns list of {label, status, fix}."""
    checks = []
    # Python version
    import sys
    checks.append({
        "label": "Python 3.11+",
        "status": "ok" if sys.version_info >= (3, 11) else "error",
        "fix": "Upgrade Python"
    })
    # Config file
    checks.append({
        "label": "Config file",
        "status": "ok" if CONFIG_PATH.exists() else "error",
        "fix": "Run: pawbot onboard"
    })
    # API key
    cfg = load_config()
    has_key = any(
        v.get("apiKey") and "xxx" not in v.get("apiKey", "")
        for v in cfg.get("providers", {}).values()
    )
    checks.append({
        "label": "API key configured",
        "status": "ok" if has_key else "error",
        "fix": "Add API key in Config section"
    })
    # Telegram
    tg = cfg.get("channels", {}).get("telegram", {})
    checks.append({
        "label": "Telegram channel",
        "status": "ok" if tg.get("enabled") else "warn",
        "fix": "Configure in Channels section"
    })
    # allowFrom security
    for ch, ch_cfg in cfg.get("channels", {}).items():
        if ch_cfg.get("enabled") and ch_cfg.get("allowFrom") == []:
            checks.append({
                "label": f"{ch} allowFrom",
                "status": "warn",
                "fix": f"Bot is public — add your ID to channels.{ch}.allowFrom"
            })
    return {"checks": checks}

# ── Chat ──────────────────────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat(body: dict):
    """Quick chat — sends message to pawbot agent, returns response."""
    message = body.get("message", "")
    if not message:
        raise HTTPException(400, "message required")
    try:
        result = subprocess.run(
            ["pawbot", "agent", "-m", message],
            capture_output=True, text=True, timeout=60
        )
        return {"response": result.stdout.strip(), "error": result.returncode != 0}
    except subprocess.TimeoutExpired:
        return {"response": "", "error": True, "detail": "Agent timed out"}

# ── Agent ─────────────────────────────────────────────────────────────────────

@app.get("/api/agent")
async def agent_info():
    cfg = load_config()
    soul_path = PAWBOT_HOME / "workspace" / "SOUL.md"
    return {
        "model": cfg.get("agents", {}).get("defaults", {}).get("model", "unknown"),
        "soul": soul_path.read_text() if soul_path.exists() else "",
        "status": _agent_status(),
    }

@app.post("/api/agent/model")
async def set_model(body: dict):
    cfg = load_config()
    cfg.setdefault("agents", {}).setdefault("defaults", {})["model"] = body["model"]
    save_config(cfg)
    return {"ok": True}

@app.post("/api/agent/soul")
async def save_soul(body: dict):
    soul_path = PAWBOT_HOME / "workspace" / "SOUL.md"
    soul_path.write_text(body["content"])
    return {"ok": True}

@app.post("/api/agent/restart")
async def restart_agent():
    # Implementation depends on how agent is managed (PID file or subprocess)
    subprocess.Popen(["pawbot", "agent", "--daemon"], start_new_session=True)
    return {"ok": True}

# ── Memory ────────────────────────────────────────────────────────────────────

@app.get("/api/memory")
async def list_memory(
    page: int = 1, limit: int = 50,
    type: str = None, search: str = None, sort: str = "newest"
):
    memory = MemoryRouter(load_config())
    items = memory.list_all(type=type, search=search, sort=sort)
    total = len(items)
    start = (page - 1) * limit
    return {
        "items": items[start:start+limit],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit,
        "stats": memory.stats()
    }

@app.post("/api/memory/{memory_id}/archive")
async def archive_memory(memory_id: str):
    memory = MemoryRouter(load_config())
    memory.archive(memory_id)
    return {"ok": True}

@app.put("/api/memory/{memory_id}")
async def update_memory(memory_id: str, body: dict):
    memory = MemoryRouter(load_config())
    memory.update(memory_id, body["content"])
    return {"ok": True}

@app.post("/api/memory/decay")
async def run_decay():
    from pawbot.agent.memory import MemoryDecayEngine
    engine = MemoryDecayEngine(load_config())
    result = engine.decay_pass()
    return {"ok": True, "archived": result.get("archived", 0)}

@app.get("/api/memory/export")
async def export_memory():
    memory = MemoryRouter(load_config())
    items = memory.list_all()
    content = "\n".join(json.dumps(item) for item in items)
    return StreamingResponse(
        iter([content]),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=pawbot-memory.jsonl"}
    )

# ── Channels ──────────────────────────────────────────────────────────────────

@app.get("/api/channels")
async def channels():
    cfg = load_config()
    return {"channels": cfg.get("channels", {}), "status": _channel_status(cfg)}

@app.post("/api/channels/{channel}/restart")
async def restart_channel(channel: str):
    subprocess.Popen(["pawbot", "gateway", "--channel", channel], start_new_session=True)
    return {"ok": True}

@app.post("/api/channels/{channel}/test")
async def test_channel(channel: str):
    result = subprocess.run(
        ["pawbot", "channels", "test", "--channel", channel],
        capture_output=True, text=True, timeout=15
    )
    return {"ok": result.returncode == 0, "output": result.stdout}

# ── Cron ──────────────────────────────────────────────────────────────────────

@app.get("/api/cron")
async def list_cron():
    cron_file = PAWBOT_HOME / "crons.json"
    if not cron_file.exists():
        return {"jobs": []}
    return {"jobs": json.loads(cron_file.read_text())}

@app.post("/api/cron")
async def create_cron(body: dict):
    result = subprocess.run(
        ["pawbot", "cron", "add",
         "--name", body["name"],
         "--message", body["message"],
         "--cron", body["schedule"]],
        capture_output=True, text=True
    )
    return {"ok": result.returncode == 0, "output": result.stdout}

@app.delete("/api/cron/{job_id}")
async def delete_cron(job_id: str):
    result = subprocess.run(
        ["pawbot", "cron", "remove", job_id],
        capture_output=True, text=True
    )
    return {"ok": result.returncode == 0}

@app.post("/api/cron/{job_id}/run")
async def run_cron_now(job_id: str):
    result = subprocess.run(
        ["pawbot", "cron", "run", job_id],
        capture_output=True, text=True, timeout=60
    )
    return {"ok": result.returncode == 0, "output": result.stdout}

@app.post("/api/cron/{job_id}/toggle")
async def toggle_cron(job_id: str, body: dict):
    enabled = body.get("enabled", True)
    cmd = "enable" if enabled else "disable"
    result = subprocess.run(["pawbot", "cron", cmd, job_id], capture_output=True, text=True)
    return {"ok": result.returncode == 0}

# ── Subagents ─────────────────────────────────────────────────────────────────

@app.get("/api/subagents")
async def subagents():
    result = subprocess.run(
        ["pawbot", "subagents", "status", "--json"],
        capture_output=True, text=True
    )
    try:
        return json.loads(result.stdout)
    except Exception:
        return {"active": [], "inbox": [], "completed": []}

@app.post("/api/subagents/spawn")
async def spawn_subagent(body: dict):
    result = subprocess.run(
        ["pawbot", "agent", "-m",
         f"spawn subagent: role={body['role']}, task={body['task']}, budget={body.get('budget',8000)}"],
        capture_output=True, text=True, timeout=10
    )
    return {"ok": result.returncode == 0, "id": result.stdout.strip()}

@app.post("/api/subagents/{agent_id}/cancel")
async def cancel_subagent(agent_id: str):
    return {"ok": True}  # Implementation depends on SubagentPool

@app.post("/api/subagents/inbox/{item_id}/accept")
async def accept_inbox(item_id: str):
    from pawbot.agent.memory import MemoryRouter
    m = MemoryRouter(load_config())
    m.inbox_accept(item_id)
    return {"ok": True}

@app.post("/api/subagents/inbox/{item_id}/reject")
async def reject_inbox(item_id: str):
    from pawbot.agent.memory import MemoryRouter
    m = MemoryRouter(load_config())
    m.inbox_reject(item_id)
    return {"ok": True}

# ── Skills ────────────────────────────────────────────────────────────────────

@app.get("/api/skills")
async def list_skills():
    result = subprocess.run(
        ["pawbot", "skills", "list", "--json"],
        capture_output=True, text=True
    )
    try:
        return json.loads(result.stdout)
    except Exception:
        return {"skills": []}

@app.post("/api/skills")
async def create_skill(body: dict):
    skills_dir = PAWBOT_HOME / "skills" / body["name"]
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "skill.json").write_text(json.dumps(body, indent=2))
    return {"ok": True}

@app.put("/api/skills/{name}")
async def update_skill(name: str, body: dict):
    skill_file = PAWBOT_HOME / "skills" / name / "skill.json"
    skill_file.write_text(json.dumps(body, indent=2))
    return {"ok": True}

@app.delete("/api/skills/{name}")
async def delete_skill(name: str):
    import shutil
    skill_dir = PAWBOT_HOME / "skills" / name
    shutil.rmtree(skill_dir, ignore_errors=True)
    return {"ok": True}

# ── Config ────────────────────────────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    cfg = load_config()
    # Mask sensitive values
    for provider in cfg.get("providers", {}).values():
        if "apiKey" in provider:
            key = provider["apiKey"]
            provider["apiKey"] = key[:8] + "••••••••" if len(key) > 8 else "••••••••"
            provider["_hasKey"] = True
    return cfg

@app.put("/api/config")
async def update_config(body: dict):
    cfg = load_config()
    # Deep merge body into cfg
    _deep_merge(cfg, body)
    save_config(cfg)
    return {"ok": True}

@app.post("/api/config/test-key")
async def test_api_key(body: dict):
    provider = body.get("provider")
    key = body.get("key")
    # Make a minimal test call to the provider
    # Implementation depends on provider type
    return {"valid": True, "latency_ms": 240}

@app.get("/api/config/raw")
async def get_raw_config():
    return json.loads(CONFIG_PATH.read_text())

@app.put("/api/config/raw")
async def put_raw_config(body: dict):
    CONFIG_PATH.write_text(json.dumps(body, indent=2))
    return {"ok": True}

# ── Logs ──────────────────────────────────────────────────────────────────────

ALLOWED_LOGS = {"agent.log", "gateway.log", "security_audit.jsonl", "traces.jsonl"}

@app.get("/api/logs/{filename}")
async def get_log(filename: str, tail: int = 200, search: str = None, level: str = None):
    if filename not in ALLOWED_LOGS:
        raise HTTPException(400, "Invalid log file")
    log_path = PAWBOT_HOME / "logs" / filename
    if not log_path.exists():
        return {"lines": [], "total": 0}
    lines = log_path.read_text().splitlines()
    if search:
        lines = [l for l in lines if search.lower() in l.lower()]
    if level and level != "ALL":
        lines = [l for l in lines if f"[{level}]" in l]
    return {"lines": lines[-tail:], "total": len(lines)}

@app.get("/api/logs/{filename}/download")
async def download_log(filename: str):
    if filename not in ALLOWED_LOGS:
        raise HTTPException(400, "Invalid log file")
    log_path = PAWBOT_HOME / "logs" / filename
    return FileResponse(log_path, filename=filename)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _agent_status():
    result = subprocess.run(["pawbot", "status"], capture_output=True, text=True)
    return "online" if result.returncode == 0 else "offline"

def _count_memories():
    try:
        m = MemoryRouter(load_config())
        return m.stats().get("total", 0)
    except Exception:
        return 0

def _channel_status(cfg):
    channels = cfg.get("channels", {})
    enabled = sum(1 for c in channels.values() if c.get("enabled"))
    return {"enabled": enabled, "total": len(channels)}

def _count_cron_jobs():
    cron_file = PAWBOT_HOME / "crons.json"
    if not cron_file.exists():
        return 0
    try:
        jobs = json.loads(cron_file.read_text())
        return len([j for j in jobs if j.get("enabled", True)])
    except Exception:
        return 0

def _deep_merge(base, override):
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ── Entry point ───────────────────────────────────────────────────────────────

def start(host="127.0.0.1", port=4000, open_browser=True):
    import uvicorn
    if open_browser:
        import threading, webbrowser
        def _open():
            import time; time.sleep(1.2)
            webbrowser.open(f"http://{host}:{port}")
        threading.Thread(target=_open, daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")

if __name__ == "__main__":
    start()
```

---

## STEP 5 — FRONTEND (`ui.html`)

**File:** `~/pawbot/pawbot/dashboard/ui.html`

This is a **single HTML file** containing all HTML, CSS, and JavaScript. No build step. No bundler. Loads from the FastAPI server. Uses vanilla JS with `fetch()` for all API calls.

### Structure requirements:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <!-- IBM Plex Mono + IBM Plex Sans from Google Fonts -->
  <!-- All CSS in one <style> block — use CSS variables for all colours -->
</head>
<body>
  <aside id="sidebar">
    <!-- Navigation: 9 items, active state, no icons except status dot for agent -->
    <!-- Bottom of sidebar: version + pawbot doctor status dot -->
  </aside>

  <main id="content">
    <!-- Each section is a <div class="section" id="section-overview"> etc -->
    <!-- Only one visible at a time — JS shows/hides -->
  </main>

  <div id="toast-container">
    <!-- Toast notifications: success/error, auto-dismiss 3s -->
  </div>

  <script>
    // All JS in one <script> block
    // Router: hashbang routing (#overview, #agent, etc)
    // API client: thin wrapper around fetch()
    // Each section: init() + render() functions
    // Toast system
    // Polling: overview refreshes every 5s, logs every 1s when tail=on
  </script>
</body>
</html>
```

### Sidebar implementation:

```html
<aside id="sidebar">
  <div class="logo">
    <span class="logo-mark">🐾</span>
    <span class="logo-text">PAWBOT</span>
  </div>

  <nav>
    <a href="#overview"  class="nav-item active">
      <span class="status-dot" id="dot-agent"></span>
      Overview
    </a>
    <a href="#agent"     class="nav-item">Agent</a>
    <a href="#memory"    class="nav-item">Memory</a>
    <a href="#channels"  class="nav-item">Channels</a>
    <a href="#cron"      class="nav-item">Cron</a>
    <a href="#subagents" class="nav-item">Subagents</a>
    <a href="#skills"    class="nav-item">Skills</a>
    <a href="#config"    class="nav-item">Config</a>
    <a href="#logs"      class="nav-item">Logs</a>
  </nav>

  <div class="sidebar-footer">
    <div class="version">v1.0.0</div>
    <div class="health-summary" id="sidebar-health">
      <!-- 3 dots: agent, config, channels -->
    </div>
  </div>
</aside>
```

### CSS requirements (key rules):

```css
:root {
  --bg:       #080808;
  --surface:  #111111;
  --border:   #222222;
  --text:     #F0F0F0;
  --muted:    #666666;
  --dim:      #333333;
  --green:    #00FF88;
  --green-10: #00FF8820;
  --warn:     #FFB800;
  --error:    #FF4444;
  --radius:   4px;
  --mono:     'IBM Plex Mono', monospace;
  --sans:     'IBM Plex Sans', sans-serif;
}

/* Layout */
body { display: flex; height: 100vh; overflow: hidden; background: var(--bg); }
#sidebar { width: 220px; flex-shrink: 0; border-right: 1px solid var(--border); display: flex; flex-direction: column; }
#content { flex: 1; overflow-y: auto; padding: 32px; }

/* Sidebar nav */
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 16px; font-family: var(--mono); font-size: 13px;
  color: var(--muted); text-decoration: none;
  border-left: 2px solid transparent;
  transition: all 150ms;
}
.nav-item:hover { color: var(--text); background: #141414; }
.nav-item.active { color: var(--green); border-left-color: var(--green); background: var(--green-10); }

/* Status dot */
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--dim); flex-shrink: 0; }
.status-dot.online { background: var(--green); animation: pulse 3s infinite; }
.status-dot.offline { background: var(--error); }
.status-dot.warn { background: var(--warn); }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

/* Section headers */
.section-title {
  font-family: var(--mono); font-size: 11px; font-weight: 600;
  letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--muted); margin-bottom: 16px;
  border-bottom: 1px solid var(--border); padding-bottom: 8px;
}

/* Tables */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { font-family: var(--mono); font-size: 11px; font-weight: 500; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); }
td { padding: 10px 12px; border-bottom: 1px solid #181818; color: var(--text); }
tr:hover td { background: #141414; }

/* Buttons */
.btn { padding: 6px 14px; font-family: var(--mono); font-size: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: transparent; color: var(--text); cursor: pointer; transition: all 100ms; }
.btn:hover { border-color: var(--text); background: #1a1a1a; }
.btn-primary { border-color: var(--green); color: var(--green); }
.btn-primary:hover { background: var(--green-10); }
.btn-danger { border-color: var(--error); color: var(--error); }
.btn-danger:hover { background: #FF444410; }

/* Form inputs */
input, textarea, select {
  background: #0d0d0d; border: 1px solid var(--border);
  border-radius: var(--radius); color: var(--text);
  font-family: var(--mono); font-size: 13px;
  padding: 8px 12px; outline: none;
  transition: border-color 100ms;
}
input:focus, textarea:focus, select:focus { border-color: var(--green); }

/* Cards / panels */
.panel { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); padding: 20px; margin-bottom: 16px; }
.panel-title { font-family: var(--mono); font-size: 11px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted); margin-bottom: 16px; }

/* Toast */
#toast-container { position: fixed; bottom: 24px; right: 24px; display: flex; flex-direction: column; gap: 8px; z-index: 1000; }
.toast { padding: 10px 16px; border-radius: var(--radius); font-family: var(--mono); font-size: 12px; border-left: 3px solid; animation: slideIn 150ms ease; }
.toast.success { background: #001a0d; border-color: var(--green); color: var(--green); }
.toast.error { background: #1a0000; border-color: var(--error); color: var(--error); }
@keyframes slideIn { from { transform: translateX(20px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }

/* Log lines */
.log-line { display: flex; gap: 12px; font-family: var(--mono); font-size: 12px; padding: 2px 0; animation: fadeUp 150ms ease; }
.log-ts { color: var(--dim); white-space: nowrap; }
.log-level { font-weight: 600; white-space: nowrap; }
.log-level.TOOL { color: #00CCFF; }
.log-level.MEM  { color: #CC88FF; }
.log-level.LLM  { color: var(--green); }
.log-level.ERR  { color: var(--error); }
.log-level.WARN { color: var(--warn); }
.log-level.INFO { color: var(--muted); }
@keyframes fadeUp { from { transform: translateY(4px); opacity: 0; } to { transform: none; opacity: 1; } }
```

---

## STEP 6 — ADD `pawbot dashboard` COMMAND

**Modify:** `~/pawbot/pawbot/cli/commands.py`

```python
@app.command("dashboard")
def dashboard(
    port: int = typer.Option(4000, "--port", "-p", help="Port to run on"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser automatically"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
):
    """Start the Pawbot local dashboard at http://localhost:4000"""
    from rich.console import Console
    console = Console()
    console.print(f"\n[green]🐾[/green] Starting Pawbot dashboard at [bold]http://{host}:{port}[/bold]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")
    from pawbot.dashboard.server import start
    start(host=host, port=port, open_browser=not no_browser)
```

---

## STEP 7 — DEPENDENCIES TO ADD

In `~/pawbot/pyproject.toml`:

```toml
"fastapi>=0.110.0",
"uvicorn>=0.29.0",
```

Then:
```bash
cd ~/pawbot && pip install -e .
```

---

## STEP 8 — VERIFY

```bash
# 1. Start the dashboard
pawbot dashboard

# 2. Confirm it opens at localhost:4000
# 3. Navigate to every section (Overview, Agent, Memory, Channels, Cron, Subagents, Skills, Config, Logs)
# 4. Confirm the Quick Chat sends a message and gets a response
# 5. Confirm Config section shows the real config values
# 6. Confirm Logs section shows real log lines
# 7. Confirm Health check runs and shows results
```

---

## RULES — DO NOT VIOLATE

1. **Single HTML file** — all CSS and JS inside `ui.html`. No external files, no bundler.
2. **No JS framework** — vanilla JS only. `fetch()` for API calls. No React, Vue, Next.js.
3. **No chart libraries** — pure CSS bars for all graphs.
4. **Colours are exact** — use only the 7 defined CSS variables. No deviations.
5. **Font is exact** — IBM Plex Mono for all labels/code, IBM Plex Sans for body. No other fonts.
6. **No animations except the 5 defined** — pulse, fadeUp, slideIn, nav hover, log arrival.
7. **Every API call has error handling** — failed calls show a red toast, never a broken UI.
8. **Sensitive values are masked** — API keys show `sk-or-v1-••••••••` by default.
9. **Dashboard must start in under 2 seconds** on a machine that already has Pawbot installed.
10. **Never break existing `pawbot` commands** — `dashboard` is additive only.

---

## DEFINITION OF DONE

- [ ] `pawbot dashboard` starts the server and opens `http://localhost:4000` in the browser
- [ ] All 9 sections render with real data from `~/.pawbot/`
- [ ] Quick Chat sends a message and shows the agent's response
- [ ] Memory section shows, edits, archives, and exports memories
- [ ] Cron section creates, runs, pauses, and deletes jobs
- [ ] Config section reads and writes `~/.pawbot/config.json`
- [ ] Logs section shows real log lines with correct colour coding
- [ ] Health check runs and shows pass/fail for all checks
- [ ] All sensitive values (API keys, tokens) are masked by default
- [ ] No external runtime dependencies — works offline after `pip install -e .`
- [ ] Aesthetic matches spec: black bg, green accent, mono type, no gradients, no shadows
