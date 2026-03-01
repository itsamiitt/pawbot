# PHASE 16 — CLI COMMANDS & CONFIG SCHEMA
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)
> **Implementation Days:** Weeks 5–8 (16.1 New CLI commands, 16.2 Complete config schema)
> **Primary Files:** `~/nanobot/cli/` (enhance existing), `~/nanobot/config/` (enhance existing)
> **Test File:** `~/nanobot/tests/test_cli.py`
> **Depends on:** All phases — CLI is the user's interface to everything built in Phases 1–15

---

## BEFORE YOU START — READ THESE FILES

```bash
cat ~/nanobot/cli/                  # all existing CLI commands — preserve every one
cat ~/nanobot/config/               # current config loading logic
cat ~/.nanobot/config.json          # current runtime config
cat ~/nanobot/pyproject.toml        # [project.scripts] entry points
```

**Existing interfaces to preserve:** Every current `nanobot` CLI subcommand. Check `pyproject.toml [project.scripts]` for the entry point. Check `cli/` for all existing commands. None of these are to be renamed or removed.

---

## WHAT YOU ARE BUILDING

### New CLI Commands

| Command | What it does |
|---|---|
| `nanobot memory search <query>` | Search memories, display as table |
| `nanobot memory list <type>` | List all memories of a given type |
| `nanobot memory delete <id>` | Delete a memory by ID |
| `nanobot memory stats` | Show memory backend statistics |
| `nanobot memory decay` | Manually trigger decay pass |
| `nanobot skills list` | List all registered skills |
| `nanobot skills show <name>` | Show full skill details |
| `nanobot skills create` | Interactive skill creation wizard |
| `nanobot skills delete <name>` | Delete a skill |
| `nanobot cron list` | List all registered cron jobs |
| `nanobot cron run <name>` | Manually trigger a cron job |
| `nanobot cron enable <name>` | Enable a disabled cron job |
| `nanobot cron disable <name>` | Disable a cron job |
| `nanobot subagents status` | Show active subagents and pool status |
| `nanobot metrics` | Show session metrics summary |
| `nanobot traces` | Show recent trace spans |
| `nanobot config show` | Display resolved config with sources |
| `nanobot config set <key> <value>` | Update a config value |
| `nanobot config validate` | Validate config against full schema |
| `nanobot doctor` | Run full system health check |

### Complete Config Schema

A Pydantic-validated config schema that covers every key added by Phases 1–15, with types, defaults, descriptions, and validation rules.

---

## CANONICAL NAMES — ALL NEW CLASSES IN THIS PHASE

| Class Name | File | Purpose |
|---|---|---|
| `NanobotConfig` | `config/schema.py` | Pydantic model for full config |
| `MemoryConfig` | `config/schema.py` | Memory backend config section |
| `SecurityConfig` | `config/schema.py` | Security layer config section |
| `ObservabilityConfig` | `config/schema.py` | Tracing and metrics config section |
| `ChannelsConfig` | `config/schema.py` | Channel adapter config section |
| `SubagentsConfig` | `config/schema.py` | Subagent pool config section |
| `LoRaConfig` | `config/schema.py` | LoRA pipeline config section |
| `ConfigLoader` | `config/loader.py` | Loads and validates config (enhance existing) |
| `CLIFormatter` | `cli/formatter.py` | Rich-formatted table/panel output |

---

## FEATURE 16.1 — NEW CLI COMMANDS

### Setup

```bash
pip install "rich>=13.0.0" "typer>=0.9.0"
# Add to pyproject.toml [project.dependencies]
```

All new commands use `typer` for argument parsing and `rich` for output formatting.

### Memory Commands

Create `~/nanobot/cli/memory_commands.py`:

