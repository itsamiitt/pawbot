#!/usr/bin/env python3
"""Coding Engine MCP server.

Registered as: mcp_servers.coding in ~/.pawbot/config.json
Index DB path: ~/.pawbot/code-indexes/{project_hash}.db
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


def _configure_logger() -> logging.Logger:
    log_path = Path.home() / ".pawbot" / "logs" / "pawbot.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pawbot.mcp.coding")
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
mcp = FastMCP(name="coding")

INDEX_DIR = os.path.expanduser("~/.pawbot/code-indexes")
CHECKPOINT_REGISTRY = os.path.expanduser("~/.pawbot/checkpoints/registry.json")

SKIP_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
}

LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".c": "c",
    ".sh": "shell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
}

TOOL_NAMES = [
    "code_index_project",
    "code_get_context",
    "code_get_dependencies",
    "code_search",
    "code_write",
    "code_edit",
    "code_run_checks",
    "code_checkpoint",
    "code_rollback",
]


def _truncate(text: str | None, limit: int) -> str:
    return (text or "")[:limit]


def _get_project_hash(project_path: str) -> str:
    return hashlib.sha256(project_path.encode("utf-8")).hexdigest()[:12]


def _init_index_db(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS symbols (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path   TEXT NOT NULL,
                symbol_type TEXT NOT NULL,
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
            """
        )


