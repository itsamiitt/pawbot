# PHASE 6 — DEPLOYMENT PIPELINE MCP SERVER
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)  
> **Implementation Days:** Day 16 (6.1), Day 17 (6.2), Day 18 (6.3 + 6.4)  
> **Primary File:** `~/.nanobot/mcp-servers/deploy/server.py` (NEW)  
> **Test File:** `~/nanobot/tests/test_deploy_mcp.py`  
> **Config registration key:** `mcp_servers.deploy`  
> **Depends on:** Phase 5 patterns (subprocess execution style)

---

## BEFORE YOU START

```bash
mkdir -p ~/.nanobot/mcp-servers/deploy
# Check what's available on the server:
which pm2 docker certbot nginx git
```

Add to `~/.nanobot/config.json`:

```json
{
  "mcp_servers": {
    "deploy": {
      "path": "~/.nanobot/mcp-servers/deploy/server.py",
      "requires_confirmation": false,
      "enabled": true
    }
  }
}
```

---

## FEATURE 6.1 — DEPLOYMENT TOOL SERVER

### deploy_app

```python
#!/usr/bin/env python3
"""
Deployment Pipeline MCP Server
Registered as: mcp_servers.deploy in ~/.nanobot/config.json
"""
import subprocess
import os
import json
import time
import shutil
import logging
from pathlib import Path

logger = logging.getLogger("nanobot.mcp.deploy")

def _run(cmd: str, cwd: str = None, timeout: int = 120) -> dict:
    """Internal helper — run command, return structured result."""
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=cwd, timeout=timeout,
            capture_output=True, text=True
        )
        return {
            "ok": r.returncode == 0,
            "stdout": r.stdout[:3000],
            "stderr": r.stderr[:2000],
            "returncode": r.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timeout after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"ok": False, "error": str(e), "returncode": -1}


def deploy_app(app_name: str, repo_url: str = "", branch: str = "main",
               deploy_path: str = "", build_command: str = "",
               start_command: str = "", env_file: str = "",
               use_pm2: bool = True) -> dict:
    """
    Full deployment sequence. Returns step-by-step result summary.
    Steps execute in exact order — halt on critical failure.
    """
    deploy_path = os.path.expanduser(deploy_path)
    steps = []

    def step(name: str, fn) -> dict:
        start = time.time()
        result = fn()
        elapsed = round(time.time() - start, 2)
        status = "✓" if result.get("ok", True) else "✗"
        entry = {"step": name, "status": status, "elapsed_s": elapsed, **result}
        steps.append(entry)
        logger.info(f"Deploy [{app_name}] {status} {name} ({elapsed}s)")
        return entry

    # ── Step 1: Create rollback snapshot ─────────────────────────────
    rollback_file = f"/tmp/.rollback_{app_name}"
    def _snapshot():
        if os.path.exists(deploy_path):
            r = _run("git rev-parse HEAD", cwd=deploy_path)
            if r["ok"]:
                with open(rollback_file, "w") as f:
                    f.write(r["stdout"].strip())
                return {"ok": True, "hash": r["stdout"].strip()}
        return {"ok": True, "hash": "none"}
    step("snapshot", _snapshot)

    # ── Step 2: Pull / Clone ──────────────────────────────────────────
    def _pull():
        if not os.path.exists(deploy_path):
            if repo_url:
                return _run(f"git clone {repo_url} {deploy_path}")
            return {"ok": False, "error": "deploy_path doesn't exist and no repo_url provided"}
        if repo_url:
            return _run(f"git pull origin {branch}", cwd=deploy_path)
        return {"ok": True, "skipped": True}
    pull_result = step("git_pull", _pull)
    if not pull_result.get("ok"):
        return {"app": app_name, "success": False, "steps": steps, "halted_at": "git_pull"}

    # ── Step 3: Copy env file ─────────────────────────────────────────
    def _env():
        if env_file and os.path.exists(os.path.expanduser(env_file)):
            shutil.copy2(os.path.expanduser(env_file), os.path.join(deploy_path, ".env"))
            return {"ok": True}
        return {"ok": True, "skipped": True}
    step("env_file", _env)

    # ── Step 4 + 5: Auto-detect project type and install deps ─────────
    def _detect_and_install():
        pj = os.path.join(deploy_path, "package.json")
        req = os.path.join(deploy_path, "requirements.txt")
        gomod = os.path.join(deploy_path, "go.mod")
        cargo = os.path.join(deploy_path, "Cargo.toml")

        if os.path.exists(pj):
            # Node.js: prefer pnpm if lockfile exists
            if os.path.exists(os.path.join(deploy_path, "pnpm-lock.yaml")):
                return _run("pnpm install", cwd=deploy_path, timeout=300)
            return _run("npm ci", cwd=deploy_path, timeout=300)
        elif os.path.exists(req):
            return _run("pip install -r requirements.txt", cwd=deploy_path, timeout=300)
        elif os.path.exists(gomod):
            return _run("go mod download", cwd=deploy_path, timeout=300)
        elif os.path.exists(cargo):
            return _run("cargo build --release", cwd=deploy_path, timeout=600)
        return {"ok": True, "skipped": True, "reason": "No recognized project type"}

    install_result = step("install_deps", _detect_and_install)
    if not install_result.get("ok"):
        return {"app": app_name, "success": False, "steps": steps, "halted_at": "install_deps"}

    # ── Step 6: Build ─────────────────────────────────────────────────
    def _build():
        if build_command:
            return _run(build_command, cwd=deploy_path, timeout=600)
        return {"ok": True, "skipped": True}
    step("build", _build)

    # ── Step 7: Database migrations ───────────────────────────────────
    def _migrate():
        return run_migrations(deploy_path)
    migration_result = step("migrations", _migrate)
    if not migration_result.get("ok"):
        # Migration failure = automatic rollback
        logger.error(f"Deploy [{app_name}] migrations failed — triggering rollback")
        deploy_rollback(app_name, deploy_path)
        return {"app": app_name, "success": False, "steps": steps,
                "halted_at": "migrations", "rollback": "triggered"}

    # ── Step 8: Start/restart with PM2 ───────────────────────────────
    def _pm2():
        if not use_pm2:
            return {"ok": True, "skipped": True}
        # Check if app already in PM2
        check = _run(f"pm2 show {app_name}")
        if check["ok"]:
            return _run(f"pm2 reload {app_name}")
        else:
            if not start_command:
                return {"ok": False, "error": "start_command required for new PM2 app"}
            r = _run(f"pm2 start {start_command} --name {app_name}", cwd=deploy_path)
            if r["ok"]:
                _run("pm2 save")
            return r
    pm2_result = step("pm2_start", _pm2)

    # ── Step 9: Health check ──────────────────────────────────────────
    def _health():
        if not use_pm2:
            return {"ok": True, "skipped": True}
        time.sleep(10)
        r = _run(f"pm2 show {app_name}")
        online = "online" in r.get("stdout", "")
        return {"ok": online, "pm2_output": r.get("stdout", "")[:500]}
    step("health_check", _health)

    success = all(s["status"] == "✓" for s in steps if not s.get("skipped"))
    return {"app": app_name, "success": success, "steps": steps}
```

