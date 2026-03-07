"""
pawbot/agent/compactor.py

Context Compactor — prevents token overflow crashes.
Called once before every LLM call in the agent loop.

Strategy:
    1. Estimate total token count of the message list
    2. If below COMPACTION_THRESHOLD × model_limit: do nothing (fast path)
    3. If above threshold: summarise the oldest turns using a cheap local model
    4. Replace old turns with a single [COMPACTED] system message
    5. Always keep the last KEEP_LAST_N_TURNS turns intact
    6. Never compact the system prompt or any [COMPACTED] entries already present

IMPORTS FROM: pawbot/contracts.py — LLMRequest, LLMResponse, ProviderName,
              TaskType, config(), get_logger()
CALLED BY:    pawbot/agent/loop.py — before model_router.complete()
SINGLETON:    compactor — import this everywhere
"""

from pawbot.contracts import get_logger

logger = get_logger(__name__)

# ── Context limits per model ────────────────────────────────────────────────────
# Extend this dict as new models are added.
# Keys must match the exact model string used in LLMRequest.model.
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # Anthropic
    "claude-opus-4-6":           200000,
    "claude-sonnet-4-6":         180000,
    "claude-haiku-4-5-20251001": 200000,
    "anthropic/claude-opus-4-5": 200000,
    "anthropic/claude-sonnet-4-5": 200000,
    # OpenAI
    "gpt-4o":                    128000,
    "gpt-4o-mini":               128000,
    # Ollama / local
    "llama3.1:8b":                 8192,
    "llama3.1:70b":               65536,
    "mistral:7b":                  8192,
    "deepseek-coder:6.7b":        16384,
    "codellama:13b":              16384,
}

# Default context limit for unknown models
DEFAULT_CONTEXT_LIMIT: int = 128000

COMPACTION_RESERVE: int     = 6144  # tokens always kept free for model reply
COMPACTION_THRESHOLD: float = 0.82  # trigger compaction at 82% of limit
KEEP_LAST_N_TURNS: int      = 6     # always preserve the most recent N turns
MIN_TURNS_TO_COMPACT: int   = 4     # do not compact if fewer than 4 turns eligible


