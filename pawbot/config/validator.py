"""
pawbot/config/validator.py

Startup Validator — validates all critical config before the gateway goes live.
Run at application boot before any connections are accepted.
Prints clear, human-readable errors with fix instructions.

Two modes:
    strict  — exit on any error
    warn    — print errors, continue

IMPORTS FROM: pawbot/contracts.py — all path constants and config keys
CALLED BY:    pawbot/cli/commands/run.py — before uvicorn.run()
"""

import os
import sqlite3
from dataclasses import dataclass, field

from pawbot.contracts import (
    SQLITE_DB, CHROMA_DIR, CONFIG_FILE, SOUL_MD,
    config, get_logger
)

logger = get_logger(__name__)


@dataclass
class ValidationIssue:
    """A single validation finding."""
    level:   str  # "ERROR" or "WARN"
    check:   str  # Short check name
    message: str  # Human-readable problem description
    fix:     str  # How to fix it


@dataclass
class ValidationResult:
    """Aggregated result of a full startup validation pass."""
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "ERROR"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "WARN"]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


class StartupValidator:
    """
    Validates Pawbot configuration before the gateway starts.
    Call validate() at boot. If result.ok is False, print errors and optionally exit.
    """

    def validate(self) -> ValidationResult:
        """Run all checks and return a ValidationResult."""
        result = ValidationResult()
        self._check_config_file(result)
        self._check_api_keys(result)
        self._check_sqlite(result)
        self._check_chroma_dir(result)
        self._check_soul_md(result)
        self._check_agents_config(result)
        self._check_ollama_if_needed(result)
        return result

    def _check_config_file(self, r: ValidationResult) -> None:
        path = os.path.expanduser(CONFIG_FILE)
        if not os.path.exists(path):
            r.issues.append(ValidationIssue(
                level   = "ERROR",
                check   = "config_file",
                message = f"Config file not found: {path}",
                fix     = "Run: pawbot onboard  OR  copy .env.example to .env and edit it"
            ))

    def _check_api_keys(self, r: ValidationResult) -> None:
        anthropic_key  = config().get("providers.anthropic.api_key",  "")
        openrouter_key = config().get("providers.openrouter.api_key", "")
        openai_key     = config().get("providers.openai.api_key",     "")
        has_cloud      = any([anthropic_key, openrouter_key, openai_key])
        has_ollama     = bool(config().get("providers.ollama.base_url", ""))

        # Also check if any other provider has keys
        for provider in ["deepseek", "groq", "gemini", "moonshot", "minimax"]:
            key = config().get(f"providers.{provider}.api_key", "")
            if key:
                has_cloud = True
                break

        if not has_cloud and not has_ollama:
            r.issues.append(ValidationIssue(
                level   = "ERROR",
                check   = "api_keys",
                message = "No LLM provider configured. At least one API key or Ollama URL is required.",
                fix     = "Set ANTHROPIC_API_KEY in .env  OR  set providers.ollama.base_url in config.json"
            ))
        elif not has_cloud and has_ollama:
            r.issues.append(ValidationIssue(
                level   = "WARN",
                check   = "api_keys",
                message = "Only Ollama configured — complex tasks may be slow.",
                fix     = "Add ANTHROPIC_API_KEY or OPENROUTER_API_KEY for better performance"
            ))

    def _check_sqlite(self, r: ValidationResult) -> None:
        path   = os.path.expanduser(SQLITE_DB)
        db_dir = os.path.dirname(path)

        if not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
            except OSError as e:
                r.issues.append(ValidationIssue(
                    level   = "ERROR",
                    check   = "sqlite_dir",
                    message = f"Cannot create database directory: {db_dir} — {e}",
                    fix     = f"Ensure parent directory is writable: ls -la {os.path.dirname(db_dir)}"
                ))
                return

        try:
            conn = sqlite3.connect(path)
            conn.execute("SELECT 1")
            conn.close()
        except sqlite3.Error as e:
            r.issues.append(ValidationIssue(
                level   = "ERROR",
                check   = "sqlite_connect",
                message = f"SQLite database error: {path} — {e}",
                fix     = "Delete the database file and run: pawbot onboard"
            ))

    def _check_chroma_dir(self, r: ValidationResult) -> None:
        path = os.path.expanduser(CHROMA_DIR)
        try:
            os.makedirs(path, exist_ok=True)
            test_file = os.path.join(path, ".write_test")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        except OSError as e:
            r.issues.append(ValidationIssue(
                level   = "ERROR",
                check   = "chroma_dir",
                message = f"Chroma directory not writable: {path} — {e}",
                fix     = f"Run: chmod -R u+w {path}  OR  check disk space: df -h"
            ))

    def _check_soul_md(self, r: ValidationResult) -> None:
        path = os.path.expanduser(SOUL_MD)
        if not os.path.exists(path):
            r.issues.append(ValidationIssue(
                level   = "WARN",
                check   = "soul_md",
                message = f"SOUL.md not found: {path} — agent will use default personality.",
                fix     = f"Create the file: touch {path}  then add personality instructions"
            ))

    def _check_agents_config(self, r: ValidationResult) -> None:
        try:
            agents = config().get("agents", [])
            if isinstance(agents, list):
                if not agents:
                    r.issues.append(ValidationIssue(
                        level   = "WARN",
                        check   = "agents_config",
                        message = "No agents configured in config.json — using built-in defaults.",
                        fix     = "Add agents[] array to config.json (see Section 1 Phase 4)"
                    ))
                elif not any(a.get("default") for a in agents if isinstance(a, dict)):
                    r.issues.append(ValidationIssue(
                        level   = "WARN",
                        check   = "agents_default",
                        message = "No default agent configured — fallback will use first agent.",
                        fix     = "Set default: true on one agent in the agents[] array"
                    ))
        except Exception:
            pass  # agents config is an object, not a list — that's fine

    def _check_ollama_if_needed(self, r: ValidationResult) -> None:
        try:
            mechanical_to_local = config().get("routing.mechanical_to_local", False)
        except Exception:
            mechanical_to_local = False

        if not mechanical_to_local:
            return  # Ollama not required

        try:
            import httpx
            base_url = config().get("providers.ollama.base_url", "http://localhost:11434")
            httpx.get(f"{base_url}/api/tags", timeout=2.0)
        except ImportError:
            # httpx not available, try requests
            try:
                import requests
                base_url = config().get("providers.ollama.base_url", "http://localhost:11434")
                requests.get(f"{base_url}/api/tags", timeout=2.0)
            except Exception:
                r.issues.append(ValidationIssue(
                    level   = "ERROR",
                    check   = "ollama_reachable",
                    message = "Ollama not reachable but routing.mechanical_to_local=true.",
                    fix     = "Start Ollama: ollama serve  OR  set routing.mechanical_to_local: false in config"
                ))
        except Exception:
            r.issues.append(ValidationIssue(
                level   = "ERROR",
                check   = "ollama_reachable",
                message = "Ollama not reachable but routing.mechanical_to_local=true.",
                fix     = "Start Ollama: ollama serve  OR  set routing.mechanical_to_local: false in config"
            ))

    def print_report(self, result: ValidationResult) -> None:
        """Print a human-readable validation report to stdout."""
        if result.ok and not result.warnings:
            print("✅ Pawbot config validation: all checks passed")
            return

        print("")
        print("══════════════════════════════════════════")
        print(" PAWBOT STARTUP VALIDATION REPORT")
        print("══════════════════════════════════════════")

        for issue in result.errors:
            print(f" ❌ ERROR [{issue.check}]")
            print(f"    Problem: {issue.message}")
            print(f"    Fix:     {issue.fix}")
            print("")

        for issue in result.warnings:
            print(f" ⚠️  WARN [{issue.check}]")
            print(f"    {issue.message}")
            print(f"    Fix: {issue.fix}")
            print("")

        print("══════════════════════════════════════════")


# ── Singleton ──────────────────────────────────────────────────────────────────
startup_validator = StartupValidator()
