"""CLI commands for pawbot — main app entry point.

Phase 0: Refactored from a 1,781-line monolith into a thin entry point
that registers all subcommand modules.

Subcommand modules:
  - _helpers.py              — shared utilities (console, terminal, prompt)
  - agent_commands.py        — `pawbot agent` (interactive + single-message)
  - gateway_commands.py      — `pawbot gateway` + `pawbot gateway run`
  - dashboard_commands.py    — `pawbot dashboard`
  - channel_commands.py      — `pawbot channels status/login`
  - cron_commands.py         — `pawbot cron list/add/remove/enable/run`
  - memory_commands.py       — `pawbot memory list/search/show/delete`
  - skills_commands.py       — `pawbot skills list/show/delete`
  - fleet_commands.py        — `pawbot fleet status/workers/cancel` + subagents
  - config_commands.py       — `pawbot config show/set/validate`
  - provider_commands.py     — `pawbot provider login`
  - observability_commands.py — `pawbot metrics` + `pawbot traces`
  - doctor_commands.py       — `pawbot doctor` + `pawbot audit`
  - doctor_audit.py          — audit logic
"""

from __future__ import annotations

import typer

from pawbot import __logo__, __version__
from pawbot.cli._helpers import console

app = typer.Typer(
    name="pawbot",
    help=f"{__logo__} pawbot - Personal AI Assistant",
    no_args_is_help=True,
)


# ── Version callback ────────────────────────────────────────────────────────


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} pawbot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """pawbot - Personal AI Assistant."""
    pass


# ── Register extracted subcommand groups ─────────────────────────────────────

from pawbot.cli.agent_commands import agent_app          # noqa: E402
from pawbot.cli.gateway_commands import gateway_app      # noqa: E402
from pawbot.cli.dashboard_commands import dashboard_app  # noqa: E402
from pawbot.cli.channel_commands import channels_app     # noqa: E402
from pawbot.cli.memory_commands import memory_app        # noqa: E402
from pawbot.cli.skills_commands import skills_app        # noqa: E402
from pawbot.cli.fleet_commands import fleet_app, subagents_app  # noqa: E402
from pawbot.cli.config_commands import config_app        # noqa: E402
from pawbot.cli.provider_commands import provider_app    # noqa: E402
from pawbot.cli.ext_commands import ext_app              # noqa: E402

# Sub-groups (typer groups with their own commands)
app.add_typer(agent_app, name="agent")
app.add_typer(gateway_app, name="gateway")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(channels_app, name="channels")
app.add_typer(memory_app, name="memory")
app.add_typer(skills_app, name="skills")
app.add_typer(fleet_app, name="fleet")
app.add_typer(subagents_app, name="subagents")
app.add_typer(config_app, name="config")
app.add_typer(provider_app, name="provider")
app.add_typer(ext_app, name="ext")


# ── Direct commands (onboard, status, metrics, traces, doctor, audit) ────────

from pawbot.cli.doctor_commands import doctor, audit                      # noqa: E402
from pawbot.cli.observability_commands import metrics, traces             # noqa: E402


# ── Onboard command (inline — it's the entry point) ─────────────────────────


