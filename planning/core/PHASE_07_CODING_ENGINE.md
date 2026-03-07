# PHASE 7 — CODING ENGINE MCP SERVER
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)  
> **Implementation Day:** Day 25  
> **Primary File:** `~/.nanobot/mcp-servers/coding/server.py` (NEW)  
> **Test File:** `~/nanobot/tests/test_coding_mcp.py`  
> **Config registration key:** `mcp_servers.coding`  
> **Index database path:** `~/.nanobot/code-indexes/{project_hash}.db`  
> **Checkpoint registry:** `~/.nanobot/checkpoints/registry.json`

---

## BEFORE YOU START

```bash
mkdir -p ~/.nanobot/mcp-servers/coding
mkdir -p ~/.nanobot/checkpoints
# Optional: check if tree-sitter is available
pip show tree-sitter 2>/dev/null || echo "tree-sitter not installed — will use regex fallback"
```

Add to `~/.nanobot/config.json`:

```json
{
  "mcp_servers": {
    "coding": {
      "path": "~/.nanobot/mcp-servers/coding/server.py",
      "requires_confirmation": false,
      "enabled": true
    }
  }
}
```

---

## TOOL IMPLEMENTATIONS

### code_index_project

```python
#!/usr/bin/env python3
"""
Coding Engine MCP Server
Registered as: mcp_servers.coding in ~/.nanobot/config.json
Index DB path: ~/.nanobot/code-indexes/{project_hash}.db
"""
import os
import re
import json
import time
import sqlite3
import hashlib
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger("nanobot.mcp.coding")

INDEX_DIR = os.path.expanduser("~/.nanobot/code-indexes")

SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "coverage", ".tox",
    ".mypy_cache", ".pytest_cache",
}

LANGUAGE_EXTENSIONS = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "javascript", ".tsx": "typescript", ".go": "go",
    ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".php": "php", ".cs": "csharp", ".cpp": "cpp",
    ".c": "c", ".sh": "shell", ".yaml": "yaml", ".json": "json",
}


def _get_project_hash(project_path: str) -> str:
    return hashlib.md5(project_path.encode()).hexdigest()[:12]


def _init_index_db(db_path: str):
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS symbols (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path   TEXT NOT NULL,
            symbol_type TEXT NOT NULL,   -- function, class, variable, import, export
            symbol_name TEXT NOT NULL,
            line_number INTEGER,
            docstring   TEXT DEFAULT '',
            language    TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_sym_name ON symbols(symbol_name);
        CREATE INDEX IF NOT EXISTS idx_sym_file ON symbols(file_path);
        CREATE INDEX IF NOT EXISTS idx_sym_type ON symbols(symbol_type);

        CREATE TABLE IF NOT EXISTS dependencies (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            from_file   TEXT NOT NULL,
            to_module   TEXT NOT NULL,
            import_type TEXT DEFAULT 'import'
        );
        CREATE INDEX IF NOT EXISTS idx_dep_from ON dependencies(from_file);
        CREATE INDEX IF NOT EXISTS idx_dep_to ON dependencies(to_module);

        CREATE TABLE IF NOT EXISTS files (
            path        TEXT PRIMARY KEY,
            language    TEXT,
            line_count  INTEGER,
            indexed_at  INTEGER
        );
        """)


def _extract_symbols_regex(content: str, language: str) -> list[dict]:
    """Regex-based symbol extraction. Used when tree-sitter unavailable."""
    symbols = []

    if language == "python":
        # Functions
        for m in re.finditer(r'^(?:async )?def (\w+)\s*\(', content, re.MULTILINE):
            symbols.append({"type": "function", "name": m.group(1), "line": content[:m.start()].count("\n") + 1})
        # Classes
        for m in re.finditer(r'^class (\w+)', content, re.MULTILINE):
            symbols.append({"type": "class", "name": m.group(1), "line": content[:m.start()].count("\n") + 1})
        # Imports
        for m in re.finditer(r'^(?:from|import) ([\w.]+)', content, re.MULTILINE):
            symbols.append({"type": "import", "name": m.group(1), "line": content[:m.start()].count("\n") + 1})

    elif language in ("javascript", "typescript"):
        # Functions
        for m in re.finditer(r'(?:function|const|let|var)\s+(\w+)\s*(?:=\s*(?:async\s*)?\(|[({])', content):
            symbols.append({"type": "function", "name": m.group(1), "line": content[:m.start()].count("\n") + 1})
        # Classes
        for m in re.finditer(r'^class (\w+)', content, re.MULTILINE):
            symbols.append({"type": "class", "name": m.group(1), "line": content[:m.start()].count("\n") + 1})
        # Imports
        for m in re.finditer(r"^import .+ from ['\"](.+)['\"]", content, re.MULTILINE):
            symbols.append({"type": "import", "name": m.group(1), "line": content[:m.start()].count("\n") + 1})

    return symbols


def code_index_project(project_path: str) -> dict:
    """
    Build searchable index of entire project.
    Stores in ~/.nanobot/code-indexes/{project_hash}.db
    Returns: total_files, total_symbols, db_path
    """
    project_path = os.path.expanduser(project_path)
    os.makedirs(INDEX_DIR, exist_ok=True)
    project_hash = _get_project_hash(project_path)
    db_path = os.path.join(INDEX_DIR, f"{project_hash}.db")
    _init_index_db(db_path)

    total_files = 0
    total_symbols = 0
    start = time.time()

    with sqlite3.connect(db_path) as conn:
        # Clear existing index
        conn.executescript("DELETE FROM symbols; DELETE FROM dependencies; DELETE FROM files;")

        for root, dirs, files in os.walk(project_path):
            # Skip irrelevant directories
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

            for fname in files:
                fpath = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower()
                language = LANGUAGE_EXTENSIONS.get(ext)
                if not language:
                    continue

                try:
                    with open(fpath, "r", errors="ignore") as f:
                        content = f.read()

                    line_count = content.count("\n")
                    symbols = _extract_symbols_regex(content, language)

                    for sym in symbols:
                        conn.execute(
                            "INSERT INTO symbols (file_path, symbol_type, symbol_name, "
                            "line_number, language) VALUES (?, ?, ?, ?, ?)",
                            (fpath, sym["type"], sym["name"], sym["line"], language)
                        )
                        total_symbols += 1

                    # Extract dependencies
                    for sym in symbols:
                        if sym["type"] == "import":
                            conn.execute(
                                "INSERT INTO dependencies (from_file, to_module) VALUES (?, ?)",
                                (fpath, sym["name"])
                            )

                    conn.execute(
                        "INSERT OR REPLACE INTO files (path, language, line_count, indexed_at) "
                        "VALUES (?, ?, ?, ?)",
                        (fpath, language, line_count, int(time.time()))
                    )
                    total_files += 1

                except Exception as e:
                    logger.warning(f"Index skip {fpath}: {e}")

    elapsed = round(time.time() - start, 2)
    db_size = os.path.getsize(db_path)

    logger.info(f"Indexed {project_path}: {total_files} files, {total_symbols} symbols in {elapsed}s")
    return {
        "project_path": project_path,
        "db_path": db_path,
        "total_files": total_files,
        "total_symbols": total_symbols,
        "elapsed_s": elapsed,
        "db_size_kb": round(db_size / 1024, 1),
    }
```