```python
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()
memory_app = typer.Typer(name="memory", help="Manage agent memory")

@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n"),
    type: str = typer.Option("", "--type", "-t", help="Filter by memory type"),
):
    """Search memories by query string."""
    from nanobot.agent.memory import get_memory_router
    router = get_memory_router()
    results = router.search(query, limit=limit)
    if type:
        results = [r for r in results if r.get("type") == type]

    if not results:
        console.print("[yellow]No memories found.[/yellow]")
        return

    table = Table(title=f"Memory search: '{query}'")
    table.add_column("ID", style="dim", width=10)
    table.add_column("Type", style="cyan")
    table.add_column("Salience", justify="right")
    table.add_column("Content", max_width=60)

    for mem in results:
        content = mem.get("content", {})
        text = content.get("text", str(content))[:80] if isinstance(content, dict) else str(content)[:80]
        table.add_row(
            mem.get("id", "")[:8],
            mem.get("type", ""),
            f"{mem.get('salience', 0):.2f}",
            text,
        )
    console.print(table)


@memory_app.command("list")
def memory_list(
    type: str = typer.Argument(..., help=f"Memory type. One of: fact, preference, decision, episode, reflection, procedure, task, risk"),
):
    """List all memories of a given type."""
    from nanobot.agent.memory import get_memory_router
    router = get_memory_router()
    memories = router.list_all(type)

    if not memories:
        console.print(f"[yellow]No memories of type '{type}'.[/yellow]")
        return

    table = Table(title=f"Memories: {type} ({len(memories)} total)")
    table.add_column("ID", style="dim", width=10)
    table.add_column("Salience", justify="right")
    table.add_column("Created", width=12)
    table.add_column("Content", max_width=70)

    for mem in memories:
        import datetime
        created = datetime.datetime.fromtimestamp(
            mem.get("created_at", 0)
        ).strftime("%Y-%m-%d")
        content = mem.get("content", {})
        text = content.get("text", str(content))[:80] if isinstance(content, dict) else str(content)[:80]
        table.add_row(mem.get("id", "")[:8], f"{mem.get('salience', 0):.2f}", created, text)
    console.print(table)


@memory_app.command("delete")
def memory_delete(
    id: str = typer.Argument(..., help="Memory ID (full or partial)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete a memory by ID. Moves to archive — not permanently deleted."""
    if not yes:
        confirm = typer.confirm(f"Archive memory '{id}'?")
        if not confirm:
            raise typer.Abort()
    from nanobot.agent.memory import get_memory_router
    router = get_memory_router()
    deleted = router.delete(id)
    if deleted:
        console.print(f"[green]✓ Memory archived: {id[:8]}[/green]")
    else:
        console.print(f"[red]✗ Memory not found: {id[:8]}[/red]")


@memory_app.command("stats")
def memory_stats_cmd():
    """Show memory backend statistics."""
    from nanobot.agent.memory import get_memory_router, memory_stats
    router = get_memory_router()
    stats = memory_stats(router)

    table = Table(title="Memory Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    for key, value in stats.get("by_type", {}).items():
        table.add_row(f"facts/{key}", str(value))
    table.add_row("Total facts", str(stats.get("total_facts", 0)))
    table.add_row("Archived", str(stats.get("archived", 0)))
    table.add_row("Episodes (ChromaDB)", str(stats.get("episodes_chroma", "N/A")))
    table.add_row("DB size (kb)", str(stats.get("db_size_kb", 0)))
    console.print(table)


@memory_app.command("decay")
def memory_decay_cmd():
    """Manually trigger a memory decay pass."""
    from nanobot.agent.memory import SQLiteFactStore, MemoryDecayEngine
    store = SQLiteFactStore({})
    engine = MemoryDecayEngine(store)
    console.print("[cyan]Running decay pass...[/cyan]")
    archived = engine.decay_pass()
    console.print(f"[green]✓ Decay complete: {archived} memories archived.[/green]")
```

### Skills Commands

Create `~/nanobot/cli/skills_commands.py`:

```python
skills_app = typer.Typer(name="skills", help="Manage agent skills")

@skills_app.command("list")
def skills_list():
    """List all registered skills sorted by usage."""
    from nanobot.agent.skills import SkillWriter
    writer = SkillWriter()
    skills = writer.list_skills()
    if not skills:
        console.print("[yellow]No skills registered yet.[/yellow]")
        return

    table = Table(title=f"Skills ({len(skills)} total)")
    table.add_column("Name", style="cyan")
    table.add_column("Description", max_width=50)
    table.add_column("Triggers")
    table.add_column("Uses", justify="right")
    table.add_column("Avg tokens", justify="right")
    table.add_column("Author")

    for skill in skills:
        table.add_row(
            skill.name,
            skill.description,
            ", ".join(skill.triggers[:3]),
            str(skill.success_count),
            str(skill.avg_tokens),
            skill.author,
        )
    console.print(table)


@skills_app.command("show")
def skills_show(name: str = typer.Argument(...)):
    """Show full details of a skill."""
    from nanobot.agent.skills import SkillWriter
    writer = SkillWriter()
    try:
        skill = writer.load_skill(name)
        panel_content = "\n".join([
            f"[bold]Description:[/bold] {skill.description}",
            f"[bold]Version:[/bold] {skill.version}",
            f"[bold]Author:[/bold] {skill.author}",
            f"[bold]Success count:[/bold] {skill.success_count}",
            f"[bold]Avg tokens:[/bold] {skill.avg_tokens}",
            f"[bold]Triggers:[/bold] {', '.join(skill.triggers)}",
            f"[bold]Tools used:[/bold] {', '.join(skill.tools_used) or 'none'}",
            f"\n[bold]Steps:[/bold]",
            "\n".join(f"  {i+1}. {s}" for i, s in enumerate(skill.steps)),
        ])
        console.print(Panel(panel_content, title=f"Skill: {name}"))
    except FileNotFoundError:
        console.print(f"[red]Skill '{name}' not found.[/red]")
        raise typer.Exit(1)


@skills_app.command("delete")
def skills_delete(
    name: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Delete a skill by name."""
    if not yes:
        confirm = typer.confirm(f"Delete skill '{name}'?")
        if not confirm:
            raise typer.Abort()
    from nanobot.agent.skills import SkillWriter
    writer = SkillWriter()
    deleted = writer.delete_skill(name)
    if deleted:
        console.print(f"[green]✓ Skill deleted: {name}[/green]")
    else:
        console.print(f"[red]✗ Skill not found: {name}[/red]")
```

### Cron Commands

Create `~/nanobot/cli/cron_commands.py`:

```python
cron_app = typer.Typer(name="cron", help="Manage scheduled jobs")

@cron_app.command("list")
def cron_list():
    """List all registered cron jobs and their status."""
    from nanobot.cron.scheduler import CronScheduler
    sched = CronScheduler()
    jobs = sched.list_jobs()
    if not jobs:
        console.print("[yellow]No cron jobs registered.[/yellow]")
        return

    table = Table(title="Cron Jobs")
    table.add_column("Name", style="cyan")
    table.add_column("Schedule")
    table.add_column("Next run")
    table.add_column("Last run")
    table.add_column("Runs", justify="right")
    table.add_column("Status")

    import datetime
    for job in jobs:
        next_run = datetime.datetime.fromtimestamp(job["next_run"]).strftime("%m-%d %H:%M") if job["next_run"] else "—"
        last_run = datetime.datetime.fromtimestamp(job["last_run"]).strftime("%m-%d %H:%M") if job["last_run"] else "never"
        status = "[red]error[/red]" if job["last_error"] else ("[green]ok[/green]" if job["enabled"] else "[yellow]disabled[/yellow]")
        table.add_row(job["name"], job["schedule"], next_run, last_run, str(job["run_count"]), status)
    console.print(table)
```

### Doctor Command

Create `~/nanobot/cli/doctor.py`:

```python
@app.command("doctor")
def doctor():
    """Run a full system health check."""
    checks = []

    def check(name: str, fn, fix: str = ""):
        try:
            fn()
            checks.append(("✓", name, ""))
        except Exception as e:
            checks.append(("✗", name, fix or str(e)[:80]))

    # Redis
    check("Redis connection",
          lambda: __import__("redis").Redis().ping(),
          "Install Redis or disable redis backend in config")

    # ChromaDB
    check("ChromaDB directory",
          lambda: os.path.exists(os.path.expanduser("~/.nanobot/memory/chroma")),
          "Run nanobot agent -m 'test' to initialise")

    # Ollama
    import httpx
    check("Ollama running",
          lambda: httpx.get("http://localhost:11434/api/tags", timeout=2).raise_for_status(),
          "Start Ollama: ollama serve")

    # Config file
    check("Config file exists",
          lambda: os.path.exists(os.path.expanduser("~/.nanobot/config.json")),
          "Run nanobot config validate to create default config")

    # SQLite
    check("SQLite database",
          lambda: os.path.exists(os.path.expanduser("~/.nanobot/memory/facts.db")),
          "Run nanobot agent -m 'test' to initialise")

    # MCP servers
    for name in ["server_control", "deploy", "coding", "browser", "app_control"]:
        path = os.path.expanduser(f"~/.nanobot/mcp-servers/{name}/server.py")
        check(f"MCP server: {name}", lambda p=path: os.path.exists(p) or (_ for _ in ()).throw(FileNotFoundError(p)),
              f"Implement Phase for {name}")

    # Print results
    table = Table(title="Nanobot Doctor")
    table.add_column("", width=3)
    table.add_column("Check", style="cyan")
    table.add_column("Issue")

    for icon, name, issue in checks:
        color = "green" if icon == "✓" else "red"
        table.add_row(f"[{color}]{icon}[/{color}]", name, issue)

    console.print(table)
    passed = sum(1 for c in checks if c[0] == "✓")
    console.print(f"\n[bold]{passed}/{len(checks)} checks passed[/bold]")
```

### Register All Commands in Main CLI

In `~/nanobot/cli/__init__.py` or main CLI app file, add:

```python
from nanobot.cli.memory_commands import memory_app
from nanobot.cli.skills_commands import skills_app
from nanobot.cli.cron_commands import cron_app

app.add_typer(memory_app)
app.add_typer(skills_app)
app.add_typer(cron_app)
```

---

## FEATURE 16.2 — COMPLETE CONFIG SCHEMA

Create `~/nanobot/config/schema.py`:

```python
from pydantic import BaseModel, Field, validator
from typing import Optional
import os

class RedisBackendConfig(BaseModel):
    enabled: bool = True
    host: str = "localhost"
    port: int = 6379
    ttl: int = 3600

class SqliteBackendConfig(BaseModel):
    enabled: bool = True
    path: str = "~/.nanobot/memory/facts.db"

class ChromaBackendConfig(BaseModel):
    enabled: bool = True
    path: str = "~/.nanobot/memory/chroma"

class MemoryBackendsConfig(BaseModel):
    redis: RedisBackendConfig = Field(default_factory=RedisBackendConfig)
    sqlite: SqliteBackendConfig = Field(default_factory=SqliteBackendConfig)
    chroma: ChromaBackendConfig = Field(default_factory=ChromaBackendConfig)

class MemoryConfig(BaseModel):
    backends: MemoryBackendsConfig = Field(default_factory=MemoryBackendsConfig)
    decay: dict = Field(default_factory=lambda: {"enabled": True, "run_at": "03:00"})
    auto_link: bool = True

class SecurityConfig(BaseModel):
    enabled: bool = True
    require_confirmation_for_dangerous: bool = True
    block_root_execution: bool = True
    min_memory_salience: float = Field(0.2, ge=0.0, le=1.0)
    max_memory_tokens: int = Field(300, ge=50, le=2000)
    injection_detection: bool = True
    audit_log_path: str = "~/.nanobot/logs/security_audit.jsonl"

class ObservabilityConfig(BaseModel):
    enabled: bool = True
    trace_file: str = "~/.nanobot/logs/traces.jsonl"
    otlp_endpoint: str = ""
    prometheus_port: int = 0
    sample_rate: float = Field(1.0, ge=0.0, le=1.0)
    include_tool_args: bool = False
    include_memory_content: bool = False

class WhatsAppConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""
    phone_number_id: str = ""
    verify_token: str = ""
    messages_per_minute: int = 10
    whisper_model: str = "base"

class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    messages_per_minute: int = 20
    respond_in_groups: bool = False

class EmailConfig(BaseModel):
    enabled: bool = False
    address: str = ""
    password: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    poll_interval_minutes: int = 5

class ChannelsConfig(BaseModel):
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)

class SubagentsConfig(BaseModel):
    enabled: bool = True
    max_concurrent: int = Field(3, ge=1, le=10)
    default_budget_tokens: int = 50000
    default_budget_seconds: int = 300
    inbox_review_after_subgoal: bool = True

class LoRaConfig(BaseModel):
    enabled: bool = False
    auto_train: bool = False
    min_examples: int = 100
    base_model: str = "meta-llama/Meta-Llama-3.1-8B"

class SkillsConfig(BaseModel):
    enabled: bool = True
    auto_create_after_novel_system2: bool = True
    skills_dir: str = "~/nanobot/skills"

class HeartbeatConfig(BaseModel):
    enabled: bool = True
    check_interval_minutes: int = 5

class NanobotConfig(BaseModel):
    """Complete validated config schema. Every key used across all phases."""
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    subagents: SubagentsConfig = Field(default_factory=SubagentsConfig)
    lora: LoRaConfig = Field(default_factory=LoRaConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)

    # API keys — loaded from environment variables if not in config
    anthropic_api_key: str = Field("", description="Set via ANTHROPIC_API_KEY env var")
    openrouter_api_key: str = Field("", description="Set via OPENROUTER_API_KEY env var")

    @validator("anthropic_api_key", pre=True, always=True)
    def load_anthropic_key(cls, v):
        return v or os.environ.get("ANTHROPIC_API_KEY", "")

    @validator("openrouter_api_key", pre=True, always=True)
    def load_openrouter_key(cls, v):
        return v or os.environ.get("OPENROUTER_API_KEY", "")
```

