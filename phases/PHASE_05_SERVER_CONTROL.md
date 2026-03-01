# PHASE 5 — SERVER CONTROL MCP SERVER
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)  
> **Implementation Day:** Day 15  
> **Primary File:** `~/.nanobot/mcp-servers/server_control/server.py` (NEW)  
> **Test File:** `~/nanobot/tests/test_server_control_mcp.py`  
> **Config registration key:** `mcp_servers.server_control`  
> **Dependency to add:** `psutil>=5.9.0`

---

## BEFORE YOU START — READ THESE FILES

```bash
cat ~/.nanobot/config.json            # understand current mcp_servers section
# Review any existing MCP server in nanobot for the pattern to follow:
ls ~/nanobot/                         # look for existing MCP server examples
```

**This file is a standalone MCP server.** It is NOT part of the main nanobot Python package. It runs as a separate process registered via config.json.

---

## SETUP

### Create Directory and Register

```bash
mkdir -p ~/.nanobot/mcp-servers/server_control
```

Add to `~/.nanobot/config.json`:

```json
{
  "mcp_servers": {
    "server_control": {
      "path": "~/.nanobot/mcp-servers/server_control/server.py",
      "requires_confirmation": true,
      "enabled": true
    }
  }
}
```

### Add Dependency

```toml
# In ~/nanobot/pyproject.toml:
"psutil>=5.9.0",
```

---

## MCP SERVER STRUCTURE

Every tool in this server follows this pattern:

```python
#!/usr/bin/env python3
"""
Server Control MCP Server
Registered as: mcp_servers.server_control in ~/.nanobot/config.json
Path: ~/.nanobot/mcp-servers/server_control/server.py
"""
import subprocess
import json
import os
import signal
import time
import logging
import psutil
from pathlib import Path

logger = logging.getLogger("nanobot.mcp.server_control")

# ── Safety Configuration ────────────────────────────────────────────────────
# Commands containing these patterns require explicit confirmation
IRREVERSIBLE_PATTERNS = [
    "rm -rf", "mkfs", "dd ", "shutdown", "reboot",
    "format", "DROP TABLE", "truncate", "> /dev/",
]

# Never write to these paths
PROTECTED_PATHS = ["/etc/passwd", "/etc/sudoers", "/etc/shadow", "/boot/"]

def _is_irreversible(command: str) -> bool:
    cmd_lower = command.lower()
    return any(p.lower() in cmd_lower for p in IRREVERSIBLE_PATTERNS)

def _is_protected_path(path: str) -> bool:
    return any(path.startswith(p) for p in PROTECTED_PATHS)

def _log_command(command: str, cwd: str, returncode: int):
    logger.info(f"CMD: {command!r} in {cwd!r} → rc={returncode}")
```

---

## TOOL IMPLEMENTATIONS

### server_run

```python
def server_run(command: str, cwd: str = None, timeout: int = 60,
               background: bool = False, confirmed: bool = False) -> dict:
    """
    Execute shell command.
    Returns: {"stdout": str, "stderr": str, "returncode": int, "pid": int}
    """
    # Safety: never run as root
    if os.getuid() == 0:
        return {"error": "Refusing to execute as root user"}

    # Safety: irreversible action gate
    if _is_irreversible(command) and not confirmed:
        return {
            "error": "CONFIRMATION_REQUIRED",
            "message": (f"This command contains an irreversible operation: {command!r}. "
                        "Call again with confirmed=True to proceed."),
            "command": command,
        }

    cwd = cwd or os.path.expanduser("~")

    if background:
        proc = subprocess.Popen(
            command, shell=True, cwd=cwd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        _log_command(command, cwd, 0)
        return {"pid": proc.pid, "background": True, "status": "started"}

    try:
        result = subprocess.run(
            command, shell=True, cwd=cwd, timeout=timeout,
            capture_output=True, text=True
        )
        combined = (result.stdout + result.stderr)[:5000]
        _log_command(command, cwd, result.returncode)
        return {
            "stdout": result.stdout[:5000],
            "stderr": result.stderr[:2000],
            "output": combined,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s", "command": command}
    except Exception as e:
        return {"error": str(e), "command": command}
```

### server_status

```python
def server_status() -> dict:
    """
    Returns system resource snapshot.
    Uses psutil — must be installed (psutil>=5.9.0).
    """
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    load = psutil.getloadavg()
    uptime = int(time.time() - psutil.boot_time())

    # Top 5 processes by CPU
    top_procs = []
    for proc in sorted(psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]),
                        key=lambda p: p.info.get("cpu_percent", 0) or 0,
                        reverse=True)[:5]:
        top_procs.append({
            "pid": proc.info["pid"],
            "name": proc.info["name"],
            "cpu_percent": proc.info["cpu_percent"],
            "memory_percent": round(proc.info.get("memory_percent", 0) or 0, 2),
        })

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
```

