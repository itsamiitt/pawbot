"""Shell completion script generation and installation (Phase 13.1).

Generates tab-completion scripts for Bash, Zsh, Fish, and PowerShell.
Covers all PawBot commands and subcommands.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console

console = Console()
completions_app = typer.Typer(name="completions", help="Shell completion management")

COMPLETIONS_DIR = Path.home() / ".pawbot" / "completions"


# ── Completion Script Generators ──────────────────────────────────────────────


def _bash_completion() -> str:
    return '''# pawbot bash completion
_pawbot_complete() {
    local IFS=$'\\n'
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Top-level commands
    local commands="agent gateway dashboard channels memory skills cron onboard audit completions config --version --help"

    # Subcommands
    case "${prev}" in
        agent)       COMPREPLY=($(compgen -W "chat ask --help" -- "${cur}"));;
        gateway)     COMPREPLY=($(compgen -W "start stop status --help" -- "${cur}"));;
        channels)    COMPREPLY=($(compgen -W "status list enable disable --help" -- "${cur}"));;
        memory)      COMPREPLY=($(compgen -W "list search clear stats --help" -- "${cur}"));;
        skills)      COMPREPLY=($(compgen -W "install uninstall list info installed --help" -- "${cur}"));;
        cron)        COMPREPLY=($(compgen -W "list add remove run --help" -- "${cur}"));;
        completions) COMPREPLY=($(compgen -W "generate install --help" -- "${cur}"));;
        config)      COMPREPLY=($(compgen -W "backup restore diff backups --help" -- "${cur}"));;
        *)           COMPREPLY=($(compgen -W "${commands}" -- "${cur}"));;
    esac
}
complete -F _pawbot_complete pawbot
'''


def _zsh_completion() -> str:
    return '''#compdef pawbot
# pawbot zsh completion

_pawbot() {
    local -a commands
    commands=(
        'agent:Interact with the agent'
        'gateway:Manage the gateway server'
        'dashboard:Launch the web dashboard'
        'channels:Manage communication channels'
        'memory:Memory operations'
        'skills:Manage skills and plugins'
        'cron:Scheduled job management'
        'onboard:Run onboarding wizard'
        'audit:Audit phase implementation status'
        'completions:Tab-completion management'
        'config:Configuration management'
    )

    _arguments -C \\
        '--version[Show version]' \\
        '--help[Show help]' \\
        '1:command:->command' \\
        '*::arg:->args'

    case "$state" in
        command)
            _describe 'command' commands
            ;;
        args)
            case "$words[1]" in
                agent)
                    _arguments \\
                        '--message[Message to send]:message:' \\
                        '--session[Session ID]:session:' \\
                        '--markdown[Render as markdown]' \\
                        '--no-markdown[Plain text output]' \\
                        '--json[Output as JSON]' \\
                        '--stream[Stream response tokens]'
                    ;;
                skills)
                    local -a subcmds
                    subcmds=(
                        'install:Install a skill'
                        'uninstall:Uninstall a skill'
                        'list:List installed skills'
                        'info:Show skill details'
                        'installed:List installed packages'
                    )
                    _describe 'subcommand' subcmds
                    ;;
                memory)
                    local -a subcmds
                    subcmds=(
                        'list:List memories'
                        'search:Search memories'
                        'clear:Clear memory store'
                        'stats:Show memory statistics'
                    )
                    _describe 'subcommand' subcmds
                    ;;
                completions)
                    local -a subcmds
                    subcmds=(
                        'generate:Generate completion script'
                        'install:Install completions'
                    )
                    _describe 'subcommand' subcmds
                    ;;
            esac
            ;;
    esac
}
_pawbot "$@"
'''


def _fish_completion() -> str:
    return '''# pawbot fish completion

# Main commands
complete -c pawbot -n '__fish_use_subcommand' -a agent -d 'Interact with the agent'
complete -c pawbot -n '__fish_use_subcommand' -a gateway -d 'Manage the gateway server'
complete -c pawbot -n '__fish_use_subcommand' -a dashboard -d 'Launch the web dashboard'
complete -c pawbot -n '__fish_use_subcommand' -a channels -d 'Manage communication channels'
complete -c pawbot -n '__fish_use_subcommand' -a memory -d 'Memory operations'
complete -c pawbot -n '__fish_use_subcommand' -a skills -d 'Manage skills and plugins'
complete -c pawbot -n '__fish_use_subcommand' -a cron -d 'Scheduled job management'
complete -c pawbot -n '__fish_use_subcommand' -a onboard -d 'Run onboarding wizard'
complete -c pawbot -n '__fish_use_subcommand' -a audit -d 'Audit phase status'
complete -c pawbot -n '__fish_use_subcommand' -a completions -d 'Tab-completion management'
complete -c pawbot -n '__fish_use_subcommand' -a config -d 'Configuration management'

# Agent subcommands
complete -c pawbot -n '__fish_seen_subcommand_from agent' -l message -s m -d 'Message to send'
complete -c pawbot -n '__fish_seen_subcommand_from agent' -l session -s s -d 'Session ID'
complete -c pawbot -n '__fish_seen_subcommand_from agent' -l json -d 'Output as JSON'
complete -c pawbot -n '__fish_seen_subcommand_from agent' -l stream -d 'Stream response'

# Skills subcommands
complete -c pawbot -n '__fish_seen_subcommand_from skills' -a install -d 'Install a skill'
complete -c pawbot -n '__fish_seen_subcommand_from skills' -a uninstall -d 'Uninstall a skill'
complete -c pawbot -n '__fish_seen_subcommand_from skills' -a list -d 'List installed skills'
complete -c pawbot -n '__fish_seen_subcommand_from skills' -a info -d 'Show skill details'
complete -c pawbot -n '__fish_seen_subcommand_from skills' -a installed -d 'List installed packages'

# Memory subcommands
complete -c pawbot -n '__fish_seen_subcommand_from memory' -a list -d 'List memories'
complete -c pawbot -n '__fish_seen_subcommand_from memory' -a search -d 'Search memories'
complete -c pawbot -n '__fish_seen_subcommand_from memory' -a clear -d 'Clear memory store'
complete -c pawbot -n '__fish_seen_subcommand_from memory' -a stats -d 'Show statistics'

# Completions subcommands
complete -c pawbot -n '__fish_seen_subcommand_from completions' -a generate -d 'Generate completion script'
complete -c pawbot -n '__fish_seen_subcommand_from completions' -a install -d 'Install completions'

# Global options
complete -c pawbot -l version -s v -d 'Show version'
complete -c pawbot -l help -d 'Show help'
'''


def _powershell_completion() -> str:
    return '''# pawbot PowerShell completion

Register-ArgumentCompleter -CommandName pawbot -Native -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)

    $commands = @{
        '' = @('agent', 'gateway', 'dashboard', 'channels', 'memory', 'skills', 'cron', 'onboard', 'audit', 'completions', 'config', '--version', '--help')
        'agent' = @('chat', 'ask', '--message', '--session', '--json', '--stream', '--help')
        'gateway' = @('start', 'stop', 'status', '--help')
        'channels' = @('status', 'list', 'enable', 'disable', '--help')
        'memory' = @('list', 'search', 'clear', 'stats', '--help')
        'skills' = @('install', 'uninstall', 'list', 'info', 'installed', '--help')
        'cron' = @('list', 'add', 'remove', 'run', '--help')
        'completions' = @('generate', 'install', '--help')
        'config' = @('backup', 'restore', 'diff', 'backups', '--help')
    }

    $elements = $commandAst.ToString().Split(' ')
    $lastWord = if ($elements.Count -gt 1) { $elements[1] } else { '' }

    $completions = if ($commands.ContainsKey($lastWord)) {
        $commands[$lastWord]
    } else {
        $commands['']
    }

    $completions | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }
}
'''


SHELL_GENERATORS: dict[str, callable] = {
    "bash": _bash_completion,
    "zsh": _zsh_completion,
    "fish": _fish_completion,
    "powershell": _powershell_completion,
    "pwsh": _powershell_completion,
}

VALID_SHELLS = set(SHELL_GENERATORS.keys())

EXT_MAP = {
    "bash": ".bash",
    "zsh": ".zsh",
    "fish": ".fish",
    "powershell": ".ps1",
    "pwsh": ".ps1",
}


def generate_completion(shell: str) -> str:
    """Generate completion script for the specified shell."""
    gen = SHELL_GENERATORS.get(shell)
    if gen is None:
        return ""
    return gen()


# ── CLI Commands ──────────────────────────────────────────────────────────────


@completions_app.command("generate")
def cmd_generate(
    shell: str = typer.Argument(
        ..., help="Shell type: bash, zsh, fish, powershell"
    ),
    output: str = typer.Option(
        "", "--output", "-o", help="Output file path (default: stdout)"
    ),
):
    """Generate a shell completion script."""
    if shell not in VALID_SHELLS:
        console.print(
            f"[red]Invalid shell: '{shell}'. Use: {', '.join(sorted(VALID_SHELLS))}[/red]"
        )
        raise typer.Exit(1)

    script = generate_completion(shell)

    if output:
        Path(output).write_text(script, encoding="utf-8")
        console.print(f"[green]✓[/green] Completion script saved to {output}")
    else:
        print(script)


@completions_app.command("install")
def cmd_install(
    shell: str = typer.Argument(
        ..., help="Shell type: bash, zsh, fish, powershell"
    ),
):
    """Install shell completions for PawBot."""
    if shell not in VALID_SHELLS:
        console.print(
            f"[red]Invalid shell: '{shell}'. Use: {', '.join(sorted(VALID_SHELLS))}[/red]"
        )
        raise typer.Exit(1)

    script = generate_completion(shell)
    COMPLETIONS_DIR.mkdir(parents=True, exist_ok=True)

    filename = f"pawbot{EXT_MAP.get(shell, '.sh')}"
    filepath = COMPLETIONS_DIR / filename
    filepath.write_text(script, encoding="utf-8")

    console.print(f"[green]✓[/green] Completion script saved to {filepath}")
    console.print()

    if shell == "bash":
        console.print("[bold]Add to your ~/.bashrc:[/bold]")
        console.print(f'  source "{filepath}"')
    elif shell == "zsh":
        console.print("[bold]Add to your ~/.zshrc:[/bold]")
        console.print(f'  source "{filepath}"')
    elif shell == "fish":
        conf_dir = Path.home() / ".config" / "fish" / "completions"
        console.print("[bold]Copy to Fish completions:[/bold]")
        console.print(f'  cp "{filepath}" "{conf_dir / "pawbot.fish"}"')
    elif shell in ("powershell", "pwsh"):
        console.print("[bold]Add to your PowerShell profile ($PROFILE):[/bold]")
        console.print(f'  . "{filepath}"')