class ContextCompactor:
    """
    Prevents hard token overflow by summarising old conversation turns.
    Thread-safe: stateless — all state comes from the messages list argument.
    """

    async def compact_if_needed(
        self,
        messages: list[dict],
        model: str,
    ) -> list[dict]:
        """
        Main entry point. Call this before every LLM API call.

        Args:
            messages: The full message list about to be sent to the model.
                      Each dict has {role: str, content: str}.
            model:    The model string (e.g. "claude-sonnet-4-6").
                      Used to look up the context limit.

        Returns:
            The (possibly compacted) message list.
            If no compaction needed, returns the same list unchanged.
        """
        limit = MODEL_CONTEXT_LIMITS.get(model, DEFAULT_CONTEXT_LIMIT) - COMPACTION_RESERVE
        total = self._estimate_tokens(messages)

        if total < limit * COMPACTION_THRESHOLD:
            return messages  # fast path — no action needed

        # Separate system prompt from conversation turns
        system_msgs = [m for m in messages if m.get("role") == "system"]
        convo_msgs  = [m for m in messages if m.get("role") != "system"]

        # Split into compactable (old) and keep (recent)
        if len(convo_msgs) <= KEEP_LAST_N_TURNS:
            to_compact = []
            keep = convo_msgs
        else:
            to_compact = convo_msgs[:-KEEP_LAST_N_TURNS]
            keep       = convo_msgs[-KEEP_LAST_N_TURNS:]

        if len(to_compact) < MIN_TURNS_TO_COMPACT:
            logger.warning(
                f"Compactor: context near limit but too few turns to compact "
                f"({len(to_compact)} turns, need {MIN_TURNS_TO_COMPACT}). "
                f"Consider reducing memory injection or increasing model."
            )
            return messages  # cannot compact further — caller must handle

        # Summarise old turns
        summary_text = await self._summarise(to_compact, model)

        compacted_msg = {
            "role": "system",
            "content": (
                f"[COMPACTED — {len(to_compact)} earlier turns] "
                f"Summary of earlier conversation: {summary_text}"
            )
        }

        result = system_msgs + [compacted_msg] + keep
        tokens_after = self._estimate_tokens(result)

        logger.info(
            f"Compactor: compacted {len(to_compact)} turns → 1 summary. "
            f"Tokens: ~{total} → ~{tokens_after} (model={model})"
        )
        return result

    def _estimate_tokens(self, messages: list[dict]) -> int:
        """
        Rough token estimate: character_count / 4.
        Fast approximation — exact count not needed, only order of magnitude.
        """
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        # ~4 chars per token on average for English text
        return int(total_chars / 4)

    async def _summarise(self, messages: list[dict], current_model: str) -> str:
        """
        Summarise a list of conversation messages using a cheap local model.
        Falls back to simple extraction if Ollama/model router is not available.
        """
        # Build a readable transcript for the summariser
        transcript_parts = []
        for m in messages[:20]:  # cap at 20 turns to keep prompt manageable
            role    = m.get("role", "unknown")
            content = str(m.get("content", ""))[:400]  # truncate each turn
            transcript_parts.append(f"{role.upper()}: {content}")

        transcript = "\n".join(transcript_parts)

        try:
            # Try to use the model router for summarization
            from pawbot.providers.router import ModelRouter
            from pawbot.config.loader import load_config

            cfg = load_config()
            router = ModelRouter(cfg.model_dump(by_alias=False))

            summary = await router.call(
                task_type="memory_save",
                complexity=0.1,
                prompt=(
                    "Summarise the following conversation in 3-5 sentences. "
                    "Preserve: all key facts, decisions made, files changed, "
                    "errors encountered, tasks completed, and user preferences. "
                    "Be concise — this summary replaces the full history.\n\n"
                    f"{transcript}"
                ),
                system="You are a precise summarizer. Output only the summary.",
            )
            return summary.strip() if summary else self._fallback_summary(messages)

        except Exception as e:
            logger.warning(f"Compactor: model-based summarization failed: {e}. Using fallback.")
            return self._fallback_summary(messages)

    def _fallback_summary(self, messages: list[dict]) -> str:
        """
        Simple fallback summarization when model router is unavailable.
        Extracts key content from the first and last messages.
        """
        parts = []
        for m in messages[:3]:
            content = str(m.get("content", ""))[:150]
            if content:
                parts.append(content)

        if len(messages) > 3:
            for m in messages[-2:]:
                content = str(m.get("content", ""))[:150]
                if content:
                    parts.append(content)

        return " [...] ".join(parts) if parts else "Earlier conversation context."

    async def force_compact(
        self,
        messages: list[dict],
        target_tokens: int = 0,
    ) -> list[dict]:
        """Force compaction regardless of threshold — emergency overflow handler.

        Args:
            messages: The full message list.
            target_tokens: Target token count to compact towards. If 0, uses
                          half the current token count as target.

        Returns:
            The compacted message list.
        """
        total = self._estimate_tokens(messages)
        if target_tokens <= 0:
            target_tokens = total // 2

        # Separate system prompt from conversation turns
        system_msgs = [m for m in messages if m.get("role") == "system"]
        convo_msgs = [m for m in messages if m.get("role") != "system"]

        if len(convo_msgs) <= 2:
            return messages  # Cannot compact further

        # Keep fewer turns when force-compacting (just the last 3)
        keep_n = min(3, len(convo_msgs))
        to_compact = convo_msgs[:-keep_n]
        keep = convo_msgs[-keep_n:]

        if not to_compact:
            return messages

        # Summarise old turns
        summary_text = await self._summarise(to_compact, "auto")

        compacted_msg = {
            "role": "system",
            "content": (
                f"[FORCE COMPACTED — {len(to_compact)} earlier turns] "
                f"Summary of earlier conversation: {summary_text}"
            ),
        }

        result = system_msgs + [compacted_msg] + keep
        tokens_after = self._estimate_tokens(result)

        logger.info(
            f"Compactor: force-compacted {len(to_compact)} turns → 1 summary. "
            f"Tokens: ~{total} → ~{tokens_after} (target={target_tokens})"
        )
        return result


# ── Singleton ──────────────────────────────────────────────────────────────────
compactor = ContextCompactor()
