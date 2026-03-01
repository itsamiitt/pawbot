# NANOBOT MASTER REFERENCE — ALL PHASES & CROSS-LINKS
> **Version:** 3.0 — All 16 Phases Documented
> **Source Repo:** https://github.com/HKUDS/nanobot
> **Working Directory:** `~/nanobot/`
> **Config File:** `~/.nanobot/config.json`
> **Log File:** `~/.nanobot/logs/nanobot.log`

---

## ⚠️ AGENT MUST READ BEFORE WRITING ANY CODE

1. Read `MASTER_REFERENCE.md` (this file) first — understand all cross-links
2. Read the specific Phase `.md` file for the feature you are implementing
3. Read the **existing source file** before modifying it — never assume contents
4. Preserve all existing public interfaces — other modules depend on them
5. Follow the **exact implementation day order** in the schedule below
6. Every feature must pass its tests before moving to the next phase

---

## REPOSITORY STRUCTURE (Current Baseline)

```
~/nanobot/
├── agent/
│   ├── loop.py          ← AgentLoop — perceive-think-act cycle  [PHASE 2]
│   ├── context.py       ← ContextBuilder — assembles prompt     [PHASE 3]
│   ├── memory.py        ← MemoryStore — reads/writes memory     [PHASE 1]
│   ├── skills.py        ← SkillLoader — loads skills/           [PHASE 13]
│   ├── subagent.py      ← SubagentRunner — background tasks     [PHASE 12]
│   └── tools/           ← Built-in tool definitions
├── skills/              ← Bundled skills (github, weather, tmux)
├── channels/            ← WhatsApp, Telegram adapters           [PHASE 10]
├── bus/                 ← MessageBus — routes messages          [PHASE 10]
├── cron/                ← CronScheduler — scheduled tasks       [PHASE 11]
├── heartbeat/           ← HeartbeatEngine — proactive wake-up  [PHASE 11]
├── providers/           ← LLM provider registry                 [PHASE 4]
│   └── router.py        ← NEW: ModelRouter                      [PHASE 4]
├── session/             ← Session state management
├── config/              ← Configuration loader                  [PHASE 16]
├── cli/                 ← CLI commands                          [PHASE 16]
├── workspace/           ← SOUL.md, USER.md, MEMORY.md, HISTORY.md
└── pyproject.toml       ← Dependencies — update for every new dep

~/.nanobot/
├── config.json          ← Runtime config (API keys, feature flags)
├── logs/nanobot.log     ← All agent actions logged here
├── memory/
│   ├── facts.db         ← SQLite — SQLiteFactStore              [PHASE 1]
│   └── chroma/          ← ChromaDB — ChromaEpisodeStore         [PHASE 1]
├── checkpoints/
│   └── registry.json    ← Code checkpoint registry             [PHASE 7]
├── browser-sessions/    ← Playwright session cookies            [PHASE 8]
├── app_registry.json    ← Desktop app launch commands           [PHASE 9]
├── crons.json           ← Tracked cron jobs                     [PHASE 5]
├── code-indexes/        ← SQLite code indexes per project       [PHASE 7]
├── templates/           ← Screen template images                [PHASE 9]
└── mcp-servers/
    ├── server_control/server.py  ← Server Control MCP           [PHASE 5]
    ├── deploy/server.py          ← Deployment Pipeline MCP      [PHASE 6]
    ├── coding/server.py          ← Coding Engine MCP            [PHASE 7]
    ├── browser/server.py         ← Browser Intelligence MCP     [PHASE 8]
    └── app_control/server.py     ← Desktop App Control MCP      [PHASE 9]
```

---

## GLOBAL CANONICAL NAMES — USE EXACTLY THESE

> ⚠️ Never create new names for these. Copy-paste from this table.

### Python Classes

