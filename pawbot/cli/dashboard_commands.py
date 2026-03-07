"""CLI dashboard command.

Extracted from commands.py Phase 0.
"""

from __future__ import annotations

import typer

from pawbot import __logo__
from pawbot.cli._helpers import console

dashboard_app = typer.Typer(name="dashboard", help="Dashboard commands")


@dashboard_app.callback(invoke_without_command=True)
def dashboard(
    ctx: typer.Context,
    port: int = typer.Option(4000, "--port", "-p", help="Port to run on"),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Don't open browser automatically"
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
):
    """Start the Pawbot local dashboard at http://localhost:4000"""
    if ctx.invoked_subcommand is not None:
        return

    console.print(
        f"\n[green]{__logo__}[/green] Starting Pawbot dashboard at "
        f"[bold]http://{host}:{port}[/bold]"
    )
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")
    from pawbot.dashboard.server import start

    start(host=host, port=port, open_browser=not no_browser)
