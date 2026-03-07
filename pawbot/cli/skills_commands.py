"""CLI commands for skill management.

Phase 9 — `pawbot skills` subcommands:
  list, show, delete, install, uninstall, info
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
skills_app = typer.Typer(name="skills", help="Manage agent skills")


@skills_app.command("list")
def skills_list():
    """List all registered skills sorted by usage."""
    from pawbot.agent.skills import SkillsLoader

    loader = SkillsLoader()
    skills = loader.list_skills()

    if not skills:
        console.print("[yellow]No skills registered yet.[/yellow]")
        return

    table = Table(title=f"Skills ({len(skills)} total)")
    table.add_column("Name", style="cyan")
    table.add_column("Description", max_width=50)
    table.add_column("Uses", justify="right")

    for skill in skills:
        name = skill.get("name", "") if isinstance(skill, dict) else getattr(skill, "name", "")
        desc = skill.get("description", "") if isinstance(skill, dict) else getattr(skill, "description", "")
        uses = skill.get("success_count", 0) if isinstance(skill, dict) else getattr(skill, "success_count", 0)
        table.add_row(str(name), str(desc), str(uses))
    console.print(table)


@skills_app.command("show")
def skills_show(name: str = typer.Argument(...)):
    """Show full details of a skill."""
    from pawbot.agent.skills import SkillsLoader

    loader = SkillsLoader()
    skills = loader.list_skills()
    skill = None
    for s in skills:
        s_name = s.get("name", "") if isinstance(s, dict) else getattr(s, "name", "")
        if s_name == name:
            skill = s
            break

    if not skill:
        console.print(f"[red]Skill '{name}' not found.[/red]")
        raise typer.Exit(1)

    if isinstance(skill, dict):
        lines = [f"[bold]{k}:[/bold] {v}" for k, v in skill.items()]
    else:
        lines = [f"[bold]Name:[/bold] {skill.name}", f"[bold]Description:[/bold] {skill.description}"]

    console.print(Panel("\n".join(lines), title=f"Skill: {name}"))


@skills_app.command("delete")
def skills_delete(
    name: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Delete a skill by name."""
    from pawbot.agent.skills import SkillWriter

    if not yes:
        confirm = typer.confirm(f"Delete skill '{name}'?")
        if not confirm:
            raise typer.Abort()

    writer = SkillWriter()
    if writer.delete_skill(name):
        console.print(f"[green]✓ Skill deleted: {name}[/green]")
    else:
        console.print(f"[yellow]Skill '{name}' not found.[/yellow]")


# ── Phase 9: Package install/uninstall/info ───────────────────────────────────


@skills_app.command("install")
def install_skill(
    source: str = typer.Argument(..., help="Path, Git URL, or pip package name"),
    git: bool = typer.Option(False, "--git", help="Install from Git repository"),
    pip: bool = typer.Option(False, "--pip", help="Install from pip package"),
    branch: str = typer.Option("main", "--branch", "-b", help="Git branch (default: main)"),
):
    """Install a skill package from a local directory, Git repo, or pip."""
    from pawbot.skills.installer import SkillInstaller

    installer = SkillInstaller()
    try:
        if git:
            manifest = installer.install_from_git(source, branch=branch)
        elif pip:
            manifest = installer.install_from_pip(source)
        else:
            manifest = installer.install_from_directory(source)

        console.print(f"[green]✓[/green] Installed [bold]{manifest.name}[/bold] v{manifest.version}")
        if manifest.tools:
            tool_names = ", ".join(t.name for t in manifest.tools)
            console.print(f"  Tools: {tool_names}")
        if manifest.description:
            console.print(f"  {manifest.description}")
    except Exception as e:
        console.print(f"[red]✗ Installation failed:[/red] {e}")
        raise typer.Exit(1)


@skills_app.command("uninstall")
def uninstall_skill(name: str = typer.Argument(..., help="Skill package name")):
    """Uninstall a skill package."""
    from pawbot.skills.installer import SkillInstaller

    installer = SkillInstaller()
    if installer.uninstall(name):
        console.print(f"[green]✓[/green] Uninstalled [bold]{name}[/bold]")
    else:
        console.print(f"[yellow]Skill '{name}' is not installed[/yellow]")


@skills_app.command("info")
def skill_info(name: str = typer.Argument(..., help="Skill package name")):
    """Show detailed information about an installed skill package."""
    from pawbot.skills.installer import SkillInstaller

    installer = SkillInstaller()
    manifest = installer.get_manifest(name)

    if not manifest:
        console.print(f"[red]Skill package '{name}' not found[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold]{manifest.name}[/bold] v{manifest.version}")
    console.print(f"  Author: {manifest.author or '—'}")
    console.print(f"  License: {manifest.license}")
    console.print(f"  Description: {manifest.description or '—'}")

    if manifest.tools:
        console.print(f"\n  Tools ({len(manifest.tools)}):")
        for t in manifest.tools:
            console.print(
                f"    • [cyan]{t.name}[/cyan] — {t.description} "
                f"[dim](risk: {t.risk_level})[/dim]"
            )

    if manifest.requires_api_key:
        console.print(f"\n  ⚠ Requires API key: {manifest.api_key_env_var}")
    if manifest.python_dependencies:
        console.print(f"\n  Python deps: {', '.join(manifest.python_dependencies)}")

    # Show permissions
    perms = []
    if manifest.requires_network:
        perms.append("network")
    if manifest.requires_filesystem:
        perms.append("filesystem")
    if manifest.requires_browser:
        perms.append("browser")
    if perms:
        console.print(f"  Permissions: {', '.join(perms)}")


@skills_app.command("installed")
def list_installed():
    """List all installed skill packages (from installer registry)."""
    from pawbot.skills.installer import SkillInstaller

    installer = SkillInstaller()
    packages = installer.list_installed()

    if not packages:
        console.print(
            "[dim]No skill packages installed. "
            "Use 'pawbot skills install <path>' to install.[/dim]"
        )
        return

    table = Table(title="Installed Skill Packages")
    table.add_column("Name", style="bold")
    table.add_column("Version")
    table.add_column("Tools")
    table.add_column("Source")
    table.add_column("Description", max_width=40)

    for s in packages:
        if s.get("broken"):
            table.add_row(s["name"], s.get("version", "?"), "—", "—", "[red]broken[/red]")
        else:
            tools_str = ", ".join(s.get("tools", [])) if s.get("tools") else "—"
            table.add_row(
                s["name"],
                s.get("version", "?"),
                tools_str,
                s.get("source_type", "—"),
                s.get("description", ""),
            )

    console.print(table)
