"""Task result validation — verify worker output addresses the task (Phase 5).

Validates that worker outputs actually contain substantive, relevant
content before accepting them as complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """Result of validating a task output."""

    is_valid: bool
    score: float           # 0.0-1.0 confidence that task was addressed
    issues: list[str]      # Why it might not be valid
    suggestion: str = ""   # What to do if invalid


class TaskValidator:
    """Validates that worker outputs actually address their assigned tasks.

    Scoring:
    - Starts at 1.0 (perfect)
    - Deducts for: empty output, too short, error prefixes, low keyword overlap
    - Valid if final score > 0.3
    """

    MIN_OUTPUT_LENGTH = 20        # Minimum chars for a valid response
    ERROR_PREFIXES = (
        "error:", "failed:", "i couldn't", "i'm unable", "cannot",
        "i apologize", "i'm sorry, i can't",
    )

    def validate(self, task_description: str, output: str) -> ValidationResult:
        """Validate a task result against its description.

        Returns:
            ValidationResult with validity assessment
        """
        issues: list[str] = []
        score = 1.0

        # Check 1: Non-empty output
        if not output or not output.strip():
            return ValidationResult(
                is_valid=False, score=0.0,
                issues=["Empty output"],
                suggestion="Re-execute task or escalate to human",
            )

        stripped = output.strip()
        output_lower = stripped.lower()

        # Check 2: Minimum length
        if len(stripped) < self.MIN_OUTPUT_LENGTH:
            issues.append(f"Output too short ({len(stripped)} chars)")
            score -= 0.3

        # Check 3: Error indicators
        for prefix in self.ERROR_PREFIXES:
            if output_lower.startswith(prefix):
                issues.append(f"Output starts with error indicator: '{prefix}'")
                score -= 0.5
                break

        # Check 4: Keyword relevance (simple heuristic)
        # Remove common stop words to focus on content words
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "to", "for",
                      "of", "in", "on", "and", "or", "but", "with", "that", "this"}
        task_words = {w for w in task_description.lower().split() if w not in stop_words and len(w) > 2}
        output_words = set(output_lower.split())
        overlap = len(task_words & output_words)
        if overlap < 2 and len(task_words) > 3:
            issues.append("Low keyword overlap between task and output")
            score -= 0.2

        # Check 5: Repetition detection (signs of LLM looping)
        lines = stripped.split("\n")
        if len(lines) > 5:
            unique_lines = set(line.strip() for line in lines if line.strip())
            if len(unique_lines) < len(lines) * 0.3:
                issues.append("High repetition detected (possible LLM loop)")
                score -= 0.4

        is_valid = score > 0.3
        suggestion = "" if is_valid else "Consider re-executing with more specific instructions"

        return ValidationResult(
            is_valid=is_valid,
            score=max(0.0, min(1.0, score)),
            issues=issues,
            suggestion=suggestion,
        )
