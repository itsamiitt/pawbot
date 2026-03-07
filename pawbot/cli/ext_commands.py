"""CLI commands for the unified extension system (Phase E5).

``pawbot ext`` subcommand group:
  list       — list all registered extensions (bundled + installed)
  show <id>  — show details of a specific extension
  install    — install from dir/git/pip/openclaw
  uninstall  — uninstall an extension
  enable     — enable a disabled extension
  disable    — disable an extension
  scan       — scan for available OpenClaw extensions
  create     — scaffold a new extension
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
ext_app = typer.Typer(name="ext", help="Manage pawbot extensions")


@ext_app.command("list")
def ext_list(
    all_exts: bool = typer.Option(False, "--all", "-a", help="Show disabled/denied too"),
):
    """List all registered extensions."""
    from pawbot.extensions.discovery import discover_all
    from pawbot.extensions.registry import ExtensionRegistry

    registry = ExtensionRegistry()
    discover_all(registry)

    records = registry.list_all() if all_exts else registry.list_enabled()

    if not records:
        console.print("[dim]No extensions found.[/dim]")
        return

    table = Table(title=f"Extensions ({len(records)} total)")
    table.add_column("ID", style="cyan")
    table.add_column("Version")
    table.add_column("Origin")
    table.add_column("Tools", justify="right")
    table.add_column("Status")
    table.add_column("Description", max_width=40)

    for rec in records:
        status_style = {
            "loaded": "[green]✔[/green]",
            "disabled": "[dim]disabled[/dim]",
            "error": f"[red]✗ {rec.error[:30]}[/red]",
            "policy_denied": "[yellow]denied[/yellow]",
        }.get(rec.status, rec.status)

        table.add_row(
            rec.id,
            rec.version,
            rec.origin.value,
            str(len(rec.tool_names)),
            status_style,
            rec.description[:40],
        )

    console.print(table)


@ext_app.command("show")
def ext_show(ext_id: str = typer.Argument(..., help="Extension ID")):
    """Show details of a specific extension."""
    from pawbot.extensions.discovery import discover_all
    from pawbot.extensions.registry import ExtensionRegistry

    registry = ExtensionRegistry()
    discover_all(registry)

    record = registry.get(ext_id)
    if not record:
        console.print(f"[red]Extension '{ext_id}' not found.[/red]")
        raise typer.Exit(1)

    lines = [
        f"[bold]ID:[/bold] {record.id}",
        f"[bold]Name:[/bold] {record.name}",
        f"[bold]Version:[/bold] {record.version}",
        f"[bold]Origin:[/bold] {record.origin.value}",
        f"[bold]Status:[/bold] {record.status}",
        f"[bold]Enabled:[/bold] {record.enabled}",
        f"[bold]Description:[/bold] {record.description}",
    ]

    if record.tool_names:
        lines.append(f"[bold]Tools:[/bold] {', '.join(record.tool_names)}")
    if record.hook_names:
        lines.append(f"[bold]Hooks:[/bold] {', '.join(record.hook_names)}")
    if record.channel_ids:
        lines.append(f"[bold]Channels:[/bold] {', '.join(record.channel_ids)}")
    if record.command_names:
        lines.append(f"[bold]Commands:[/bold] {', '.join(record.command_names)}")
    if record.capabilities:
        lines.append(f"[bold]Capabilities:[/bold] {', '.join(record.capabilities)}")
    if record.source:
        lines.append(f"[bold]Source:[/bold] {record.source}")
    if record.error:
        lines.append(f"[bold red]Error:[/bold red] {record.error}")

    console.print(Panel("\n".join(lines), title=f"Extension: {record.id}"))


@ext_app.command("install")
def ext_install(
    source: str = typer.Argument(..., help="Path, Git URL, pip package, or openclaw:<name>"),
    branch: str = typer.Option("main", "--branch", "-b", help="Git branch"),
):
    """Install an extension from various sources.

    Examples:
      pawbot ext install ./my-extension
      pawbot ext install git+https://github.com/user/pawbot-ext-tool.git
      pawbot ext install pip:my-pawbot-extension
      pawbot ext install openclaw:weather
    """
    from pawbot.extensions.installer import ExtensionInstaller
    from pawbot.extensions.registry import ExtensionRegistry

    registry = ExtensionRegistry()
    installer = ExtensionInstaller(registry=registry)

    try:
        manifest = installer.install(source, branch=branch)
        console.print(
            f"[green]✔[/green] Installed [bold]{manifest.id}[/bold] v{manifest.version}"
        )
        if manifest.tools:
            tool_names = ", ".join(t.name for t in manifest.tools)
            console.print(f"  Tools: {tool_names}")
        if manifest.description:
            console.print(f"  {manifest.description}")
    except Exception as e:
        console.print(f"[red]✗ Installation failed:[/red] {e}")
        raise typer.Exit(1)


@ext_app.command("uninstall")
def ext_uninstall(
    ext_id: str = typer.Argument(..., help="Extension ID to uninstall"),
):
    """Uninstall an extension."""
    from pawbot.extensions.installer import ExtensionInstaller
    from pawbot.extensions.registry import ExtensionRegistry

    registry = ExtensionRegistry()
    installer = ExtensionInstaller(registry=registry)

    if installer.uninstall(ext_id):
        console.print(f"[green]✔[/green] Uninstalled [bold]{ext_id}[/bold]")
    else:
        console.print(f"[yellow]Extension '{ext_id}' is not installed.[/yellow]")


@ext_app.command("enable")
def ext_enable(
    ext_id: str = typer.Argument(..., help="Extension ID to enable"),
):
    """Enable a disabled extension."""
    from pawbot.extensions.discovery import discover_all
    from pawbot.extensions.registry import ExtensionRegistry

    registry = ExtensionRegistry()
    discover_all(registry)

    if registry.enable(ext_id):
        console.print(f"[green]✔[/green] Enabled [bold]{ext_id}[/bold]")
    else:
        record = registry.get(ext_id)
        if not record:
            console.print(f"[red]Extension '{ext_id}' not found.[/red]")
        else:
            console.print(
                f"[yellow]Cannot enable '{ext_id}': {record.error}[/yellow]"
            )


@ext_app.command("disable")
def ext_disable(
    ext_id: str = typer.Argument(..., help="Extension ID to disable"),
):
    """Disable an extension."""
    from pawbot.extensions.discovery import discover_all
    from pawbot.extensions.registry import ExtensionRegistry

    registry = ExtensionRegistry()
    discover_all(registry)

    if registry.disable(ext_id):
        console.print(f"[green]✔[/green] Disabled [bold]{ext_id}[/bold]")
    else:
        console.print(f"[red]Extension '{ext_id}' not found.[/red]")


@ext_app.command("scan")
def ext_scan():
    """Scan for available OpenClaw extensions and skills."""
    from pawbot.extensions.adapters.openclaw import OpenClawAdapter

    adapter = OpenClawAdapter()

    if not adapter.available:
        console.print(
            "[yellow]OpenClaw is not installed. "
            "Install with: npm install -g openclaw[/yellow]"
        )
        return

    skills = adapter.list_available_skills()
    plugins = adapter.list_available_plugins()

    if skills:
        table = Table(title=f"OpenClaw Skills ({len(skills)})")
        table.add_column("Name", style="cyan")
        table.add_column("Install Command")
        for name in skills:
            table.add_row(name, f"pawbot ext install openclaw:{name}")
        console.print(table)

    if plugins:
        table = Table(title=f"OpenClaw Plugins ({len(plugins)})")
        table.add_column("Name", style="cyan")
        table.add_column("Install Command")
        for name in plugins:
            table.add_row(name, f"pawbot ext install openclaw:{name}")
        console.print(table)

    if not skills and not plugins:
        console.print("[dim]No OpenClaw extensions found.[/dim]")

    console.print(
        f"\n[dim]Total: {len(skills)} skills, {len(plugins)} plugins "
        f"(from {adapter.openclaw_dir})[/dim]"
    )


@ext_app.command("create")
def ext_create(
    name: str = typer.Argument(..., help="Extension name"),
    dest: str = typer.Option(".", "--dest", "-d", help="Destination directory"),
):
    """Scaffold a new extension."""
    from pawbot.extensions.installer import ExtensionInstaller

    installer = ExtensionInstaller()
    dest_path = Path(dest) / name if dest == "." else Path(dest)

    try:
        created = installer.create_scaffold(name, dest_dir=dest_path)
        console.print(f"[green]✔[/green] Created extension scaffold at [bold]{created}[/bold]")
        console.print("\nNext steps:")
        console.print(f"  1. Edit [cyan]{created / 'extension.json'}[/cyan]")
        console.print(f"  2. Add tools in [cyan]{created / 'tools/'}[/cyan]")
        console.print(f"  3. Install: [cyan]pawbot ext install {created}[/cyan]")
    except Exception as e:
        console.print(f"[red]✗ Scaffold creation failed:[/red] {e}")
        raise typer.Exit(1)
