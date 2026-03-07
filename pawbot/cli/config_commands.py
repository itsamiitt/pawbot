"""Config CLI commands — show, set, validate (extracted from commands.py, Phase 0)."""

from __future__ import annotations

import typer
from rich.table import Table

from pawbot.cli._helpers import console

config_app = typer.Typer(help="Configuration management")


@config_app.command("show")
def config_show():
    """Display resolved configuration with sources."""
    import json

    from pawbot.config.loader import get_config_path, load_config

    config = load_config()
    path = get_config_path()
    console.print(f"[dim]Config file: {path}[/dim]\n")

    data = config.model_dump(by_alias=False)
    console.print_json(json.dumps(data, indent=2, default=str))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Dotted config key (e.g. 'security.enabled')"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Update a config value."""
    from pawbot.config.loader import load_config, save_config

    config = load_config()
    data = config.model_dump(by_alias=False)

    # Navigate dotted key path
    parts = key.split(".")
    target = data
    for part in parts[:-1]:
        if isinstance(target, dict) and part in target:
            target = target[part]
        else:
            console.print(f"[red]Key not found: {key}[/red]")
            raise typer.Exit(1)

    final_key = parts[-1]
    if isinstance(target, dict) and final_key in target:
        old_value = target[final_key]
        # Coerce value to same type
        if isinstance(old_value, bool):
            target[final_key] = value.lower() in ("true", "1", "yes")
        elif isinstance(old_value, int):
            target[final_key] = int(value)
        elif isinstance(old_value, float):
            target[final_key] = float(value)
        else:
            target[final_key] = value

        from pawbot.config.schema import Config
        new_config = Config.model_validate(data)
        save_config(new_config)
        console.print(f"[green]✔ {key} = {target[final_key]}[/green]")
    else:
        console.print(f"[red]Key not found: {key}[/red]")
        raise typer.Exit(1)


@config_app.command("validate")
def config_validate():
    """Validate config against full schema."""
    from pawbot.config.loader import get_config_path, load_config

    path = get_config_path()
    if not path.exists():
        console.print(f"[yellow]No config file at {path}[/yellow]")
        console.print("Run [cyan]pawbot onboard[/cyan] to create one.")
        return

    try:
        config = load_config()
        console.print("[green]✔ Config is valid[/green]")
        data = config.model_dump(by_alias=False)
        t = Table(title="Config Sections")
        t.add_column("Section", style="cyan")
        t.add_column("Keys", justify="right")
        for section_name, section_value in data.items():
            if isinstance(section_value, dict):
                t.add_row(section_name, str(len(section_value)))
            else:
                t.add_row(section_name, "scalar")
        console.print(t)
    except Exception as e:
        console.print(f"[red]✗ Config validation failed: {e}[/red]")
        raise typer.Exit(1)
