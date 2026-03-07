# Phase 0 — Foundation Cleanup

> **Goal:** Fix structural issues that make every subsequent phase harder.  
> **Duration:** 5-7 days  
> **Risk Level:** Low (refactoring, no behavior changes)  
> **Dependencies:** None (this is the starting point)

---

## 0.1 — Split `commands.py` Monolith (1,781 lines → 7 modules)

### Problem
`pawbot/cli/commands.py` is a 67KB monolith containing **all** CLI commands: onboarding, gateway, dashboard, agent, channels, cron, doctor, and bridge management. This makes:
- Testing individual commands impossible
- Merge conflicts guaranteed
- Circular import debugging painful

### Current File Structure
```
pawbot/cli/
├── __init__.py          (29 bytes)
├── commands.py          (67,926 bytes — THE PROBLEM)
├── cron_commands.py     (2,996 bytes — already extracted)
├── formatter.py         (3,852 bytes)
├── memory_commands.py   (4,753 bytes — already extracted)
├── onboard.py           (36,519 bytes)
└── skills_commands.py   (2,463 bytes — already extracted)
```

### Target File Structure
```
pawbot/cli/
├── __init__.py          (updated — exports `app`)
├── commands.py          (< 150 lines — main app + version + callback only)
├── agent_commands.py    (NEW — `agent` command + interactive mode)
├── gateway_commands.py  (NEW — `gateway` + `run` commands)
├── dashboard_commands.py (NEW — `dashboard` command)
├── channel_commands.py  (NEW — `channels` subgroup)
├── cron_commands.py     (existing — already extracted)
├── formatter.py         (existing)
├── memory_commands.py   (existing — already extracted)
├── onboard.py           (existing)
├── skills_commands.py   (existing — already extracted)
└── _helpers.py          (NEW — shared utilities)
```

### Step 0.1.1 — Create `_helpers.py` (shared CLI utilities)

Extract these functions from `commands.py` into `pawbot/cli/_helpers.py`:

```python
"""Shared CLI utilities used across command modules."""

import os
import select
import signal
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from pawbot import __logo__, __version__
from pawbot.config.schema import Config

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    try:
        import termios
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    history_file = Path.home() / ".pawbot" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,
    )


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(f"[cyan]{__logo__} pawbot[/cyan]")
    console.print(body)
    console.print()


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit."""
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc


def _make_provider(config: Config):
    """Create the appropriate LLM provider from config."""
    import typer
    from pawbot.providers.factory import create_provider
    try:
        return create_provider(config)
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)
```

### Step 0.1.2 — Create `agent_commands.py`

Move the `agent()` command (lines 598-765 in `commands.py`) to a new file:

```python
"""CLI agent command — interactive and single-message modes."""

import asyncio
import os
import signal

import typer

from pawbot.cli._helpers import (
    _flush_pending_tty_input,
    _init_prompt_session,
    _is_exit_command,
    _make_provider,
    _print_agent_response,
    _read_interactive_input_async,
    _restore_terminal,
    console,
)
from pawbot import __logo__
from pawbot.utils.helpers import sync_workspace_templates

agent_app = typer.Typer(help="Agent commands")


@agent_app.command(name="chat")
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs"),
):
    """Interact with the agent directly."""
    from loguru import logger
    from pawbot.agent.loop import AgentLoop
    from pawbot.bus.queue import MessageBus
    from pawbot.config.loader import get_data_dir, load_config
    from pawbot.cron.service import CronService

    config = load_config()
    sync_workspace_templates(config.workspace_path)

    bus = MessageBus()
    provider = _make_provider(config)

    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    if logs:
        logger.enable("pawbot")
    else:
        logger.disable("pawbot")

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        reasoning_effort=config.agents.defaults.reasoning_effort,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
    )

    # ... (move the rest of the agent function here)
    # This includes: _thinking_ctx, _cli_progress, run_once, run_interactive
```

> [!NOTE]
> The full extraction follows the same pattern for `gateway_commands.py` (lines 330-551) and `dashboard_commands.py` (lines 306-322) and `channel_commands.py` (lines 773-951).

### Step 0.1.3 — Update `commands.py` to import submodules

After extraction, `commands.py` should be reduced to:

```python
"""CLI commands for pawbot — main app entry point."""

import typer
from pawbot import __logo__, __version__

app = typer.Typer(
    name="pawbot",
    help=f"{__logo__} pawbot - Personal AI Assistant",
    no_args_is_help=True,
)


def version_callback(value: bool):
    if value:
        from rich.console import Console
        Console().print(f"{__logo__} pawbot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """pawbot - Personal AI Assistant."""
    pass


# ── Register all subcommand groups ──────────────────────────────────────────
from pawbot.cli.agent_commands import agent_app        # noqa: E402
from pawbot.cli.gateway_commands import gateway_app    # noqa: E402
from pawbot.cli.dashboard_commands import dashboard_app  # noqa: E402
from pawbot.cli.channel_commands import channels_app   # noqa: E402
from pawbot.cli.memory_commands import memory_app      # noqa: E402
from pawbot.cli.skills_commands import skills_app      # noqa: E402
from pawbot.cli.cron_commands import cron_app           # noqa: E402

# Direct commands (not sub-groups)
app.command(name="agent")(agent_app)
app.command(name="gateway")(gateway_app)
app.command(name="dashboard")(dashboard_app)

# Sub-groups
app.add_typer(channels_app, name="channels")
app.add_typer(memory_app, name="memory")
app.add_typer(skills_app, name="skills")
app.add_typer(cron_app, name="cron")

# Onboard stays inline (it's the entry point)
from pawbot.cli.onboard import register_onboard_command  # noqa: E402
register_onboard_command(app)
```

### Verification
```bash
# All existing CLI commands still work
pawbot --version
pawbot --help
pawbot agent --help
pawbot gateway --help
pawbot dashboard --help
pawbot channels status
pawbot memory list
pawbot skills list
pawbot cron list

# Tests pass
pytest tests/ -v --tb=short

# Lint clean
ruff check pawbot/cli/
```

---

## 0.2 — Audit Phase Status Markers

### Problem
`phases/MASTER_REFERENCE.md` marks many phases as `✅ Implemented` or `✅ Documented` when the actual code is scaffolded stubs.

### Action

Create `pawbot/cli/doctor_audit.py` that programmatically checks each phase:

```python
"""Phase status audit — verify MASTER_REFERENCE.md claims against reality."""

from pathlib import Path
from typing import NamedTuple


class PhaseCheck(NamedTuple):
    phase: str
    claim: str
    actual: str
    details: str


def audit_phases() -> list[PhaseCheck]:
    """Check each phase's claimed status against reality."""
    results: list[PhaseCheck] = []

    # Phase 1: Memory System
    try:
        from pawbot.agent.memory.sqlite_store import SQLiteFactStore
        from pawbot.agent.memory.router import MemoryRouter
        # Try to instantiate — verifies it actually works
        store = SQLiteFactStore({})
        results.append(PhaseCheck("Phase 1", "✅ Implemented", "🟢 Verified", "SQLiteFactStore instantiates"))
    except Exception as e:
        results.append(PhaseCheck("Phase 1", "✅ Implemented", "🔶 Partial", f"SQLiteFactStore failed: {e}"))

    # Phase 2: Agent Loop Intelligence
    try:
        from pawbot.agent.classifier import ComplexityClassifier
        c = ComplexityClassifier()
        score = c.score("hello")
        assert isinstance(score, float)
        results.append(PhaseCheck("Phase 2", "✅ Implemented", "🟢 Verified", f"ComplexityClassifier works (score={score:.2f})"))
    except Exception as e:
        results.append(PhaseCheck("Phase 2", "✅ Implemented", "🔶 Partial", f"ComplexityClassifier failed: {e}"))

    # Phase 4: Model Router
    try:
        from pawbot.providers.router import ModelRouter, ROUTING_TABLE
        assert len(ROUTING_TABLE) > 0
        results.append(PhaseCheck("Phase 4", "✅ Implemented", "🟢 Verified", f"ROUTING_TABLE has {len(ROUTING_TABLE)} entries"))
    except Exception as e:
        results.append(PhaseCheck("Phase 4", "✅ Implemented", "🔶 Partial", f"ModelRouter failed: {e}"))

    # Phase 13: LoRA Pipeline
    try:
        from pawbot.agent.skills import SkillLoader
        results.append(PhaseCheck("Phase 13", "✅ Documented", "🔶 Partial", "SkillLoader importable, LoRA training NOT implemented"))
    except Exception as e:
        results.append(PhaseCheck("Phase 13", "✅ Documented", "⚪ Stub", f"Import failed: {e}"))

    # Phase 14: Security
    try:
        from pawbot.agent.security import ActionGate, InjectionDetector
        gate = ActionGate()
        detector = InjectionDetector()
        results.append(PhaseCheck("Phase 14", "✅ Documented", "🟢 Verified", "ActionGate + InjectionDetector instantiate"))
    except Exception as e:
        results.append(PhaseCheck("Phase 14", "✅ Documented", "🔶 Partial", f"Security init failed: {e}"))

    # Phase 15: Observability
    try:
        from pawbot.agent.telemetry import PawbotTracer
        results.append(PhaseCheck("Phase 15", "✅ Documented", "🟢 Verified", "PawbotTracer importable"))
    except Exception as e:
        results.append(PhaseCheck("Phase 15", "✅ Documented", "🔶 Partial", f"Telemetry import failed: {e}"))

    # Phase 18: Fleet Commander
    try:
        from pawbot.fleet.commander import FleetCommander
        from pawbot.fleet.models import FleetConfig
        c = FleetCommander(config=FleetConfig())
        results.append(PhaseCheck("Phase 18", "✅ Implemented", "🔶 Partial", "FleetCommander instantiates but no E2E tests with real LLM"))
    except Exception as e:
        results.append(PhaseCheck("Phase 18", "✅ Implemented", "⚪ Stub", f"Fleet init failed: {e}"))

    return results
```