### deploy_docker

```python
def deploy_docker(app_name: str, dockerfile_path: str = ".",
                  port_mapping: str = "", env_file: str = "",
                  restart_policy: str = "unless-stopped",
                  build_args: dict = None) -> dict:
    """Docker build and run workflow."""
    steps = []

    def step(name, cmd, cwd=None):
        r = _run(cmd, cwd=cwd, timeout=300)
        steps.append({"step": name, "ok": r["ok"], "output": r.get("stdout", "")[:500]})
        return r

    # Build
    build_args_str = " ".join(f"--build-arg {k}={v}" for k, v in (build_args or {}).items())
    build_r = step("docker_build",
                   f"docker build -t {app_name}:latest {build_args_str} {dockerfile_path}")
    if not build_r["ok"]:
        return {"app": app_name, "success": False, "steps": steps,
                "build_log": build_r.get("stderr", "")}

    # Stop existing
    step("docker_stop", f"docker stop {app_name} && docker rm {app_name}")

    # Start new container
    port_flag = f"-p {port_mapping}" if port_mapping else ""
    env_flag = f"--env-file {os.path.expanduser(env_file)}" if env_file else ""
    start_r = step("docker_run",
                   f"docker run -d --name {app_name} "
                   f"--restart {restart_policy} {port_flag} {env_flag} {app_name}:latest")

    time.sleep(5)
    inspect_r = step("docker_inspect", f"docker inspect {app_name}")

    running = False
    container_id = ""
    if inspect_r["ok"]:
        try:
            data = json.loads(inspect_r["stdout"])
            running = data[0]["State"]["Running"]
            container_id = data[0]["Id"][:12]
        except Exception:
            pass

    return {"app": app_name, "success": running,
            "container_id": container_id, "steps": steps}
```