| Class Name | File | Phase | Purpose |
|---|---|---|---|
| `MemoryProvider` | `agent/memory.py` | 1 | Abstract base for all backends |
| `RedisWorkingMemory` | `agent/memory.py` | 1 | Session-scoped Redis backend |
| `SQLiteFactStore` | `agent/memory.py` | 1 | Persistent structured facts |
| `ChromaEpisodeStore` | `agent/memory.py` | 1 | Semantic episode search |
| `MemoryRouter` | `agent/memory.py` | 1 | Unified routing across backends |
| `MemoryClassifier` | `agent/memory.py` | 1.2 | Assigns types + salience |
| `MemoryLinker` | `agent/memory.py` | 1.3 | Auto-links related memories |
| `MemoryDecayEngine` | `agent/memory.py` | 1.4 | Nightly salience decay |
| `ComplexityClassifier` | `agent/loop.py` | 2 | System 1/2 routing |
| `ThoughtTreePlanner` | `agent/loop.py` | 2.4 | Tree of Thoughts planning |
| `ContextBudget` | `agent/context.py` | 3 | Hard token limits per section |
| `TaskTypeDetector` | `agent/context.py` | 3.4 | Classifies task before load |
| `ModelRouter` | `providers/router.py` | 4 | Routes tasks to correct model |
| `OllamaProvider` | `providers/ollama.py` | 4 | New Ollama LLM provider |
| `ChannelMessage` | `channels/base.py` | 10 | Unified message dataclass across all channels |
| `BaseChannel` | `channels/base.py` | 10 | Abstract base for all channel adapters |
| `WhatsAppChannel` | `channels/whatsapp.py` | 10 | Enhanced WhatsApp adapter |
| `TelegramChannel` | `channels/telegram.py` | 10 | Enhanced Telegram adapter |
| `EmailChannel` | `channels/email.py` | 10 | Email channel (IMAP/SMTP) |
| `MessageBus` | `bus/message_bus.py` | 10 | Central message routing hub |
| `ChannelRouter` | `bus/router.py` | 10 | Routes messages to correct channel adapter |
| `RateLimiter` | `channels/base.py` | 10 | Per-channel outbound rate limiting |
| `MessageQueue` | `bus/queue.py` | 10 | Async queue for when agent is busy |
| `CronScheduler` | `cron/scheduler.py` | 11 | Registers and runs scheduled jobs |
| `CronJob` | `cron/scheduler.py` | 11 | Single registered job dataclass |
| `HeartbeatEngine` | `heartbeat/engine.py` | 11 | Proactive agent wake-up logic |
| `HeartbeatTrigger` | `heartbeat/engine.py` | 11 | Dataclass describing a wake-up condition |
| `TaskWatcher` | `heartbeat/engine.py` | 11 | Monitors long-running background tasks |
| `SubagentRunner` | `agent/subagent.py` | 12 | Manages spawning and lifecycle of subagents |
| `Subagent` | `agent/subagent.py` | 12 | Single subagent instance with isolated context |
| `SubagentRole` | `agent/subagent.py` | 12 | Dataclass defining a subagent's capabilities |
| `SubagentResult` | `agent/subagent.py` | 12 | Structured return from a completed subagent |
| `SubagentBudget` | `agent/subagent.py` | 12 | Token/time limits per subagent |
| `SubagentPool` | `agent/subagent.py` | 12 | Manages concurrent subagent execution |
| `SubagentMessageBus` | `agent/subagent.py` | 12 | Simple message-passing between subagents |
| `SkillLoader` | `agent/skills.py` | 13 | Loads and injects skills into context |
| `SkillWriter` | `agent/skills.py` | 13 | Creates and saves skills at runtime |
| `Skill` | `agent/skills.py` | 13 | Dataclass representing one skill |
| `SkillExecutor` | `agent/skills.py` | 13 | Runs a skill with given parameters |
| `LoRAPipeline` | `agent/skills.py` | 13 | Fine-tuning dataset collector and trainer |
| `TrainingExample` | `agent/skills.py` | 13 | Single training example dataclass |
| `ActionGate` | `agent/security.py` | 14 | Intercepts and validates tool calls |
| `ActionRisk` | `agent/security.py` | 14 | Risk level constants |
| `MemorySanitizer` | `agent/security.py` | 14 | Cleans memories before context injection |
| `InjectionDetector` | `agent/security.py` | 14 | Detects prompt injection in untrusted content |
| `SecurityAuditLog` | `agent/security.py` | 14 | Append-only security event log |
| `NanobotTracer` | `agent/telemetry.py` | 15 | Main tracing interface |
| `Span` | `agent/telemetry.py` | 15 | Single trace span context manager |
| `SessionMetrics` | `agent/telemetry.py` | 15 | Per-session aggregated metrics |
| `MetricsCollector` | `agent/telemetry.py` | 15 | Collects and exports Prometheus metrics |
| `TraceExporter` | `agent/telemetry.py` | 15 | Exports traces to OTLP or file |
| `NanobotConfig` | `config/schema.py` | 16 | Pydantic model for full validated config |
| `MemoryConfig` | `config/schema.py` | 16 | Memory backend config section |
| `SecurityConfig` | `config/schema.py` | 16 | Security layer config section |
| `ObservabilityConfig` | `config/schema.py` | 16 | Tracing and metrics config section |
| `ChannelsConfig` | `config/schema.py` | 16 | Channel adapter config section |
| `SubagentsConfig` | `config/schema.py` | 16 | Subagent pool config section |
| `LoRaConfig` | `config/schema.py` | 16 | LoRA pipeline config section |
| `ConfigLoader` | `config/loader.py` | 16 | Loads and validates config |
| `CLIFormatter` | `cli/formatter.py` | 16 | Rich-formatted table/panel output |

