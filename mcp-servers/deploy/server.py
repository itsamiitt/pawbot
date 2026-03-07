#!/usr/bin/env python3
"""Deployment Pipeline MCP server.

Registered as: mcp_servers.deploy in ~/.pawbot/config.json
Path: ~/.pawbot/mcp-servers/deploy/server.py
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


def _configure_logger() -> logging.Logger:
    log_path = Path.home() / ".pawbot" / "logs" / "pawbot.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pawbot.mcp.deploy")
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
mcp = FastMCP(name="deploy")

TOOL_NAMES = [
    "deploy_app",
    "deploy_docker",
    "deploy_ssl",
    "deploy_rollback",
    "deploy_status",
    "deploy_logs",
    "nginx_generate_config",
    "db_backup",
]


def _truncate(text: str | None, limit: int) -> str:
    return (text or "")[:limit]


def _rollback_file(app_name: str) -> str:
    return os.path.join(tempfile.gettempdir(), f".rollback_{app_name}")


def _run(cmd: str | list[str], cwd: str | None = None, timeout: int = 120) -> dict[str, Any]:
    """Run a command and return a structured result."""
    argv = cmd if isinstance(cmd, list) else shlex.split(cmd)
    try:
        result = subprocess.run(
            argv,
            shell=False,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": _truncate(result.stdout, 3000),
            "stderr": _truncate(result.stderr, 2000),
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timeout after {timeout}s", "returncode": -1}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "returncode": -1}


def list_tools() -> dict[str, Any]:
    """Return an explicit tool inventory for diagnostics and tests."""
    return {"tools": TOOL_NAMES.copy(), "count": len(TOOL_NAMES)}


def run_migrations(deploy_path: str) -> dict[str, Any]:
    """Detect and run database migrations for known frameworks."""
    deploy_root = os.path.expanduser(deploy_path)
    detectors = [
        ("prisma", "prisma/schema.prisma", "npx prisma migrate deploy"),
        ("alembic", "alembic.ini", "alembic upgrade head"),
        ("rails", "db/migrate", "rails db:migrate"),
        ("flyway", "flyway.conf", "flyway migrate"),
        ("liquibase", "liquibase.properties", "liquibase update"),
        ("knex", "knexfile.js", "npx knex migrate:latest"),
    ]

    for tool, indicator, command in detectors:
        indicator_path = os.path.join(deploy_root, indicator)
        if not os.path.exists(indicator_path):
            continue
        logger.info("Migration detected: %s -> %s", tool, command)
        result = _run(command, cwd=deploy_root, timeout=60)
        if not result.get("ok"):
            logger.error(
                "Migration failed for %s: %s",
                tool,
                _truncate(result.get("stderr") or result.get("error"), 500),
            )
            return {
                "ok": False,
                "tool": tool,
                "error": _truncate(result.get("stderr") or result.get("error"), 500),
            }
        return {"ok": True, "tool": tool, "output": _truncate(result.get("stdout"), 500)}

    return {"ok": True, "skipped": True, "reason": "No migration tool detected"}


@mcp.tool()
def deploy_app(
    app_name: str,
    repo_url: str = "",
    branch: str = "main",
    deploy_path: str = "",
    build_command: str = "",
    start_command: str = "",
    env_file: str = "",
    use_pm2: bool = True,
) -> dict[str, Any]:
    """Run a full deployment sequence and return a step-by-step summary."""
    deploy_root = os.path.expanduser(deploy_path) if deploy_path else os.path.expanduser(f"~/{app_name}")
    steps: list[dict[str, Any]] = []
    rollback_file = _rollback_file(app_name)

    def step(name: str, fn) -> dict[str, Any]:
        started = time.time()
        result = fn()
        elapsed = round(time.time() - started, 2)
        ok = bool(result.get("ok", True))
        entry = {"step": name, "status": "ok" if ok else "error", "elapsed_s": elapsed, **result}
        steps.append(entry)
        logger.info("Deploy [%s] %s %s (%ss)", app_name, entry["status"], name, elapsed)
        return entry

    def _snapshot() -> dict[str, Any]:
        if os.path.exists(deploy_root):
            result = _run("git rev-parse HEAD", cwd=deploy_root)
            if result.get("ok"):
                commit_hash = (result.get("stdout") or "").strip()
                with open(rollback_file, "w", encoding="utf-8") as handle:
                    handle.write(commit_hash)
                return {"ok": True, "hash": commit_hash}
        return {"ok": True, "hash": "none"}

    step("snapshot", _snapshot)

    def _pull() -> dict[str, Any]:
        if not os.path.exists(deploy_root):
            if repo_url:
                return _run(f"git clone {repo_url} {deploy_root}", timeout=300)
            return {"ok": False, "error": "deploy_path does not exist and no repo_url provided"}
        if repo_url:
            return _run(f"git pull origin {branch}", cwd=deploy_root, timeout=180)
        return {"ok": True, "skipped": True}

    pull_result = step("git_pull", _pull)
    if not pull_result.get("ok"):
        return {"app": app_name, "success": False, "steps": steps, "halted_at": "git_pull"}

    def _copy_env() -> dict[str, Any]:
        if not env_file:
            return {"ok": True, "skipped": True}
        source = os.path.expanduser(env_file)
        if not os.path.exists(source):
            return {"ok": True, "skipped": True}
        os.makedirs(deploy_root, exist_ok=True)
        shutil.copy2(source, os.path.join(deploy_root, ".env"))
        return {"ok": True}

    step("env_file", _copy_env)

    def _detect_and_install() -> dict[str, Any]:
        package_json = os.path.join(deploy_root, "package.json")
        requirements = os.path.join(deploy_root, "requirements.txt")
        gomod = os.path.join(deploy_root, "go.mod")
        cargo = os.path.join(deploy_root, "Cargo.toml")

        if os.path.exists(package_json):
            if os.path.exists(os.path.join(deploy_root, "pnpm-lock.yaml")):
                return _run("pnpm install", cwd=deploy_root, timeout=300)
            return _run("npm ci", cwd=deploy_root, timeout=300)
        if os.path.exists(requirements):
            return _run("pip install -r requirements.txt", cwd=deploy_root, timeout=300)
        if os.path.exists(gomod):
            return _run("go mod download", cwd=deploy_root, timeout=300)
        if os.path.exists(cargo):
            return _run("cargo build --release", cwd=deploy_root, timeout=600)
        return {"ok": True, "skipped": True, "reason": "No recognized project type"}

    install_result = step("install_deps", _detect_and_install)
    if not install_result.get("ok"):
        return {"app": app_name, "success": False, "steps": steps, "halted_at": "install_deps"}

    def _build() -> dict[str, Any]:
        if build_command:
            return _run(build_command, cwd=deploy_root, timeout=600)
        return {"ok": True, "skipped": True}

    step("build", _build)

    migration_result = step("migrations", lambda: run_migrations(deploy_root))
    if not migration_result.get("ok"):
        logger.error("Deploy [%s] migrations failed. Triggering rollback.", app_name)
        rollback = deploy_rollback(app_name, deploy_root)
        return {
            "app": app_name,
            "success": False,
            "steps": steps,
            "halted_at": "migrations",
            "rollback": "triggered",
            "rollback_result": rollback,
        }

    def _pm2() -> dict[str, Any]:
        if not use_pm2:
            return {"ok": True, "skipped": True}
        check = _run(f"pm2 show {app_name}")
        if check.get("ok"):
            return _run(f"pm2 reload {app_name}")
        if not start_command:
            return {"ok": False, "error": "start_command required for new PM2 app"}
        started = _run(f"pm2 start {start_command} --name {app_name}", cwd=deploy_root, timeout=180)
        if started.get("ok"):
            _run("pm2 save")
        return started

    step("pm2_start", _pm2)

    def _health() -> dict[str, Any]:
        if not use_pm2:
            return {"ok": True, "skipped": True}
        time.sleep(10)
        result = _run(f"pm2 show {app_name}")
        output = (result.get("stdout", "") + result.get("stderr", "")).lower()
        online = result.get("ok") and "online" in output
        return {"ok": bool(online), "pm2_output": _truncate(output, 500)}

    step("health_check", _health)

    success = all(
        entry.get("ok", True)
        for entry in steps
        if not entry.get("skipped")
    )
    return {"app": app_name, "success": bool(success), "steps": steps}


@mcp.tool()
def deploy_docker(
    app_name: str,
    dockerfile_path: str = ".",
    port_mapping: str = "",
    env_file: str = "",
    restart_policy: str = "unless-stopped",
    build_args: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build and run a Docker deployment."""
    steps: list[dict[str, Any]] = []

    def step(name: str, cmd: str, cwd: str | None = None) -> dict[str, Any]:
        result = _run(cmd, cwd=cwd, timeout=300)
        output = _truncate((result.get("stdout", "") + result.get("stderr", "")), 500)
        steps.append({"step": name, "ok": bool(result.get("ok")), "output": output})
        return result

    args = " ".join(f"--build-arg {k}={v}" for k, v in (build_args or {}).items())
    build_cmd = f"docker build -t {app_name}:latest {args} {dockerfile_path}".strip()
    build_result = step("docker_build", build_cmd)
    if not build_result.get("ok"):
        return {
            "app": app_name,
            "success": False,
            "steps": steps,
            "build_log": build_result.get("stderr") or build_result.get("error", ""),
        }

    step("docker_stop", f"docker stop {app_name} && docker rm {app_name}")

    port_flag = f"-p {port_mapping}" if port_mapping else ""
    env_flag = f"--env-file {os.path.expanduser(env_file)}" if env_file else ""
    start_cmd = (
        f"docker run -d --name {app_name} "
        f"--restart {restart_policy} {port_flag} {env_flag} {app_name}:latest"
    ).strip()
    step("docker_run", start_cmd)

    time.sleep(5)
    inspect_result = step("docker_inspect", f"docker inspect {app_name}")

    running = False
    container_id = ""
    if inspect_result.get("ok"):
        try:
            inspect_data = json.loads(inspect_result.get("stdout", "") or "[]")
            if inspect_data and isinstance(inspect_data, list):
                state = inspect_data[0].get("State", {})
                running = bool(state.get("Running", False))
                container_id = (inspect_data[0].get("Id", "") or "")[:12]
        except Exception:
            pass

    return {"app": app_name, "success": running, "container_id": container_id, "steps": steps}