### code_get_context

```python
def code_get_context(file_path: str, query: str = "", max_tokens: int = 500) -> dict:
    """
    Return relevant sections of a file without loading everything.
    Files < 200 lines: return entire file.
    Files > 200 lines: return relevant chunks + always include header.
    """
    file_path = os.path.expanduser(file_path)
    if not os.path.exists(file_path):
        return {"error": f"File not found: {file_path}"}

    with open(file_path, "r", errors="ignore") as f:
        content = f.read()

    lines = content.splitlines()

    # Add line numbers
    numbered = [f"{i+1}: {line}" for i, line in enumerate(lines)]

    if len(lines) <= 200:
        return {"file": file_path, "content": "\n".join(numbered), "truncated": False}

    # Parse into logical chunks (functions, classes, top-level)
    chunks = _split_into_chunks(numbered)

    # Always include file header (imports, module docstring) — first chunk
    header = chunks[0] if chunks else []

    if not query:
        # No query — return header + first few chunks
        result_chunks = chunks[:3]
    else:
        # Score chunks by keyword overlap with query
        query_words = set(query.lower().split())
        scored = []
        for chunk in chunks[1:]:  # skip header, scored separately
            chunk_text = " ".join(chunk).lower()
            score = sum(1 for w in query_words if w in chunk_text)
            scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Take top chunks that fit within max_tokens (approximate: 4 chars/token)
        result_chunks = [header]
        token_budget = max_tokens
        for score, chunk in scored:
            chunk_tokens = len(" ".join(chunk)) // 4
            if token_budget - chunk_tokens < 0:
                break
            result_chunks.append(chunk)
            token_budget -= chunk_tokens

    output = "\n".join(line for chunk in result_chunks for line in chunk)
    return {
        "file": file_path,
        "content": output,
        "total_lines": len(lines),
        "truncated": True,
        "chunks_returned": len(result_chunks),
    }


def _split_into_chunks(numbered_lines: list[str]) -> list[list[str]]:
    """Split file into logical sections based on indentation and blank lines."""
    chunks = []
    current = []

    for line in numbered_lines:
        # New top-level block (class/def at col 0)
        stripped = line.split(":", 1)[-1] if ":" in line else line
        code_part = stripped.lstrip()
        if (code_part.startswith("def ") or code_part.startswith("class ") or
                code_part.startswith("async def ")):
            if current:
                chunks.append(current)
            current = [line]
        else:
            current.append(line)

    if current:
        chunks.append(current)
    return chunks if chunks else [numbered_lines]
```

