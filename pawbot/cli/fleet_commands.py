"""Fleet & subagent CLI commands — extracted from commands.py (Phase 0)."""

from __future__ import annotations

import json

import typer
from rich.table import Table

from pawbot import __logo__
from pawbot.cli._helpers import console

fleet_app = typer.Typer(help="Fleet Commander — multi-agent orchestration")


# ── Subagents ────────────────────────────────────────────────────────────────

subagents_app = typer.Typer(help="Manage subagents")


@subagents_app.command("status")
def subagents_status():
    """Show active subagents and pool status."""
    table = Table(title="Subagent Pool Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    try:
        from pawbot.config.loader import load_config
        cfg = load_config()
        table.add_row("Enabled", "✔" if cfg.subagents.enabled else "✗")
        table.add_row("Max concurrent", str(cfg.subagents.max_concurrent))
        table.add_row("Default budget (tokens)", f"{cfg.subagents.default_budget_tokens:,}")
        table.add_row("Default budget (seconds)", str(cfg.subagents.default_budget_seconds))
        table.add_row("Inbox review", "✔" if cfg.subagents.inbox_review_after_subgoal else "✗")
    except Exception as e:
        table.add_row("Error", str(e)[:60])
    console.print(table)


# ── Fleet ────────────────────────────────────────────────────────────────────


def _read_fleet_status():
    """Read fleet status.json, returning (data, error_message)."""
    from pawbot.config.loader import get_data_dir
    status_path = get_data_dir() / "shared" / "status.json"
    if not status_path.exists():
        return None, f"No fleet session active. Expected at: {status_path}"
    try:
        return json.loads(status_path.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, OSError) as exc:
        return None, f"Failed to read status.json: {exc}"


@fleet_app.command("status")
def fleet_status():
    """Show fleet status: workers, tasks, and DAG."""
    data, err = _read_fleet_status()
    if err:
        console.print(f"[dim]{err}[/dim]")
        return

    fleet = data.get("fleet", {})

    # Summary
    summary = Table(title="Fleet Summary")
    summary.add_column("Metric", style="cyan")
    summary.add_column("Value", justify="right")
    summary.add_row("Active tasks", str(fleet.get("active_task_count", 0)))
    summary.add_row("Completed tasks", str(fleet.get("completed_task_count", 0)))
    summary.add_row("Failed tasks", str(fleet.get("failed_task_count", 0)))
    console.print(summary)
    console.print()

    # Workers
    workers = fleet.get("workers", {})
    if workers:
        wt = Table(title="Workers")
        wt.add_column("ID", style="cyan")
        wt.add_column("Role")
        wt.add_column("Circuit")
        for wid, info in workers.items():
            circuit = info.get("circuit", "closed")
            color = "green" if circuit == "closed" else ("yellow" if circuit == "half-open" else "red")
            wt.add_row(wid, info.get("role", ""), f"[{color}]{circuit}[/{color}]")
        console.print(wt)
        console.print()

    # Tasks
    tasks = fleet.get("tasks", [])
    if tasks:
        tt = Table(title="Tasks")
        tt.add_column("ID", style="cyan", max_width=12)
        tt.add_column("Title", max_width=30)
        tt.add_column("Status")
        tt.add_column("Worker")
        tt.add_column("Priority", justify="right")
        for task in tasks:
            st = task.get("status", "pending")
            sc = {"pending": "dim", "running": "cyan", "done": "green",
                  "failed": "red", "cancelled": "yellow"}.get(st, "white")
            tt.add_row(
                task.get("id", "")[:12],
                task.get("title", "")[:30],
                f"[{sc}]{st}[/{sc}]",
                task.get("assigned_to") or "—",
                str(task.get("priority", 5)),
            )
        console.print(tt)
        console.print()

    # DAG mermaid
    mermaid = fleet.get("dag_mermaid", "")
    if mermaid:
        console.print("[dim]DAG (Mermaid):[/dim]")
        console.print(mermaid)

    # Recent log
    log = data.get("execution_log", [])
    if log:
        console.print()
        lt = Table(title="Recent Events (last 10)")
        lt.add_column("Event", style="cyan")
        lt.add_column("Detail", max_width=50)
        for entry in log[-10:]:
            lt.add_row(entry.get("event", ""), entry.get("detail", "")[:50])
        console.print(lt)


@fleet_app.command("workers")
def fleet_workers():
    """Show detailed worker information."""
    data, err = _read_fleet_status()
    if err:
        console.print(f"[dim]{err}[/dim]")
        return

    workers = data.get("fleet", {}).get("workers", {})
    if not workers:
        console.print("[dim]No workers registered.[/dim]")
        return

    for wid, info in workers.items():
        wt = Table(title=f"Worker: {wid}")
        wt.add_column("Property", style="cyan")
        wt.add_column("Value")
        wt.add_row("Role", info.get("role", ""))
        wt.add_row("Model", info.get("model_preference", ""))
        wt.add_row("Max concurrent", str(info.get("max_concurrent_tasks", 0)))
        wt.add_row("Description", info.get("description", ""))
        circuit = info.get("circuit", "closed")
        color = "green" if circuit == "closed" else ("yellow" if circuit == "half-open" else "red")
        wt.add_row("Circuit breaker", f"[{color}]{circuit}[/{color}]")
        console.print(wt)
        console.print()


@fleet_app.command("cancel")
def fleet_cancel(
    task_id: str = typer.Argument(..., help="Task ID to cancel"),
):
    """Cancel a specific fleet task."""
    from pawbot.config.loader import get_data_dir

    status_path = get_data_dir() / "shared" / "status.json"
    if not status_path.exists():
        console.print("[red]No fleet session active.[/red]")
        raise typer.Exit(1)

    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        console.print(f"[red]Failed to read status.json: {exc}[/red]")
        raise typer.Exit(1)

    tasks = data.get("fleet", {}).get("tasks", [])
    found = next((t for t in tasks if t.get("id", "").startswith(task_id)), None)
    if not found:
        console.print(f"[red]Task '{task_id}' not found in fleet.[/red]")
        raise typer.Exit(1)

    if found.get("status") in ("done", "cancelled", "failed"):
        console.print(f"[yellow]Task '{task_id}' is already {found['status']}.[/yellow]")
        return

    found["status"] = "cancelled"
    status_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    console.print(f"[green]✔[/green] Cancelled task '{found.get('title', task_id)}'")