@mcp.tool()
def deploy_ssl(domain: str, email: str) -> dict[str, Any]:
    """Request a Let's Encrypt certificate via certbot."""
    if not shutil.which("certbot"):
        check = _run("which certbot")
        if not check.get("ok"):
            return {"error": "certbot not installed. Run: apt install certbot python3-certbot-nginx"}

    primary = _run(
        (
            f"certbot --nginx -d {domain} -d www.{domain} "
            f"--email {email} --agree-tos --non-interactive"
        ),
        timeout=120,
    )
    if not primary.get("ok"):
        primary = _run(
            (
                f"certbot certonly --standalone -d {domain} -d www.{domain} "
                f"--email {email} --agree-tos --non-interactive"
            ),
            timeout=120,
        )
    if not primary.get("ok"):
        return {"error": "certbot failed", "output": primary.get("stderr") or primary.get("error", "")}

    verify = _run("certbot certificates", timeout=20)
    cert_output = verify.get("stdout", "")
    filtered = [line for line in cert_output.splitlines() if domain in line]
    certificate_info = "\n".join(filtered) if filtered else cert_output
    return {"success": True, "domain": domain, "certificate_info": _truncate(certificate_info, 2000)}


@mcp.tool()
def deploy_rollback(app_name: str, deploy_path: str) -> dict[str, Any]:
    """Rollback app to a previously snapshotted git hash and restart PM2."""
    deploy_root = os.path.expanduser(deploy_path) if deploy_path else os.path.expanduser(f"~/{app_name}")
    rollback_file = _rollback_file(app_name)

    if not os.path.exists(rollback_file):
        return {"error": f"No rollback snapshot found for {app_name}"}

    with open(rollback_file, encoding="utf-8") as handle:
        prev_hash = handle.read().strip()
    if prev_hash == "none":
        return {"error": "No git hash recorded in snapshot"}

    steps: list[dict[str, Any]] = []

    checkout = _run(f"git checkout {prev_hash}", cwd=deploy_root)
    steps.append({"step": "git_checkout", "ok": bool(checkout.get("ok"))})
    if not checkout.get("ok"):
        return {"success": False, "steps": steps, "error": checkout.get("stderr") or checkout.get("error", "")}

    _run(
        "npm ci || pip install -r requirements.txt 2>/dev/null || true",
        cwd=deploy_root,
        timeout=300,
    )
    steps.append({"step": "reinstall_deps", "ok": True})

    reload_result = _run(f"pm2 reload {app_name}")
    steps.append({"step": "pm2_reload", "ok": bool(reload_result.get("ok"))})

    time.sleep(5)
    health = _run(f"pm2 show {app_name}")
    online = "online" in (health.get("stdout", "") + health.get("stderr", "")).lower()
    steps.append({"step": "health_check", "ok": bool(online)})

    logger.info("ROLLBACK: %s -> %s", app_name, prev_hash[:8])
    return {"success": bool(online), "hash": prev_hash[:8], "steps": steps}


