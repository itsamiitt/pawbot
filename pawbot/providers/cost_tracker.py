"""LLM cost tracking — logs usage to SQLite for analysis.

Phase 3: Records every LLM API call to the llm_usage table
(created by Phase 2 migration v3) for cost analysis and budgeting.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

from loguru import logger


# Approximate cost per 1M tokens (input, output) in USD
MODEL_COSTS: dict[str, tuple[float, float]] = {
    # Anthropic via OpenRouter
    "anthropic/claude-sonnet-4-6": (3.0, 15.0),
    "anthropic/claude-sonnet-4-5": (3.0, 15.0),
    "anthropic/claude-opus-4-6": (15.0, 75.0),
    "anthropic/claude-opus-4-5": (15.0, 75.0),
    "anthropic/claude-haiku-4-5": (0.80, 4.0),
    "anthropic/claude-haiku-4-5-20251001": (1.0, 5.0),

    # Direct Anthropic
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-opus-4-6": (15.0, 75.0),
    "claude-opus-4-5": (15.0, 75.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),

    # OpenAI
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-3.5-turbo": (0.50, 1.50),

    # Ollama (free, local)
    "llama3.1:8b": (0.0, 0.0),
    "llama3.1:70b": (0.0, 0.0),
    "nomic-embed-text": (0.0, 0.0),
    "deepseek-coder:6.7b": (0.0, 0.0),
    "codellama:13b": (0.0, 0.0),
    "mistral:7b": (0.0, 0.0),
}


class CostTracker:
    """Track LLM API usage and costs.

    Usage::

        tracker = CostTracker()
        tracker.record("openrouter", "anthropic/claude-sonnet-4-6", 1000, 500, 2345.0)
        summary = tracker.get_usage_summary(hours=24)
    """

    def __init__(self, db_path: str = "~/.pawbot/memory/facts.db"):
        self.db_path = os.path.expanduser(db_path)

    def record(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        task_type: str = "",
        session_key: str = "",
    ) -> None:
        """Record a single LLM call."""
        cost = self._estimate_cost(model, input_tokens, output_tokens)

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO llm_usage "
                "(timestamp, provider, model, input_tokens, output_tokens, "
                "latency_ms, cost_usd, task_type, session_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), provider, model, input_tokens, output_tokens,
                 latency_ms, cost, task_type, session_key),
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.debug("Cost tracking write failed (table may not exist yet)")

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD."""
        costs = MODEL_COSTS.get(model)
        if costs is None:
            # Try partial match
            for key, val in MODEL_COSTS.items():
                if key in model or model in key:
                    costs = val
                    break
        if costs is None:
            return 0.0

        input_cost, output_cost = costs
        return (input_tokens * input_cost + output_tokens * output_cost) / 1_000_000

    def get_usage_summary(self, hours: int = 24) -> dict[str, Any]:
        """Get usage summary for the last N hours."""
        try:
            conn = sqlite3.connect(self.db_path)
            since = time.time() - (hours * 3600)
            cursor = conn.execute(
                "SELECT provider, model, "
                "SUM(input_tokens), SUM(output_tokens), "
                "SUM(cost_usd), COUNT(*), AVG(latency_ms) "
                "FROM llm_usage WHERE timestamp > ? "
                "GROUP BY provider, model "
                "ORDER BY SUM(cost_usd) DESC",
                (since,),
            )
            rows = cursor.fetchall()
            conn.close()

            return {
                "period_hours": hours,
                "by_model": [
                    {
                        "provider": r[0],
                        "model": r[1],
                        "input_tokens": r[2] or 0,
                        "output_tokens": r[3] or 0,
                        "cost_usd": round(r[4] or 0, 4),
                        "calls": r[5] or 0,
                        "avg_latency_ms": round(r[6] or 0, 1),
                    }
                    for r in rows
                ],
                "total_cost_usd": round(sum(r[4] or 0 for r in rows), 4),
                "total_calls": sum(r[5] or 0 for r in rows),
            }
        except Exception:
            return {"period_hours": hours, "by_model": [], "total_cost_usd": 0, "total_calls": 0}

    def get_daily_breakdown(self, days: int = 7) -> list[dict[str, Any]]:
        """Get daily cost breakdown for the last N days."""
        try:
            conn = sqlite3.connect(self.db_path)
            since = time.time() - (days * 86400)
            cursor = conn.execute(
                "SELECT date(timestamp, 'unixepoch') as day, "
                "SUM(cost_usd), COUNT(*), "
                "SUM(input_tokens), SUM(output_tokens) "
                "FROM llm_usage WHERE timestamp > ? "
                "GROUP BY day ORDER BY day DESC",
                (since,),
            )
            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    "date": r[0],
                    "cost_usd": round(r[1] or 0, 4),
                    "calls": r[2] or 0,
                    "input_tokens": r[3] or 0,
                    "output_tokens": r[4] or 0,
                }
                for r in rows
            ]
        except Exception:
            return []
