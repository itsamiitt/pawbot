"""CLI commands for pawbot."""

import asyncio
import os
import select
import signal
import sys
from pathlib import Path

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from pawbot import __logo__, __version__
from pawbot.config.schema import Config
from pawbot.utils.helpers import sync_workspace_templates

app = typer.Typer(
    name="pawbot",
    help=f"{__logo__} pawbot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ── Phase 16: Register new subcommand groups ──────────────────────────
from pawbot.cli.memory_commands import memory_app   # noqa: E402
from pawbot.cli.skills_commands import skills_app     # noqa: E402

app.add_typer(memory_app, name="memory")
app.add_typer(skills_app, name="skills")


# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


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
    """Restore terminal to its original state (echo, line buffering, etc.)."""
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

    # Save terminal state so we can restore it on exit
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
        multiline=False,   # Enter submits (single line mode)
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
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc



def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} pawbot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """pawbot - Personal AI Assistant."""
    pass


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard(
    setup: bool = typer.Option(False, "--setup", help="Interactive setup: configure API key and model"),
):
    """Initialize pawbot configuration and workspace."""
    from pawbot.config.loader import get_config_path, load_config, save_config
    from pawbot.config.schema import Config
    from pawbot.utils.helpers import get_workspace_path

    config_path = get_config_path()

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
        if typer.confirm("Overwrite?"):
            config = Config()
            save_config(config)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            config = load_config()
            save_config(config)
            console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        config = Config()
        save_config(config)
        console.print(f"[green]✓[/green] Created config at {config_path}")

    # Create workspace
    workspace = get_workspace_path()

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace}")

    sync_workspace_templates(workspace)

    # ── Interactive setup: provider, API key, model ────────────────────
    if setup:
        import getpass
        config = load_config()

        console.print(f"\n{__logo__} [bold]Interactive Setup[/bold]\n")

        # Provider selection
        providers = [
            ("1", "openrouter", "OpenRouter", "Access to all models (recommended)", "https://openrouter.ai/keys"),
            ("2", "anthropic", "Anthropic", "Claude models direct", "https://console.anthropic.com/keys"),
            ("3", "openai", "OpenAI", "GPT models direct", "https://platform.openai.com/api-keys"),
            ("4", "deepseek", "DeepSeek", "DeepSeek models direct", "https://platform.deepseek.com"),
            ("5", "gemini", "Gemini", "Google Gemini direct", "https://aistudio.google.com"),
        ]

        console.print("[bold]Choose your LLM provider:[/bold]")
        for num, _, name, desc, url in providers:
            console.print(f"  [cyan]{num}[/cyan]) {name} — {desc}")
        console.print(f"  [cyan]s[/cyan]) Skip (configure manually later)")
        console.print()

        choice = typer.prompt("Select provider", default="1").strip()

        if choice.lower() != "s":
            selected = None
            for num, key, name, desc, url in providers:
                if choice == num:
                    selected = (key, name, url)
                    break

            if not selected:
                console.print("[yellow]Invalid choice, defaulting to OpenRouter[/yellow]")
                selected = ("openrouter", "OpenRouter", "https://openrouter.ai/keys")

            prov_key, prov_name, prov_url = selected
            console.print(f"\n[bold]{prov_name}[/bold] selected.")
            console.print(f"  Get your API key at: [cyan]{prov_url}[/cyan]\n")

            # API key input (masked)
            api_key = getpass.getpass(f"  Paste your {prov_name} API key (hidden): ").strip()

            if api_key:
                # Set the key on the correct provider config
                provider_cfg = getattr(config.providers, prov_key)
                provider_cfg.api_key = api_key
                console.print(f"  [green]✓[/green] API key saved for {prov_name}")

                # Model selection
                default_models = {
                    "openrouter": "anthropic/claude-sonnet-4-5",
                    "anthropic": "claude-sonnet-4-20250514",
                    "openai": "gpt-4o",
                    "deepseek": "deepseek-chat",
                    "gemini": "gemini-2.0-flash",
                }
                default_model = default_models.get(prov_key, "anthropic/claude-sonnet-4-5")

                console.print(f"\n[bold]Choose your default model:[/bold]")
                console.print(f"  Press Enter for default: [cyan]{default_model}[/cyan]")
                model = typer.prompt("  Model", default=default_model).strip()

                config.agents.defaults.model = model
                config.agents.defaults.provider = prov_key
                console.print(f"  [green]✓[/green] Model set to [cyan]{model}[/cyan]")
            else:
                console.print("  [yellow]No key entered — you can add it later in ~/.pawbot/config.json[/yellow]")

            # Save updated config
            save_config(config)
            console.print(f"\n[green]✓[/green] Config saved to {config_path}")
        else:
            console.print("\n[dim]Skipped. Add your API key manually to ~/.pawbot/config.json[/dim]")

    console.print(f"\n{__logo__} pawbot is ready!")
    console.print("\nNext steps:")
    if not setup:
        console.print("  1. Add your API key to [cyan]~/.pawbot/config.json[/cyan]")
        console.print("     Get one at: https://openrouter.ai/keys")
        console.print("  2. Chat: [cyan]pawbot agent -m \"Hello!\"[/cyan]")
    else:
        console.print("  Chat: [cyan]pawbot agent -m \"Hello!\"[/cyan]")
        console.print("  Interactive: [cyan]pawbot agent[/cyan]")
        console.print("  Gateway: [cyan]pawbot gateway[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/pawbot#-chat-apps[/dim]")