@mcp.tool()
def deploy_status() -> dict[str, Any]:
    """Return status for PM2 apps and Docker containers."""
    pm2_result = _run("pm2 jlist")
    pm2_apps: list[dict[str, Any]] = []
    if pm2_result.get("ok"):
        try:
            apps = json.loads(pm2_result.get("stdout") or "[]")
            for app in apps if isinstance(apps, list) else []:
                memory_bytes = app.get("monit", {}).get("memory", 0) or 0
                pm2_apps.append(
                    {
                        "name": app.get("name"),
                        "status": app.get("pm2_env", {}).get("status"),
                        "cpu": app.get("monit", {}).get("cpu"),
                        "memory_mb": round(memory_bytes / 1e6, 1),
                        "restarts": app.get("pm2_env", {}).get("restart_time"),
                        "uptime": app.get("pm2_env", {}).get("pm_uptime"),
                    }
                )
        except Exception:
            pass

    docker_result = _run(
        'docker ps --format \'{"name":"{{.Names}}","image":"{{.Image}}","status":"{{.Status}}","ports":"{{.Ports}}"}\''
    )
    docker_containers: list[dict[str, Any]] = []
    if docker_result.get("ok"):
        for line in (docker_result.get("stdout") or "").splitlines():
            try:
                docker_containers.append(json.loads(line))
            except Exception:
                continue

    return {"pm2": pm2_apps, "docker": docker_containers}