### Memory Types (Canonical Strings)

```python
MEMORY_TYPES = ["fact", "preference", "decision", "episode",
                "reflection", "procedure", "task", "risk"]
```

### Link Types (Canonical Strings)

```python
LINK_TYPES = ["caused_by", "supports", "contradicts", "extends",
              "resolves", "depends_on"]
```

### Complexity Score Thresholds

```python
SYSTEM_1_MAX    = 0.3   # fast path
SYSTEM_1_5_MAX  = 0.7   # ReAct path
SYSTEM_2_MIN    = 0.7   # deliberative path
```

### Context Budget Token Limits

```python
BUDGET = {
    "system_prompt":    150,
    "user_facts":       100,
    "goal_state":       100,
    "reflections":      150,
    "episode_memory":   200,
    "procedure_memory": 150,
    "file_context":     500,
    "conversation":     200,
    "tool_results":     250,
    "TOTAL":           1800,
}
```

### Task Types (Canonical Strings)

```python
TASK_TYPES = ["casual_chat", "coding_task", "deployment_task",
              "memory_task", "planning_task", "research_task", "debugging_task"]
```

### Model Routing Table

| Task Type | Complexity | Provider | Model |
|---|---|---|---|
| memory_save | any | ollama | llama3.1:8b |
| memory_search | any | ollama | nomic-embed-text |
| file_index | any | ollama | deepseek-coder:6.7b |
| result_compress | any | ollama | llama3.1:8b |
| casual_chat | < 0.4 | openrouter | claude-haiku-4-5 |
| casual_chat | ≥ 0.4 | openrouter | claude-sonnet-4-6 |
| code_generation | any | openrouter | claude-sonnet-4-6 |
| architecture | any | openrouter | claude-opus-4-6 |
| debugging | any | openrouter | claude-sonnet-4-6 |
| reasoning | ≥ 0.7 | openrouter | claude-opus-4-6 |

### SQLite Databases & Their Owners

| Database Path | Owner Class | Phase |
|---|---|---|
| `~/.nanobot/memory/facts.db` | `SQLiteFactStore` | 1 |
| `~/.nanobot/code-indexes/{hash}.db` | `code_index_project()` tool | 7 |
| `~/.nanobot/memory/chroma/` | `ChromaEpisodeStore` | 1 |

### JSON Data Files

| File Path | Owner | Phase | Description |
|---|---|---|---|
| `~/.nanobot/crons.json` | `CronScheduler` | 11 | Registered cron job metadata |
| `~/.nanobot/heartbeat_triggers.json` | `HeartbeatEngine` | 11 | Persisted heartbeat triggers |
| `~/.nanobot/app_registry.json` | `app_launch` tool | 9 | Desktop app launch commands |
| `~/.nanobot/checkpoints/registry.json` | `code_checkpoint` tool | 7 | Code checkpoint registry |

### SQLite Table Names

| Table | Database | Phase | Description |
|---|---|---|---|
| `facts` | facts.db | 1.1 | Core fact storage |
| `memory_links` | facts.db | 1.3 | Links between memories |
| `reflections` | facts.db | 1.1 | Learned failure lessons |
| `procedures` | facts.db | 1.1 | Proven task sequences |
| `archived_memories` | facts.db | 1.4 | Decayed memory archive |
| `subagent_inbox` | facts.db | 1.5 | Pending subagent discoveries |

### MCP Server Names (as registered in config.json)