### Add CLI command
```python
# In cron_commands.py or a new doctor.py
@app.command("audit")
def audit():
    """Audit phase implementation status against MASTER_REFERENCE claims."""
    from pawbot.cli.doctor_audit import audit_phases
    results = audit_phases()
    # ... render as rich table
```

---

## 0.3 — Add Type Hints to Core Modules

### Files to annotate (priority order):
1. `pawbot/contracts.py` — already well-typed, verify with mypy
2. `pawbot/agent/loop.py` — add return types to all private methods  
3. `pawbot/providers/router.py` — replace `dict[str, Any]` config with `Config` type
4. `pawbot/agent/memory/router.py` — type all method returns
5. `pawbot/config/schema.py` — already Pydantic, verify model validators

### Add mypy configuration to `pyproject.toml`:
```toml
[tool.mypy]
python_version = "3.11"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = false  # Start permissive, tighten later

[[tool.mypy.overrides]]
module = "pawbot.config.*"
disallow_untyped_defs = true

[[tool.mypy.overrides]]
module = "pawbot.agent.memory.*"
disallow_untyped_defs = true
```

### Add `py.typed` marker:
```bash
# Create the marker file
touch pawbot/py.typed
```

---

## 0.4 — Standardize Exception Handling

### Problem
The codebase has 40+ instances of `except Exception as e:  # noqa: F841` — bare Exception catches that swallow errors silently.

### Rules
1. **Never** `except Exception` without logging
2. **Always** use typed exceptions where possible
3. **Create** custom exceptions for each subsystem

### Update `pawbot/errors.py`:
```python
"""Pawbot exception hierarchy."""


class PawbotError(Exception):
    """Base exception for all pawbot errors."""
    pass


class ConfigError(PawbotError):
    """Configuration validation or loading error."""
    pass


class ProviderError(PawbotError):
    """LLM provider communication error."""
    pass


class ProviderUnavailableError(ProviderError):
    """A provider is temporarily unavailable."""
    pass


class MemoryError(PawbotError):
    """Memory backend operation error."""
    pass


class ToolError(PawbotError):
    """Tool execution error."""
    pass


class SecurityError(PawbotError):
    """Security check failed."""
    pass


class FleetError(PawbotError):
    """Fleet orchestration error."""
    pass


class ChannelError(PawbotError):
    """Channel communication error."""
    pass
```

### Replace bare catches in critical paths:

```python
# BEFORE (in agent/memory/router.py line 36-37):
except Exception as e:  # noqa: F841
    logger.exception("Redis backend init failed")

# AFTER:
except Exception:
    logger.exception("Redis backend init failed — will use SQLite fallback")
    # NOTE: We intentionally continue without Redis; it's optional
```

---

## 0.5 — Enforce Import Style with Ruff

### Add to `pyproject.toml`:
```toml
[tool.ruff.lint]
ignore = ["E501"]
select = [
    "E", "F", "W",      # pycodestyle + pyflakes
    "I",                  # isort
    "UP",                 # pyupgrade
    "B",                  # flake8-bugbear
    "SIM",                # flake8-simplify
]

[tool.ruff.lint.isort]
known-first-party = ["pawbot"]
force-single-line = false
combine-as-imports = true
```

### Run once:
```bash
ruff check pawbot/ --fix
ruff format pawbot/
```

---

## Verification Checklist — Phase 0 Complete

- [ ] `commands.py` is < 200 lines
- [ ] All CLI commands work: `pawbot --version`, `pawbot agent --help`, `pawbot gateway --help`
- [ ] `pawbot audit` runs and shows phase status table
- [ ] `py.typed` marker exists at `pawbot/py.typed`
- [ ] `mypy pawbot/config/ --ignore-missing-imports` passes with 0 errors
- [ ] `ruff check pawbot/` passes with 0 errors
- [ ] `pytest tests/ -v --tb=short` passes (no regressions)
- [ ] `pawbot/errors.py` has typed exception hierarchy
- [ ] No bare `except Exception` without logging in `agent/loop.py`
- [ ] All `# noqa: F841` comments are justified (unused variable was intentional)
