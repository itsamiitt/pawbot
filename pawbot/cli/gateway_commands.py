"""CLI gateway and server commands.

Extracted from commands.py Phase 0.
Contains:
  - `gateway` — start the full agent gateway with channels, cron, heartbeat
  - `run` — start the WebSocket + REST gateway server
"""

from __future__ import annotations

import asyncio
import os

import typer

from pawbot import __logo__
from pawbot.cli._helpers import _make_provider, console
from pawbot.utils.helpers import sync_workspace_templates

gateway_app = typer.Typer(name="gateway", help="Gateway and server commands")


@gateway_app.callback(invoke_without_command=True)
def gateway(
    ctx: typer.Context,
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the pawbot gateway."""
    if ctx.invoked_subcommand is not None:
        return

    from loguru import logger

    from pawbot.agent.memory import MemoryDecayEngine, SQLiteFactStore
    from pawbot.agents.pool import AgentPool
    from pawbot.bus.queue import MessageBus
    from pawbot.channels.manager import ChannelManager
    from pawbot.config.loader import get_data_dir, load_config
    from pawbot.cron.scheduler import CronScheduler
    from pawbot.cron.service import CronService
    from pawbot.cron.types import CronJob
    from pawbot.heartbeat.engine import HeartbeatEngine
    from pawbot.heartbeat.service import HeartbeatService

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    console.print(f"{__logo__} Starting pawbot gateway on port {port}...")

    config = load_config()

    from pawbot.config.validation import summarize_issues, validate_runtime_config
    issues = validate_runtime_config(config)
    critical, warnings = summarize_issues(issues)
    for item in warnings:
        console.print(f"[yellow]WARN {item.check}: {item.message}[/yellow]")
    if critical:
        console.print("[red]Critical config/runtime validation failed. Refusing to start gateway.[/red]")
        for item in critical:
            fix = f" | fix: {item.fix}" if item.fix else ""
            console.print(f"[red]- {item.check}: {item.message}{fix}[/red]")
        raise typer.Exit(1)

    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    provider = _make_provider(config)

    # Create cron service first (callback set after agent creation)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    pool = AgentPool(
        config=config.agents,
        bus=bus,
        provider=provider,
        cron_service=cron,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
    )

    # Set cron callback (runs through the default routed agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        response = await pool.process_direct(
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
                agent_loop=pool,
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
        for item in pool.list_sessions():
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

        return await pool.process_direct(
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
        model=config.agents.defaults.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
    )

    if channels.enabled_channels:
        console.print(f"[green]✔[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✔[/green] Cron: {cron_status['jobs']} scheduled jobs")

    if phase11_cron_cfg.enabled:
        console.print(
            f"[green]✔[/green] Internal CronScheduler: every {phase11_cron_cfg.check_interval_seconds}s"
        )
    if heartbeat_engine is not None:
        console.print(
            f"[green]✔[/green] HeartbeatEngine: every {phase11_hb_cfg.check_interval_minutes}m"
        )

    console.print(f"[green]✔[/green] Heartbeat: every {hb_cfg.interval_s}s")

    async def run():
        channel_task = None
        try:
            if phase11_cron_cfg.enabled:
                internal_cron.start()
            await cron.start()
            await pool.start_all()
            await heartbeat.start()
            if channels.enabled_channels:
                channel_task = asyncio.create_task(channels.start_all())
                await channel_task
            else:
                await asyncio.Future()
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            if channel_task is not None and not channel_task.done():
                channel_task.cancel()
                await asyncio.gather(channel_task, return_exceptions=True)
            heartbeat.stop()
            cron.stop()
            internal_cron.stop()
            await pool.stop_all()
            await channels.stop_all()

    asyncio.run(run())


@gateway_app.command("run")
def run_server(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host (use 0.0.0.0 for Docker)"),
    port: int = typer.Option(8080, "--port", "-p", help="Bind port"),
    strict: bool = typer.Option(False, "--strict", help="Exit on any validation error"),
    reload: bool = typer.Option(False, "--reload", help="Hot-reload on code changes"),
):
    """Start the Pawbot Gateway server (WebSocket + REST)."""
    import uvicorn

    # ── Phase 5: Validate config before starting ──────────────────────────────
    try:
        from pawbot.config.validator import startup_validator
        result = startup_validator.validate()
        startup_validator.print_report(result)

        if not result.ok and strict:
            console.print("\n[red]Startup validation failed. Fix errors above and retry.[/red]")
            raise typer.Exit(1)
        elif not result.ok:
            console.print("\n[yellow]Validation errors found - starting anyway (use --strict to block).[/yellow]")
    except ImportError:
        console.print("[dim]Startup validator not available - skipping validation[/dim]")

    console.print(f"\nPawbot Gateway starting at {host}:{port}")
    uvicorn.run(
        "pawbot.gateway.server:app",
        host      = host,
        port      = port,
        reload    = reload,
        log_level = "info",
    )