### deploy_ssl

```python
def deploy_ssl(domain: str, email: str) -> dict:
    """Request Let's Encrypt SSL certificate via certbot."""
    # Check certbot installed
    r = _run("which certbot")
    if not r["ok"]:
        return {"error": "certbot not installed. Run: apt install certbot python3-certbot-nginx"}

    # Try nginx mode first
    nginx_r = _run(
        f"certbot --nginx -d {domain} -d www.{domain} "
        f"--email {email} --agree-tos --non-interactive",
        timeout=120
    )

    if not nginx_r["ok"]:
        # Fallback: standalone mode
        nginx_r = _run(
            f"certbot certonly --standalone -d {domain} -d www.{domain} "
            f"--email {email} --agree-tos --non-interactive",
            timeout=120
        )

    if not nginx_r["ok"]:
        return {"error": "certbot failed", "output": nginx_r.get("stderr", "")}

    # Verify certificate
    verify_r = _run(f"certbot certificates 2>&1 | grep -A5 {domain}")
    return {
        "success": True,
        "domain": domain,
        "certificate_info": verify_r.get("stdout", ""),
    }
```

### deploy_rollback

```python
def deploy_rollback(app_name: str, deploy_path: str) -> dict:
    """Rollback app to previous git hash. Re-installs deps and restarts."""
    deploy_path = os.path.expanduser(deploy_path)
    rollback_file = f"/tmp/.rollback_{app_name}"

    if not os.path.exists(rollback_file):
        return {"error": f"No rollback snapshot found for {app_name}"}

    with open(rollback_file) as f:
        prev_hash = f.read().strip()

    if prev_hash == "none":
        return {"error": "No git hash recorded in snapshot"}

    steps = []

    r = _run(f"git checkout {prev_hash}", cwd=deploy_path)
    steps.append({"step": "git_checkout", "ok": r["ok"]})
    if not r["ok"]:
        return {"success": False, "steps": steps, "error": r.get("stderr")}

    # Re-install deps
    install = deploy_app.__wrapped__ if hasattr(deploy_app, "__wrapped__") else None
    r2 = _run("npm ci || pip install -r requirements.txt 2>/dev/null || true",
              cwd=deploy_path, timeout=300)
    steps.append({"step": "reinstall_deps", "ok": True})  # best-effort

    # Restart PM2
    r3 = _run(f"pm2 reload {app_name}")
    steps.append({"step": "pm2_reload", "ok": r3["ok"]})

    # Health check
    time.sleep(5)
    r4 = _run(f"pm2 show {app_name}")
    online = "online" in r4.get("stdout", "")
    steps.append({"step": "health_check", "ok": online})

    logger.info(f"ROLLBACK: {app_name} → {prev_hash[:8]}")
    return {"success": online, "hash": prev_hash[:8], "steps": steps}
```

### deploy_status

