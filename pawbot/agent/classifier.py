"""Complexity classification and system path selection.

Extracted from loop.py — these determine the execution path (System 1/1.5/2)
that the agent loop follows for each incoming message.
"""

from __future__ import annotations

import re

# ── Complexity Score Thresholds ──────────────────────────────────────────────
# These constants are referenced in loop.py, context.py, and router.py
# DO NOT change these values — they are in MASTER_REFERENCE.md

SYSTEM_1_MAX = 0.3    # fast path
SYSTEM_1_5_MAX = 0.7  # ReAct path
SYSTEM_2_MIN = 0.7    # deliberative path

SYSTEM_PATHS = {
    "system_1": {
        "max_iterations": 20,   # raised from 5 — simple tasks still need tool calls
        "context_mode": "minimal",
        "model_hint": "cheap",
    },
    "system_1_5": {
        "max_iterations": 40,
        "context_mode": "standard",
        "model_hint": "balanced",
    },
    "system_2": {
        "max_iterations": 100,
        "context_mode": "full",
        "model_hint": "best",
        "use_tree_of_thoughts": True,
        "pre_task_reflection": True,
    },
}

# Phase 18: Fleet Commander trigger keywords
_FLEET_TRIGGER_KEYWORDS = {
    "deploy across", "build and test", "coordinate",
    "fleet", "multi-step", "parallel tasks",
    "distribute", "orchestrate", "pipeline",
}


def get_system_path(complexity_score: float) -> str:
    """Map a complexity score to a system path name."""
    if complexity_score <= SYSTEM_1_MAX:
        return "system_1"
    elif complexity_score <= SYSTEM_1_5_MAX:
        return "system_1_5"
    else:
        return "system_2"


class ComplexityClassifier:
    """
    Scores an incoming message from 0.0 (trivial) to 1.0 (maximum complexity).
    Score determines which execution path the agent takes:
    - System 1 (≤0.3): Fast path — minimal context, cheap model, max 5 iterations
    - System 1.5 (0.3–0.7): ReAct path — standard context, balanced model
    - System 2 (>0.7): Deliberative — full context, best model, Tree of Thoughts
    """

    KEYWORD_SIGNALS = {
        "refactor", "deploy", "debug", "architect", "design",
        "implement", "migrate", "integrate", "analyze", "optimize"
    }

    URGENCY_SIGNALS = {"urgent", "asap", "broken", "down"}

    FAILURE_SIGNALS = {"error", "failed", "broke", "crash", "exception", "traceback"}

    def score(self, message: str) -> float:
        """Score an incoming message for complexity (0.0 to 1.0)."""
        score = 0.0
        words = message.lower().split()
        word_set = set(words)

        # Signal: long message
        if len(words) > 100:
            score += 0.2

        # Signal: contains complexity keywords
        if word_set & self.KEYWORD_SIGNALS:
            score += 0.2

        # Signal: references multiple files/components (look for .py, .js, / patterns)
        file_refs = re.findall(r'\b\w+\.\w{2,4}\b|/\w+', message)
        if len(file_refs) >= 2:
            score += 0.15

        # Signal: deep "why" or "how does" questions
        if any(message.lower().startswith(p) for p in ["why ", "how does "]):
            score += 0.1

        # Signal: references past failure
        if word_set & self.FAILURE_SIGNALS:
            score += 0.15

        # Signal: spans multiple topics (crude check: sentence count > 3)
        sentences = re.split(r'[.!?]', message)
        if len(sentences) > 3:
            score += 0.1

        # Signal: urgency
        if word_set & self.URGENCY_SIGNALS:
            score += 0.1

        return min(1.0, round(score, 2))