### server_processes

```python
def server_processes(filter: str = "") -> dict:
    """Lists top 20 processes by CPU. Optionally filter by name."""
    procs = []
    for proc in psutil.process_iter(
        ["pid", "name", "cpu_percent", "memory_percent", "status", "username", "cmdline"]
    ):
        try:
            info = proc.info
            name = info.get("name", "")
            if filter and filter.lower() not in name.lower():
                continue
            procs.append({
                "pid": info["pid"],
                "name": name,
                "cpu_percent": info.get("cpu_percent", 0),
                "memory_percent": round(info.get("memory_percent", 0) or 0, 2),
                "status": info.get("status", ""),
                "user": info.get("username", ""),
                "cmdline": " ".join(info.get("cmdline", []))[:100],
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    top20 = sorted(procs, key=lambda x: x["cpu_percent"], reverse=True)[:20]
    return {"processes": top20, "count": len(top20)}
```

### server_kill

```python
def server_kill(target: str, confirmed: bool = False) -> dict:
    """Kill process by name or PID. Sends SIGTERM, then SIGKILL after 5s."""
    logger.info(f"Kill request: target={target!r}, confirmed={confirmed}")

    # Determine if target is PID or name
    try:
        pid = int(target)
        # Kill by PID directly
        proc = psutil.Process(pid)
        name = proc.name()
        proc.terminate()
        time.sleep(5)
        if proc.is_running():
            proc.kill()
        logger.info(f"Killed process: pid={pid} name={name}")
        return {"killed": True, "pid": pid, "name": name}
    except ValueError:
        # Kill by name — requires confirmation (multiple processes possible)
        matches = [p for p in psutil.process_iter(["pid", "name"])
                   if target.lower() in p.info.get("name", "").lower()]
        if not matches:
            return {"error": f"No process found matching '{target}'"}

        if not confirmed:
            return {
                "error": "CONFIRMATION_REQUIRED",
                "message": f"Found {len(matches)} processes matching '{target}'",
                "processes": [{"pid": p.info["pid"], "name": p.info["name"]}
                               for p in matches],
            }

        killed = []
        for proc in matches:
            try:
                p = psutil.Process(proc.info["pid"])
                p.terminate()
                time.sleep(2)
                if p.is_running():
                    p.kill()
                killed.append(proc.info["pid"])
                logger.info(f"Killed: pid={proc.info['pid']} name={proc.info['name']}")
            except Exception:
                pass
        return {"killed": killed}
```

### server_read_file

```python
def server_read_file(path: str, lines: int = None) -> dict:
    """Read file content. If lines specified, return last N lines."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}

    try:
        with open(path, "r", errors="replace") as f:
            content = f.read()

        if lines:
            # Return last N lines
            all_lines = content.splitlines()
            content = "\n".join(all_lines[-lines:])

        truncated = len(content) > 10000
        content = content[:10000]

        return {
            "path": path,
            "content": content,
            "truncated": truncated,
            "chars": len(content),
        }
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except Exception as e:
        return {"error": str(e)}
```

### server_write_file

```python
def server_write_file(path: str, content: str, mode: str = "write") -> dict:
    """
    Write or append to file.
    Always creates a .bak backup before overwriting.
    Refuses to write to protected system paths.
    Logs every write.
    """
    path = os.path.expanduser(path)

    if _is_protected_path(path):
        return {"error": f"Refusing to write to protected path: {path}"}

    # Create backup before overwriting
    if os.path.exists(path) and mode == "write":
        bak_path = f"{path}.bak"
        import shutil
        shutil.copy2(path, bak_path)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    file_mode = "w" if mode == "write" else "a"
    with open(path, file_mode) as f:
        f.write(content)

    logger.info(f"FILE WRITE: {path!r} mode={mode} chars={len(content)}")
    return {"written": True, "path": path, "chars": len(content), "mode": mode}
```

### server_list_dir

```python
def server_list_dir(path: str, depth: int = 2) -> dict:
    """List directory contents recursively up to depth (max 4)."""
    path = os.path.expanduser(path)
    depth = min(depth, 4)

    def _walk(p: str, current_depth: int) -> list:
        if current_depth > depth:
            return []
        entries = []
        try:
            for entry in sorted(os.scandir(p), key=lambda e: (not e.is_dir(), e.name)):
                info = {
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
                if entry.is_dir() and current_depth < depth:
                    info["children"] = _walk(entry.path, current_depth + 1)
                entries.append(info)
        except PermissionError:
            pass
        return entries

    return {"path": path, "entries": _walk(path, 1)}
```

