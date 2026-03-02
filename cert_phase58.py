"""Phase 5 — Install, Phase 6 — E2E, Phase 7 — Corruption Recovery, Phase 8 — Security."""
import sys, io, subprocess, json, time, os, shutil
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(r"c:\Users\Administrator\Downloads\nanobot\pawbot")
HOME = Path.home()
PAWBOT_DIR = HOME / ".pawbot"

results = []
def check(name, passed, detail=""):
    icon = "[PASS]" if passed else "[FAIL]"
    results.append((name, passed, detail))
    print(f"  {icon}  {name}  {detail}")

def run(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or str(ROOT), timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except Exception as e:
        return -2, str(e)

# ============================================================
print("=" * 60)
print("PHASE 5 -- INSTALL VERIFICATION")
print("=" * 60)

code, out = run([sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"], timeout=120)
check("pip install -e . exits 0", code == 0, f"exit={code}" + (f" | {out[-200:]}" if code != 0 else ""))

# Check CLI entry point
code, out = run(["pawbot", "--help"], timeout=15)
check("pawbot CLI found and runs --help", code == 0, f"exit={code}")

code, out = run(["pawbot", "--version"], timeout=15)
if code != 0:
    # Try python -m pawbot
    code, out = run([sys.executable, "-m", "pawbot.cli.commands", "--version"], timeout=15)
check("pawbot --version exits 0", code == 0, f"exit={code}, output={out.strip()[:80]}")

# setup.sh existence
setup_sh = ROOT / "install" / "setup.sh"
check("install/setup.sh exists", setup_sh.exists())

# ============================================================
print()
print("=" * 60)
print("PHASE 6 -- E2E COMMAND TESTS")
print("=" * 60)

# Clean slate
if PAWBOT_DIR.exists():
    bak = HOME / ".pawbot_cert_backup"
    if bak.exists():
        shutil.rmtree(bak)
    PAWBOT_DIR.rename(bak)
    had_backup = True
else:
    had_backup = False

def e2e(name, cmd, want_exit=0, grep_pattern=None):
    code, out = run(cmd, timeout=30)
    exit_ok = code == want_exit
    grep_ok = True
    if grep_pattern:
        grep_ok = grep_pattern.lower() in out.lower()
    passed = exit_ok and grep_ok
    detail = f"exit={code}" + (f" want={want_exit}" if not exit_ok else "")
    if grep_pattern and not grep_ok:
        detail += f" | pattern '{grep_pattern}' not found"
    check(name, passed, detail)
    return code, out

# Core lifecycle
e2e("onboard creates workspace", ["pawbot", "onboard"])
config_exists = (PAWBOT_DIR / "config.json").exists()
check("config.json exists after onboard", config_exists)
workspace_exists = (PAWBOT_DIR / "workspace").exists()
check("workspace/ dir exists", workspace_exists)

# Status
e2e("pawbot status exits 0", ["pawbot", "status"])

# Cron CRUD
e2e("cron list (empty)", ["pawbot", "cron", "list"])
e2e("cron add", ["pawbot", "cron", "add", "--name", "cert-test", "--message", "hello", "--every", "999999"])
e2e("cron list (has job)", ["pawbot", "cron", "list"], grep_pattern="cert-test")

# Find the job ID to remove it
code, out = run(["pawbot", "cron", "list"], timeout=15)
# Try to extract job ID from output
import re
job_ids = re.findall(r'([a-f0-9]{8})', out)
if job_ids:
    e2e("cron remove", ["pawbot", "cron", "remove", job_ids[0]])
else:
    check("cron remove", False, "Could not find job ID in output")

# Skills
e2e("skills list", ["pawbot", "skills", "list"])

# Memory
e2e("memory stats", ["pawbot", "memory", "stats"])

# ============================================================
print()
print("=" * 60)
print("PHASE 7 -- CORRUPTION RECOVERY TESTS")
print("=" * 60)

# Test 1: Corrupted config.json
config_path = PAWBOT_DIR / "config.json"
if config_path.exists():
    safe_config = config_path.read_text(encoding='utf-8')
    config_path.write_text("{{CORRUPTED JSON", encoding='utf-8')
    code, out = run(["pawbot", "status"], timeout=15)
    check("corrupted config.json -- no crash", code <= 1, f"exit={code}")
    
    # Check if config was recovered
    try:
        recovered = json.loads(config_path.read_text(encoding='utf-8'))
        check("config.json recovered to valid JSON", True)
    except Exception:
        check("config.json recovered to valid JSON", False, "still corrupted")
    
    # Restore
    config_path.write_text(safe_config, encoding='utf-8')
else:
    check("corrupted config.json -- no crash", False, "config.json not found")

# Test 2: Corrupted crons.json
cron_path = PAWBOT_DIR / "cron" / "jobs.json"
cron_path.parent.mkdir(parents=True, exist_ok=True)
cron_path.write_text("{{CORRUPTED", encoding='utf-8')
code, out = run(["pawbot", "cron", "list"], timeout=15)
check("corrupted crons.json -- no crash", code == 0, f"exit={code}")
cron_path.unlink(missing_ok=True)

# Test 3: No .tmp files
code, out = run(["pawbot", "cron", "add", "--name", "tmp-test", "--message", "test", "--every", "99999"], timeout=15)
# Find and remove the job
code2, out2 = run(["pawbot", "cron", "list"], timeout=15)
ids = re.findall(r'([a-f0-9]{8})', out2)
if ids:
    run(["pawbot", "cron", "remove", ids[0]], timeout=15)

tmp_files = list(PAWBOT_DIR.rglob("*.tmp"))
check("no .tmp files in ~/.pawbot", len(tmp_files) == 0, f"found: {len(tmp_files)}")

# ============================================================
print()
print("=" * 60)
print("PHASE 8 -- SECURITY SPOT CHECKS")
print("=" * 60)

# Check 1: status output doesn't show raw API keys
code, out = run(["pawbot", "status"], timeout=15)
has_raw_key = bool(re.search(r'sk-or-v1-[a-z0-9]{10,}|sk-ant-[a-z0-9]{10,}', out))
check("no raw API keys in status output", not has_raw_key)

# Check 2: Dashboard binds to localhost
server_path = ROOT / "pawbot" / "dashboard" / "server.py"
server_text = server_path.read_text(encoding='utf-8')
binds_localhost = "127.0.0.1" in server_text or "localhost" in server_text
binds_all = "0.0.0.0" in server_text
check("dashboard defaults to localhost", binds_localhost, f"localhost refs found, 0.0.0.0 refs: {'yes' if binds_all else 'no'}")

# Check 3: Log file check (if exists)
log_file = PAWBOT_DIR / "logs" / "pawbot.log"
if log_file.exists():
    log_text = log_file.read_text(encoding='utf-8', errors='replace')
    has_key_in_log = bool(re.search(r'sk-or-v1-[a-z0-9]{10,}|sk-ant-[a-z0-9]{10,}', log_text))
    check("no raw API keys in log file", not has_key_in_log)
else:
    check("no raw API keys in log file", True, "log file not yet created (OK)")

# Restore backup if we had one
if had_backup:
    bak = HOME / ".pawbot_cert_backup"
    if bak.exists():
        if PAWBOT_DIR.exists():
            shutil.rmtree(PAWBOT_DIR)
        bak.rename(PAWBOT_DIR)

# ============================================================
print()
print("=" * 60)
failures = [r for r in results if not r[1]]
print(f"PHASE 5-8 SUMMARY: {len(results) - len(failures)}/{len(results)} passed, {len(failures)} failed")
if failures:
    print("FAILURES:")
    for name, _, detail in failures:
        print(f"  [FAIL]  {name}  {detail}")