@mcp.tool()
def deploy_logs(app_name: str, lines: int = 100) -> dict[str, Any]:
    """Return recent PM2 or Docker logs for an app."""
    pm2_result = _run(f"pm2 logs {app_name} --lines {lines} --nostream", timeout=120)
    pm2_output = (pm2_result.get("stdout", "") + pm2_result.get("stderr", "")).strip()
    if pm2_result.get("ok") and pm2_output:
        return {"source": "pm2", "app": app_name, "logs": pm2_output[-5000:]}

    docker_result = _run(f"docker logs {app_name} --tail {lines}", timeout=120)
    docker_output = (docker_result.get("stdout", "") + docker_result.get("stderr", "")).strip()
    if docker_result.get("ok") and docker_output:
        return {"source": "docker", "app": app_name, "logs": docker_output[-5000:]}

    return {"error": f"Could not get logs for {app_name}"}


@mcp.tool()
def nginx_generate_config(
    domain: str,
    app_port: int,
    ssl: bool = False,
    static_path: str = "",
    websocket: bool = False,
) -> dict[str, Any]:
    """Generate an nginx server block for reverse proxy deployment."""
    ws_headers = ""
    if websocket:
        ws_headers = (
            "\n        proxy_http_version 1.1;"
            "\n        proxy_set_header Upgrade $http_upgrade;"
            '\n        proxy_set_header Connection "upgrade";'
        )

    static_block = ""
    if static_path:
        static_block = (
            "\n    location /static/ {\n"
            f"        alias {static_path};\n"
            "        expires 30d;\n"
            "    }\n"
        )

    ssl_redirect = ""
    if ssl:
        ssl_redirect = (
            "\nserver {\n"
            "    listen 80;\n"
            f"    server_name {domain} www.{domain};\n"
            "    return 301 https://$host$request_uri;\n"
            "}\n"
        )

    ssl_config = (
        "\n    listen 443 ssl;\n"
        f"    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;\n"
        f"    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;\n"
        '    add_header Strict-Transport-Security "max-age=31536000" always;'
        if ssl
        else "\n    listen 80;"
    )

    zone = "".join(c if c.isalnum() else "_" for c in domain)
    config = (
        f"{ssl_redirect}\n"
        f"server {{{ssl_config}\n"
        f"    server_name {domain} www.{domain};\n\n"
        "    # Security headers\n"
        "    add_header X-Frame-Options SAMEORIGIN;\n"
        "    add_header X-Content-Type-Options nosniff;\n\n"
        "    # Gzip\n"
        "    gzip on;\n"
        "    gzip_types text/plain application/json application/javascript text/css;\n\n"
        "    # Rate limiting\n"
        f"    limit_req_zone $binary_remote_addr zone={zone}:10m rate=10r/s;\n"
        f"    limit_req zone={zone} burst=20 nodelay;"
        f"{static_block}"
        "    location / {\n"
        f"        proxy_pass http://127.0.0.1:{app_port};\n"
        "        proxy_set_header Host $host;\n"
        "        proxy_set_header X-Real-IP $remote_addr;\n"
        f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;{ws_headers}\n"
        "    }\n"
        "}\n"
    )
    return {"config": config, "domain": domain, "port": app_port}