```json
{
  "mcp_servers": {
    "server_control": { "path": "~/.nanobot/mcp-servers/server_control/server.py" },
    "deploy":         { "path": "~/.nanobot/mcp-servers/deploy/server.py" },
    "coding":         { "path": "~/.nanobot/mcp-servers/coding/server.py" },
    "browser":        { "path": "~/.nanobot/mcp-servers/browser/server.py" },
    "app_control":    { "path": "~/.nanobot/mcp-servers/app_control/server.py" }
  }
}
```

---

## COMPLETE PHASE INDEX

| Phase | File | Status | Key Classes | Key Files Modified |
|---|---|---|---|---|
| [Phase 1 — Memory System](./PHASE_01_MEMORY_SYSTEM.md) | `PHASE_01_MEMORY_SYSTEM.md` | ✅ Implemented | MemoryRouter, SQLiteFactStore, ChromaEpisodeStore | `agent/memory.py` |
| [Phase 2 — Agent Loop Intelligence](./PHASE_02_AGENT_LOOP.md) | `PHASE_02_AGENT_LOOP.md` | ✅ Implemented | ComplexityClassifier, ThoughtTreePlanner | `agent/loop.py` |
| [Phase 3 — Context Builder](./PHASE_03_CONTEXT_BUILDER.md) | `PHASE_03_CONTEXT_BUILDER.md` | ✅ Implemented | ContextBudget, TaskTypeDetector | `agent/context.py` |
| [Phase 4 — Model Router](./PHASE_04_MODEL_ROUTER.md) | `PHASE_04_MODEL_ROUTER.md` | ✅ Implemented | ModelRouter, OllamaProvider | `providers/router.py` |
| [Phase 5 — Server Control MCP](./PHASE_05_SERVER_CONTROL.md) | `PHASE_05_SERVER_CONTROL.md` | ✅ Implemented | MCP Tools | `~/.nanobot/mcp-servers/server_control/server.py` |
| [Phase 6 — Deployment Pipeline MCP](./PHASE_06_DEPLOY_PIPELINE.md) | `PHASE_06_DEPLOY_PIPELINE.md` | ✅ Implemented | MCP Tools | `~/.nanobot/mcp-servers/deploy/server.py` |
| [Phase 7 — Coding Engine MCP](./PHASE_07_CODING_ENGINE.md) | `PHASE_07_CODING_ENGINE.md` | ✅ Implemented | MCP Tools | `~/.nanobot/mcp-servers/coding/server.py` |
| [Phase 8 — Browser Control MCP](./PHASE_08_BROWSER_CONTROL.md) | `PHASE_08_BROWSER_CONTROL.md` | ✅ Implemented | MCP Tools | `~/.nanobot/mcp-servers/browser/server.py` |
| [Phase 9 — App Control MCP](./PHASE_09_APP_CONTROL.md) | `PHASE_09_APP_CONTROL.md` | ✅ Documented | Desktop GUI tools | `~/.nanobot/mcp-servers/app_control/server.py` |
| [Phase 10 — Channels](./PHASE_10_CHANNELS.md) | `PHASE_10_CHANNELS.md` | ✅ Documented | WhatsAppChannel, TelegramChannel, EmailChannel, ChannelRouter | `channels/`, `bus/` |
| [Phase 11 — Heartbeat & Cron](./PHASE_11_HEARTBEAT_CRON.md) | `PHASE_11_HEARTBEAT_CRON.md` | ✅ Documented | CronScheduler, HeartbeatEngine, TaskWatcher | `heartbeat/`, `cron/` |
| [Phase 12 — Enhanced Subagents](./PHASE_12_SUBAGENTS.md) | `PHASE_12_SUBAGENTS.md` | ✅ Documented | SubagentRunner, SubagentPool, SubagentRole | `agent/subagent.py` |
| [Phase 13 — Skill System](./PHASE_13_SKILLS.md) | `PHASE_13_SKILLS.md` | ✅ Documented | SkillWriter, SkillLoader, LoRAPipeline | `agent/skills.py` |
| [Phase 14 — Security](./PHASE_14_SECURITY.md) | `PHASE_14_SECURITY.md` | ✅ Documented | ActionGate, MemorySanitizer, InjectionDetector | `agent/security.py` |
| [Phase 15 — Observability](./PHASE_15_OBSERVABILITY.md) | `PHASE_15_OBSERVABILITY.md` | ✅ Documented | NanobotTracer, Span, SessionMetrics | `agent/telemetry.py` |
| [Phase 16 — CLI & Config](./PHASE_16_CLI_CONFIG.md) | `PHASE_16_CLI_CONFIG.md` | ✅ Documented | NanobotConfig, ConfigLoader, CLIFormatter | `cli/`, `config/` |