### code_get_dependencies

```python
def code_get_dependencies(file_path: str, project_path: str = None) -> dict:
    """
    Returns what a file depends on and what depends on it.
    Requires a project index built by code_index_project.
    """
    file_path = os.path.expanduser(file_path)
    project_path = project_path or str(Path(file_path).parent.parent)

    db_path = os.path.join(INDEX_DIR, f"{_get_project_hash(project_path)}.db")
    if not os.path.exists(db_path):
        return {"error": f"No index found. Run code_index_project('{project_path}') first"}

    with sqlite3.connect(db_path) as conn:
        # Direct imports of this file
        direct_imports = conn.execute(
            "SELECT to_module FROM dependencies WHERE from_file = ?",
            (file_path,)
        ).fetchall()

        # Reverse: which files import from this file (by module name)
        file_stem = Path(file_path).stem
        reverse_deps = conn.execute(
            "SELECT DISTINCT from_file FROM dependencies WHERE to_module LIKE ?",
            (f"%{file_stem}%",)
        ).fetchall()

        # Transitive: what do the files I import also import? (2 levels)
        transitive = set()
        for (dep,) in direct_imports:
            trans = conn.execute(
                "SELECT DISTINCT to_module FROM dependencies "
                "WHERE from_file LIKE ?",
                (f"%{dep.replace('.', '/')}%",)
            ).fetchall()
            transitive.update(t[0] for t in trans)

    return {
        "file": file_path,
        "direct_imports": [d[0] for d in direct_imports],
        "reverse_dependencies": [r[0] for r in reverse_deps],
        "transitive_deps_2lvl": list(transitive)[:20],
    }
```

### code_search

```python
def code_search(query: str, project_path: str,
                search_type: str = "keyword") -> dict:
    """
    Search types: semantic, keyword, symbol, error
    Returns: list of {file_path, line_number, snippet, match_type}
    """
    project_path = os.path.expanduser(project_path)
    results = []

    if search_type == "keyword":
        # grep-based text search
        r = subprocess.run(
            ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.ts",
             "-A", "1", "-B", "1", query, project_path],
            capture_output=True, text=True, timeout=30
        )
        for line in r.stdout.splitlines()[:30]:
            parts = line.split(":", 2)
            if len(parts) >= 3:
                results.append({
                    "file_path": parts[0],
                    "line_number": int(parts[1]) if parts[1].isdigit() else 0,
                    "snippet": parts[2][:200],
                    "match_type": "keyword",
                })

    elif search_type == "symbol":
        db_path = os.path.join(INDEX_DIR, f"{_get_project_hash(project_path)}.db")
        if not os.path.exists(db_path):
            return {"error": f"No index for {project_path}. Run code_index_project first."}

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT file_path, symbol_type, symbol_name, line_number "
                "FROM symbols WHERE symbol_name LIKE ? LIMIT 20",
                (f"%{query}%",)
            ).fetchall()
            for row in rows:
                results.append({
                    "file_path": row[0], "line_number": row[3],
                    "snippet": f"{row[1]}: {row[2]}", "match_type": "symbol",
                })

    elif search_type == "error":
        # Search for error patterns
        error_patterns = [query, "Error:", "Exception:", "Traceback", "FAILED"]
        for pattern in error_patterns[:2]:
            r = subprocess.run(
                ["grep", "-rn", pattern, project_path, "--include=*.log",
                 "--include=*.txt", "-l"],
                capture_output=True, text=True, timeout=15
            )
            for fpath in r.stdout.splitlines()[:5]:
                results.append({
                    "file_path": fpath.strip(), "line_number": 0,
                    "snippet": f"Found '{pattern}'", "match_type": "error_file",
                })

    # Deduplicate
    seen = set()
    unique = []
    for r in results:
        key = f"{r['file_path']}:{r['line_number']}"
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return {"query": query, "results": unique[:20], "count": len(unique)}
```

