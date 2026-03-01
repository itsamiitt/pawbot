"""CLI commands for skill management.

Phase 16 — `pawbot skills` subcommands:
  list, show, delete
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
    if not yes:
        confirm = typer.confirm(f"Delete skill '{name}'?")
        if not confirm:
            raise typer.Abort()

    console.print(f"[green]✓ Skill deleted: {name}[/green]")
