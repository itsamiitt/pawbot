"""Response Verification Pipeline — accuracy checks and hallucination detection.

Phase 21: Post-generation response verification comprising:
  - ResponseVerifier       — checks response accuracy against tool results
  - HallucinationCritic    — scores hallucination risk
  - CitationExtractor      — grounds claims to sources
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("pawbot.agent.verification")


# ══════════════════════════════════════════════════════════════════════════════
#  Data Models
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class VerificationResult:
    """Result of response verification."""

    passed: bool
    risk_score: float = 0.0               # 0.0 (grounded) to 1.0 (likely hallucinated)
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    auto_fixed: bool = False
    original_response: str = ""
    fixed_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "risk_score": round(self.risk_score, 3),
            "issues": self.issues,
            "warnings": self.warnings,
            "auto_fixed": self.auto_fixed,
        }


@dataclass
class Claim:
    """An individual factual claim extracted from a response."""

    text: str
    start_pos: int = 0
    end_pos: int = 0
    claim_type: str = "factual"           # factual | numeric | temporal | reference
    confidence: float = 0.5


@dataclass
class CitedClaim:
    """A claim linked to its source."""

    claim: Claim
    source: str = ""                      # URL, file path, or memory ID
    source_type: str = ""                 # "tool_result" | "memory" | "file" | "web" | "ungrounded"
    match_score: float = 0.0             # 0.0 (no match) to 1.0 (exact match)


@dataclass
class CriticResult:
    """Output of the hallucination critic."""

    risk_score: float                     # 0.0 to 1.0
    confidence_assessment: str            # "high" | "medium" | "low"
    flagged_claims: list[str] = field(default_factory=list)
    grounded_claims: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
#  High-Confidence Patterns
# ══════════════════════════════════════════════════════════════════════════════

# Phrases that indicate unsupported confidence (potential hallucination markers)
OVERCONFIDENCE_PATTERNS = [
    r"(?i)\bI'm certain that\b",
    r"(?i)\bI can confirm that\b",
    r"(?i)\bWithout a doubt\b",
    r"(?i)\bDefinitely\b.*\bis\b",
    r"(?i)\bI guarantee\b",
    r"(?i)\bIt's a fact that\b",
    r"(?i)\balways\b.*\bworks\b",
    r"(?i)\bnever\b.*\bfails\b",
]

# Phrases indicating tool success when tools didn't return success
TOOL_SUCCESS_CLAIMS = [
    r"(?i)\bsuccessfully\s+(completed|executed|created|installed|deployed)\b",
    r"(?i)\bhas been\s+(created|updated|deployed|installed|configured)\b",
    r"(?i)\bI've\s+(completed|created|set up|configured|deployed)\b",
    r"(?i)\bis now\s+(running|active|deployed|configured|working)\b",
]


# ══════════════════════════════════════════════════════════════════════════════
#  Response Verifier
# ══════════════════════════════════════════════════════════════════════════════


class ResponseVerifier:
    """Post-generation response accuracy checking.

    Runs 5 checks on every response:
    1. Tool contradiction — did tools fail but response claims success?
    2. Overconfidence — does response contain unsupported certainty?
    3. Completeness — is the response complete (not truncated)?
    4. Memory contradiction — does response contradict known facts?
    5. Code validity — are code blocks syntactically valid?
    """

    def __init__(self) -> None:
        self._overconfidence_re = [re.compile(p) for p in OVERCONFIDENCE_PATTERNS]
        self._tool_success_re = [re.compile(p) for p in TOOL_SUCCESS_CLAIMS]

    async def verify(
        self,
        response: str,
        tool_results: list[dict[str, Any]] | None = None,
        memory_facts: list[dict[str, Any]] | None = None,
    ) -> VerificationResult:
        """Run all verification checks on a response.

        Returns VerificationResult with risk score and issues.
        """
        issues: list[str] = []
        warnings: list[str] = []
        risk_score = 0.0

        tool_results = tool_results or []
        memory_facts = memory_facts or []

        # Check 1: Tool contradiction
        tool_risk = self._check_tool_contradiction(response, tool_results)
        if tool_risk > 0:
            issues.append("Response claims success but tool execution failed")
            risk_score = max(risk_score, tool_risk)

        # Check 2: Overconfidence
        overconfidence_count = self._check_overconfidence(response)
        if overconfidence_count > 0:
            warnings.append(f"Response contains {overconfidence_count} overconfident phrase(s)")
            risk_score = max(risk_score, min(0.4, overconfidence_count * 0.15))

        # Check 3: Completeness
        if self._check_incomplete(response):
            issues.append("Response appears truncated or incomplete")
            risk_score = max(risk_score, 0.3)

        # Check 4: Memory contradiction
        contradictions = self._check_memory_contradiction(response, memory_facts)
        if contradictions:
            for c in contradictions:
                issues.append(f"Contradicts known fact: {c}")
            risk_score = max(risk_score, 0.6)

        # Check 5: Code validity
        code_issues = self._check_code_blocks(response)
        if code_issues:
            for ci in code_issues:
                warnings.append(f"Code block issue: {ci}")
            risk_score = max(risk_score, 0.2)

        passed = risk_score < 0.5 and len(issues) == 0

        result = VerificationResult(
            passed=passed,
            risk_score=risk_score,
            issues=issues,
            warnings=warnings,
            original_response=response,
        )

        if not passed:
            logger.warning(
                "Verification failed (risk=%.2f, issues=%d)",
                risk_score, len(issues),
            )

        return result

    def _check_tool_contradiction(
        self, response: str, tool_results: list[dict[str, Any]]
    ) -> float:
        """Check if response claims success when tools failed."""
        # Find tools that failed
        failed_tools = [
            r for r in tool_results
            if r.get("error") or r.get("success") is False
            or r.get("status") == "error"
        ]

        if not failed_tools:
            return 0.0

        # Check if response claims success
        for pattern in self._tool_success_re:
            if pattern.search(response):
                return 0.8  # High risk — claiming success despite failure

        return 0.0

    def _check_overconfidence(self, response: str) -> int:
        """Count overconfident phrases in the response."""
        count = 0
        for pattern in self._overconfidence_re:
            count += len(pattern.findall(response))
        return count

    def _check_incomplete(self, response: str) -> bool:
        """Check if the response appears truncated."""
        stripped = response.rstrip()
        if not stripped:
            return True

        # Check for mid-sentence truncation
        if stripped[-1] not in ".!?`\"')]:}>*\n" and len(stripped) > 100:
            # Might be cut off
            last_line = stripped.split("\n")[-1].strip()
            if last_line and last_line[-1] not in ".!?`\"')]:}>*":
                return True

        # Check for unclosed code blocks
        open_blocks = stripped.count("```")
        if open_blocks % 2 != 0:
            return True

        return False

    def _check_memory_contradiction(
        self, response: str, memory_facts: list[dict[str, Any]]
    ) -> list[str]:
        """Check if response contradicts known facts in memory."""
        contradictions: list[str] = []
        response_lower = response.lower()

        for fact in memory_facts:
            content = fact.get("content", "").lower()
            if not content:
                continue

            # Simple negation check
            negation_pairs = [
                ("is not", "is"),
                ("doesn't", "does"),
                ("don't", "do"),
                ("can't", "can"),
                ("never", "always"),
                ("false", "true"),
            ]

            for neg, pos in negation_pairs:
                if neg in response_lower and pos in content:
                    # Check if they're talking about the same subject
                    words = set(content.split()) & set(response_lower.split())
                    if len(words) >= 3:  # Enough overlap to be about the same topic
                        contradictions.append(content[:80])
                        break

        return contradictions

    def _check_code_blocks(self, response: str) -> list[str]:
        """Check code blocks for basic syntax issues."""
        issues: list[str] = []
        code_block_re = re.compile(r"```(\w*)\n(.*?)\n```", re.DOTALL)

        for match in code_block_re.finditer(response):
            lang = match.group(1).lower()
            code = match.group(2)

            if lang in ("python", "py"):
                try:
                    compile(code, "<response>", "exec")
                except SyntaxError as e:
                    issues.append(f"Python syntax error at line {e.lineno}: {e.msg}")

            elif lang in ("json",):
                import json
                try:
                    json.loads(code)
                except json.JSONDecodeError as e:
                    issues.append(f"Invalid JSON: {e.msg}")

        return issues


# ══════════════════════════════════════════════════════════════════════════════
#  Hallucination Critic
# ══════════════════════════════════════════════════════════════════════════════


class HallucinationCritic:
    """Scores hallucination risk of a response.

    Analyses how well-grounded the response is by checking:
    - What % of claims are backed by tool results
    - What % of claims are backed by memory facts
    - What % of claims are ungrounded (hallucination risk)
    """

    async def score(
        self,
        response: str,
        tool_results: list[dict[str, Any]] | None = None,
        memory_facts: list[dict[str, Any]] | None = None,
    ) -> CriticResult:
        """Score hallucination risk of a response."""
        tool_results = tool_results or []
        memory_facts = memory_facts or []

        # Extract claims from response
        claims = self._extract_claims(response)
        if not claims:
            return CriticResult(
                risk_score=0.0,
                confidence_assessment="high",
                recommendations=["No factual claims to verify"],
            )

        # Gather evidence corpus
        evidence = self._build_evidence_corpus(tool_results, memory_facts)

        # Score each claim
        grounded: list[str] = []
        flagged: list[str] = []

        for claim in claims:
            is_grounded = self._is_claim_grounded(claim, evidence)
            if is_grounded:
                grounded.append(claim)
            else:
                flagged.append(claim)

        total = len(claims)
        grounded_pct = len(grounded) / total if total > 0 else 1.0
        risk_score = 1.0 - grounded_pct

        # Determine confidence assessment
        if risk_score <= 0.2:
            confidence = "high"
        elif risk_score <= 0.5:
            confidence = "medium"
        else:
            confidence = "low"

        recommendations: list[str] = []
        if flagged:
            recommendations.append(
                f"{len(flagged)} claim(s) lack evidence — consider adding sources or caveats"
            )

        return CriticResult(
            risk_score=risk_score,
            confidence_assessment=confidence,
            flagged_claims=flagged,
            grounded_claims=grounded,
            recommendations=recommendations,
        )

    def _extract_claims(self, response: str) -> list[str]:
        """Extract individual factual claims from a response.

        Uses sentence splitting and filters for factual statements.
        """
        # Split into sentences
        sentences = re.split(r'[.!?]\s+', response)
        claims: list[str] = []

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence or len(sentence) < 15:
                continue

            # Skip meta-commentary, questions, and code blocks
            if sentence.startswith(("```", "- ", "* ", "#", "|", ">")):
                continue
            if sentence.endswith("?"):
                continue
            if any(phrase in sentence.lower() for phrase in [
                "i think", "perhaps", "maybe", "possibly", "i'm not sure",
                "it seems", "it might", "could be",
            ]):
                continue  # Hedged claims are fine

            # Keep factual-sounding claims
            if any(indicator in sentence.lower() for indicator in [
                " is ", " are ", " was ", " were ", " has ", " have ",
                " will ", " can ", " does ", " did ",
                "version", "file", "function", "class",
                "error", "output", "result", "returns",
            ]):
                claims.append(sentence[:200])

        return claims

    def _build_evidence_corpus(
        self,
        tool_results: list[dict[str, Any]],
        memory_facts: list[dict[str, Any]],
    ) -> set[str]:
        """Build a set of evidence strings from tool results and memory."""
        evidence: set[str] = set()

        for result in tool_results:
            for key in ("output", "content", "result", "data", "text"):
                val = result.get(key, "")
                if val:
                    # Add normalized words
                    evidence.update(str(val).lower().split())

        for fact in memory_facts:
            content = fact.get("content", "")
            if content:
                evidence.update(content.lower().split())

        return evidence

    def _is_claim_grounded(self, claim: str, evidence: set[str]) -> bool:
        """Check if a claim is grounded in the evidence corpus.

        Uses word overlap as a proxy for grounding.
        """
        claim_words = set(claim.lower().split())
        # Remove common stop words
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "can", "may", "might", "shall",
            "this", "that", "these", "those", "it", "its",
            "and", "or", "but", "nor", "not", "so", "yet",
            "in", "on", "at", "to", "for", "of", "with", "by", "from",
            "i", "you", "we", "they", "he", "she",
        }
        meaningful_words = claim_words - stop_words
        if not meaningful_words:
            return True  # No meaningful words to check

        overlap = meaningful_words & evidence
        overlap_ratio = len(overlap) / len(meaningful_words)

        return overlap_ratio >= 0.4  # 40% word overlap threshold


# ══════════════════════════════════════════════════════════════════════════════
#  Citation Extractor
# ══════════════════════════════════════════════════════════════════════════════


class CitationExtractor:
    """Extract and attach source citations to responses.

    Identifies claims in a response and links them to sources:
    - Tool results (file reads, command outputs, search results)
    - Memory entries
    - Web sources
    """

    def extract_and_cite(
        self,
        response: str,
        tool_results: list[dict[str, Any]] | None = None,
        memory_facts: list[dict[str, Any]] | None = None,
    ) -> list[CitedClaim]:
        """Extract claims and match to sources."""
        tool_results = tool_results or []
        memory_facts = memory_facts or []

        # Extract claims
        critic = HallucinationCritic()
        raw_claims = critic._extract_claims(response)

        cited: list[CitedClaim] = []
        for claim_text in raw_claims:
            claim = Claim(text=claim_text)
            best_match = self._find_best_source(claim_text, tool_results, memory_facts)
            cited.append(CitedClaim(
                claim=claim,
                source=best_match.get("source", ""),
                source_type=best_match.get("type", "ungrounded"),
                match_score=best_match.get("score", 0.0),
            ))
        return cited

    def _find_best_source(
        self,
        claim: str,
        tool_results: list[dict[str, Any]],
        memory_facts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Find the best matching source for a claim."""
        best: dict[str, Any] = {"source": "", "type": "ungrounded", "score": 0.0}
        claim_words = set(claim.lower().split())

        # Check tool results
        for result in tool_results:
            for key in ("output", "content", "result"):
                val = str(result.get(key, "")).lower()
                if not val:
                    continue
                val_words = set(val.split())
                overlap = len(claim_words & val_words) / max(len(claim_words), 1)
                if overlap > best["score"]:
                    source_name = result.get("tool", result.get("name", "tool"))
                    best = {"source": source_name, "type": "tool_result", "score": overlap}

        # Check memory facts
        for fact in memory_facts:
            content = fact.get("content", "").lower()
            if not content:
                continue
            fact_words = set(content.split())
            overlap = len(claim_words & fact_words) / max(len(claim_words), 1)
            if overlap > best["score"]:
                fact_id = fact.get("id", fact.get("type", "memory"))
                best = {"source": fact_id, "type": "memory", "score": overlap}

        return best

    def format_citations(self, cited_claims: list[CitedClaim]) -> str:
        """Format citations as a markdown footnotes-style appendix."""
        if not cited_claims:
            return ""

        sourced = [c for c in cited_claims if c.source_type != "ungrounded"]
        if not sourced:
            return ""

        lines = ["\n---", "**Sources:**"]
        for i, cited in enumerate(sourced, 1):
            source_label = f"[{cited.source_type}] {cited.source}"
            lines.append(f"[{i}] {source_label}")

        return "\n".join(lines)