def _make_provider(config: Config):
    """Create the appropriate LLM provider from config."""
    from pawbot.providers.custom_provider import CustomProvider
    from pawbot.providers.litellm_provider import LiteLLMProvider
    from pawbot.providers.openai_codex_provider import OpenAICodexProvider

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)

    # OpenAI Codex (OAuth)
    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        return OpenAICodexProvider(default_model=model)

    # Custom: direct OpenAI-compatible endpoint, bypasses LiteLLM
    if provider_name == "custom":
        return CustomProvider(
            api_key=p.api_key if p else "no-key",
            api_base=config.get_api_base(model) or "http://localhost:8000/v1",
            default_model=model,
        )

    from pawbot.providers.registry import find_by_name
    spec = find_by_name(provider_name)
    if not model.startswith("bedrock/") and not (p and p.api_key) and not (spec and spec.is_oauth):
        console.print("[red]Error: No API key configured.[/red]")
        console.print("Set one in ~/.pawbot/config.json under providers section")
        raise typer.Exit(1)

    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(model),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=provider_name,
    )


# ============================================================================
# Dashboard
# ============================================================================


@app.command("dashboard")
def dashboard(
    port: int = typer.Option(4000, "--port", "-p", help="Port to run on"),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Don't open browser automatically"
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
):
    """Start the Pawbot local dashboard at http://localhost:4000"""
    console.print(
        f"\n[green]{__logo__}[/green] Starting Pawbot dashboard at "
        f"[bold]http://{host}:{port}[/bold]"
    )
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")
    from pawbot.dashboard.server import start

    start(host=host, port=port, open_browser=not no_browser)


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the pawbot gateway."""
    from loguru import logger

    from pawbot.agent.memory import MemoryDecayEngine, SQLiteFactStore
    from pawbot.agent.loop import AgentLoop
    from pawbot.bus.queue import MessageBus
    from pawbot.channels.manager import ChannelManager
    from pawbot.config.loader import get_data_dir, load_config
    from pawbot.cron.scheduler import CronScheduler
    from pawbot.cron.service import CronService
    from pawbot.cron.types import CronJob
    from pawbot.heartbeat.engine import HeartbeatEngine
    from pawbot.heartbeat.service import HeartbeatService
    from pawbot.session.manager import SessionManager

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    console.print(f"{__logo__} Starting pawbot gateway on port {port}...")

    config = load_config()
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)

    # Create cron service first (callback set after agent creation)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # Create agent with cron service
    agent = AgentLoop(
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
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
    )

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        response = await agent.process_direct(
            job.payload.message,
            session_key=f"cron:{job.id}",
            channel=job.payload.channel or "cli",
            chat_id=job.payload.to or "direct",
        )
        if job.payload.deliver and job.payload.to:
            from pawbot.bus.events import OutboundMessage
            await bus.publish_outbound(OutboundMessage(
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to,
                content=response or ""
            ))
        return response
    cron.on_job = on_cron_job

    # Phase 11 internal scheduler (memory decay + heartbeat triggers)
    phase11_cron_cfg = config.cron
    internal_cron = CronScheduler(
        registry_path=phase11_cron_cfg.registry_path,
        check_interval_seconds=phase11_cron_cfg.check_interval_seconds,
    )
    if phase11_cron_cfg.enabled:
        try:
            sqlite_store = SQLiteFactStore(config.model_dump(by_alias=False))
            decay_engine = MemoryDecayEngine(sqlite_store)
            internal_cron.register(
                name=MemoryDecayEngine.JOB_NAME,
                schedule=MemoryDecayEngine.CRON_SCHEDULE,
                fn=decay_engine.decay_pass,
                description="Nightly memory salience decay and archival",
            )
        except Exception as e:
            logger.warning("Failed to initialize memory decay cron registration: {}", e)

    # Create channel manager
    channels = ChannelManager(config, bus)

    # Phase 11 heartbeat engine (trigger-driven proactive wake-ups)
    heartbeat_engine = None
    phase11_hb_cfg = config.heartbeat
    if phase11_cron_cfg.enabled and phase11_hb_cfg.enabled:
        try:
            interval_mins = max(1, int(phase11_hb_cfg.check_interval_minutes))
            check_schedule = "0 * * * *" if interval_mins >= 60 else f"*/{interval_mins} * * * *"
            heartbeat_engine = HeartbeatEngine(
                agent_loop=agent,
                cron_scheduler=internal_cron,
                channel_router=channels,
                memory_router=None,
                triggers_path=phase11_hb_cfg.triggers_path,
                check_schedule=check_schedule,
            )
        except Exception as e:
            logger.warning("Failed to initialize HeartbeatEngine: {}", e)
    elif phase11_hb_cfg.enabled and not phase11_cron_cfg.enabled:
        logger.warning("Phase 11 heartbeat is enabled but cron scheduler is disabled")

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        enabled = set(channels.enabled_channels)
        # Prefer the most recently updated non-internal session on an enabled channel.
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # Fallback keeps prior behavior but remains explicit.
        return "cli", "direct"

    # Create heartbeat service
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        return await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from pawbot.bus.events import OutboundMessage
        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # No external channel available to deliver to
        await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    if phase11_cron_cfg.enabled:
        console.print(
            f"[green]✓[/green] Internal CronScheduler: every {phase11_cron_cfg.check_interval_seconds}s"
        )
    if heartbeat_engine is not None:
        console.print(
            f"[green]✓[/green] HeartbeatEngine: every {phase11_hb_cfg.check_interval_minutes}m"
        )

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")

    async def run():
        try:
            if phase11_cron_cfg.enabled:
                internal_cron.start()
            await cron.start()
            await heartbeat.start()
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            await agent.close_mcp()
            heartbeat.stop()
            cron.stop()
            internal_cron.stop()
            agent.stop()
            await channels.stop_all()

    asyncio.run(run())




# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show pawbot runtime logs during chat"),
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

    # Create cron service for tool usage (no callback needed for CLI unless running)
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

    # Show spinner when logs are off (no output to miss); skip when logs are on
    def _thinking_ctx():
        if logs:
            from contextlib import nullcontext
            return nullcontext()
        # Animated spinner is safe to use with prompt_toolkit input handling
        return console.status("[dim]pawbot is thinking...[/dim]", spinner="dots")

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        console.print(f"  [dim]↳ {content}[/dim]")

    if message:
        # Single message mode — direct call, no bus needed
        async def run_once():
            with _thinking_ctx():
                response = await agent_loop.process_direct(message, session_id, on_progress=_cli_progress)
            _print_agent_response(response, render_markdown=markdown)
            await agent_loop.close_mcp()

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from pawbot.bus.events import InboundMessage
        _init_prompt_session()
        console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        def _exit_on_sigint(signum, frame):
            _restore_terminal()
            console.print("\nGoodbye!")
            os._exit(0)

        signal.signal(signal.SIGINT, _exit_on_sigint)

        async def run_interactive():
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[str] = []

            async def _consume_outbound():
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass
                            elif ch and not is_tool_hint and not ch.send_progress:
                                pass
                            else:
                                console.print(f"  [dim]↳ {msg.content}[/dim]")
                        elif not turn_done.is_set():
                            if msg.content:
                                turn_response.append(msg.content)
                            turn_done.set()
                        elif msg.content:
                            console.print()
                            _print_agent_response(msg.content, render_markdown=markdown)
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()

                        await bus.publish_inbound(InboundMessage(
                            channel=cli_channel,
                            sender_id="user",
                            chat_id=cli_chat_id,
                            content=user_input,
                        ))

                        with _thinking_ctx():
                            await turn_done.wait()

                        if turn_response:
                            _print_agent_response(turn_response[0], render_markdown=markdown)
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()

        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from pawbot.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row(
        "WhatsApp",
        "✓" if wa.enabled else "✗",
        wa.bridge_url
    )

    dc = config.channels.discord
    table.add_row(
        "Discord",
        "✓" if dc.enabled else "✗",
        dc.gateway_url
    )

    # Feishu
    fs = config.channels.feishu
    fs_config = f"app_id: {fs.app_id[:10]}..." if fs.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "Feishu",
        "✓" if fs.enabled else "✗",
        fs_config
    )

    # Mochat
    mc = config.channels.mochat
    mc_base = mc.base_url or "[dim]not configured[/dim]"
    table.add_row(
        "Mochat",
        "✓" if mc.enabled else "✗",
        mc_base
    )

    # Telegram
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row(
        "Telegram",
        "✓" if tg.enabled else "✗",
        tg_config
    )

    # Slack
    slack = config.channels.slack
    slack_config = "socket" if slack.app_token and slack.bot_token else "[dim]not configured[/dim]"
    table.add_row(
        "Slack",
        "✓" if slack.enabled else "✗",
        slack_config
    )

    # DingTalk
    dt = config.channels.dingtalk
    dt_config = f"client_id: {dt.client_id[:10]}..." if dt.client_id else "[dim]not configured[/dim]"
    table.add_row(
        "DingTalk",
        "✓" if dt.enabled else "✗",
        dt_config
    )

    # QQ
    qq = config.channels.qq
    qq_config = f"app_id: {qq.app_id[:10]}..." if qq.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "QQ",
        "✓" if qq.enabled else "✗",
        qq_config
    )

    # Email
    em = config.channels.email
    em_config = em.imap_host if em.imap_host else "[dim]not configured[/dim]"
    table.add_row(
        "Email",
        "✓" if em.enabled else "✗",
        em_config
    )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    user_bridge = Path.home() / ".pawbot" / "bridge"

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # pawbot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall pawbot")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess

    from pawbot.config.loader import load_config

    config = load_config()
    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    env = {**os.environ}
    if config.channels.whatsapp.bridge_token:
        env["BRIDGE_TOKEN"] = config.channels.whatsapp.bridge_token

    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Cron Commands
# ============================================================================

cron_app = typer.Typer(help="Manage scheduled tasks")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
):
    """List scheduled jobs."""
    from pawbot.config.loader import get_data_dir
    from pawbot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    jobs = service.list_jobs(include_disabled=all)

    if not jobs:
        console.print("No scheduled jobs.")
        return

    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Next Run")

    import time
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    for job in jobs:
        # Format schedule
        if job.schedule.kind == "every":
            sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
        elif job.schedule.kind == "cron":
            sched = f"{job.schedule.expr or ''} ({job.schedule.tz})" if job.schedule.tz else (job.schedule.expr or "")
        else:
            sched = "one-time"

        # Format next run
        next_run = ""
        if job.state.next_run_at_ms:
            ts = job.state.next_run_at_ms / 1000
            try:
                tz = ZoneInfo(job.schedule.tz) if job.schedule.tz else None
                next_run = _dt.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M")
            except Exception:
                next_run = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

        status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"

        table.add_row(job.id, job.name, sched, status, next_run)

    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
    every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
    tz: str | None = typer.Option(None, "--tz", help="IANA timezone for cron (e.g. 'America/Vancouver')"),
    at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
    to: str = typer.Option(None, "--to", help="Recipient for delivery"),
    channel: str = typer.Option(None, "--channel", help="Channel for delivery (e.g. 'telegram', 'whatsapp')"),
):
    """Add a scheduled job."""
    from pawbot.config.loader import get_data_dir
    from pawbot.cron.service import CronService
    from pawbot.cron.types import CronSchedule

    if tz and not cron_expr:
        console.print("[red]Error: --tz can only be used with --cron[/red]")
        raise typer.Exit(1)

    # Determine schedule type
    if every:
        schedule = CronSchedule(kind="every", every_ms=every * 1000)
    elif cron_expr:
        schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz)
    elif at:
        import datetime
        dt = datetime.datetime.fromisoformat(at)
        schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
    else:
        console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
        raise typer.Exit(1)

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    try:
        job = service.add_job(
            name=name,
            schedule=schedule,
            message=message,
            deliver=deliver,
            to=to,
            channel=channel,
        )
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    console.print(f"[green]✓[/green] Added job '{job.name}' ({job.id})")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    from pawbot.config.loader import get_data_dir
    from pawbot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    if service.remove_job(job_id):
        console.print(f"[green]✓[/green] Removed job {job_id}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID"),
    disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
):
    """Enable or disable a job."""
    from pawbot.config.loader import get_data_dir
    from pawbot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    job = service.enable_job(job_id, enabled=not disable)
    if job:
        status = "disabled" if disable else "enabled"
        console.print(f"[green]✓[/green] Job '{job.name}' {status}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
    force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
):
    """Manually run a job."""
    from loguru import logger

    from pawbot.agent.loop import AgentLoop
    from pawbot.bus.queue import MessageBus
    from pawbot.config.loader import get_data_dir, load_config
    from pawbot.cron.service import CronService
    from pawbot.cron.types import CronJob
    logger.disable("pawbot")

    config = load_config()
    provider = _make_provider(config)
    bus = MessageBus()
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
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
    )

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    result_holder = []

    async def on_job(job: CronJob) -> str | None:
        response = await agent_loop.process_direct(
            job.payload.message,
            session_key=f"cron:{job.id}",
            channel=job.payload.channel or "cli",
            chat_id=job.payload.to or "direct",
        )
        result_holder.append(response)
        return response

    service.on_job = on_job

    async def run():
        return await service.run_job(job_id, force=force)

    if asyncio.run(run()):
        console.print("[green]✓[/green] Job executed")
        if result_holder:
            _print_agent_response(result_holder[0], render_markdown=True)
    else:
        console.print(f"[red]Failed to run job {job_id}[/red]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show pawbot status."""
    from pawbot.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} pawbot Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        from pawbot.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")


