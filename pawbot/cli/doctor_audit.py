"""Phase status audit — verify MASTER_REFERENCE.md claims against reality.

Provides a `pawbot audit` command that programmatically checks each
claimed "✅ Implemented" phase against the actual codebase.
"""

from __future__ import annotations

from typing import NamedTuple

from loguru import logger


class PhaseCheck(NamedTuple):
    phase: str
    claim: str
    actual: str
    details: str


def audit_phases() -> list[PhaseCheck]:
    """Check each phase's claimed status against reality."""
    results: list[PhaseCheck] = []

    # Phase 1: Memory System
    try:
        from pawbot.agent.memory.sqlite_store import SQLiteFactStore
        from pawbot.agent.memory.router import MemoryRouter  # noqa: F401
        # Try to instantiate — verifies it actually works
        SQLiteFactStore({})
        results.append(PhaseCheck(
            "Phase 1: Memory System", "✅ Implemented", "🟢 Verified",
            "SQLiteFactStore instantiates",
        ))
    except Exception as e:
        results.append(PhaseCheck(
            "Phase 1: Memory System", "✅ Implemented", "🔶 Partial",
            f"SQLiteFactStore failed: {e}",
        ))

    # Phase 2: Agent Loop Intelligence
    try:
        from pawbot.agent.classifier import ComplexityClassifier
        c = ComplexityClassifier()
        score = c.score("hello")
        assert isinstance(score, float)
        results.append(PhaseCheck(
            "Phase 2: Complexity Classifier", "✅ Implemented", "🟢 Verified",
            f"ComplexityClassifier works (score={score:.2f})",
        ))
    except Exception as e:
        results.append(PhaseCheck(
            "Phase 2: Complexity Classifier", "✅ Implemented", "🔶 Partial",
            f"ComplexityClassifier failed: {e}",
        ))

    # Phase 4: Model Router
    try:
        from pawbot.providers.router import ROUTING_TABLE
        assert len(ROUTING_TABLE) > 0
        results.append(PhaseCheck(
            "Phase 4: Model Router", "✅ Implemented", "🟢 Verified",
            f"ROUTING_TABLE has {len(ROUTING_TABLE)} entries",
        ))
    except Exception as e:
        results.append(PhaseCheck(
            "Phase 4: Model Router", "✅ Implemented", "🔶 Partial",
            f"ModelRouter failed: {e}",
        ))

    # Phase 13: LoRA Pipeline
    try:
        from pawbot.agent.skills import SkillsLoader  # noqa: F401
        results.append(PhaseCheck(
            "Phase 13: LoRA Pipeline", "✅ Documented", "🔶 Partial",
            "SkillsLoader importable, LoRA training NOT implemented",
        ))
    except Exception as e:
        results.append(PhaseCheck(
            "Phase 13: LoRA Pipeline", "✅ Documented", "⚪ Stub",
            f"Import failed: {e}",
        ))

    # Phase 14: Security
    try:
        from pawbot.agent.security import ActionGate, InjectionDetector
        ActionGate()
        InjectionDetector()
        results.append(PhaseCheck(
            "Phase 14: Security", "✅ Documented", "🟢 Verified",
            "ActionGate + InjectionDetector instantiate",
        ))
    except Exception as e:
        results.append(PhaseCheck(
            "Phase 14: Security", "✅ Documented", "🔶 Partial",
            f"Security init failed: {e}",
        ))

    # Phase 15: Observability
    try:
        from pawbot.agent.telemetry import PawbotTracer  # noqa: F401
        results.append(PhaseCheck(
            "Phase 15: Observability", "✅ Documented", "🟢 Verified",
            "PawbotTracer importable",
        ))
    except Exception as e:
        results.append(PhaseCheck(
            "Phase 15: Observability", "✅ Documented", "🔶 Partial",
            f"Telemetry import failed: {e}",
        ))

    # Phase 18: Fleet Commander
    try:
        from pawbot.fleet.commander import FleetCommander
        from pawbot.fleet.models import FleetConfig
        FleetCommander(config=FleetConfig())
        results.append(PhaseCheck(
            "Phase 18: Fleet Commander", "✅ Implemented", "🔶 Partial",
            "FleetCommander instantiates but no E2E tests with real LLM",
        ))
    except Exception as e:
        results.append(PhaseCheck(
            "Phase 18: Fleet Commander", "✅ Implemented", "⚪ Stub",
            f"Fleet init failed: {e}",
        ))

    return results