```python
def deploy_status() -> dict:
    """Status of all PM2 processes and Docker containers."""
    # PM2
    pm2_r = _run("pm2 jlist")
    pm2_apps = []
    if pm2_r["ok"]:
        try:
            apps = json.loads(pm2_r["stdout"])
            for app in apps:
                pm2_apps.append({
                    "name": app.get("name"),
                    "status": app.get("pm2_env", {}).get("status"),
                    "cpu": app.get("monit", {}).get("cpu"),
                    "memory_mb": round((app.get("monit", {}).get("memory", 0)) / 1e6, 1),
                    "restarts": app.get("pm2_env", {}).get("restart_time"),
                    "uptime": app.get("pm2_env", {}).get("pm_uptime"),
                })
        except Exception:
            pass

    # Docker
    docker_r = _run(
        'docker ps --format \'{"name":"{{.Names}}","image":"{{.Image}}",'
        '"status":"{{.Status}}","ports":"{{.Ports}}"}\''
    )
    docker_containers = []
    if docker_r["ok"]:
        for line in docker_r["stdout"].splitlines():
            try:
                docker_containers.append(json.loads(line))
            except Exception:
                pass

    return {"pm2": pm2_apps, "docker": docker_containers}
```

### deploy_logs

```python
def deploy_logs(app_name: str, lines: int = 100) -> dict:
    """Returns last N lines of PM2 or Docker logs."""
    # Try PM2 first
    r = _run(f"pm2 logs {app_name} --lines {lines} --nostream")
    if r["ok"] and r["stdout"]:
        return {"source": "pm2", "app": app_name, "logs": r["stdout"][-5000:]}

    # Try Docker
    r2 = _run(f"docker logs {app_name} --tail {lines}")
    if r2["ok"]:
        return {"source": "docker", "app": app_name, "logs": r2["stdout"][-5000:]}

    return {"error": f"Could not get logs for {app_name}"}
```

---

## FEATURE 6.2 — NGINX CONFIG GENERATOR

```python
def nginx_generate_config(domain: str, app_port: int,
                           ssl: bool = False, static_path: str = "",
                           websocket: bool = False) -> dict:
    """Generate complete nginx server block. Returns config string."""

    ws_headers = """
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";""" if websocket else ""

    static_block = f"""
    location /static/ {{
        alias {static_path};
        expires 30d;
    }}""" if static_path else ""

    ssl_redirect = f"""
server {{
    listen 80;
    server_name {domain} www.{domain};
    return 301 https://$host$request_uri;
}}""" if ssl else ""

    ssl_config = f"""
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    add_header Strict-Transport-Security "max-age=31536000" always;""" if ssl else """
    listen 80;"""

    config = f"""{ssl_redirect}

server {{{ssl_config}
    server_name {domain} www.{domain};

    # Security headers
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;

    # Gzip
    gzip on;
    gzip_types text/plain application/json application/javascript text/css;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone={domain.replace('.','_')}:10m rate=10r/s;
    limit_req zone={domain.replace('.','_')} burst=20 nodelay;
{static_block}
    location / {{
        proxy_pass http://127.0.0.1:{app_port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;{ws_headers}
    }}
}}"""

    return {"config": config, "domain": domain, "port": app_port}
```

---

## FEATURE 6.3 — DATABASE MIGRATION DETECTION

```python
def run_migrations(deploy_path: str) -> dict:
    """
    Auto-detect and run database migrations.
    Always run with 60s timeout.
    Returns ok=True if no migrations found (not an error).
    """
    deploy_path = os.path.expanduser(deploy_path)

    MIGRATION_DETECTORS = [
        ("prisma", "prisma/schema.prisma",    "npx prisma migrate deploy"),
        ("alembic", "alembic.ini",             "alembic upgrade head"),
        ("rails",  "db/migrate",               "rails db:migrate"),
        ("flyway",  "flyway.conf",              "flyway migrate"),
        ("liquibase", "liquibase.properties",  "liquibase update"),
        ("knex",   "knexfile.js",              "npx knex migrate:latest"),
    ]

    for (tool, indicator, command) in MIGRATION_DETECTORS:
        indicator_path = os.path.join(deploy_path, indicator)
        if os.path.exists(indicator_path):
            logger.info(f"Migration: detected {tool}, running: {command}")
            r = _run(command, cwd=deploy_path, timeout=60)
            if not r["ok"]:
                logger.error(f"Migration FAILED: {tool}: {r.get('stderr', '')[:500]}")
                return {"ok": False, "tool": tool, "error": r.get("stderr", "")[:500]}
            logger.info(f"Migration: {tool} completed successfully")
            return {"ok": True, "tool": tool, "output": r.get("stdout", "")[:500]}

    return {"ok": True, "skipped": True, "reason": "No migration tool detected"}
```