# ============================================================================
# OAuth Login
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


_LOGIN_HANDLERS: dict[str, callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn
    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
):
    """Authenticate with an OAuth provider."""
    from pawbot.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive
        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),
                prompt_fn=lambda s: typer.prompt(s),
            )
        if not (token and token.access):
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]")
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    import asyncio

    console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")

    async def _trigger():
        from litellm import acompletion
        await acompletion(model="github_copilot/gpt-4o", messages=[{"role": "user", "content": "hi"}], max_tokens=1)

    try:
        asyncio.run(_trigger())
        console.print("[green]✓ Authenticated with GitHub Copilot[/green]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# Phase 16 — New top-level commands
# ============================================================================


# ── Subagents status ──────────────────────────────────────────────────────


subagents_app = typer.Typer(help="Manage subagents")
app.add_typer(subagents_app, name="subagents")


@subagents_app.command("status")
def subagents_status():
    """Show active subagents and pool status."""
    table = Table(title="Subagent Pool Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    try:
        from pawbot.config.loader import load_config
        cfg = load_config()
        table.add_row("Enabled", "✓" if cfg.subagents.enabled else "✗")
        table.add_row("Max concurrent", str(cfg.subagents.max_concurrent))
        table.add_row("Default budget (tokens)", f"{cfg.subagents.default_budget_tokens:,}")
        table.add_row("Default budget (seconds)", str(cfg.subagents.default_budget_seconds))
        table.add_row("Inbox review", "✓" if cfg.subagents.inbox_review_after_subgoal else "✗")
    except Exception as e:
        table.add_row("Error", str(e)[:60])
    console.print(table)


# ── Metrics command ───────────────────────────────────────────────────────


@app.command()
def metrics():
    """Show session metrics summary."""
    from pawbot.agent.telemetry import PawbotTracer
    from pawbot.config.loader import load_config

    config = load_config()
    tracer = PawbotTracer(config.model_dump(by_alias=False))
    summary = tracer.session_summary()

    table = Table(title="Session Metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total spans", str(summary.get("total_spans", 0)))
    table.add_row("Total tokens", f"{summary.get('total_tokens', 0):,}")
    table.add_row("Errors", str(summary.get("errors", 0)))
    table.add_row("Elapsed", f"{summary.get('session_elapsed_seconds', 0):.1f}s")

    for tool, stats in summary.get("tool_calls", {}).items():
        table.add_row(f"Tool: {tool}", f"{stats['count']} calls")
    for model, stats in summary.get("model_calls", {}).items():
        table.add_row(f"Model: {model}", f"{stats['count']} calls, {stats['tokens']} tokens")

    mem = summary.get("memory_ops", {})
    table.add_row("Memory saves", str(mem.get("save", 0)))
    table.add_row("Memory searches", str(mem.get("search", 0)))
    table.add_row("Cache hits", str(mem.get("cache_hits", 0)))

    console.print(table)


# ── Traces command ────────────────────────────────────────────────────────


@app.command()
def traces(
    n: int = typer.Option(20, "--limit", "-n", help="Number of recent traces to show"),
):
    """Show recent trace spans."""
    import json

    from pawbot.config.loader import load_config

    config = load_config()
    obs_cfg = config.observability
    trace_file = os.path.expanduser(obs_cfg.trace_file)

    if not os.path.exists(trace_file):
        console.print("[yellow]No trace file found yet.[/yellow]")
        console.print(f"[dim]Expected at: {trace_file}[/dim]")
        return

    try:
        with open(trace_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        spans = [json.loads(line) for line in lines[-n:] if line.strip()]
    except Exception as e:
        console.print(f"[red]Failed to read traces: {e}[/red]")
        return

    if not spans:
        console.print("[yellow]No traces recorded yet.[/yellow]")
        return

    table = Table(title=f"Recent Traces ({len(spans)} spans)")
    table.add_column("Trace", style="dim", width=8)
    table.add_column("Span", style="dim", width=8)
    table.add_column("Name", style="cyan")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Attributes", max_width=40)

    for span in spans:
        status_str = span.get("status", "ok")
        status_color = "green" if status_str == "ok" else "red"
        attrs = span.get("attributes", {})
        attr_preview = ", ".join(f"{k}={v}" for k, v in list(attrs.items())[:3])
        table.add_row(
            span.get("trace_id", "")[:8],
            span.get("span_id", "")[:8],
            span.get("name", ""),
            f"[{status_color}]{status_str}[/{status_color}]",
            f"{span.get('duration_ms', 0):.1f}ms",
            attr_preview[:40] if attr_preview else "—",
        )
    console.print(table)


# ── Config commands ───────────────────────────────────────────────────────


config_app = typer.Typer(help="Configuration management")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show():
    """Display resolved configuration with sources."""
    import json

    from pawbot.config.loader import get_config_path, load_config

    config = load_config()
    path = get_config_path()
    console.print(f"[dim]Config file: {path}[/dim]\n")

    data = config.model_dump(by_alias=False)
    console.print_json(json.dumps(data, indent=2, default=str))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Dotted config key (e.g. 'security.enabled')"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Update a config value."""
    from pawbot.config.loader import load_config, save_config

    config = load_config()
    data = config.model_dump(by_alias=False)

    # Navigate dotted key path
    parts = key.split(".")
    target = data
    for part in parts[:-1]:
        if isinstance(target, dict) and part in target:
            target = target[part]
        else:
            console.print(f"[red]Key not found: {key}[/red]")
            raise typer.Exit(1)

    final_key = parts[-1]
    if isinstance(target, dict) and final_key in target:
        old_value = target[final_key]
        # Coerce value to same type
        if isinstance(old_value, bool):
            target[final_key] = value.lower() in ("true", "1", "yes")
        elif isinstance(old_value, int):
            target[final_key] = int(value)
        elif isinstance(old_value, float):
            target[final_key] = float(value)
        else:
            target[final_key] = value

        from pawbot.config.schema import Config
        new_config = Config.model_validate(data)
        save_config(new_config)
        console.print(f"[green]✓ {key} = {target[final_key]}[/green]")
    else:
        console.print(f"[red]Key not found: {key}[/red]")
        raise typer.Exit(1)


@config_app.command("validate")
def config_validate():
    """Validate config against full schema."""
    from pawbot.config.loader import get_config_path, load_config

    path = get_config_path()
    if not path.exists():
        console.print(f"[yellow]No config file at {path}[/yellow]")
        console.print("Run [cyan]pawbot onboard[/cyan] to create one.")
        return

    try:
        config = load_config()
        console.print("[green]✓ Config is valid[/green]")
        data = config.model_dump(by_alias=False)
        t = Table(title="Config Sections")
        t.add_column("Section", style="cyan")
        t.add_column("Keys", justify="right")
        for section_name, section_value in data.items():
            if isinstance(section_value, dict):
                t.add_row(section_name, str(len(section_value)))
            else:
                t.add_row(section_name, "scalar")
        console.print(t)
    except Exception as e:
        console.print(f"[red]✗ Config validation failed: {e}[/red]")
        raise typer.Exit(1)


# ── Doctor command ────────────────────────────────────────────────────────


@app.command()
def doctor():
    """Run full system health check."""
    checks: list[tuple[str, str, str]] = []

    def check(name: str, fn, fix: str = ""):
        try:
            result = fn()
            if result is False:
                raise RuntimeError("check returned False")
            checks.append(("✓", name, ""))
        except Exception as e:
            checks.append(("✗", name, fix or str(e)[:80]))

    # Config file
    check(
        "Config file exists",
        lambda: os.path.exists(os.path.expanduser("~/.pawbot/config.json")),
        "Run: pawbot onboard",
    )

    # SQLite
    check(
        "SQLite database",
        lambda: os.path.exists(os.path.expanduser("~/.pawbot/memory/facts.db")),
        "Run pawbot agent -m 'test' to initialise",
    )

    # ChromaDB
    check(
        "ChromaDB directory",
        lambda: os.path.exists(os.path.expanduser("~/.pawbot/memory/chroma")),
        "Run pawbot agent -m 'test' to initialise",
    )

    # Ollama
    def check_ollama():
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
    check("Ollama running", check_ollama, "Start Ollama: ollama serve")

    # MCP servers
    mcp_base = Path(__file__).resolve().parent.parent
    for name in ["server_control", "deploy", "coding", "browser", "app_control"]:
        server_dir = mcp_base / "mcp-servers" / name
        check(
            f"MCP server: {name}",
            lambda d=server_dir: d.exists(),
            f"Phase not implemented for {name}",
        )

    # Security module
    def check_security():
        from pawbot.agent.security import ActionGate  # noqa: F401
    check("Security module", check_security, "Phase 14 not installed")

    # Telemetry module
    def check_telemetry():
        from pawbot.agent.telemetry import PawbotTracer  # noqa: F401
    check("Telemetry module", check_telemetry, "Phase 15 not installed")

    # Config schema validation
    def check_schema():
        from pawbot.config.loader import load_config
        load_config()
    check("Config schema valid", check_schema, "Run: pawbot config validate")

    # Print results
    from pawbot.cli.formatter import CLIFormatter
    fmt = CLIFormatter(console)
    fmt.print_doctor_results(checks)


if __name__ == "__main__":
    app()
