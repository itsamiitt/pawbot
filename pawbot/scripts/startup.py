"""Generate OS-specific startup scripts (Phase 13.4).

Creates gateway.cmd/.sh and node.cmd/.sh scripts that launch
PawBot with the correct Python interpreter path.
"""

from __future__ import annotations

import sys
from pathlib import Path


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
    """Generate node startup script (gateway + dashboard)."""
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


def generate_agent_script() -> str:
    """Generate agent interactive chat script."""
    python = sys.executable

    if sys.platform == "win32":
        return f'''@echo off
REM PawBot Agent Chat Script
"{python}" -m pawbot agent chat %*
'''
    else:
        return f'''#!/bin/bash
# PawBot Agent Chat Script
exec "{python}" -m pawbot agent chat "$@"
'''


def install_scripts(target_dir: Path | None = None) -> list[str]:
    """Install startup scripts to ~/.pawbot/ (or specified directory).

    Returns:
        List of installed script paths.
    """
    scripts_dir = target_dir or (Path.home() / ".pawbot")
    scripts_dir.mkdir(parents=True, exist_ok=True)
    installed = []

    ext = ".cmd" if sys.platform == "win32" else ".sh"

    scripts = {
        f"gateway{ext}": generate_gateway_script(),
        f"node{ext}": generate_node_script(),
        f"agent{ext}": generate_agent_script(),
    }

    for filename, content in scripts.items():
        filepath = scripts_dir / filename
        filepath.write_text(content, encoding="utf-8")
        if sys.platform != "win32":
            filepath.chmod(0o755)
        installed.append(str(filepath))

    return installed
