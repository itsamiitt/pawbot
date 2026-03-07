"""Doctor & audit CLI commands — extracted from commands.py (Phase 0)."""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.table import Table

from pawbot.cli._helpers import console

# These are registered as direct commands on the main app.


def doctor():
    """Run full system health check."""
    checks: list[tuple[str, str, str]] = []
    critical_failures = 0

    def check(name: str, fn, fix: str = "", severity: str = "warning"):
        nonlocal critical_failures
        try:
            result = fn()
            if result is False:
                raise RuntimeError("check returned False")
            checks.append(("✓", name, ""))
        except Exception as e:
            checks.append(("✗", name, fix or str(e)[:120]))
            if severity == "critical":
                critical_failures += 1

    check(
        "Config file exists",
        lambda: os.path.exists(os.path.expanduser("~/.pawbot/config.json")),
        "Run: pawbot onboard",
        severity="critical",
    )

    from pawbot.config.loader import load_config
    from pawbot.config.validation import summarize_issues, validate_runtime_config

    config = load_config()
    issues = validate_runtime_config(config)
    critical, warnings = summarize_issues(issues)
    for issue in critical:
        checks.append(("✗", issue.check, issue.fix or issue.message))
        critical_failures += 1
    for issue in warnings:
        checks.append(("✗", issue.check, issue.fix or issue.message))

    check(
        "SQLite database",
        lambda: os.path.exists(os.path.expanduser("~/.pawbot/memory/facts.db")),
        "Run pawbot agent -m 'test' to initialise",
    )

    check(
        "ChromaDB directory",
        lambda: os.path.exists(os.path.expanduser("~/.pawbot/memory/chroma")),
        "Run pawbot agent -m 'test' to initialise",
    )

    def check_ollama():
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)

    check("Ollama running", check_ollama, "Start Ollama: ollama serve")

    mcp_base = Path(__file__).resolve().parent.parent
    for name in ["server_control", "deploy", "coding", "browser", "app_control"]:
        server_dir = mcp_base / "mcp-servers" / name
        check(
            f"MCP server: {name}",
            lambda d=server_dir: d.exists(),
            f"Phase not implemented for {name}",
        )

    def check_security():
        from pawbot.agent.security import ActionGate  # noqa: F401

    check("Security module", check_security, "Phase 14 not installed")

    def check_telemetry():
        from pawbot.agent.telemetry import PawbotTracer, summarize_trace_file  # noqa: F401

    check("Telemetry module", check_telemetry, "Phase 15 not installed")

    def check_schema():
        load_config()

    check("Config schema valid", check_schema, "Run: pawbot config validate", severity="critical")

    from pawbot.cli.formatter import CLIFormatter

    fmt = CLIFormatter(console)
    fmt.print_doctor_results(checks)

    if critical_failures > 0:
        raise typer.Exit(1)


def audit():
    """Audit phase implementation status against MASTER_REFERENCE claims."""
    from pawbot.cli.doctor_audit import audit_phases

    results = audit_phases()

    table = Table(title="Phase Implementation Audit")
    table.add_column("Phase", style="cyan")
    table.add_column("Claimed")
    table.add_column("Actual")
    table.add_column("Details", max_width=50)

    for result in results:
        table.add_row(result.phase, result.claim, result.actual, result.details)

    console.print(table)

    verified = sum(1 for r in results if "🟢" in r.actual)
    partial = sum(1 for r in results if "🔶" in r.actual)
    stub = sum(1 for r in results if "⚪" in r.actual)
    console.print(f"\n[green]Verified: {verified}[/green]  [yellow]Partial: {partial}[/yellow]  [dim]Stub: {stub}[/dim]")
