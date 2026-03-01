"""CLI commands for memory management.

Phase 16 — `pawbot memory` subcommands:
  search, list, delete, stats, decay
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

console = Console()
memory_app = typer.Typer(name="memory", help="Manage agent memory")


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n"),
    type: str = typer.Option("", "--type", "-t", help="Filter by memory type"),
):
    """Search memories by query string."""
    from pawbot.agent.memory import get_memory_router

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
        text = (
            content.get("text", str(content))[:80]
            if isinstance(content, dict)
            else str(content)[:80]
        )
        table.add_row(
            mem.get("id", "")[:8],
            mem.get("type", ""),
            f"{mem.get('salience', 0):.2f}",
            text,
        )
    console.print(table)


@memory_app.command("list")
def memory_list(
    type: str = typer.Argument(
        ...,
        help="Memory type: fact, preference, decision, episode, reflection, procedure, task, risk",
    ),
):
    """List all memories of a given type."""
    from pawbot.agent.memory import get_memory_router
    import datetime

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
        created = datetime.datetime.fromtimestamp(
            mem.get("created_at", 0)
        ).strftime("%Y-%m-%d")
        content = mem.get("content", {})
        text = (
            content.get("text", str(content))[:80]
            if isinstance(content, dict)
            else str(content)[:80]
        )
        table.add_row(
            mem.get("id", "")[:8],
            f"{mem.get('salience', 0):.2f}",
            created,
            text,
        )
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

    from pawbot.agent.memory import get_memory_router

    router = get_memory_router()
    deleted = router.delete(id)
    if deleted:
        console.print(f"[green]✓ Memory archived: {id[:8]}[/green]")
    else:
        console.print(f"[red]✗ Memory not found: {id[:8]}[/red]")


@memory_app.command("stats")
def memory_stats_cmd():
    """Show memory backend statistics."""
    from pawbot.agent.memory import get_memory_router, memory_stats

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
    from pawbot.agent.memory import SQLiteFactStore, MemoryDecayEngine

    store = SQLiteFactStore({})
    engine = MemoryDecayEngine(store)
    console.print("[cyan]Running decay pass...[/cyan]")
    archived = engine.decay_pass()
    console.print(f"[green]✓ Decay complete: {archived} memories archived.[/green]")
