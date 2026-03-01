"""Rich-formatted table and panel output helpers for CLI commands.

Phase 16 canonical class: CLIFormatter
Provides consistent formatting across all pawbot CLI subcommands.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()


class CLIFormatter:
    """Rich-formatted output for pawbot CLI commands.

    Provides a consistent look across memory, skills, cron, config,
    doctor, metrics, and traces subcommands.
    """

    def __init__(self, console_instance: Console | None = None):
        self.console = console_instance or console

    # ── Tables ────────────────────────────────────────────────────────────

    def print_table(
        self,
        title: str,
        columns: list[dict],
        rows: list[list[str]],
    ) -> None:
        """Print a rich table.

        columns: [{"name": str, "style": str, "justify": str, "width": int}, ...]
        rows: [[col1, col2, ...], ...]
        """
        table = Table(title=title)
        for col in columns:
            table.add_column(
                col["name"],
                style=col.get("style", ""),
                justify=col.get("justify", "left"),
                width=col.get("width"),
                max_width=col.get("max_width"),
            )
        for row in rows:
            table.add_row(*row)
        self.console.print(table)

    def print_kv_table(self, title: str, data: dict, key_style: str = "cyan") -> None:
        """Print a two-column key/value table."""
        table = Table(title=title)
        table.add_column("Key", style=key_style)
        table.add_column("Value", justify="right")
        for k, v in data.items():
            table.add_row(str(k), str(v))
        self.console.print(table)

    # ── Panels ────────────────────────────────────────────────────────────

    def print_panel(self, content: str, title: str = "", border_style: str = "cyan") -> None:
        """Print a rich panel."""
        self.console.print(Panel(content, title=title, border_style=border_style))

    # ── Status messages ───────────────────────────────────────────────────

    def success(self, message: str) -> None:
        self.console.print(f"[green]✓ {message}[/green]")

    def error(self, message: str) -> None:
        self.console.print(f"[red]✗ {message}[/red]")

    def warning(self, message: str) -> None:
        self.console.print(f"[yellow]⚠ {message}[/yellow]")

    def info(self, message: str) -> None:
        self.console.print(f"[cyan]ℹ {message}[/cyan]")

    # ── Doctor checks ─────────────────────────────────────────────────────

    def print_doctor_results(self, checks: list[tuple[str, str, str]]) -> None:
        """Print doctor-style check results.

        checks: [(icon, name, issue), ...] where icon is "✓" or "✗"
        """
        table = Table(title="Pawbot Doctor")
        table.add_column("", width=3)
        table.add_column("Check", style="cyan")
        table.add_column("Issue")

        for icon, name, issue in checks:
            color = "green" if icon == "✓" else "red"
            table.add_row(f"[{color}]{icon}[/{color}]", name, issue)

        self.console.print(table)
        passed = sum(1 for c in checks if c[0] == "✓")
        self.console.print(f"\n[bold]{passed}/{len(checks)} checks passed[/bold]")