def _extract_symbols_regex(content: str, language: str) -> list[dict[str, Any]]:
    """Extract symbols using regex heuristics."""
    symbols: list[dict[str, Any]] = []

    def _line_number(start: int) -> int:
        return content[:start].count("\n") + 1

    if language == "python":
        for match in re.finditer(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", content, re.MULTILINE):
            symbols.append(
                {"type": "function", "name": match.group(1), "line": _line_number(match.start())}
            )
        for match in re.finditer(r"^class\s+([A-Za-z_]\w*)", content, re.MULTILINE):
            symbols.append(
                {"type": "class", "name": match.group(1), "line": _line_number(match.start())}
            )
        for match in re.finditer(
            r"^(?:from\s+([A-Za-z0-9_\.]+)\s+import|import\s+([A-Za-z0-9_\.]+))",
            content,
            re.MULTILINE,
        ):
            module = match.group(1) or match.group(2) or ""
            symbols.append({"type": "import", "name": module, "line": _line_number(match.start())})

    elif language in {"javascript", "typescript"}:
        for match in re.finditer(
            r"(?:function\s+([A-Za-z_]\w*)\s*\(|(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\()",
            content,
        ):
            name = match.group(1) or match.group(2) or ""
            symbols.append({"type": "function", "name": name, "line": _line_number(match.start())})
        for match in re.finditer(r"^class\s+([A-Za-z_]\w*)", content, re.MULTILINE):
            symbols.append(
                {"type": "class", "name": match.group(1), "line": _line_number(match.start())}
            )
        for match in re.finditer(
            r"^import\s+.+\s+from\s+['\"]([^'\"]+)['\"]",
            content,
            re.MULTILINE,
        ):
            symbols.append(
                {"type": "import", "name": match.group(1), "line": _line_number(match.start())}
            )

    return symbols


def _split_into_chunks(numbered_lines: list[str]) -> list[list[str]]:
    """Split numbered lines into rough logical chunks."""
    chunks: list[list[str]] = []
    current: list[str] = []

    for line in numbered_lines:
        _, _, raw = line.partition(":")
        code = raw.lstrip()
        is_top_level = raw.startswith(code)
        starts_block = (
            code.startswith("def ")
            or code.startswith("class ")
            or code.startswith("async def ")
        )
        if starts_block and is_top_level:
            if current:
                chunks.append(current)
            current = [line]
            continue
        current.append(line)

    if current:
        chunks.append(current)
    return chunks if chunks else [numbered_lines]


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return handle.read()


def _run_subprocess(
    args: list[str],
    cwd: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except FileNotFoundError:
        return {"ok": False, "error": f"Command not found: {args[0]}", "returncode": -1}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timeout after {timeout}s", "returncode": -1}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "returncode": -1}


def _run_shell(cmd: str | list[str], cwd: str | None = None, timeout: int = 30) -> dict[str, Any]:
    argv = cmd if isinstance(cmd, list) else shlex.split(cmd)
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timeout after {timeout}s", "returncode": -1}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "returncode": -1}


def _search_keyword_fallback(query: str, project_path: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    query_lower = query.lower()
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            ext = Path(name).suffix.lower()
            if ext not in {".py", ".js", ".ts", ".jsx", ".tsx"}:
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    for idx, line in enumerate(handle, start=1):
                        if query_lower in line.lower():
                            results.append(
                                {
                                    "file_path": path,
                                    "line_number": idx,
                                    "snippet": line.strip()[:200],
                                    "match_type": "keyword",
                                }
                            )
                            if len(results) >= 30:
                                return results
            except Exception:
                continue
    return results


def _syntax_check(file_path: str) -> dict[str, Any]:
    """Run language-aware syntax checks."""
    ext = Path(file_path).suffix.lower()
    if ext == ".py":
        result = _run_subprocess(["python", "-m", "py_compile", file_path], timeout=30)
    elif ext in {".js", ".jsx"}:
        result = _run_subprocess(["node", "--check", file_path], timeout=30)
    elif ext in {".ts", ".tsx"}:
        result = _run_subprocess(["tsc", "--noEmit", file_path], timeout=30)
        if not result.get("ok"):
            result = _run_subprocess(["node", "--check", file_path], timeout=30)
    elif ext == ".json":
        try:
            with open(file_path, encoding="utf-8", errors="strict") as handle:
                json.load(handle)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": _truncate(str(exc), 500)}
    else:
        return {"ok": True, "skipped": True}

    if result.get("ok"):
        return {"ok": True}
    if result.get("error"):
        if "Command not found" in str(result["error"]):
            return {"ok": True, "skipped": True, "reason": result["error"]}
        return {"ok": False, "error": _truncate(result["error"], 500)}
    return {
        "ok": False,
        "error": _truncate((result.get("stderr", "") + result.get("stdout", "")).strip(), 500),
    }


def _load_checkpoints() -> dict[str, Any]:
    if os.path.exists(CHECKPOINT_REGISTRY):
        try:
            with open(CHECKPOINT_REGISTRY, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _save_checkpoints(data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CHECKPOINT_REGISTRY), exist_ok=True)
    with open(CHECKPOINT_REGISTRY, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def list_tools() -> dict[str, Any]:
    """Return explicit tool inventory for tests and diagnostics."""
    return {"tools": TOOL_NAMES.copy(), "count": len(TOOL_NAMES)}


@mcp.tool()
def code_index_project(project_path: str) -> dict[str, Any]:
    """Build a searchable project index in ~/.pawbot/code-indexes."""
    project_root = os.path.abspath(os.path.expanduser(project_path))
    if not os.path.exists(project_root):
        return {"error": f"Project path not found: {project_root}"}

    os.makedirs(INDEX_DIR, exist_ok=True)
    db_path = os.path.join(INDEX_DIR, f"{_get_project_hash(project_root)}.db")
    _init_index_db(db_path)

    total_files = 0
    total_symbols = 0
    started = time.time()

    with sqlite3.connect(db_path) as conn:
        conn.executescript("DELETE FROM symbols; DELETE FROM dependencies; DELETE FROM files;")
        for root, dirs, files in os.walk(project_root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                file_path = os.path.join(root, fname)
                ext = Path(fname).suffix.lower()
                language = LANGUAGE_EXTENSIONS.get(ext)
                if not language:
                    continue
                try:
                    content = _read_text(file_path)
                    line_count = content.count("\n") + 1 if content else 0
                    symbols = _extract_symbols_regex(content, language)

                    for symbol in symbols:
                        conn.execute(
                            (
                                "INSERT INTO symbols (file_path, symbol_type, symbol_name, line_number, language) "
                                "VALUES (?, ?, ?, ?, ?)"
                            ),
                            (
                                file_path,
                                symbol["type"],
                                symbol["name"],
                                symbol["line"],
                                language,
                            ),
                        )
                        total_symbols += 1

                    for symbol in symbols:
                        if symbol["type"] != "import":
                            continue
                        conn.execute(
                            "INSERT INTO dependencies (from_file, to_module, import_type) VALUES (?, ?, ?)",
                            (file_path, symbol["name"], "import"),
                        )

                    conn.execute(
                        "INSERT OR REPLACE INTO files (path, language, line_count, indexed_at) VALUES (?, ?, ?, ?)",
                        (file_path, language, line_count, int(time.time())),
                    )
                    total_files += 1
                except Exception as exc:
                    logger.warning("Index skip %s: %s", file_path, exc)

    elapsed = round(time.time() - started, 2)
    db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    logger.info(
        "Indexed %s: %s files, %s symbols in %ss",
        project_root,
        total_files,
        total_symbols,
        elapsed,
    )
    return {
        "project_path": project_root,
        "db_path": db_path,
        "total_files": total_files,
        "total_symbols": total_symbols,
        "elapsed_s": elapsed,
        "db_size_kb": round(db_size / 1024, 1),
    }


@mcp.tool()
def code_get_context(file_path: str, query: str = "", max_tokens: int = 500) -> dict[str, Any]:
    """Return line-numbered file context, chunked for large files."""
    path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}

    content = _read_text(path)
    lines = content.splitlines()
    numbered = [f"{i + 1}: {line}" for i, line in enumerate(lines)]

    if len(lines) <= 200:
        return {"file": path, "content": "\n".join(numbered), "truncated": False}

    chunks = _split_into_chunks(numbered)
    header = chunks[0] if chunks else []
    if not query.strip():
        result_chunks = chunks[:3]
    else:
        query_words = {word for word in query.lower().split() if word}
        scored: list[tuple[int, list[str]]] = []
        for chunk in chunks[1:]:
            chunk_text = " ".join(chunk).lower()
            score = sum(1 for word in query_words if word in chunk_text)
            scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)

        token_budget = max(50, int(max_tokens))
        result_chunks = [header]
        for score, chunk in scored:
            if score == 0 and len(result_chunks) > 1:
                continue
            chunk_tokens = max(1, len(" ".join(chunk)) // 4)
            if token_budget - chunk_tokens < 0:
                break
            result_chunks.append(chunk)
            token_budget -= chunk_tokens
        if len(result_chunks) == 1 and len(chunks) > 1:
            result_chunks.append(chunks[1])

    output = "\n".join(line for chunk in result_chunks for line in chunk)
    return {
        "file": path,
        "content": output,
        "total_lines": len(lines),
        "truncated": True,
        "chunks_returned": len(result_chunks),
    }


@mcp.tool()
def code_get_dependencies(file_path: str, project_path: str = "") -> dict[str, Any]:
    """Return direct/reverse/transitive dependencies for a file."""
    path = os.path.abspath(os.path.expanduser(file_path))
    root = (
        os.path.abspath(os.path.expanduser(project_path))
        if project_path
        else str(Path(path).resolve().parent.parent)
    )
    db_path = os.path.join(INDEX_DIR, f"{_get_project_hash(root)}.db")
    if not os.path.exists(db_path):
        return {"error": f"No index found. Run code_index_project('{root}') first"}

    with sqlite3.connect(db_path) as conn:
        direct_rows = conn.execute(
            "SELECT to_module FROM dependencies WHERE from_file = ?",
            (path,),
        ).fetchall()

        stem = Path(path).stem
        reverse_rows = conn.execute(
            "SELECT DISTINCT from_file FROM dependencies WHERE to_module LIKE ?",
            (f"%{stem}%",),
        ).fetchall()

        transitive: set[str] = set()
        for (dep,) in direct_rows:
            rows = conn.execute(
                "SELECT DISTINCT to_module FROM dependencies WHERE from_file LIKE ?",
                (f"%{dep.replace('.', '/')}%",),
            ).fetchall()
            transitive.update(item[0] for item in rows)

    return {
        "file": path,
        "direct_imports": [item[0] for item in direct_rows],
        "reverse_dependencies": [item[0] for item in reverse_rows],
        "transitive_deps_2lvl": list(transitive)[:20],
    }


def _search_keyword_mode(query: str, root: str) -> list[dict[str, Any]]:
    """Search source files via ripgrep with Python fallback."""
    rg_cmd = [
        "rg",
        "-n",
        "--glob",
        "*.py",
        "--glob",
        "*.js",
        "--glob",
        "*.ts",
        "--glob",
        "*.jsx",
        "--glob",
        "*.tsx",
        query,
        root,
    ]
    rg = _run_subprocess(rg_cmd, timeout=30)
    if rg.get("ok"):
        results: list[dict[str, Any]] = []
        for line in (rg.get("stdout", "") or "").splitlines()[:40]:
            parts = line.rsplit(":", 2)
            if len(parts) < 3:
                continue
            results.append(
                {
                    "file_path": parts[0],
                    "line_number": int(parts[1]) if parts[1].isdigit() else 0,
                    "snippet": parts[2][:200],
                    "match_type": "keyword",
                }
            )
        return results
    return _search_keyword_fallback(query, root)


def _search_symbol_mode(query: str, root: str) -> list[dict[str, Any]] | dict[str, Any]:
    """Search indexed symbols from the per-project SQLite DB."""
    db_path = os.path.join(INDEX_DIR, f"{_get_project_hash(root)}.db")
    if not os.path.exists(db_path):
        return {"error": f"No index for {root}. Run code_index_project first."}

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT file_path, symbol_type, symbol_name, line_number "
            "FROM symbols WHERE symbol_name LIKE ? LIMIT 20",
            (f"%{query}%",),
        ).fetchall()

    return [
        {
            "file_path": row[0],
            "line_number": row[3],
            "snippet": f"{row[1]}: {row[2]}",
            "match_type": "symbol",
        }
        for row in rows
    ]


def _search_error_mode(query: str, root: str) -> list[dict[str, Any]]:
    """Search log/text files for error patterns and query text."""
    patterns = [query, "Error:", "Exception:", "Traceback", "FAILED"]
    seen_files: set[str] = set()
    results: list[dict[str, Any]] = []

    for pattern in patterns[:2]:
        for root_dir, dirs, files in os.walk(root):
            dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
            for name in files:
                if Path(name).suffix.lower() not in {".log", ".txt"}:
                    continue
                path = os.path.join(root_dir, name)
                if path in seen_files:
                    continue
                seen_files.add(path)
                try:
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        for line_no, line in enumerate(handle, start=1):
                            if pattern.lower() in line.lower():
                                results.append(
                                    {
                                        "file_path": path,
                                        "line_number": line_no,
                                        "snippet": line.strip()[:200],
                                        "match_type": "error",
                                    }
                                )
                                if len(results) >= 20:
                                    return results
                except OSError:
                    continue
    return results


_SEARCH_MODE_ALIASES: dict[str, str] = {
    "semantic": "keyword",
}

_SEARCH_MODES: dict[str, Any] = {
    "keyword": _search_keyword_mode,
    "symbol": _search_symbol_mode,
    "error": _search_error_mode,
}


@mcp.tool()
def code_search(query: str, project_path: str, search_type: str = "keyword") -> dict[str, Any]:
    """Search code by keyword/symbol/error with indexed and fallback methods."""
    root = os.path.abspath(os.path.expanduser(project_path))
    if not os.path.exists(root):
        return {"error": f"Project path not found: {root}"}
    if not query.strip():
        return {"error": "query is required"}

    mode = _SEARCH_MODE_ALIASES.get(search_type.lower().strip(), search_type.lower().strip())
    handler = _SEARCH_MODES.get(mode)
    if not handler:
        return {"error": "search_type must be one of: semantic, keyword, symbol, error"}

    raw_results = handler(query, root)
    if isinstance(raw_results, dict):
        return raw_results

    dedup: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_results:
        key = f"{item['file_path']}:{item['line_number']}:{item['match_type']}"
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)

    return {"query": query, "results": dedup[:20], "count": len(dedup)}


@mcp.tool()
def code_write(file_path: str, content: str, backup: bool = True) -> dict[str, Any]:
    """Write content to file with syntax-check rollback safety."""
    path = os.path.abspath(os.path.expanduser(file_path))
    backup_path: str | None = None

    try:
        if backup and os.path.exists(path):
            timestamp = int(time.time())
            backup_path = f"{path}.bak.{timestamp}"
            shutil.copy2(path, backup_path)

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

        syntax = _syntax_check(path)
        if not syntax.get("ok"):
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, path)
            return {
                "success": False,
                "error": "Syntax check failed - backup restored",
                "syntax_error": syntax.get("error", ""),
                "file": path,
            }

        logger.info("CODE WRITE: %s (%s chars)", path, len(content))
        return {
            "success": True,
            "file": path,
            "bytes_written": len(content.encode("utf-8")),
            "backup": backup_path,
            "syntax_check": "passed",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "file": path}


@mcp.tool()
def code_edit(
    file_path: str,
    find_text: str,
    replace_text: str,
    line_range: list[int] | None = None,
) -> dict[str, Any]:
    """Apply targeted find-and-replace with disambiguation safeguards."""
    path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}
    if not find_text:
        return {"error": "find_text is required"}

    content = _read_text(path)
    occurrences = list(re.finditer(re.escape(find_text), content))
    if not occurrences:
        return {"error": f"Text not found in {path}"}

    selected_match = None
    if len(occurrences) > 1 and not line_range:
        line_numbers = [content[:m.start()].count("\n") + 1 for m in occurrences]
        return {
            "error": f"Found {len(occurrences)} matches. Specify line_range to disambiguate.",
            "match_lines": line_numbers,
        }

    if line_range and len(line_range) == 2:
        start_line = min(line_range)
        end_line = max(line_range)
        for match in occurrences:
            line_number = content[:match.start()].count("\n") + 1
            if start_line <= line_number <= end_line:
                selected_match = match
                break
        if selected_match is None:
            return {
                "error": "No match found in specified line_range",
                "match_lines": [content[:m.start()].count("\n") + 1 for m in occurrences],
            }
    else:
        selected_match = occurrences[0]

    assert selected_match is not None
    new_content = (
        content[: selected_match.start()]
        + replace_text
        + content[selected_match.end() :]
    )

    write_result = code_write(path, new_content, backup=True)
    if not write_result.get("success"):
        return write_result

    change_line = new_content[: selected_match.start()].count("\n") + 1
    lines = new_content.splitlines()
    start = max(0, change_line - 4)
    end = min(len(lines), change_line + 7)
    preview = "\n".join(
        f"{idx + 1}: {line}" for idx, line in enumerate(lines[start:end], start=start)
    )

    return {
        "success": True,
        "file": path,
        "lines_changed": replace_text.count("\n") - find_text.count("\n"),
        "change_preview": preview,
    }


