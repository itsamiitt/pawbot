"""Shell execution tool."""

import asyncio
import os
import re
import shlex
from pathlib import Path
from typing import Any

from loguru import logger

from pawbot.agent.tools.base import Tool


# Patterns that are ALWAYS blocked (even in non-restricted mode)
_ALWAYS_BLOCKED: list[re.Pattern] = [
    re.compile(r'\brm\s+(-[rfR]+\s+)?/(?!\w)'),    # rm -rf /
    re.compile(r'\bfind\s+/\s+.*-delete\b'),         # find / -delete
    re.compile(r'\bmkfs\b'),                           # format disk
    re.compile(r'\bdd\s+.*of=/dev/'),                  # dd to device
    re.compile(r'>\s*/dev/sd[a-z]'),                   # overwrite disk
    re.compile(r'\bchmod\s+-R\s+777\s+/'),             # chmod 777 /
    re.compile(r'\bcurl\b.*\|\s*(ba)?sh'),             # curl | bash
    re.compile(r'\bwget\b.*\|\s*(ba)?sh'),             # wget | bash
]


class ExecTool(Tool):
    """Tool to execute shell commands."""

    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        path_append: str = "",
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",          # rm -r, rm -rf, rm -fr
            r"\bdel\s+/[fq]\b",              # del /f, del /q
            r"\brmdir\s+/s\b",               # rmdir /s
            r"(?:^|[;&|]\s*)format\b",       # format (as standalone command only)
            r"\b(mkfs|diskpart)\b",          # disk operations
            r"\bdd\s+if=",                   # dd
            r">\s*/dev/sd",                  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",          # fork bomb
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.path_append = path_append
        self.environment = os.environ.get("PAWBOT_ENV", "dev").lower()

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output. Use with caution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command"
                }
            },
            "required": ["command"]
        }
    
    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error
        
        env = os.environ.copy()
        if self.path_append:
            env["PATH"] = env.get("PATH", "") + os.pathsep + self.path_append

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                # Wait for the process to fully terminate so pipes are
                # drained and file descriptors are released.
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                return f"Error: Command timed out after {self.timeout} seconds"
            
            output_parts = []
            
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")
            
            if process.returncode != 0:
                output_parts.append(f"\nExit code: {process.returncode}")
            
            result = "\n".join(output_parts) if output_parts else "(no output)"
            
            # Truncate very long output
            max_len = 10000
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"
            
            return result
            
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands.

        Layer 1: _ALWAYS_BLOCKED compiled regexes (cannot be overridden)
        Layer 2: Configurable deny_patterns
        Layer 3: Production-mode restrictions
        Layer 4: Workspace path restriction
        """
        cmd = command.strip()
        lower = cmd.lower()

        # Layer 1: Always-blocked patterns (compiled, cannot be overridden)
        for pattern in _ALWAYS_BLOCKED:
            if pattern.search(lower):
                logger.warning("Blocked dangerous command: {}", cmd[:100])
                return f"Error: Command blocked by safety guard (dangerous pattern: {pattern.pattern})"

        # Layer 2: Production environment restrictions
        if self.environment in {"prod", "production"} and self._is_prod_restricted(cmd):
            return "Error: Command blocked by production safety policy"

        # Layer 3: Configurable deny patterns
        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        # Layer 4: Allowlist enforcement
        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        # Layer 5: Workspace restriction — verify all file paths
        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            # Use shlex for better path extraction
            try:
                parts = shlex.split(command)
                for part in parts:
                    if part.startswith('/') and not str(cwd_path).startswith('/'):
                        continue  # Skip POSIX paths on Windows
                    # Check extracted absolute paths
                    try:
                        p = Path(part).resolve()
                    except Exception:
                        continue
                    if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                        return f"Error: Command blocked by safety guard (path '{part}' outside workspace)"
            except ValueError:
                pass  # shlex parse error — fall through to regex-based check

            for raw in self._extract_absolute_paths(cmd):
                try:
                    p = Path(raw.strip()).resolve()
                except Exception:
                    continue
                if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    @staticmethod
    def _is_prod_restricted(command: str) -> bool:
        lower = command.lower()
        restricted = [
            "git push --force",
            "systemctl stop",
            "systemctl disable",
            "shutdown",
            "reboot",
            "diskpart",
            "mkfs",
            "drop database",
        ]
        return any(p in lower for p in restricted)

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]+", command)   # Windows: C:\...
        posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", command) # POSIX: /absolute only
        return win_paths + posix_paths