---

## IMPLEMENTATION DAY ORDER

```
WEEK 1 — Core Infrastructure
  Day 1:  Phase 1.1 — Three-database memory backend
  Day 2:  Phase 1.2 — Memory types and salience
  Day 3:  Phase 3.1 — Context budget enforcement
  Day 4:  Phase 3.3 — Prompt cache markers
  Day 5:  Phase 4.1 — Model router + Ollama provider
  Day 6:  Phase 3.4 — Task-aware lazy loading
  Day 7:  End-to-end test all Week 1

WEEK 2 — Loop Intelligence
  Day 8:  Phase 2.1 — Dual system router
  Day 9:  Phase 2.2 — Pre-task reflection check
  Day 10: Phase 2.3 — Post-task learning
  Day 11: Phase 1.3 — Memory self-linking
  Day 12: Phase 1.4 — Memory decay engine
  Day 13: Phase 12.1 — Enhanced subagent system
  Day 14: End-to-end test all Week 2

WEEK 3 — Server & Deploy Tools
  Day 15: Phase 5.1 — Server control MCP server
  Day 16: Phase 6.1 — Deploy app tool
  Day 17: Phase 6.2 — Nginx config generator
  Day 18: Phase 6.3 + 6.4 — DB migration + backup
  Day 19: Phase 10.1 — WhatsApp enhancements
  Day 20: Phase 11.1 — Smart heartbeat
  Day 21: End-to-end test all Week 3

WEEK 4 — Browser & Coding Tools
  Day 22: Phase 8.1 — Browser server (accessibility mode)
  Day 23: Phase 8.1 — Browser server (vision + anti-detection)
  Day 24: Phase 9.1 — Desktop app control server
  Day 25: Phase 7.1 — Coding engine MCP server
  Day 26: Phase 2.4 — Tree of Thoughts planner
  Day 27: Phase 14.1 + 14.2 — Security features
  Day 28: End-to-end test all Week 4

WEEKS 5-8 — Ecosystem
  Phase 13.1 — Skill writer
  Phase 15.1 — OpenTelemetry tracing
  Phase 10.2 — Telegram enhancements
  Phase 10.3 — Email channel
  Phase 2.5 — Self-correction protocol
  Phase 3.2 — Relevance-based context loading
  Phase 1.5 — Subagent memory inbox
  Phase 13.2 — LoRA fine-tuning pipeline
  Phase 16.1 — New CLI commands
  Phase 16.2 — Complete config schema
```

---

## GLOBAL DEPENDENCY REGISTRY

> Every new dependency must be added to `pyproject.toml` before use.

| Library | Version | Phase | Purpose |
|---|---|---|---|
| `redis` | >=5.0.0 | 1 | RedisWorkingMemory backend |
| `chromadb` | >=0.4.0 | 1 | ChromaEpisodeStore |
| `sentence-transformers` | >=2.0.0 | 1 | Local embeddings fallback |
| `tiktoken` | >=0.5.0 | 3 | Token counting |
| `httpx` | >=0.25.0 | 4 | Async HTTP for Ollama |
| `psutil` | >=5.9.0 | 5 | Server status tool |
| `playwright` | >=1.40.0 | 8 | Browser automation |
| `pyautogui` | >=0.9.54 | 9 | Desktop mouse/keyboard control |
| `pillow` | >=10.0.0 | 9 | Image handling for screenshots |
| `pytesseract` | >=0.3.10 | 9 | OCR for screen_read |
| `pynput` | >=1.7.6 | 9 | Keyboard/mouse input |
| `pyperclip` | >=1.8.2 | 9 | Clipboard access |
| `faster-whisper` | >=0.10.0 | 10 | Voice message transcription |
| `croniter` | >=2.0.0 | 11 | Cron expression parsing |
| `pydantic` | >=2.0.0 | 16 | Config schema validation |
| `rich` | >=13.0.0 | 16 | CLI table and panel formatting |
| `typer` | >=0.9.0 | 16 | CLI argument parsing |
| `opentelemetry-sdk` | >=1.20.0 | 15 | Distributed tracing (optional) |