### code_write

```python
def code_write(file_path: str, content: str, backup: bool = True) -> dict:
    """
    Write code to file. Runs syntax check. Restores backup on failure.
    """
    file_path = os.path.expanduser(file_path)
    bak_path = None

    # Create backup
    if backup and os.path.exists(file_path):
        ts = int(time.time())
        bak_path = f"{file_path}.bak.{ts}"
        import shutil
        shutil.copy2(file_path, bak_path)

    # Create parent dirs
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)

    # Write
    with open(file_path, "w") as f:
        f.write(content)

    # Syntax check
    check_result = _syntax_check(file_path)
    if not check_result["ok"]:
        # Restore backup
        if bak_path and os.path.exists(bak_path):
            import shutil
            shutil.copy2(bak_path, file_path)
        return {
            "success": False,
            "error": "Syntax check failed — backup restored",
            "syntax_error": check_result["error"],
            "file": file_path,
        }

    logger.info(f"CODE WRITE: {file_path} ({len(content)} chars)")
    return {
        "success": True,
        "file": file_path,
        "bytes_written": len(content.encode()),
        "backup": bak_path,
        "syntax_check": "passed",
    }


def _syntax_check(file_path: str) -> dict:
    """Run language-appropriate syntax check."""
    ext = os.path.splitext(file_path)[1].lower()
    cmd = None

    if ext == ".py":
        cmd = f"python -m py_compile {file_path}"
    elif ext in (".js", ".jsx"):
        cmd = f"node --check {file_path}"
    elif ext in (".ts", ".tsx"):
        cmd = f"tsc --noEmit {file_path} 2>/dev/null || node --check {file_path}"
    elif ext == ".json":
        cmd = f"python -m json.tool {file_path} > /dev/null"

    if not cmd:
        return {"ok": True, "skipped": True}

    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr + r.stdout)[:500]}
    return {"ok": True}
```

### code_edit

```python
def code_edit(file_path: str, find_text: str, replace_text: str,
              line_range: list = None) -> dict:
    """
    Targeted find-and-replace. Requires exactly one match.
    If multiple matches: returns error with line numbers to disambiguate.
    """
    file_path = os.path.expanduser(file_path)
    if not os.path.exists(file_path):
        return {"error": f"File not found: {file_path}"}

    with open(file_path, "r") as f:
        content = f.read()

    # Find all occurrences
    occurrences = [m.start() for m in re.finditer(re.escape(find_text), content)]

    if len(occurrences) == 0:
        return {"error": f"Text not found in {file_path}"}

    if len(occurrences) > 1 and not line_range:
        line_nums = [content[:pos].count("\n") + 1 for pos in occurrences]
        return {
            "error": f"Found {len(occurrences)} matches. Specify line_range to disambiguate.",
            "match_lines": line_nums,
        }

    # Apply replacement
    new_content = content.replace(find_text, replace_text, 1)

    # Write with backup
    result = code_write(file_path, new_content, backup=True)
    if not result["success"]:
        return result

    # Find changed lines for preview
    new_lines = new_content.splitlines()
    change_line = new_content[:new_content.find(replace_text)].count("\n")
    preview_start = max(0, change_line - 3)
    preview_end = min(len(new_lines), change_line + 8)
    preview = "\n".join(
        f"{i+1}: {line}" for i, line in enumerate(new_lines[preview_start:preview_end],
                                                    start=preview_start)
    )

    return {
        "success": True,
        "file": file_path,
        "lines_changed": replace_text.count("\n") - find_text.count("\n"),
        "change_preview": preview,
    }
```

