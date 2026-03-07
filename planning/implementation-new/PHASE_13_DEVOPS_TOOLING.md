# Phase 13 — DevOps Tooling: Shell Completions, Config Backup & Update System

> **Goal:** Add developer experience features — shell tab-completions for 4 shells, automatic config backup/versioning, and an auto-update checker.  
> **Duration:** 5-7 days  
> **Risk Level:** Low (no behavioral changes, pure DX improvements)  
> **Depends On:** Phase 0 (CLI structure)

---

## Why This Phase Exists

OpenClaw ships with polished DevOps tooling:
- Shell completions for **Bash, Zsh, Fish, and PowerShell** (`completions/` directory)
- Automatic config backups with versioning (`.bak`, `.bak.1`, `.bak.2`, etc.)
- `update-check.json` for auto-update detection
- `gateway.cmd` and `node.cmd` startup scripts

PawBot has **none of these**. This phase adds them all.

---

## 13.1 — Shell Tab-Completion Generation

PawBot uses Typer, which supports shell completion natively. We need to generate and install completion scripts.

**Create:** `pawbot/cli/completions.py`

```python
"""Shell completion script generation and installation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

console = Console()
completions_app = typer.Typer(help="Shell completion management")

COMPLETIONS_DIR = Path.home() / ".pawbot" / "completions"


def _generate_completion(shell: str) -> str:
    """Generate completion script for the specified shell."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pawbot.cli.commands", "--show-completion", shell],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout
    # Fallback: Use typer's built-in completion generation
    if shell == "bash":
        return _bash_completion()
    elif shell == "zsh":
        return _zsh_completion()
    elif shell == "fish":
        return _fish_completion()
    elif shell in ("powershell", "pwsh"):
        return _powershell_completion()
    return ""


def _bash_completion() -> str:
    return '''# pawbot bash completion
_pawbot_complete() {
    local IFS=$'\\n'
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    
    # Top-level commands
    local commands="agent gateway dashboard channels memory skills cron onboard audit --version --help"
    
    # Subcommands
    case "${prev}" in
        agent)   COMPREPLY=($(compgen -W "chat ask --help" -- "${cur}"));;
        gateway) COMPREPLY=($(compgen -W "start stop status --help" -- "${cur}"));;
        channels) COMPREPLY=($(compgen -W "status list enable disable --help" -- "${cur}"));;
        memory)  COMPREPLY=($(compgen -W "list search clear stats --help" -- "${cur}"));;
        skills)  COMPREPLY=($(compgen -W "install uninstall list info --help" -- "${cur}"));;
        cron)    COMPREPLY=($(compgen -W "list add remove run --help" -- "${cur}"));;
        *)       COMPREPLY=($(compgen -W "${commands}" -- "${cur}"));;
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

# Memory subcommands
complete -c pawbot -n '__fish_seen_subcommand_from memory' -a list -d 'List memories'
complete -c pawbot -n '__fish_seen_subcommand_from memory' -a search -d 'Search memories'
complete -c pawbot -n '__fish_seen_subcommand_from memory' -a clear -d 'Clear memory store'
complete -c pawbot -n '__fish_seen_subcommand_from memory' -a stats -d 'Show statistics'

# Global options
complete -c pawbot -l version -s v -d 'Show version'
complete -c pawbot -l help -d 'Show help'
'''


def _powershell_completion() -> str:
    return '''# pawbot PowerShell completion

Register-ArgumentCompleter -CommandName pawbot -Native -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)

    $commands = @{
        '' = @('agent', 'gateway', 'dashboard', 'channels', 'memory', 'skills', 'cron', 'onboard', 'audit', '--version', '--help')
        'agent' = @('chat', 'ask', '--message', '--session', '--json', '--stream', '--help')
        'gateway' = @('start', 'stop', 'status', '--help')
        'channels' = @('status', 'list', 'enable', 'disable', '--help')
        'memory' = @('list', 'search', 'clear', 'stats', '--help')
        'skills' = @('install', 'uninstall', 'list', 'info', '--help')
        'cron' = @('list', 'add', 'remove', 'run', '--help')
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


@completions_app.command("generate")
def generate(
    shell: str = typer.Argument(
        ..., help="Shell type: bash, zsh, fish, powershell"
    ),
    output: str = typer.Option("", "--output", "-o", help="Output file path (default: stdout)"),
):
    """Generate shell completion script."""
    valid_shells = {"bash", "zsh", "fish", "powershell", "pwsh"}
    if shell not in valid_shells:
        console.print(f"[red]Invalid shell: '{shell}'. Use: {', '.join(valid_shells)}[/red]")
        raise typer.Exit(1)

    script = _generate_completion(shell)
    
    if output:
        Path(output).write_text(script)
        console.print(f"[green]✓[/green] Completion script saved to {output}")
    else:
        print(script)


@completions_app.command("install")
def install_completions(
    shell: str = typer.Argument(
        ..., help="Shell type: bash, zsh, fish, powershell"
    ),
):
    """Install shell completions for PawBot."""
    script = _generate_completion(shell)
    COMPLETIONS_DIR.mkdir(parents=True, exist_ok=True)

    ext_map = {"bash": ".bash", "zsh": ".zsh", "fish": ".fish", "powershell": ".ps1", "pwsh": ".ps1"}
    filename = f"pawbot{ext_map.get(shell, '.sh')}"
    filepath = COMPLETIONS_DIR / filename
    filepath.write_text(script)

    # Show installation instructions
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
        console.print(f"[bold]Copy to Fish completions:[/bold]")
        console.print(f'  cp "{filepath}" "{conf_dir / "pawbot.fish"}"')
    elif shell in ("powershell", "pwsh"):
        console.print("[bold]Add to your PowerShell profile ($PROFILE):[/bold]")
        console.print(f'  . "{filepath}"')
```

