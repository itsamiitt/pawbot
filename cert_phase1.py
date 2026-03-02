"""Phase 1 — Structural Verification for Pawbot Final Certification."""
import pathlib, re, sys, os, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = pathlib.Path(r"c:\Users\Administrator\Downloads\nanobot\pawbot")
PAWBOT = ROOT / "pawbot"
results = []

def check(name, passed, detail=""):
    icon = "[PASS]" if passed else "[FAIL]"
    results.append((name, passed, detail))
    print(f"  {icon}  {name}  {detail}")

print("=" * 60)
print("PHASE 1.1 — FILE EXISTENCE")
print("=" * 60)
files = [
    ROOT / "pyproject.toml",
    PAWBOT / "errors.py",
    PAWBOT / "utils" / "paths.py",
    PAWBOT / "utils" / "secrets.py",
    PAWBOT / "utils" / "fs.py",
    PAWBOT / "utils" / "retry.py",
    PAWBOT / "utils" / "logging_setup.py",
]
for f in files:
    check(f"EXISTS {f.name}", f.exists())

# Check for test files — may not exist yet
test_files = [
    ROOT / "tests" / "__init__.py",
    ROOT / "tests" / "conftest.py",
    ROOT / "tests" / "test_utils.py",
    ROOT / "tests" / "test_cli.py",
    ROOT / "tests" / "test_security.py",
    ROOT / "tests" / "test_install.py",
]
for f in test_files:
    check(f"EXISTS {f.name}", f.exists())

print()
print("=" * 60)
print("PHASE 1.2 — PYPROJECT.TOML VERIFICATION")
print("=" * 60)
try:
    import tomllib
    with open(ROOT / "pyproject.toml", "rb") as fh:
        d = tomllib.load(fh)
    check("TOML parseable", True)
    proj = d.get("project", {})
    check("name == pawbot-ai", proj.get("name") == "pawbot-ai")
    check("version present", bool(proj.get("version")))
    check("requires-python >= 3.11", ">=3.11" in proj.get("requires-python", ""))
    check("pawbot script entry", "pawbot" in proj.get("scripts", {}))
    check("hatchling in build-system", "hatchling" in str(d.get("build-system", {}).get("requires", "")))
    check("dependencies >= 5", len(proj.get("dependencies", [])) >= 5)
    check("optional-deps: dashboard", "dashboard" in proj.get("optional-dependencies", {}))
    check("optional-deps: dev", "dev" in proj.get("optional-dependencies", {}))
except Exception as e:
    check("TOML parseable", False, str(e))

print()
print("=" * 60)
print("PHASE 1.3 — EXCEPTION HANDLING")
print("=" * 60)
bare_count = 0
silent_count = 0
import_fallback = ['redis = None', 'chromadb = None', 'croniter = None', 'OutboundMessage = None', 'embedding_functions = None']
for f in PAWBOT.rglob("*.py"):
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        continue
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "except:" or (stripped.startswith("except:") and "Exception" not in stripped):
            bare_count += 1
        if "except Exception:" in stripped and " as e" not in stripped and "pragma" not in stripped:
            # Skip import fallbacks
            if i + 1 < len(lines):
                next_s = lines[i+1].strip()
                if any(p in next_s for p in import_fallback):
                    continue
            silent_count += 1

check(f"No bare except:", bare_count == 0, f"count: {bare_count}")
check(f"No silent except Exception:", silent_count == 0, f"count: {silent_count}")

print()
print("=" * 60)
print("PHASE 1.4 — THREAD DAEMON CHECK")
print("=" * 60)
non_daemon = 0
for f in PAWBOT.rglob("*.py"):
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        continue
    for line in lines:
        if "Thread(" in line and "daemon=True" not in line and "import" not in line and "#" not in line.split("Thread(")[0]:
            # Check for legitimate Thread references
            if "threading.Thread" in line or "Thread(target" in line:
                non_daemon += 1
                print(f"    {f.relative_to(ROOT)}: {line.strip()}")

check("All threads daemon", non_daemon == 0, f"non-daemon: {non_daemon}")

