"""Tree of Thoughts planner — generates and evaluates multiple approaches.

Extracted from loop.py. Activates when complexity > 0.7 AND task_type is
coding_task or architecture.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from pawbot.providers.base import LLMProvider


class ThoughtTreePlanner:
    """
    Generate 3 candidate approaches, evaluate each, return best.
    Falls back to second-best if first fails during execution.
    Only activates when complexity > 0.7 AND task_type in [coding_task, architecture].
    """

    def __init__(self, provider: LLMProvider, model: str, memory: Any):
        self.provider = provider
        self.model = model
        self.memory = memory

    async def plan(self, task: str) -> dict:
        """
        Generate 3 candidate approaches, evaluate each, return best.
        Falls back to second-best if first fails during execution.
        """
        approaches = await self._generate_approaches(task)
        scored = [self._score_approach(a, task) for a in approaches]
        scored.sort(key=lambda x: x["score"], reverse=True)

        # Log rejected approaches to reasoning log
        logger.info(
            "ToT: selected '{}' (rejected: {})",
            scored[0]["name"],
            [a["name"] for a in scored[1:]],
        )

        return {
            "primary": scored[0],
            "fallback": scored[1] if len(scored) > 1 else None,
            "all": scored,
        }

    async def _generate_approaches(self, task: str) -> list[dict]:
        """Use LLM to generate 3 candidate approaches for the task."""
        prompt = f"""Given this task, propose 3 different technical approaches.
Task: {task}

For each approach respond in JSON array:
[
  {{
    "name": "Approach name",
    "core_idea": "One sentence description",
    "trade_offs": "Pros and cons",
    "estimated_complexity": "low|medium|high",
    "risk_level": "low|medium|high"
  }}
]
Respond with ONLY the JSON array."""

        try:
            response = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.7,
                max_tokens=1024,
            )
            content = response.content or ""
            # Try to extract JSON from response
            # Some models wrap in ```json ... ```
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return json.loads(content)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("ToT approach generation failed: {}", e)
            return [{
                "name": "default",
                "core_idea": task,
                "trade_offs": "",
                "estimated_complexity": "medium",
                "risk_level": "medium",
            }]

    def _score_approach(self, approach: dict, task: str) -> dict:
        """Score an approach based on heuristics and memory."""
        score = 0.5  # baseline

        # Penalty for high risk irreversible approaches
        if approach.get("risk_level") == "high":
            score -= 0.2

        # Bonus for low complexity when task is not architecture
        if approach.get("estimated_complexity") == "low":
            score += 0.1

        # Check consistency with past decisions in memory
        if self.memory is not None:
            try:
                past_decisions = self.memory.search(
                    query=f"{task} {approach['name']}",
                    limit=3,
                )
                relevant_decisions = [
                    d for d in past_decisions if d.get("type") == "decision"
                ]
                if relevant_decisions:
                    score += 0.15  # past precedent supports this approach
            except Exception:
                pass  # graceful degradation

        approach["score"] = round(score, 2)
        return approach