---

## 13.2 — Config Auto-Backup System

**Create:** `pawbot/config/backup.py`

```python
"""Configuration backup system — automatic versioned backups."""

from __future__ import annotations

import gzip
import json
import shutil
import time
from pathlib import Path
from typing import Any

from loguru import logger


class ConfigBackupManager:
    """Automatic config backup with rotation."""

    MAX_BACKUPS = 5         # Keep N most recent backups
    MIN_BACKUP_INTERVAL = 60  # Don't backup more than once per minute

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self._last_backup_time: float = 0

    def backup_before_write(self) -> str | None:
        """Create a backup before writing a config change.
        
        Returns:
            Backup file path, or None if no backup needed.
        """
        if not self.config_path.exists():
            return None

        # Rate limit backups
        now = time.time()
        if now - self._last_backup_time < self.MIN_BACKUP_INTERVAL:
            return None

        # Rotate existing backups: .bak.4 -> delete, .bak.3 -> .bak.4, etc.
        for i in range(self.MAX_BACKUPS - 1, 0, -1):
            old = self.config_path.with_suffix(f".json.bak.{i}")
            new = self.config_path.with_suffix(f".json.bak.{i + 1}")
            if old.exists():
                if new.exists():
                    new.unlink()
                old.rename(new)

        # Current .bak -> .bak.1
        bak = self.config_path.with_suffix(".json.bak")
        bak1 = self.config_path.with_suffix(".json.bak.1")
        if bak.exists():
            if bak1.exists():
                bak1.unlink()
            bak.rename(bak1)

        # Create new .bak
        shutil.copy2(self.config_path, bak)
        self._last_backup_time = now

        logger.debug("Config backed up: {}", bak)
        return str(bak)

    def restore(self, version: int = 0) -> bool:
        """Restore config from a backup.
        
        Args:
            version: 0 = latest .bak, 1 = .bak.1, etc.
        
        Returns:
            True if restored successfully.
        """
        if version == 0:
            backup = self.config_path.with_suffix(".json.bak")
        else:
            backup = self.config_path.with_suffix(f".json.bak.{version}")

        if not backup.exists():
            logger.error("Backup not found: {}", backup)
            return False

        # Backup current before restoring
        self.backup_before_write()

        shutil.copy2(backup, self.config_path)
        logger.info("Config restored from {}", backup)
        return True

    def list_backups(self) -> list[dict[str, Any]]:
        """List all available backups."""
        backups = []
        
        # Check .bak
        bak = self.config_path.with_suffix(".json.bak")
        if bak.exists():
            stat = bak.stat()
            backups.append({
                "version": 0,
                "path": str(bak),
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
                "age_hours": round((time.time() - stat.st_mtime) / 3600, 1),
            })

        # Check .bak.1 through .bak.N
        for i in range(1, self.MAX_BACKUPS + 1):
            bak_n = self.config_path.with_suffix(f".json.bak.{i}")
            if bak_n.exists():
                stat = bak_n.stat()
                backups.append({
                    "version": i,
                    "path": str(bak_n),
                    "size_bytes": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "age_hours": round((time.time() - stat.st_mtime) / 3600, 1),
                })

        return backups

    def diff(self, version: int = 0) -> dict[str, Any]:
        """Compare current config with a backup version.
        
        Returns:
            Dict with added, removed, and changed keys.
        """
        if version == 0:
            backup_path = self.config_path.with_suffix(".json.bak")
        else:
            backup_path = self.config_path.with_suffix(f".json.bak.{version}")

        if not backup_path.exists() or not self.config_path.exists():
            return {"error": "Files not found"}

        current = json.loads(self.config_path.read_text())
        backup = json.loads(backup_path.read_text())

        return self._deep_diff(backup, current, prefix="")

    def _deep_diff(
        self, old: dict, new: dict, prefix: str = ""
    ) -> dict[str, list[str]]:
        """Deep diff two dicts."""
        result = {"added": [], "removed": [], "changed": []}

        all_keys = set(old.keys()) | set(new.keys())
        for key in sorted(all_keys):
            full_key = f"{prefix}.{key}" if prefix else key
            if key not in old:
                result["added"].append(full_key)
            elif key not in new:
                result["removed"].append(full_key)
            elif old[key] != new[key]:
                if isinstance(old[key], dict) and isinstance(new[key], dict):
                    sub = self._deep_diff(old[key], new[key], full_key)
                    result["added"].extend(sub["added"])
                    result["removed"].extend(sub["removed"])
                    result["changed"].extend(sub["changed"])
                else:
                    result["changed"].append(full_key)

        return result
```