---

## GLOBAL RULES — NEVER VIOLATE

1. **Never hardcode secrets** — all keys come from `~/.nanobot/config.json` or environment variables
2. **Never break `nanobot agent -m "..."`** — test this after every change
3. **Log every significant action** to `~/.nanobot/logs/nanobot.log`
4. **Every new MCP server** must be registered in `config.json` under `mcp_servers`
5. **Graceful degradation** — if a backend is unavailable, log warning and use fallback
6. **Parameterized SQL only** — never string-concatenate SQL queries
7. **Tests before done** — `pytest tests/ -v --tb=short` must pass
8. **Read before modify** — always read the target file before editing it
9. **All tool calls pass through ActionGate** — Phase 14's `ActionGate.wrap()` wraps every tool
10. **All memories sanitized before injection** — Phase 14's `MemorySanitizer.sanitize_batch()` called in Phase 3 context builder
11. **Background tasks are daemon threads** — `thread.start()` never `thread.join()` for non-blocking operations
12. **Subagents write to inbox only** — `SQLiteFactStore.inbox_write()`, never `MemoryRouter.save()` directly

---

## NEW FILE PATHS — PHASES 9–16

```
~/nanobot/
├── agent/
│   ├── security.py      ← ActionGate, MemorySanitizer, InjectionDetector [PHASE 14]
│   └── telemetry.py     ← NanobotTracer, Span, SessionMetrics             [PHASE 15]
├── channels/
│   ├── base.py          ← BaseChannel, ChannelMessage, RateLimiter         [PHASE 10]
│   ├── whatsapp.py      ← WhatsAppChannel (enhanced)                       [PHASE 10]
│   ├── telegram.py      ← TelegramChannel (enhanced)                       [PHASE 10]
│   └── email.py         ← EmailChannel (new)                               [PHASE 10]
├── bus/
│   ├── message_bus.py   ← MessageBus (enhanced)                            [PHASE 10]
│   ├── router.py        ← ChannelRouter                                    [PHASE 10]
│   └── queue.py         ← MessageQueue                                     [PHASE 10]
├── cron/
│   └── scheduler.py     ← CronScheduler, CronJob                          [PHASE 11]
├── heartbeat/
│   └── engine.py        ← HeartbeatEngine, HeartbeatTrigger, TaskWatcher  [PHASE 11]
├── config/
│   ├── schema.py        ← NanobotConfig (Pydantic full schema)             [PHASE 16]
│   └── loader.py        ← ConfigLoader (enhanced)                          [PHASE 16]
└── cli/
    ├── memory_commands.py  ← memory_app (typer sub-app)                    [PHASE 16]
    ├── skills_commands.py  ← skills_app (typer sub-app)                    [PHASE 16]
    ├── cron_commands.py    ← cron_app (typer sub-app)                      [PHASE 16]
    ├── formatter.py        ← CLIFormatter (rich output)                    [PHASE 16]
    └── doctor.py           ← doctor command                                [PHASE 16]

~/.nanobot/
├── heartbeat_triggers.json  ← HeartbeatEngine persisted triggers           [PHASE 11]
├── training/
│   ├── dataset.jsonl         ← LoRA training examples                      [PHASE 13]
│   └── axolotl_config.yaml   ← LoRA training config                        [PHASE 13]
├── models/
│   └── nanobot-lora/         ← Fine-tuned LoRA model output                [PHASE 13]
└── logs/
    ├── nanobot.log            ← General agent action log                   [ALL]
    ├── security_audit.jsonl   ← SecurityAuditLog — append-only             [PHASE 14]
    └── traces.jsonl           ← NanobotTracer span output                  [PHASE 15]
```

---

## DEFINITION OF DONE (Per Feature)

- [ ] Code written and all tests pass
- [ ] Registered in `config.json` schema with defaults
- [ ] Error handling for all failure modes
- [ ] Logs significant actions to `nanobot.log`
- [ ] Degrades gracefully if optional deps are missing
- [ ] `nanobot agent -m "test"` still works
- [ ] No unbounded memory growth
- [ ] New user can enable feature in < 5 minutes per README

---

*This file is the single source of truth for all names, paths, and cross-references.*  
*When in doubt, check this file first before creating any new identifiers.*