### service_control

```python
def service_control(service: str, action: str, log_lines: int = 50) -> dict:
    """
    Control systemd services.
    Actions: start, stop, restart, status, enable, disable, logs
    """
    ALLOWED_ACTIONS = {"start", "stop", "restart", "status", "enable", "disable", "logs"}
    if action not in ALLOWED_ACTIONS:
        return {"error": f"Invalid action. Must be one of: {ALLOWED_ACTIONS}"}

    if action == "logs":
        result = subprocess.run(
            ["journalctl", "-u", service, "-n", str(log_lines), "--no-pager"],
            capture_output=True, text=True, timeout=10
        )
        return {"service": service, "logs": result.stdout}

    if action == "status":
        result = subprocess.run(
            ["systemctl", "status", service, "--no-pager"],
            capture_output=True, text=True, timeout=10
        )
        is_active = "Active: active" in result.stdout
        return {
            "service": service,
            "active": is_active,
            "output": result.stdout[:2000],
            "returncode": result.returncode,
        }

    result = subprocess.run(
        ["systemctl", action, service],
        capture_output=True, text=True, timeout=30
    )
    logger.info(f"SERVICE: {action} {service!r} → rc={result.returncode}")
    return {
        "service": service,
        "action": action,
        "success": result.returncode == 0,
        "output": (result.stdout + result.stderr)[:1000],
    }
```

### server_ports

```python
def server_ports() -> dict:
    """Returns all listening ports with process info."""
    connections = []
    for conn in psutil.net_connections(kind="inet"):
        if conn.status == "LISTEN":
            proc_name = ""
            try:
                if conn.pid:
                    proc_name = psutil.Process(conn.pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            connections.append({
                "port": conn.laddr.port,
                "host": conn.laddr.ip,
                "protocol": "tcp" if conn.type.name == "SOCK_STREAM" else "udp",
                "pid": conn.pid,
                "process_name": proc_name,
            })
    connections.sort(key=lambda x: x["port"])
    return {"listening_ports": connections, "count": len(connections)}
```

### server_nginx

```python
def server_nginx(action: str, domain: str = "", config: str = "") -> dict:
    """
    Actions: reload, test, status, add_vhost, remove_vhost, list_vhosts
    For add_vhost: writes config, creates symlink, tests, reloads.
    """
    SITES_AVAILABLE = "/etc/nginx/sites-available"
    SITES_ENABLED = "/etc/nginx/sites-enabled"

    if action == "test":
        r = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
        return {"ok": r.returncode == 0, "output": r.stderr}

    if action == "reload":
        test = server_nginx("test")
        if not test["ok"]:
            return {"error": "nginx config test failed", "details": test["output"]}
        r = subprocess.run(["systemctl", "reload", "nginx"], capture_output=True, text=True)
        return {"reloaded": r.returncode == 0}

    if action == "status":
        return service_control("nginx", "status")

    if action == "list_vhosts":
        vhosts = list(Path(SITES_AVAILABLE).glob("*")) if Path(SITES_AVAILABLE).exists() else []
        return {"vhosts": [v.name for v in vhosts]}

    if action == "add_vhost":
        if not domain or not config:
            return {"error": "domain and config required for add_vhost"}
        avail_path = f"{SITES_AVAILABLE}/{domain}"
        enabled_path = f"{SITES_ENABLED}/{domain}"
        with open(avail_path, "w") as f:
            f.write(config)
        if not os.path.exists(enabled_path):
            os.symlink(avail_path, enabled_path)
        test = server_nginx("test")
        if not test["ok"]:
            os.remove(avail_path)
            if os.path.exists(enabled_path):
                os.remove(enabled_path)
            return {"error": "nginx config test failed", "details": test["output"]}
        server_nginx("reload")
        logger.info(f"NGINX: added vhost for {domain!r}")
        return {"added": True, "domain": domain, "path": avail_path}

    if action == "remove_vhost":
        for path in [f"{SITES_AVAILABLE}/{domain}", f"{SITES_ENABLED}/{domain}"]:
            if os.path.exists(path):
                os.remove(path)
        server_nginx("reload")
        logger.info(f"NGINX: removed vhost for {domain!r}")
        return {"removed": True, "domain": domain}

    return {"error": f"Unknown action: {action}"}
```

### cron_manage