---

## 13.3 — Update Checker

**Create:** `pawbot/utils/update_checker.py`

```python
"""Auto-update checker — notifies when a new PawBot version is available."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger


UPDATE_CHECK_FILE = Path.home() / ".pawbot" / "update-check.json"
CHECK_INTERVAL_HOURS = 24


class UpdateChecker:
    """Check for PawBot updates periodically."""

    PYPI_URL = "https://pypi.org/pypi/pawbot/json"

    def __init__(self):
        self._last_check = self._load_last_check()

    def _load_last_check(self) -> dict[str, Any]:
        if UPDATE_CHECK_FILE.exists():
            try:
                return json.loads(UPDATE_CHECK_FILE.read_text())
            except Exception:
                pass
        return {"last_check": 0, "latest_version": "", "current_version": ""}

    def _save_check(self) -> None:
        UPDATE_CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
        UPDATE_CHECK_FILE.write_text(json.dumps(self._last_check, indent=2))

    def should_check(self) -> bool:
        """Check if enough time has passed since last check."""
        last = self._last_check.get("last_check", 0)
        return (time.time() - last) > (CHECK_INTERVAL_HOURS * 3600)

    async def check(self) -> dict[str, Any] | None:
        """Check PyPI for the latest version.
        
        Returns:
            Dict with version info if update available, None otherwise.
        """
        if not self.should_check():
            cached = self._last_check
            if cached.get("update_available"):
                return cached
            return None

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(self.PYPI_URL)
                r.raise_for_status()
                data = r.json()
                latest = data.get("info", {}).get("version", "")

            from pawbot import __version__
            current = __version__

            self._last_check = {
                "last_check": time.time(),
                "latest_version": latest,
                "current_version": current,
                "update_available": self._is_newer(latest, current),
            }
            self._save_check()

            if self._last_check["update_available"]:
                return self._last_check
            return None

        except Exception as e:
            logger.debug("Update check failed: {}", e)
            self._last_check["last_check"] = time.time()
            self._save_check()
            return None

    @staticmethod
    def _is_newer(latest: str, current: str) -> bool:
        """Compare semver versions."""
        try:
            from packaging.version import Version
            return Version(latest) > Version(current)
        except Exception:
            return latest != current

    def get_cached_status(self) -> dict[str, Any]:
        """Get the cached update status without making a network request."""
        return self._last_check
```