@mcp.tool()
def db_backup(
    db_type: str,
    db_name: str,
    db_host: str = "localhost",
    db_user: str = "",
    output_path: str = "",
) -> dict[str, Any]:
    """Create a database backup, compress it, and prune old archives."""
    if not output_path:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.expanduser(f"~/.pawbot/backups/{db_type}")
        os.makedirs(backup_dir, exist_ok=True)
        name_part = Path(db_name).name if db_type == "sqlite" else db_name
        output_path = os.path.join(backup_dir, f"{name_part}_{timestamp}.sql")

    target = os.path.expanduser(output_path)
    commands = {
        "postgres": f"pg_dump -h {db_host} -U {db_user} {db_name} > {target}",
        "mysql": f"mysqldump -h {db_host} -u {db_user} {db_name} > {target}",
        "sqlite": f"cp {db_name} {target}",
        "mongodb": f"mongodump --db {db_name} --out {target}",
    }
    command = commands.get(db_type)
    if not command:
        return {
            "error": (
                "Unsupported db_type: "
                f"{db_type}. Use: postgres, mysql, sqlite, mongodb"
            )
        }

    started = time.time()
    result = _run(command, timeout=300)
    elapsed = round(time.time() - started, 2)
    if not result.get("ok"):
        return {"error": "Backup failed", "details": _truncate(result.get("stderr") or result.get("error"), 500)}

    gz_path = f"{target}.gz"
    if os.path.isdir(target):
        archive = shutil.make_archive(target, "gztar", root_dir=target)
        gz_path = archive
    else:
        _run(f"gzip -f {target}")

    size_bytes = 0
    size_kb = 0.0
    if os.path.exists(gz_path):
        size_bytes = os.path.getsize(gz_path)
        size_kb = round(size_bytes / 1024, 1)
    if size_bytes == 0:
        return {"error": "Backup file is empty - possible backup failure"}

    backup_dir = os.path.dirname(target) or "."
    cutoff = time.time() - (7 * 86400)
    cleaned = 0
    for file in Path(backup_dir).glob("*.gz"):
        try:
            if file.stat().st_mtime < cutoff:
                file.unlink()
                cleaned += 1
        except Exception:
            continue

    logger.info(
        "DB backup: %s (%s) -> %s [%sKB] in %ss",
        db_name,
        db_type,
        gz_path,
        size_kb,
        elapsed,
    )
    return {
        "success": True,
        "path": gz_path,
        "size_kb": size_kb,
        "elapsed_s": elapsed,
        "cleaned_old_backups": cleaned,
    }


def main() -> None:
    """Run the MCP server via stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