print()
print("=" * 60)
print("PHASE 1.5 — SUBPROCESS TIMEOUT CHECK")
print("=" * 60)
missing_timeout = 0
for f in PAWBOT.rglob("*.py"):
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    for i, line in enumerate(text.splitlines()):
        if "subprocess.run(" in line and "timeout=" not in line:
            # Check if timeout is on a continuation line
            block = text.splitlines()[i:i+5]
            full = " ".join(b.strip() for b in block)
            if "timeout=" not in full:
                missing_timeout += 1
                print(f"    {f.relative_to(ROOT)}:{i+1}: {line.strip()}")

check("All subprocess.run have timeout=", missing_timeout == 0, f"missing: {missing_timeout}")

print()
print("=" * 60)
print("PHASE 1.6 — NANOBOT REFERENCE CHECK")
print("=" * 60)
nanobot_refs = 0
for f in PAWBOT.rglob("*.py"):
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    for i, line in enumerate(text.splitlines()):
        if "nanobot" in line.lower() and "#" not in line.split("nanobot")[0].strip()[-1:]:
            nanobot_refs += 1
            print(f"    {f.relative_to(ROOT)}:{i+1}: {line.strip()}")

check("No nanobot references", nanobot_refs == 0, f"count: {nanobot_refs}")

print()
print("=" * 60)
print("PHASE 1.7 — ATOMIC WRITE VERIFICATION")
print("=" * 60)
atomic_files = {
    "pawbot/config/loader.py": PAWBOT / "config" / "loader.py",
    "pawbot/dashboard/server.py": PAWBOT / "dashboard" / "server.py",
    "pawbot/cron/service.py": PAWBOT / "cron" / "service.py",
    "pawbot/heartbeat/engine.py": PAWBOT / "heartbeat" / "engine.py",
    "pawbot/agent/memory.py": PAWBOT / "agent" / "memory.py",
}
for name, path in atomic_files.items():
    try:
        text = path.read_text(encoding="utf-8")
        atomic = text.count("atomic_write") + text.count("write_json_with_backup")
        # Count raw writes that should have been converted
        raw = len(re.findall(r'\.write_text\(.*json\.dumps|open\(.*["\']w["\'].*json\.dump', text))
        check(f"Atomic writes: {name}", atomic > 0 and raw == 0, f"atomic={atomic}, raw={raw}")
    except Exception as e:
        check(f"Atomic writes: {name}", False, str(e))

print()
print("=" * 60)
print("PHASE 1.8 — SAFE JSON READ VERIFICATION")
print("=" * 60)
safe_read_files = {
    "pawbot/config/loader.py": PAWBOT / "config" / "loader.py",
    "pawbot/cron/service.py": PAWBOT / "cron" / "service.py",
    "pawbot/heartbeat/engine.py": PAWBOT / "heartbeat" / "engine.py",
}
for name, path in safe_read_files.items():
    try:
        text = path.read_text(encoding="utf-8")
        safe = text.count("safe_read_json")
        raw = text.count("json.loads") + text.count("json.load(")
        check(f"Safe reads: {name}", safe > 0, f"safe_read_json={safe}, raw_json.load={raw}")
    except Exception as e:
        check(f"Safe reads: {name}", False, str(e))

print()
print("=" * 60)
print("PHASE 1.9 — RETRY LOGIC VERIFICATION")
print("=" * 60)
router_path = PAWBOT / "providers" / "router.py"
try:
    text = router_path.read_text(encoding="utf-8")
    retry_count = text.count("call_with_retry")
    validate_count = text.count("_validate_key") + text.count("is_placeholder")
    check("call_with_retry >= 3 calls", retry_count >= 3, f"count: {retry_count}")
    check("placeholder detection present", validate_count >= 1, f"refs: {validate_count}")
except Exception as e:
    check("Retry logic", False, str(e))

print()
print("=" * 60)
print("PHASE 1.10 — PRINT STATEMENT CHECK")
print("=" * 60)
loader_path = PAWBOT / "config" / "loader.py"
try:
    text = loader_path.read_text(encoding="utf-8")
    prints = len(re.findall(r'^\s*print\(', text, re.MULTILINE))
    check("No print() in config/loader.py", prints == 0, f"count: {prints}")
except Exception as e:
    check("print check", False, str(e))

# Summary
print()
print("=" * 60)
failures = [r for r in results if not r[1]]
print(f"PHASE 1 SUMMARY: {len(results) - len(failures)}/{len(results)} passed, {len(failures)} failed")
if failures:
    print("FAILURES:")
    for name, _, detail in failures:
        print(f"  ❌  {name}  {detail}")
