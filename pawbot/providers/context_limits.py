"""Model context window limits — prevents overflow before LLM call.

Phase 1: Maps known models to their context window sizes and provides
a rough token estimation function for pre-flight overflow checks.
"""

from __future__ import annotations


# Known context window sizes (in tokens)
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # ── Anthropic ────────────────────────────────────────────────────────────
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    # ── OpenAI ───────────────────────────────────────────────────────────────
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3-mini": 200_000,
    # ── DeepSeek ─────────────────────────────────────────────────────────────
    "deepseek-chat": 64_000,
    "deepseek-reasoner": 64_000,
    "deepseek-coder": 16_384,
    # ── Google Gemini ────────────────────────────────────────────────────────
    "gemini-2.0-flash": 1_048_576,
    "gemini-2.0-pro": 1_048_576,
    "gemini-1.5-flash": 1_048_576,
    "gemini-1.5-pro": 2_097_152,
    # ── Ollama (local) ───────────────────────────────────────────────────────
    "llama3.1:8b": 8_192,
    "llama3.1:70b": 8_192,
    "nomic-embed-text": 8_192,
    "deepseek-coder:6.7b": 16_384,
    "mistral:7b": 8_192,
    "codellama:13b": 16_384,
    # ── Groq ─────────────────────────────────────────────────────────────────
    "llama-3.1-70b-versatile": 128_000,
    "llama-3.1-8b-instant": 128_000,
    "mixtral-8x7b-32768": 32_768,
}

# Default if model not in table
DEFAULT_CONTEXT_WINDOW = 32_000

# Safety margin — never use more than this % of the context window
SAFETY_MARGIN = 0.85


def get_context_limit(model: str) -> int:
    """Get the effective context limit for a model (with safety margin).

    Tries exact match, then substring match for prefixed models
    like "anthropic/claude-sonnet-4-5" or "openrouter/deepseek-chat".
    """
    # Try exact match first
    if model in MODEL_CONTEXT_WINDOWS:
        return int(MODEL_CONTEXT_WINDOWS[model] * SAFETY_MARGIN)

    # Try partial match (for prefixed models like "anthropic/claude-sonnet-4-5")
    model_lower = model.lower()
    for key, value in MODEL_CONTEXT_WINDOWS.items():
        if key in model_lower:
            return int(value * SAFETY_MARGIN)

    return int(DEFAULT_CONTEXT_WINDOW * SAFETY_MARGIN)


def estimate_message_tokens(messages: list[dict]) -> int:
    """Rough token count for a message list (4 chars ≈ 1 token).

    This is intentionally a fast, conservative estimate. For exact counts,
    use tiktoken — but that adds latency and a large dependency.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(str(block.get("text", ""))) // 4
        # Tool calls add overhead
        if msg.get("tool_calls"):
            total += len(str(msg["tool_calls"])) // 4
        # Role + overhead per message (approx 4 tokens)
        total += 4
    return total


def check_context_overflow(messages: list[dict], model: str) -> tuple[bool, int, int]:
    """Check if messages would overflow the model's context window.

    Returns:
        (is_overflow, estimated_tokens, context_limit)
    """
    limit = get_context_limit(model)
    estimated = estimate_message_tokens(messages)
    return estimated > limit, estimated, limit