def _check_lint_python(path: str) -> dict[str, Any]:
    """Run ruff check on a Python file."""
    lint = _run_subprocess(["ruff", "check", path], timeout=30)
    if lint.get("error") and "Command not found" in lint["error"]:
        return {"ok": True, "skipped": True, "reason": lint["error"]}
    return {
        "ok": bool(lint.get("ok")),
        "output": _truncate((lint.get("stdout", "") + lint.get("stderr", "")).strip(), 1000),
    }


def _check_lint_js(path: str, root: str) -> dict[str, Any]:
    """Run eslint on JS/TS files, or skip when config/tool is unavailable."""
    has_eslint = any(
        os.path.exists(os.path.join(root, name))
        for name in [".eslintrc", ".eslintrc.js", ".eslintrc.json"]
    )
    if not has_eslint:
        return {"ok": True, "skipped": True, "reason": "No eslint config found"}

    lint = _run_subprocess(["eslint", path], timeout=30)
    if lint.get("error") and "Command not found" in lint["error"]:
        return {"ok": True, "skipped": True, "reason": lint["error"]}
    return {
        "ok": bool(lint.get("ok")),
        "output": _truncate((lint.get("stdout", "") + lint.get("stderr", "")).strip(), 1000),
    }


def _check_typecheck_python(path: str, root: str) -> dict[str, Any] | None:
    """Run mypy for Python files when project config exists."""
    has_mypy = any(
        os.path.exists(os.path.join(root, name))
        for name in ["mypy.ini", "setup.cfg", "pyproject.toml"]
    )
    if not has_mypy:
        return None

    typecheck = _run_subprocess(["mypy", path], timeout=60)
    if typecheck.get("error") and "Command not found" in typecheck["error"]:
        return {"ok": True, "skipped": True, "reason": typecheck["error"]}
    return {
        "ok": bool(typecheck.get("ok")),
        "output": _truncate((typecheck.get("stdout", "") + typecheck.get("stderr", "")).strip(), 1000),
    }


