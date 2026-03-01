"""CLI commands for cron job management.

Phase 16 — `pawbot cron` subcommands:
  list, run, enable, disable
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

console = Console()
cron_app = typer.Typer(name="cron", help="Manage scheduled jobs")


@cron_app.command("list")
def cron_list():
    """List all registered cron jobs and their status."""
    import datetime

    from pawbot.cron.scheduler import CronScheduler

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

    for job in jobs:
        next_run = (
            datetime.datetime.fromtimestamp(job["next_run"]).strftime("%m-%d %H:%M")
            if job.get("next_run")
            else "—"
        )
        last_run = (
            datetime.datetime.fromtimestamp(job["last_run"]).strftime("%m-%d %H:%M")
            if job.get("last_run")
            else "never"
        )
        if job.get("last_error"):
            status = "[red]error[/red]"
        elif job.get("enabled", True):
            status = "[green]ok[/green]"
        else:
            status = "[yellow]disabled[/yellow]"

        table.add_row(
            job.get("name", ""),
            job.get("schedule", ""),
            next_run,
            last_run,
            str(job.get("run_count", 0)),
            status,
        )
    console.print(table)


@cron_app.command("run")
def cron_run(name: str = typer.Argument(..., help="Job name to trigger")):
    """Manually trigger a cron job."""
    from pawbot.cron.scheduler import CronScheduler

    sched = CronScheduler()
    try:
        sched.run_now(name)
        console.print(f"[green]✓ Job '{name}' triggered.[/green]")
    except Exception as e:
        console.print(f"[red]✗ Failed to trigger '{name}': {e}[/red]")


@cron_app.command("enable")
def cron_enable(name: str = typer.Argument(..., help="Job name to enable")):
    """Enable a disabled cron job."""
    from pawbot.cron.scheduler import CronScheduler

    sched = CronScheduler()
    try:
        sched.set_enabled(name, True)
        console.print(f"[green]✓ Job '{name}' enabled.[/green]")
    except Exception as e:
        console.print(f"[red]✗ Failed: {e}[/red]")


@cron_app.command("disable")
def cron_disable(name: str = typer.Argument(..., help="Job name to disable")):
    """Disable a cron job."""
    from pawbot.cron.scheduler import CronScheduler

    sched = CronScheduler()
    try:
        sched.set_enabled(name, False)
        console.print(f"[yellow]✓ Job '{name}' disabled.[/yellow]")
    except Exception as e:
        console.print(f"[red]✗ Failed: {e}[/red]")