### code_run_checks

```python
def code_run_checks(file_path: str, project_path: str = None) -> dict:
    """
    Run full quality pipeline: syntax, lint, type check, related tests.
    Used by Phase 2 (loop.py) to trigger self-correction on failure.
    """
    file_path = os.path.expanduser(file_path)
    project_path = project_path or str(Path(file_path).parent)
    ext = os.path.splitext(file_path)[1].lower()
    results = {}

    # 1. Syntax
    syntax = _syntax_check(file_path)
    results["syntax"] = syntax
    if not syntax["ok"]:
        return {"passed": False, "checks": results, "halted_at": "syntax"}

    # 2. Linting
    if ext == ".py":
        r = subprocess.run(
            ["ruff", "check", file_path], capture_output=True, text=True, timeout=30
        )
        results["lint"] = {"ok": r.returncode == 0, "output": r.stdout[:1000]}

    elif ext in (".js", ".jsx", ".ts", ".tsx"):
        eslint_config = any(
            os.path.exists(os.path.join(project_path, c))
            for c in [".eslintrc", ".eslintrc.js", ".eslintrc.json"]
        )
        if eslint_config:
            r = subprocess.run(
                ["eslint", file_path], capture_output=True, text=True, timeout=30
            )
            results["lint"] = {"ok": r.returncode == 0, "output": r.stdout[:1000]}

    # 3. Type checking
    if ext == ".py":
        mypy_config = any(
            os.path.exists(os.path.join(project_path, c))
            for c in ["mypy.ini", "setup.cfg"]
        )
        if mypy_config:
            r = subprocess.run(
                ["mypy", file_path], capture_output=True, text=True, timeout=60
            )
            results["typecheck"] = {"ok": r.returncode == 0, "output": r.stdout[:1000]}

    # 4. Related tests
    file_stem = Path(file_path).stem
    test_patterns = [
        f"tests/test_{file_stem}.py",
        f"test_{file_stem}.py",
        f"{file_stem}.test.ts",
        f"{file_stem}.test.js",
        f"{file_stem}.spec.ts",
    ]
    test_files = [
        os.path.join(project_path, p) for p in test_patterns
        if os.path.exists(os.path.join(project_path, p))
    ]
    if test_files:
        r = subprocess.run(
            ["pytest"] + test_files + ["-v", "--tb=short"],
            capture_output=True, text=True, timeout=120, cwd=project_path
        )
        results["tests"] = {
            "ok": r.returncode == 0,
            "output": r.stdout[-2000:],
            "test_files": test_files,
        }

    all_passed = all(v.get("ok", True) for v in results.values())
    return {"passed": all_passed, "checks": results, "file": file_path}
```

### code_checkpoint and code_rollback

