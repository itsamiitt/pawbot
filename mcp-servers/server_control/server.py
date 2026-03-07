#!/usr/bin/env python3
"""Server Control MCP server.

Registered as: mcp_servers.server_control in ~/.pawbot/config.json
Path: ~/.pawbot/mcp-servers/server_control/server.py
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None


def _configure_logger() -> logging.Logger:
    log_path = Path.home() / ".pawbot" / "logs" / "pawbot.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pawbot.mcp.server_control")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.propagate = False
    return logger


logger = _configure_logger()
mcp = FastMCP(name="server_control")


IRREVERSIBLE_PATTERNS = [
    "rm -rf",
    "mkfs",
    "dd ",
    "shutdown",
    "reboot",
    "format",
    "drop table",
    "truncate",
    "> /dev/",
]

PROTECTED_PATHS = [
    "/etc/passwd",
    "/etc/sudoers",
    "/etc/shadow",
    "/boot/",
    "c:/windows/system32",
    "c:/windows/system32/config",
]

CRONS_REGISTRY = os.path.expanduser("~/.pawbot/crons.json")
SENSITIVE_KEY_PATTERNS = ["password", "secret", "key", "token", "auth"]

TOOL_NAMES = [
    "server_run",
    "server_status",
    "server_processes",
    "server_kill",
    "server_read_file",
    "server_write_file",
    "server_list_dir",
    "service_control",
    "server_ports",
    "server_nginx",
    "cron_manage",
    "env_manage",
]


def _is_irreversible(command: str) -> bool:
    cmd_lower = (command or "").lower()
    return any(pattern in cmd_lower for pattern in IRREVERSIBLE_PATTERNS)


def _is_protected_path(path: str) -> bool:
    expanded = os.path.expanduser(path)
    raw = expanded.replace("\\", "/").lower()
    absolute = os.path.abspath(expanded).replace("\\", "/").lower()

    for prefix in PROTECTED_PATHS:
        p = prefix.lower()
        if raw.startswith(p) or absolute.startswith(p):
            return True
        # On Windows, a POSIX-looking path like "/etc/passwd" resolves to "c:/etc/passwd".
        if p.startswith("/") and absolute.startswith(f"c:{p}"):
            return True
    return False


def _log_command(command: str, cwd: str, returncode: int) -> None:
    logger.info("CMD: %r in %r -> rc=%s", command, cwd, returncode)


def _is_root_user() -> bool:
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        return getuid() == 0
    return False


def _truncate(text: str | None, limit: int) -> str:
    return (text or "")[:limit]


def _command_to_argv(command: str) -> list[str]:
    """Convert command string to argv, preserving Windows shell built-ins."""
    if os.name == "nt":
        try:
            parts = shlex.split(command, posix=False)
        except ValueError:
            parts = []
        if parts:
            builtins = {
                "assoc", "break", "call", "cd", "chdir", "cls", "color", "copy", "date",
                "del", "dir", "echo", "endlocal", "erase", "for", "ftype", "goto", "if",
                "md", "mkdir", "mklink", "move", "path", "pause", "popd", "prompt",
                "pushd", "rd", "ren", "rename", "rmdir", "set", "setlocal", "shift",
                "start", "time", "title", "type", "ver", "verify", "vol",
            }
            if parts[0].lower() in builtins:
                return ["cmd", "/c", command]
        return shlex.split(command, posix=False)
    return shlex.split(command)


def _require_psutil() -> dict[str, Any] | None:
    if psutil is None:
        return {"error": "psutil is not installed"}
    return None


def _run_command(
    args: list[str],
    timeout: int = 30,
    cwd: str | None = None,
    input_text: str | None = None,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
            input=input_text,
        )
        return {
            "returncode": result.returncode,
            "stdout": _truncate(result.stdout, 4000),
            "stderr": _truncate(result.stderr, 2000),
        }
    except FileNotFoundError:
        return {"error": f"Command not found: {args[0]}"}
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s"}
    except Exception as exc:
        return {"error": str(exc)}


def list_tools() -> dict[str, Any]:
    """Return an explicit tool inventory for testing and diagnostics."""
    return {"tools": TOOL_NAMES.copy(), "count": len(TOOL_NAMES)}


@mcp.tool()
def server_run(
    command: str,
    cwd: str | None = None,
    timeout: int = 60,
    background: bool = False,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Execute a shell command with safety gates."""
    if _is_root_user():
        return {"error": "Refusing to execute as root user"}

    if _is_irreversible(command) and not confirmed:
        return {
            "error": "CONFIRMATION_REQUIRED",
            "message": (
                f"This command contains an irreversible operation: {command!r}. "
                "Call again with confirmed=True to proceed."
            ),
            "command": command,
        }

    run_cwd = os.path.expanduser(cwd or "~")
    if not os.path.isdir(run_cwd):
        return {"error": f"Working directory not found: {run_cwd}"}

    if background:
        try:
            argv = _command_to_argv(command)
            proc = subprocess.Popen(
                argv,
                shell=False,
                cwd=run_cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _log_command(command, run_cwd, 0)
            return {"pid": proc.pid, "background": True, "status": "started"}
        except Exception as exc:
            return {"error": str(exc), "command": command}

    try:
        argv = _command_to_argv(command)
        result = subprocess.run(
            argv,
            shell=False,
            cwd=run_cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        combined = _truncate((result.stdout or "") + (result.stderr or ""), 5000)
        _log_command(command, run_cwd, result.returncode)
        return {
            "stdout": _truncate(result.stdout, 5000),
            "stderr": _truncate(result.stderr, 2000),
            "output": combined,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s", "command": command}
    except Exception as exc:
        return {"error": str(exc), "command": command}


@mcp.tool()
def server_status() -> dict[str, Any]:
    """Return a host resource snapshot."""
    missing = _require_psutil()
    if missing:
        return missing

    assert psutil is not None
    try:
        cpu = float(psutil.cpu_percent(interval=0.1))
        ram = psutil.virtual_memory()
        disk_target = Path.home().anchor or "/"
        disk = psutil.disk_usage(disk_target)
        try:
            load = psutil.getloadavg()
        except Exception:
            load = (0.0, 0.0, 0.0)
        uptime = int(time.time() - psutil.boot_time())

        top_procs: list[dict[str, Any]] = []
        procs = sorted(
            psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]),
            key=lambda p: p.info.get("cpu_percent", 0) or 0,
            reverse=True,
        )[:5]
        for proc in procs:
            top_procs.append(
                {
                    "pid": proc.info.get("pid"),
                    "name": proc.info.get("name", ""),
                    "cpu_percent": proc.info.get("cpu_percent", 0),
                    "memory_percent": round(proc.info.get("memory_percent", 0) or 0, 2),
                }
            )

        return {
            "cpu_percent": cpu,
            "ram_used_gb": round(ram.used / 1e9, 2),
            "ram_total_gb": round(ram.total / 1e9, 2),
            "ram_percent": ram.percent,
            "disk_used_gb": round(disk.used / 1e9, 2),
            "disk_total_gb": round(disk.total / 1e9, 2),
            "disk_percent": disk.percent,
            "load_avg_1m": load[0],
            "load_avg_5m": load[1],
            "uptime_seconds": uptime,
            "top_processes": top_procs,
        }
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def server_processes(filter: str = "") -> dict[str, Any]:
    """List top 20 processes by CPU usage, optionally filtered by name."""
    missing = _require_psutil()
    if missing:
        return missing

    assert psutil is not None
    procs: list[dict[str, Any]] = []
    for proc in psutil.process_iter(
        ["pid", "name", "cpu_percent", "memory_percent", "status", "username", "cmdline"]
    ):
        try:
            info = proc.info
            name = info.get("name", "")
            if filter and filter.lower() not in (name or "").lower():
                continue
            cmdline = info.get("cmdline") or []
            cmdline_text = " ".join(cmdline) if isinstance(cmdline, list) else str(cmdline)
            procs.append(
                {
                    "pid": info.get("pid"),
                    "name": name,
                    "cpu_percent": info.get("cpu_percent", 0),
                    "memory_percent": round(info.get("memory_percent", 0) or 0, 2),
                    "status": info.get("status", ""),
                    "user": info.get("username", ""),
                    "cmdline": _truncate(cmdline_text, 100),
                }
            )
        except Exception:
            continue

    top20 = sorted(procs, key=lambda x: x.get("cpu_percent", 0), reverse=True)[:20]
    return {"processes": top20, "count": len(top20)}


@mcp.tool()
def server_kill(target: str, confirmed: bool = False) -> dict[str, Any]:
    """Kill process by PID or by name (name kill requires confirmation)."""
    missing = _require_psutil()
    if missing:
        return missing

    assert psutil is not None
    logger.info("Kill request: target=%r, confirmed=%s", target, confirmed)

    try:
        pid = int(target)
        proc = psutil.Process(pid)
        name = proc.name()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            if proc.is_running():
                proc.kill()
        logger.info("Killed process: pid=%s name=%s", pid, name)
        return {"killed": True, "pid": pid, "name": name}
    except ValueError:
        matches = [
            p
            for p in psutil.process_iter(["pid", "name"])
            if target.lower() in (p.info.get("name", "") or "").lower()
        ]
        if not matches:
            return {"error": f"No process found matching '{target}'"}

        if not confirmed:
            return {
                "error": "CONFIRMATION_REQUIRED",
                "message": f"Found {len(matches)} processes matching '{target}'",
                "processes": [{"pid": p.info.get("pid"), "name": p.info.get("name")} for p in matches],
            }

        killed: list[int] = []
        for proc in matches:
            pid = proc.info.get("pid")
            if not pid:
                continue
            try:
                p = psutil.Process(pid)
                p.terminate()
                try:
                    p.wait(timeout=2)
                except Exception:
                    if p.is_running():
                        p.kill()
                killed.append(pid)
                logger.info("Killed: pid=%s name=%s", pid, proc.info.get("name"))
            except Exception:
                continue
        return {"killed": killed}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def server_read_file(path: str, lines: int | None = None) -> dict[str, Any]:
    """Read full file content or the last N lines."""
    file_path = os.path.expanduser(path)
    if not os.path.exists(file_path):
        return {"error": f"File not found: {file_path}"}

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read()

        if lines is not None:
            all_lines = content.splitlines()
            content = "\n".join(all_lines[-max(0, lines):])

        truncated = len(content) > 10000
        content = _truncate(content, 10000)
        return {
            "path": file_path,
            "content": content,
            "truncated": truncated,
            "chars": len(content),
        }
    except PermissionError:
        return {"error": f"Permission denied: {file_path}"}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def server_write_file(path: str, content: str, mode: str = "write") -> dict[str, Any]:
    """Write or append content to a file with overwrite backup protection."""
    file_path = os.path.expanduser(path)
    if _is_protected_path(file_path):
        return {"error": f"Refusing to write to protected path: {file_path}"}
    if mode not in {"write", "append"}:
        return {"error": "mode must be 'write' or 'append'"}

    backup_path = ""
    try:
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        if os.path.exists(file_path) and mode == "write":
            backup_path = f"{file_path}.bak"
            shutil.copy2(file_path, backup_path)

        file_mode = "w" if mode == "write" else "a"
        with open(file_path, file_mode, encoding="utf-8") as handle:
            handle.write(content)

        logger.info("FILE WRITE: %r mode=%s chars=%s", file_path, mode, len(content))
        return {
            "written": True,
            "path": file_path,
            "chars": len(content),
            "mode": mode,
            "backup": backup_path,
        }
    except Exception as exc:
        return {"error": str(exc), "path": file_path}


@mcp.tool()
def server_list_dir(path: str, depth: int = 2) -> dict[str, Any]:
    """List directory contents recursively up to max depth 4."""
    root = os.path.expanduser(path)
    if not os.path.exists(root):
        return {"error": f"Path not found: {root}"}
    if not os.path.isdir(root):
        return {"error": f"Not a directory: {root}"}

    max_depth = max(1, min(depth, 4))

    def _walk(current_path: str, current_depth: int) -> list[dict[str, Any]]:
        if current_depth > max_depth:
            return []
        entries: list[dict[str, Any]] = []
        try:
            with os.scandir(current_path) as iterator:
                for entry in sorted(iterator, key=lambda e: (not e.is_dir(), e.name.lower())):
                    info: dict[str, Any] = {
                        "name": entry.name,
                        "type": "dir" if entry.is_dir() else "file",
                        "path": entry.path,
                    }
                    try:
                        stat = entry.stat()
                        info["size"] = stat.st_size
                        info["modified_at"] = int(stat.st_mtime)
                    except Exception:
                        pass
                    if entry.is_dir() and current_depth < max_depth:
                        info["children"] = _walk(entry.path, current_depth + 1)
                    entries.append(info)
        except PermissionError:
            return []
        return entries

    return {"path": root, "entries": _walk(root, 1)}


@mcp.tool()
def service_control(service: str, action: str, log_lines: int = 50) -> dict[str, Any]:
    """Control systemd services: start, stop, restart, status, enable, disable, logs."""
    allowed_actions = {"start", "stop", "restart", "status", "enable", "disable", "logs"}
    if action not in allowed_actions:
        return {"error": f"Invalid action. Must be one of: {sorted(allowed_actions)}"}
    if not service:
        return {"error": "service is required"}

    if action == "logs":
        result = _run_command(
            ["journalctl", "-u", service, "-n", str(log_lines), "--no-pager"],
            timeout=10,
        )
        if "error" in result:
            return result
        return {"service": service, "logs": result.get("stdout", "")}

    if action == "status":
        result = _run_command(["systemctl", "status", service, "--no-pager"], timeout=10)
        if "error" in result:
            return result
        output = (result.get("stdout", "") + result.get("stderr", ""))[:2000]
        is_active = "Active: active" in output
        return {
            "service": service,
            "active": is_active,
            "output": output,
            "returncode": result.get("returncode", 1),
        }

    result = _run_command(["systemctl", action, service], timeout=30)
    if "error" in result:
        return result
    output = _truncate((result.get("stdout", "") + result.get("stderr", "")), 1000)
    logger.info("SERVICE: %s %r -> rc=%s", action, service, result.get("returncode", 1))
    return {
        "service": service,
        "action": action,
        "success": result.get("returncode", 1) == 0,
        "output": output,
    }


@mcp.tool()
def server_ports() -> dict[str, Any]:
    """Return all listening ports with process info."""
    missing = _require_psutil()
    if missing:
        return missing

    assert psutil is not None
    connections: list[dict[str, Any]] = []
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != "LISTEN":
                continue
            process_name = ""
            try:
                if conn.pid:
                    process_name = psutil.Process(conn.pid).name()
            except Exception:
                process_name = ""
            protocol = "tcp" if conn.type == socket.SOCK_STREAM else "udp"
            connections.append(
                {
                    "port": conn.laddr.port,
                    "host": conn.laddr.ip,
                    "protocol": protocol,
                    "pid": conn.pid,
                    "process_name": process_name,
                }
            )
        connections.sort(key=lambda x: x["port"])
        return {"listening_ports": connections, "count": len(connections)}
    except Exception as exc:
        return {"error": str(exc)}


def _nginx_test() -> dict[str, Any]:
    """Run nginx config validation."""
    result = _run_command(["nginx", "-t"], timeout=20)
    if "error" in result:
        return result
    output = result.get("stderr") or result.get("stdout") or ""
    return {"ok": result.get("returncode", 1) == 0, "output": _truncate(output, 2000)}


def _nginx_reload() -> dict[str, Any]:
    """Validate nginx config and reload service when valid."""
    test = _nginx_test()
    if not test.get("ok"):
        return {"error": "nginx config test failed", "details": test.get("output", "")}
    result = _run_command(["systemctl", "reload", "nginx"], timeout=30)
    if "error" in result:
        return result
    return {"reloaded": result.get("returncode", 1) == 0}


def _nginx_list_vhosts(sites_available: Path) -> dict[str, Any]:
    """List vhost files from nginx sites-available directory."""
    if not sites_available.exists():
        return {"vhosts": []}
    vhosts = [entry.name for entry in sorted(sites_available.iterdir()) if entry.is_file()]
    return {"vhosts": vhosts}


def _nginx_add_vhost(
    domain: str,
    config: str,
    sites_available: Path,
    sites_enabled: Path,
) -> dict[str, Any]:
    """Add vhost config and enable it, with rollback on failed nginx test."""
    if not domain or not config:
        return {"error": "domain and config required for add_vhost"}
    try:
        sites_available.mkdir(parents=True, exist_ok=True)
        sites_enabled.mkdir(parents=True, exist_ok=True)
        avail_path = sites_available / domain
        enabled_path = sites_enabled / domain

        avail_path.write_text(config, encoding="utf-8")
        if not enabled_path.exists():
            try:
                enabled_path.symlink_to(avail_path)
            except Exception:
                shutil.copy2(avail_path, enabled_path)

        test = _nginx_test()
        if not test.get("ok"):
            for rollback_path in (avail_path, enabled_path):
                if rollback_path.exists():
                    rollback_path.unlink()
            return {"error": "nginx config test failed", "details": test.get("output", "")}

        _nginx_reload()
        logger.info("NGINX: added vhost for %r", domain)
        return {"added": True, "domain": domain, "path": str(avail_path)}
    except Exception as exc:
        return {"error": str(exc)}


def _nginx_remove_vhost(
    domain: str,
    sites_available: Path,
    sites_enabled: Path,
) -> dict[str, Any]:
    """Remove vhost config and enabled link, then reload nginx."""
    if not domain:
        return {"error": "domain is required for remove_vhost"}
    try:
        for path in (sites_available / domain, sites_enabled / domain):
            if path.exists():
                path.unlink()
        _nginx_reload()
        logger.info("NGINX: removed vhost for %r", domain)
        return {"removed": True, "domain": domain}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def server_nginx(action: str, domain: str = "", config: str = "") -> dict[str, Any]:
    """Manage nginx operations and vhosts."""
    sites_available = Path("/etc/nginx/sites-available")
    sites_enabled = Path("/etc/nginx/sites-enabled")
    actions: dict[str, Any] = {
        "test": _nginx_test,
        "reload": _nginx_reload,
        "status": lambda: service_control("nginx", "status"),
        "list_vhosts": lambda: _nginx_list_vhosts(sites_available),
        "add_vhost": lambda: _nginx_add_vhost(domain, config, sites_available, sites_enabled),
        "remove_vhost": lambda: _nginx_remove_vhost(domain, sites_available, sites_enabled),
    }

    handler = actions.get(action)
    if handler is None:
        return {"error": f"Unknown action: {action}"}
    return handler()


def _load_registry(path: str) -> dict[str, Any]:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _save_registry(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


@mcp.tool()
def cron_manage(
    action: str,
    name: str = "",
    schedule: str = "",
    command: str = "",
) -> dict[str, Any]:
    """Manage crontab entries and keep a registry at ~/.pawbot/crons.json."""
    if action == "list":
        return {"crons": _load_registry(CRONS_REGISTRY)}

    if action == "add":
        if not all([name, schedule, command]):
            return {"error": "name, schedule, and command required"}

        existing = _run_command(["crontab", "-l"], timeout=10)
        if "error" in existing and "Command not found" in existing["error"]:
            return {"error": "crontab command not found"}
        current = existing.get("stdout", "")
        cron_line = f"{schedule} {command} # pawbot:{name}\n"
        updated = current + cron_line
        applied = _run_command(["crontab", "-"], timeout=10, input_text=updated)
        if "error" in applied:
            return applied
        if applied.get("returncode", 1) != 0:
            return {"error": _truncate(applied.get("stderr", "failed to update crontab"), 500)}

        registry = _load_registry(CRONS_REGISTRY)
        registry[name] = {
            "schedule": schedule,
            "command": command,
            "added_at": int(time.time()),
        }
        _save_registry(CRONS_REGISTRY, registry)
        logger.info("CRON: added %r: %s %s", name, schedule, command)
        return {"added": True, "name": name, "schedule": schedule}

    if action == "remove":
        registry = _load_registry(CRONS_REGISTRY)
        if name not in registry:
            return {"error": f"Cron '{name}' not found in registry"}

        existing = _run_command(["crontab", "-l"], timeout=10)
        if "error" in existing and "Command not found" in existing["error"]:
            return {"error": "crontab command not found"}
        lines = [
            line
            for line in (existing.get("stdout", "") or "").splitlines()
            if f"# pawbot:{name}" not in line
        ]
        applied = _run_command(["crontab", "-"], timeout=10, input_text="\n".join(lines) + "\n")
        if "error" in applied:
            return applied
        if applied.get("returncode", 1) != 0:
            return {"error": _truncate(applied.get("stderr", "failed to update crontab"), 500)}

        del registry[name]
        _save_registry(CRONS_REGISTRY, registry)
        logger.info("CRON: removed %r", name)
        return {"removed": True, "name": name}

    return {"error": f"Unknown action: {action}"}


def _is_sensitive_key(key: str) -> bool:
    return any(pattern in key.lower() for pattern in SENSITIVE_KEY_PATTERNS)


def _parse_env(path: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not os.path.exists(path):
        return result
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _write_env(path: str, data: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for key, value in data.items():
            handle.write(f'{key}="{value}"\n')


@mcp.tool()
def env_manage(
    action: str,
    file: str,
    key: str = "",
    value: str = "",
) -> dict[str, Any]:
    """Manage .env files while masking sensitive values in responses/logs."""
    env_path = os.path.expanduser(file)

    if action == "list":
        env = _parse_env(env_path)
        masked = {k: ("***" if _is_sensitive_key(k) else v) for k, v in env.items()}
        return {"file": env_path, "vars": masked}

    if action == "get":
        env = _parse_env(env_path)
        val = env.get(key, "")
        return {"key": key, "value": "***" if _is_sensitive_key(key) else val}

    if action == "set":
        if not key:
            return {"error": "key is required for set"}
        env = _parse_env(env_path)
        env[key] = value
        _write_env(env_path, env)
        logger.info("ENV SET: %s=%s in %r", key, "***" if _is_sensitive_key(key) else value, env_path)
        return {"set": True, "key": key}

    if action == "delete":
        if not key:
            return {"error": "key is required for delete"}
        env = _parse_env(env_path)
        if key in env:
            del env[key]
            _write_env(env_path, env)
        return {"deleted": True, "key": key}

    return {"error": f"Unknown action: {action}"}


def main() -> None:
    """Run the MCP server over stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