---

## 13.4 — Startup Scripts

**Create:** `pawbot/scripts/startup.py`

```python
"""Generate OS-specific startup scripts."""

from pathlib import Path
import sys


def generate_gateway_script() -> str:
    """Generate gateway startup script for the current OS."""
    python = sys.executable
    
    if sys.platform == "win32":
        return f'''@echo off
REM PawBot Gateway Startup Script
echo Starting PawBot Gateway...
"{python}" -m pawbot gateway start %*
'''
    else:
        return f'''#!/bin/bash
# PawBot Gateway Startup Script
echo "Starting PawBot Gateway..."
exec "{python}" -m pawbot gateway start "$@"
'''


def generate_node_script() -> str:
    """Generate node startup script."""
    python = sys.executable
    
    if sys.platform == "win32":
        return f'''@echo off
REM PawBot Node Startup Script
echo Starting PawBot Node...
"{python}" -m pawbot gateway start --with-dashboard %*
'''
    else:
        return f'''#!/bin/bash
# PawBot Node Startup Script
echo "Starting PawBot Node..."
exec "{python}" -m pawbot gateway start --with-dashboard "$@"
'''


def install_scripts() -> list[str]:
    """Install startup scripts to ~/.pawbot/."""
    scripts_dir = Path.home() / ".pawbot"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    installed = []

    ext = ".cmd" if sys.platform == "win32" else ".sh"

    # Gateway script
    gateway = scripts_dir / f"gateway{ext}"
    gateway.write_text(generate_gateway_script())
    if sys.platform != "win32":
        gateway.chmod(0o755)
    installed.append(str(gateway))

    # Node script
    node = scripts_dir / f"node{ext}"
    node.write_text(generate_node_script())
    if sys.platform != "win32":
        node.chmod(0o755)
    installed.append(str(node))

    return installed
```

---

## Verification Checklist — Phase 13 Complete

- [ ] `pawbot completions generate bash|zsh|fish|powershell` outputs completion scripts
- [ ] `pawbot completions install bash` saves and shows installation instructions
- [ ] Completion scripts cover all commands and subcommands
- [ ] `ConfigBackupManager` creates `.bak`, `.bak.1`, `.bak.2` etc.
- [ ] `MAX_BACKUPS = 5` rotation works (oldest deleted)
- [ ] `pawbot config restore --version 0` restores latest backup
- [ ] `pawbot config diff` shows changes between current and backup
- [ ] `UpdateChecker` queries PyPI every 24 hours
- [ ] Update notification shown on CLI startup if new version available
- [ ] Startup scripts generated for Windows (`.cmd`) and Unix (`.sh`)
- [ ] All tests pass: `pytest tests/ -v --tb=short`