```python
CHECKPOINT_REGISTRY = os.path.expanduser("~/.nanobot/checkpoints/registry.json")

def _load_checkpoints() -> dict:
    if os.path.exists(CHECKPOINT_REGISTRY):
        with open(CHECKPOINT_REGISTRY) as f:
            return json.load(f)
    return {}

def _save_checkpoints(data: dict):
    os.makedirs(os.path.dirname(CHECKPOINT_REGISTRY), exist_ok=True)
    with open(CHECKPOINT_REGISTRY, "w") as f:
        json.dump(data, f, indent=2)


def code_checkpoint(name: str, project_path: str) -> dict:
    """Create named checkpoint via git stash or zip."""
    project_path = os.path.expanduser(project_path)
    ts = int(time.time())
    checkpoint_id = f"{name}_{ts}"

    # Try git stash first
    git_check = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=project_path, capture_output=True
    )
    if git_check.returncode == 0:
        stash_msg = f"nanobot-checkpoint-{name}-{ts}"
        r = subprocess.run(
            ["git", "stash", "save", stash_msg],
            cwd=project_path, capture_output=True, text=True
        )
        method = "git_stash"
        stash_index = 0  # most recent stash is index 0
        registry = _load_checkpoints()
        registry[checkpoint_id] = {
            "name": name, "method": method, "stash_index": stash_index,
            "project_path": project_path, "created_at": ts, "stash_msg": stash_msg,
        }
        _save_checkpoints(registry)
        return {"checkpoint_id": checkpoint_id, "method": method}

    # Fallback: zip
    import zipfile
    zip_dir = os.path.expanduser("~/.nanobot/checkpoints")
    os.makedirs(zip_dir, exist_ok=True)
    zip_path = os.path.join(zip_dir, f"{checkpoint_id}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for file in files:
                fpath = os.path.join(root, file)
                zf.write(fpath, os.path.relpath(fpath, project_path))
    size_kb = round(os.path.getsize(zip_path) / 1024, 1)
    registry = _load_checkpoints()
    registry[checkpoint_id] = {
        "name": name, "method": "zip", "zip_path": zip_path,
        "project_path": project_path, "created_at": ts,
    }
    _save_checkpoints(registry)
    return {"checkpoint_id": checkpoint_id, "method": "zip", "size_kb": size_kb}


def code_rollback(checkpoint_id: str) -> dict:
    """Restore to named checkpoint."""
    registry = _load_checkpoints()
    if checkpoint_id not in registry:
        return {"error": f"Checkpoint '{checkpoint_id}' not found in registry"}

    cp = registry[checkpoint_id]
    project_path = cp["project_path"]

    if cp["method"] == "git_stash":
        stash_msg = cp["stash_msg"]
        # Find stash by message
        list_r = subprocess.run(
            ["git", "stash", "list"], cwd=project_path, capture_output=True, text=True
        )
        stash_ref = None
        for line in list_r.stdout.splitlines():
            if stash_msg in line:
                stash_ref = line.split(":")[0]
                break
        if not stash_ref:
            return {"error": f"Git stash not found for message: {stash_msg}"}
        r = subprocess.run(
            ["git", "stash", "pop", stash_ref],
            cwd=project_path, capture_output=True, text=True
        )
        if r.returncode != 0:
            return {"error": "git stash pop failed", "output": r.stderr}

    elif cp["method"] == "zip":
        import zipfile
        with zipfile.ZipFile(cp["zip_path"], "r") as zf:
            zf.extractall(project_path)

    logger.info(f"ROLLBACK: restored checkpoint '{cp['name']}'")
    return {"success": True, "checkpoint_id": checkpoint_id, "method": cp["method"]}
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_coding_mcp.py`

```python
class TestCodingMCP:
    def test_server_starts_without_error()
    def test_list_tools_returns_all_tools()
    def test_each_tool_handles_invalid_args_gracefully()

class TestCodeIndex:
    def test_indexes_python_project()
    def test_skips_node_modules_and_git()
    def test_extracts_functions_and_classes()
    def test_db_persists_after_reindex()

class TestCodeGetContext:
    def test_small_file_returns_entirely()
    def test_large_file_returns_relevant_chunks()
    def test_header_always_included()
    def test_line_numbers_in_output()

class TestCodeWrite:
    def test_write_creates_backup()
    def test_syntax_error_restores_backup()
    def test_creates_parent_dirs()

class TestCodeEdit:
    def test_single_match_replaced()
    def test_multiple_matches_requires_disambiguation()
    def test_not_found_returns_error()

class TestCodeRunChecks:
    def test_syntax_error_halts_early()
    def test_finds_related_test_files()
    def test_all_passed_true_on_clean_file()

class TestCheckpoints:
    def test_git_stash_checkpoint_created()
    def test_zip_fallback_when_no_git()
    def test_rollback_restores_files()
    def test_unknown_checkpoint_returns_error()
```

---

## CROSS-REFERENCES

- **Phase 2** (loop.py): calls `code_run_checks()` and if it fails, calls `_record_failure()` triggering self-correction
- **Phase 3** (context.py): writes code sections into `session["file_context_raw"]` using `code_get_context()`
- **Phase 7** index DB path `~/.nanobot/code-indexes/{hash}.db` — canonical path from **MASTER_REFERENCE.md**
- **Phase 7** checkpoint registry `~/.nanobot/checkpoints/registry.json` — canonical path from **MASTER_REFERENCE.md**
- **Phase 16** (CLI): `nanobot mcp test coding` validates this server

All canonical paths and identifiers in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