def _find_related_tests(path: str, root: str) -> list[str]:
    """Locate convention-based test files related to a source file."""
    stem = Path(path).stem
    patterns = [
        os.path.join(root, "tests", f"test_{stem}.py"),
        os.path.join(root, f"test_{stem}.py"),
        os.path.join(root, f"{stem}.test.ts"),
        os.path.join(root, f"{stem}.test.js"),
        os.path.join(root, f"{stem}.spec.ts"),
    ]
    return [item for item in patterns if os.path.exists(item)]


@mcp.tool()
def code_run_checks(file_path: str, project_path: str = "") -> dict[str, Any]:
    """Run syntax, lint, optional typecheck, and related tests."""
    path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}
    root = os.path.abspath(os.path.expanduser(project_path)) if project_path else str(Path(path).parent)
    ext = Path(path).suffix.lower()
    checks: dict[str, dict[str, Any]] = {}

    syntax = _syntax_check(path)
    checks["syntax"] = syntax
    if not syntax.get("ok"):
        return {"passed": False, "checks": checks, "halted_at": "syntax", "file": path}

    if ext == ".py":
        checks["lint"] = _check_lint_python(path)
    if ext in {".js", ".jsx", ".ts", ".tsx"}:
        checks["lint"] = _check_lint_js(path, root)

    if ext == ".py":
        typecheck = _check_typecheck_python(path, root)
        if typecheck is not None:
            checks["typecheck"] = typecheck

    test_files = _find_related_tests(path, root)
    if test_files:
        test_result = _run_subprocess(["pytest", *test_files, "-v", "--tb=short"], cwd=root, timeout=120)
        if test_result.get("error") and "Command not found" in test_result["error"]:
            checks["tests"] = {"ok": True, "skipped": True, "reason": test_result["error"]}
        else:
            checks["tests"] = {
                "ok": bool(test_result.get("ok")),
                "output": _truncate(
                    (test_result.get("stdout", "") + test_result.get("stderr", "")),
                    2000,
                ),
                "test_files": test_files,
            }

    passed = all(item.get("ok", True) for item in checks.values())
    return {"passed": bool(passed), "checks": checks, "file": path}


