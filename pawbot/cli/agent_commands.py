"""CLI agent command — interactive and single-message modes.

Extracted from commands.py Phase 0.
"""

from __future__ import annotations

import asyncio
import os
import signal

import typer

from pawbot import __logo__
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
from pawbot.utils.helpers import sync_workspace_templates

agent_app = typer.Typer(name="agent", help="Agent interaction commands")


@agent_app.callback(invoke_without_command=True)
def agent(
    ctx: typer.Context,
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show pawbot runtime logs during chat"),
):
    """Interact with the agent directly."""
    if ctx.invoked_subcommand is not None:
        return

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


# ── Phase 4: Non-interactive ask command ─────────────────────────────────────


@agent_app.command(name="ask")
def agent_ask(
    message: str = typer.Argument(..., help="Message to send (use '-' for pipe input)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    session_id: str = typer.Option("cli:direct", "--session", "-s"),
):
    """Send a single message (non-interactive). Supports piping.

    Examples:
        pawbot agent ask "What is Python?"
        echo "explain asyncio" | pawbot agent ask -
        pawbot agent ask "summarize" --json
    """
    import json as json_mod
    import sys

    from loguru import logger

    from pawbot.agent.loop import AgentLoop
    from pawbot.bus.queue import MessageBus
    from pawbot.config.loader import get_data_dir, load_config
    from pawbot.cron.service import CronService

    # Support pipe input: echo "hello" | pawbot agent ask -
    if message == "-":
        if sys.stdin.isatty():
            console.print("[red]No pipe input detected. Provide a message or pipe input.[/red]")
            raise typer.Exit(1)
        message = sys.stdin.read().strip()
        if not message:
            console.print("[red]Empty pipe input.[/red]")
            raise typer.Exit(1)

    config = load_config()
    logger.disable("pawbot")

    bus = MessageBus()
    provider = _make_provider(config)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

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

    async def _run():
        response = await agent_loop.process_direct(message, session_id)
        await agent_loop.close_mcp()
        return response

    response = asyncio.run(_run())

    if json_output:
        print(json_mod.dumps({
            "response": response,
            "session_id": session_id,
            "model": config.agents.defaults.model,
        }, ensure_ascii=False, indent=2))
    else:
        _print_agent_response(response, render_markdown=True)

