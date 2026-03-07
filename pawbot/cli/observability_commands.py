"""Observability CLI commands — metrics and traces (extracted from commands.py, Phase 0)."""

from __future__ import annotations

import os

import typer
from rich.table import Table

from pawbot.cli._helpers import console


@typer.Typer()
def _dummy():
    pass


# These are registered as direct commands on the main app, not as a sub-group.


def metrics():
    """Show session metrics summary."""
    from pawbot.agent.telemetry import PawbotTracer, summarize_trace_file
    from pawbot.config.loader import load_config

    config = load_config()
    tracer = PawbotTracer(config.model_dump(by_alias=False))
    summary = tracer.session_summary()
    slo = summarize_trace_file(config.observability.trace_file, limit=500)

    table = Table(title="Session Metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total spans", str(summary.get("total_spans", 0)))
    table.add_row("Total tokens", f"{summary.get('total_tokens', 0):,}")
    table.add_row("Errors", str(summary.get("errors", 0)))
    table.add_row("Elapsed", f"{summary.get('session_elapsed_seconds', 0):.1f}s")
    table.add_row("SLO success", f"{slo.get('success_rate_pct', 100):.2f}%")
    table.add_row("SLO latency p50", f"{slo.get('latency_p50_ms', 0):.2f} ms")
    table.add_row("SLO latency p95", f"{slo.get('latency_p95_ms', 0):.2f} ms")

    for tool, stats in summary.get("tool_calls", {}).items():
        table.add_row(f"Tool: {tool}", f"{stats['count']} calls")
    for model, stats in summary.get("model_calls", {}).items():
        table.add_row(f"Model: {model}", f"{stats['count']} calls, {stats['tokens']} tokens")

    mem = summary.get("memory_ops", {})
    table.add_row("Memory saves", str(mem.get("save", 0)))
    table.add_row("Memory searches", str(mem.get("search", 0)))
    table.add_row("Cache hits", str(mem.get("cache_hits", 0)))

    console.print(table)


def traces(
    n: int = typer.Option(20, "--limit", "-n", help="Number of recent traces to show"),
):
    """Show recent trace spans."""
    import json

    from pawbot.config.loader import load_config

    config = load_config()
    obs_cfg = config.observability
    trace_file = os.path.expanduser(obs_cfg.trace_file)

    if not os.path.exists(trace_file):
        console.print("[yellow]No trace file found yet.[/yellow]")
        console.print(f"[dim]Expected at: {trace_file}[/dim]")
        return

    try:
        with open(trace_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        spans = [json.loads(line) for line in lines[-n:] if line.strip()]
    except Exception as e:
        console.print(f"[red]Failed to read traces: {e}[/red]")
        return

    if not spans:
        console.print("[yellow]No traces recorded yet.[/yellow]")
        return

    table = Table(title=f"Recent Traces ({len(spans)} spans)")
    table.add_column("Trace", style="dim", width=8)
    table.add_column("Span", style="dim", width=8)
    table.add_column("Name", style="cyan")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Attributes", max_width=40)

    for span in spans:
        status_str = span.get("status", "ok")
        status_color = "green" if status_str == "ok" else "red"
        attrs = span.get("attributes", {})
        attr_preview = ", ".join(f"{k}={v}" for k, v in list(attrs.items())[:3])
        table.add_row(
            span.get("trace_id", "")[:8],
            span.get("span_id", "")[:8],
            span.get("name", ""),
            f"[{status_color}]{status_str}[/{status_color}]",
            f"{span.get('duration_ms', 0):.1f}ms",
            attr_preview[:40] if attr_preview else "—",
        )
    console.print(table)