@mcp.tool()
def code_checkpoint(name: str, project_path: str) -> dict[str, Any]:
    """Create a named checkpoint via git stash when possible, else zip."""
    root = os.path.abspath(os.path.expanduser(project_path))
    if not os.path.isdir(root):
        return {"error": f"Project path not found: {root}"}

    timestamp = int(time.time())
    checkpoint_id = f"{name}_{timestamp}"

    git_check = _run_subprocess(["git", "rev-parse", "--is-inside-work-tree"], cwd=root)
    if git_check.get("ok"):
        stash_msg = f"pawbot-checkpoint-{name}-{timestamp}"
        stash = _run_subprocess(["git", "stash", "push", "-u", "-m", stash_msg], cwd=root)
        if stash.get("ok"):
            registry = _load_checkpoints()
            registry[checkpoint_id] = {
                "name": name,
                "method": "git_stash",
                "stash_index": 0,
                "project_path": root,
                "created_at": timestamp,
                "stash_msg": stash_msg,
            }
            _save_checkpoints(registry)
            logger.info("CHECKPOINT: %s via git_stash", checkpoint_id)
            return {"checkpoint_id": checkpoint_id, "method": "git_stash"}

    zip_dir = os.path.expanduser("~/.pawbot/checkpoints")
    os.makedirs(zip_dir, exist_ok=True)
    zip_path = os.path.join(zip_dir, f"{checkpoint_id}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for walk_root, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                full_path = os.path.join(walk_root, fname)
                rel_path = os.path.relpath(full_path, root)
                archive.write(full_path, rel_path)

    registry = _load_checkpoints()
    registry[checkpoint_id] = {
        "name": name,
        "method": "zip",
        "zip_path": zip_path,
        "project_path": root,
        "created_at": timestamp,
    }
    _save_checkpoints(registry)
    size_kb = round(os.path.getsize(zip_path) / 1024, 1)
    logger.info("CHECKPOINT: %s via zip", checkpoint_id)
    return {"checkpoint_id": checkpoint_id, "method": "zip", "size_kb": size_kb}


@mcp.tool()
def code_rollback(checkpoint_id: str) -> dict[str, Any]:
    """Restore project state from a checkpoint."""
    registry = _load_checkpoints()
    if checkpoint_id not in registry:
        return {"error": f"Checkpoint '{checkpoint_id}' not found in registry"}

    checkpoint = registry[checkpoint_id]
    root = checkpoint["project_path"]

    if checkpoint["method"] == "git_stash":
        stash_msg = checkpoint["stash_msg"]
        listing = _run_subprocess(["git", "stash", "list"], cwd=root)
        if not listing.get("ok"):
            return {"error": "git stash list failed", "output": listing.get("error", "")}
        stash_ref = ""
        for line in (listing.get("stdout", "") or "").splitlines():
            if stash_msg in line:
                stash_ref = line.split(":", 1)[0]
                break
        if not stash_ref:
            return {"error": f"Git stash not found for message: {stash_msg}"}
        pop = _run_subprocess(["git", "stash", "pop", stash_ref], cwd=root)
        if not pop.get("ok"):
            output = pop.get("stderr") or pop.get("error") or pop.get("stdout", "")
            return {"error": "git stash pop failed", "output": _truncate(output, 1000)}

    elif checkpoint["method"] == "zip":
        zip_path = checkpoint.get("zip_path", "")
        if not zip_path or not os.path.exists(zip_path):
            return {"error": f"Checkpoint zip not found: {zip_path}"}
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(root)
    else:
        return {"error": f"Unknown checkpoint method: {checkpoint['method']}"}

    logger.info("ROLLBACK: restored checkpoint %s", checkpoint_id)
    return {
        "success": True,
        "checkpoint_id": checkpoint_id,
        "method": checkpoint["method"],
    }


def main() -> None:
    """Run the MCP server over stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