Add `pydantic>=2.0.0` to `pyproject.toml`.

### Enhanced `ConfigLoader`

In `~/nanobot/config/loader.py`, replace existing load function with schema-validated version:

```python
import json, os
from nanobot.config.schema import NanobotConfig

CONFIG_PATH = os.path.expanduser("~/.nanobot/config.json")

def load_config() -> NanobotConfig:
    """
    Load config from ~/.nanobot/config.json.
    Validates against NanobotConfig schema.
    Missing keys filled with defaults.
    """
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            raw = json.load(f)
    else:
        raw = {}
        save_config(NanobotConfig())
        logger.info(f"Created default config at {CONFIG_PATH}")

    return NanobotConfig(**raw)

def save_config(config: NanobotConfig):
    """Write config back to disk."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config.model_dump(), f, indent=2)
```

---

## CONFIG KEYS TO ADD

This phase consolidates all config from all phases into the `NanobotConfig` Pydantic model. No new keys beyond the schema defined above.

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_cli.py`

```python
class TestMemoryCommands:
    def test_memory_search_outputs_table()
    def test_memory_list_filters_by_type()
    def test_memory_delete_archives_not_hard_deletes()
    def test_memory_stats_shows_counts()
    def test_memory_decay_triggers_pass()

class TestSkillsCommands:
    def test_skills_list_shows_table()
    def test_skills_show_displays_steps()
    def test_skills_delete_removes_skill()

class TestCronCommands:
    def test_cron_list_shows_jobs()
    def test_cron_run_executes_job()
    def test_cron_disable_updates_enabled()

class TestDoctorCommand:
    def test_doctor_runs_all_checks()
    def test_doctor_shows_pass_fail_counts()

class TestNanobotConfig:
    def test_defaults_populated_for_missing_keys()
    def test_api_key_loaded_from_env()
    def test_invalid_sample_rate_raises()
    def test_serialise_and_deserialise_roundtrip()

class TestConfigLoader:
    def test_creates_default_config_if_missing()
    def test_loads_and_validates_existing_config()
    def test_missing_keys_filled_with_defaults()
```

---

## CROSS-REFERENCES

- **All phases**: `ConfigLoader.load_config()` returns a `NanobotConfig` object. Every phase reads its config section from this object. The config is the single source of truth for all runtime settings.
- **Phase 1** (MemoryRouter): `memory_app` CLI commands call `get_memory_router()` helper — ensure this helper is accessible
- **Phase 11** (CronScheduler): `cron_app` CLI commands call `CronScheduler()` directly
- **Phase 13** (SkillWriter): `skills_app` CLI commands call `SkillWriter()` directly
- **Phase 15** (NanobotTracer): `nanobot metrics` and `nanobot traces` commands read `session_summary()` and `traces.jsonl`

All canonical names are in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