```python
CRONS_REGISTRY = os.path.expanduser("~/.nanobot/crons.json")

def cron_manage(action: str, name: str = "", schedule: str = "", command: str = "") -> dict:
    """
    Manage cron jobs. Tracks in ~/.nanobot/crons.json.
    Actions: list, add, remove
    """
    def _load_registry() -> dict:
        if os.path.exists(CRONS_REGISTRY):
            with open(CRONS_REGISTRY) as f:
                return json.load(f)
        return {}

    def _save_registry(data: dict):
        with open(CRONS_REGISTRY, "w") as f:
            json.dump(data, f, indent=2)

    if action == "list":
        return {"crons": _load_registry()}

    if action == "add":
        if not all([name, schedule, command]):
            return {"error": "name, schedule, and command required"}
        cron_line = f"{schedule} {command} # nanobot:{name}\n"
        # Add to crontab
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        new_crontab = existing.stdout + cron_line
        proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True,
                               capture_output=True)
        registry = _load_registry()
        registry[name] = {"schedule": schedule, "command": command, "added_at": int(time.time())}
        _save_registry(registry)
        logger.info(f"CRON: added '{name}': {schedule} {command}")
        return {"added": True, "name": name, "schedule": schedule}

    if action == "remove":
        registry = _load_registry()
        if name not in registry:
            return {"error": f"Cron '{name}' not found in registry"}
        # Remove from crontab
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        new_lines = [l for l in existing.stdout.splitlines()
                     if f"# nanobot:{name}" not in l]
        subprocess.run(["crontab", "-"], input="\n".join(new_lines) + "\n",
                        text=True, capture_output=True)
        del registry[name]
        _save_registry(registry)
        logger.info(f"CRON: removed '{name}'")
        return {"removed": True, "name": name}

    return {"error": f"Unknown action: {action}"}
```

### env_manage

```python
SENSITIVE_KEY_PATTERNS = ["password", "secret", "key", "token", "auth"]

def _is_sensitive_key(key: str) -> bool:
    return any(p in key.lower() for p in SENSITIVE_KEY_PATTERNS)

def env_manage(action: str, file: str, key: str = "", value: str = "") -> dict:
    """Manage .env files. Never logs sensitive values."""
    file = os.path.expanduser(file)

    def _parse_env(path: str) -> dict:
        result = {}
        if not os.path.exists(path):
            return result
        with open(path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    result[k.strip()] = v.strip().strip('"').strip("'")
        return result

    def _write_env(path: str, data: dict):
        with open(path, "w") as f:
            for k, v in data.items():
                f.write(f'{k}="{v}"\n')

    if action == "list":
        env = _parse_env(file)
        # Mask sensitive values
        masked = {k: ("***" if _is_sensitive_key(k) else v) for k, v in env.items()}
        return {"file": file, "vars": masked}

    if action == "get":
        env = _parse_env(file)
        val = env.get(key, "")
        return {"key": key, "value": "***" if _is_sensitive_key(key) else val}

    if action == "set":
        env = _parse_env(file)
        env[key] = value
        _write_env(file, env)
        log_val = "***" if _is_sensitive_key(key) else value
        logger.info(f"ENV SET: {key}={log_val} in {file!r}")
        return {"set": True, "key": key}

    if action == "delete":
        env = _parse_env(file)
        if key in env:
            del env[key]
            _write_env(file, env)
        return {"deleted": True, "key": key}

    return {"error": f"Unknown action: {action}"}
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_server_control_mcp.py`

```python
class TestServerControlMCP:
    # Server lifecycle
    def test_server_starts_without_error()
    def test_list_tools_returns_all_tools()
    def test_each_tool_callable_with_valid_args()
    def test_each_tool_handles_invalid_args_gracefully()

    # server_run
    def test_run_simple_echo_command()
    def test_run_refuses_root_execution()
    def test_run_irreversible_requires_confirmed_flag()
    def test_run_background_returns_pid()
    def test_run_timeout_returns_error_not_exception()

    # server_status
    def test_status_returns_required_keys()
    def test_status_cpu_is_float()

    # server_processes
    def test_processes_returns_list()
    def test_processes_filter_by_name()

    # server_kill
    def test_kill_by_name_requires_confirmation()
    def test_kill_nonexistent_returns_error()

    # server_read_file / server_write_file
    def test_read_existing_file()
    def test_read_nonexistent_returns_error()
    def test_write_creates_backup()
    def test_write_refuses_protected_path()

    # cron_manage
    def test_cron_add_list_remove_cycle()

    # env_manage
    def test_env_set_and_get()
    def test_env_sensitive_key_masked_in_list()
```

---

## CROSS-REFERENCES

- **Phase 6** (deploy/server.py) uses similar subprocess patterns — reuse `_is_irreversible` logic
- **Phase 11** (CronScheduler) uses `cron_manage("add", ...)` to register nanobot's own cron jobs
- **Phase 16** (CLI) `nanobot mcp test server_control` calls `list_tools()` on this server
- **MASTER_REFERENCE.md** has the canonical `config.json` `mcp_servers` key structure