---

## FEATURE 6.4 — DATABASE BACKUP TOOL

```python
def db_backup(db_type: str, db_name: str, db_host: str = "localhost",
              db_user: str = "", output_path: str = "") -> dict:
    """
    Backup database. Compresses output. Auto-cleans backups > 7 days old.
    Supported: postgres, mysql, sqlite, mongodb
    """
    if not output_path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.expanduser(f"~/.nanobot/backups/{db_type}")
        os.makedirs(backup_dir, exist_ok=True)
        output_path = os.path.join(backup_dir, f"{db_name}_{ts}.sql")

    output_path = os.path.expanduser(output_path)

    BACKUP_COMMANDS = {
        "postgres": (f"pg_dump -h {db_host} -U {db_user} {db_name} > {output_path}"),
        "mysql":    (f"mysqldump -h {db_host} -u {db_user} {db_name} > {output_path}"),
        "sqlite":   (f"cp {db_name} {output_path}"),
        "mongodb":  (f"mongodump --db {db_name} --out {output_path}"),
    }

    cmd = BACKUP_COMMANDS.get(db_type)
    if not cmd:
        return {"error": f"Unsupported db_type: {db_type}. Use: postgres, mysql, sqlite, mongodb"}

    start = time.time()
    r = _run(cmd, timeout=300)
    elapsed = round(time.time() - start, 2)

    if not r["ok"]:
        return {"error": "Backup failed", "details": r.get("stderr", "")[:500]}

    # Compress
    _run(f"gzip -f {output_path}")
    gz_path = f"{output_path}.gz"

    # Verify size
    size_kb = 0
    if os.path.exists(gz_path):
        size_kb = round(os.path.getsize(gz_path) / 1024, 1)

    if size_kb == 0:
        return {"error": "Backup file is empty — possible backup failure"}

    # Auto-cleanup backups older than 7 days
    backup_dir = os.path.dirname(output_path)
    cutoff = time.time() - (7 * 86400)
    cleaned = 0
    for f in Path(backup_dir).glob("*.gz"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            cleaned += 1

    logger.info(f"DB backup: {db_name} ({db_type}) → {gz_path} [{size_kb}KB] in {elapsed}s")
    return {
        "success": True, "path": gz_path, "size_kb": size_kb,
        "elapsed_s": elapsed, "cleaned_old_backups": cleaned,
    }
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_deploy_mcp.py`

```python
class TestDeployMCP:
    def test_server_starts_without_error()
    def test_list_tools_returns_all_tools()

class TestDeployApp:
    def test_rollback_snapshot_created()
    def test_git_clone_if_path_not_exists()
    def test_nodejs_install_uses_pnpm_when_lockfile_present()
    def test_migration_failure_triggers_rollback()
    def test_pm2_reload_if_app_exists()
    def test_step_summary_returned()

class TestDeployDocker:
    def test_build_failure_halts_early()
    def test_inspect_after_start()

class TestNginxGenerator:
    def test_ssl_redirect_block_present_when_ssl_true()
    def test_websocket_headers_present()
    def test_rate_limiting_included()

class TestMigrations:
    def test_prisma_detected_by_schema_file()
    def test_alembic_detected_by_ini_file()
    def test_no_migration_returns_ok_skipped()
    def test_migration_failure_returns_ok_false()

class TestDbBackup:
    def test_unknown_db_type_returns_error()
    def test_sqlite_backup_creates_gz_file()
    def test_empty_backup_detected()
    def test_old_backups_cleaned()
```

---

## CROSS-REFERENCES

- **Phase 5** (server_control): uses same `_run()` helper pattern — both servers are standalone, they do NOT import from each other
- **Phase 6.3** migrations: called by `deploy_app()` as Step 7 — it's a helper in the same file, not a separate MCP tool
- **Phase 11** (CronScheduler): `db_backup()` can be called via cron — register via `cron_manage("add", "nightly_backup", "0 2 * * *", "...")`
- **Phase 16** (CLI): `nanobot mcp test deploy` validates this server starts and all tools list correctly

All canonical paths in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