@app.command()
def onboard(
    setup: bool = typer.Option(False, "--setup", help="Interactive setup: configure API key and model"),
):
    """Initialize pawbot configuration and workspace."""
    from pawbot.config.loader import get_config_path, load_config, save_config
    from pawbot.config.schema import Config
    from pawbot.utils.helpers import get_workspace_path, sync_workspace_templates

    config_path = get_config_path()

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
        if typer.confirm("Overwrite?"):
            config = Config()
            save_config(config)
            console.print(f"[green]✔[/green] Config reset to defaults at {config_path}")
        else:
            config = load_config()
            save_config(config)
            console.print(f"[green]✔[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        config = Config()
        save_config(config)
        console.print(f"[green]✔[/green] Created config at {config_path}")

    # Create workspace
    workspace = get_workspace_path()

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✔[/green] Created workspace at {workspace}")

    sync_workspace_templates(workspace)

    # ── Interactive setup: provider, API key, model ────────────────────────
    if setup:
        import getpass
        config = load_config()

        console.print(f"\n{__logo__} [bold]Interactive Setup[/bold]\n")

        # Provider selection
        providers = [
            ("1", "openrouter", "OpenRouter", "Access to all models (recommended)", "https://openrouter.ai/keys"),
            ("2", "anthropic", "Anthropic", "Claude models direct", "https://console.anthropic.com/keys"),
            ("3", "openai", "OpenAI", "GPT models direct", "https://platform.openai.com/api-keys"),
            ("4", "deepseek", "DeepSeek", "DeepSeek models direct", "https://platform.deepseek.com"),
            ("5", "gemini", "Gemini", "Google Gemini direct", "https://aistudio.google.com"),
        ]

        console.print("[bold]Choose your LLM provider:[/bold]")
        for num, _, name, desc, url in providers:
            console.print(f"  [cyan]{num}[/cyan]) {name} — {desc}")
        console.print("  [cyan]s[/cyan]) Skip (configure manually later)")
        console.print()

        choice = typer.prompt("Select provider", default="1").strip()

        if choice.lower() != "s":
            selected = None
            for num, key, name, desc, url in providers:
                if choice == num:
                    selected = (key, name, url)
                    break

            if not selected:
                console.print("[yellow]Invalid choice, defaulting to OpenRouter[/yellow]")
                selected = ("openrouter", "OpenRouter", "https://openrouter.ai/keys")

            prov_key, prov_name, prov_url = selected
            console.print(f"\n[bold]{prov_name}[/bold] selected.")
            console.print(f"  Get your API key at: [cyan]{prov_url}[/cyan]\n")

            # API key input (masked)
            api_key = getpass.getpass(f"  Paste your {prov_name} API key (hidden): ").strip()

            if api_key:
                # Set the key on the correct provider config
                provider_cfg = getattr(config.providers, prov_key)
                provider_cfg.api_key = api_key
                console.print(f"  [green]✔[/green] API key saved for {prov_name}")

                # Model selection
                default_models = {
                    "openrouter": "anthropic/claude-sonnet-4-5",
                    "anthropic": "claude-sonnet-4-20250514",
                    "openai": "gpt-4o",
                    "deepseek": "deepseek-chat",
                    "gemini": "gemini-2.0-flash",
                }
                default_model = default_models.get(prov_key, "anthropic/claude-sonnet-4-5")

                console.print("\n[bold]Choose your default model:[/bold]")
                console.print(f"  Press Enter for default: [cyan]{default_model}[/cyan]")
                model = typer.prompt("  Model", default=default_model).strip()

                config.agents.defaults.model = model
                config.agents.defaults.provider = prov_key
                console.print(f"  [green]✔[/green] Model set to [cyan]{model}[/cyan]")
            else:
                console.print("  [yellow]No key entered — you can add it later in ~/.pawbot/config.json[/yellow]")

            # Save updated config
            save_config(config)
            console.print(f"\n[green]✔[/green] Config saved to {config_path}")
        else:
            console.print("\n[dim]Skipped. Add your API key manually to ~/.pawbot/config.json[/dim]")

    console.print(f"\n{__logo__} pawbot is ready!")
    console.print("\nNext steps:")
    if not setup:
        console.print("  1. Add your API key to [cyan]~/.pawbot/config.json[/cyan]")
        console.print("     Get one at: https://openrouter.ai/keys")
        console.print('  2. Chat: [cyan]pawbot agent -m "Hello!"[/cyan]')
    else:
        console.print('  Chat: [cyan]pawbot agent -m "Hello!"[/cyan]')
        console.print("  Interactive: [cyan]pawbot agent[/cyan]")
        console.print("  Gateway: [cyan]pawbot gateway[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/pawbot#-chat-apps[/dim]")


# ── Status command ───────────────────────────────────────────────────────────


@app.command()
def status():
    """Show pawbot status."""
    from pawbot.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} pawbot Status\n")

    console.print(f"Config: {config_path} {'[green]✔[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✔[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        from pawbot.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✔ (OAuth)[/green]")
            elif spec.is_local:
                if p.api_base:
                    console.print(f"{spec.label}: [green]✔ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]✔[/green]' if has_key else '[dim]not set[/dim]'}")


# ── Register direct commands ─────────────────────────────────────────────────

app.command()(metrics)
app.command()(traces)
app.command()(doctor)
app.command()(audit)


if __name__ == "__main__":
    app()
