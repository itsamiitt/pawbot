"""
tests/test_compactor.py
Run: pytest tests/test_compactor.py -v
"""

import pytest
from unittest.mock import AsyncMock, patch
from pawbot.agent.compactor import ContextCompactor, MODEL_CONTEXT_LIMITS


@pytest.fixture
def cx():
    return ContextCompactor()


def make_messages(n: int, content_len: int = 200) -> list[dict]:
    """Generate n fake conversation messages."""
    msgs = [{"role": "system", "content": "You are Pawbot."}]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": "x" * content_len})
    return msgs


@pytest.mark.asyncio
async def test_no_compaction_below_threshold(cx):
    """Short conversations should pass through unchanged."""
    msgs   = make_messages(5, content_len=100)
    result = await cx.compact_if_needed(msgs, "llama3.1:8b")
    assert result == msgs, "Short conversations should not be modified"


@pytest.mark.asyncio
async def test_compaction_triggers_above_threshold(cx):
    """Long conversations should be compacted — result must be shorter."""
    model = "llama3.1:8b"  # smallest limit: 8192 tokens
    # Generate enough content to fill ~85% of 8192 tokens
    msgs  = make_messages(200, content_len=180)

    with patch.object(cx, "_summarise", new=AsyncMock(return_value="summary text")) as mock_sum:
        result = await cx.compact_if_needed(msgs, model)
        assert len(result) < len(msgs), "Result must be shorter than input"
        mock_sum.assert_called_once()


@pytest.mark.asyncio
async def test_system_prompt_never_compacted(cx):
    """System messages must always survive compaction intact."""
    model          = "llama3.1:8b"
    msgs           = make_messages(200, content_len=180)
    system_content = msgs[0]["content"]

    with patch.object(cx, "_summarise", new=AsyncMock(return_value="compact summary")):
        result = await cx.compact_if_needed(msgs, model)

    system_msgs = [m for m in result if m["role"] == "system" and "COMPACTED" not in m["content"]]
    assert any(m["content"] == system_content for m in system_msgs), \
        "Original system prompt must be preserved after compaction"


@pytest.mark.asyncio
async def test_last_n_turns_always_kept(cx):
    """The most recent KEEP_LAST_N_TURNS turns must survive compaction intact."""
    from pawbot.agent.compactor import KEEP_LAST_N_TURNS

    model = "llama3.1:8b"
    msgs  = make_messages(200, content_len=180)

    # Tag the last N conversation turns with a unique marker
    convo = [m for m in msgs if m["role"] != "system"]
    for m in convo[-KEEP_LAST_N_TURNS:]:
        m["content"] = "KEEP_ME_" + m["content"][:10]

    with patch.object(cx, "_summarise", new=AsyncMock(return_value="summary")):
        result = await cx.compact_if_needed(msgs, model)

    kept = [m for m in result if str(m.get("content", "")).startswith("KEEP_ME_")]
    assert len(kept) == KEEP_LAST_N_TURNS, \
        f"Expected {KEEP_LAST_N_TURNS} kept turns, got {len(kept)}"


@pytest.mark.asyncio
async def test_token_estimate_correctness(cx):
    """Token estimator must return plausible values."""
    msgs     = [{"role": "user", "content": "Hello world this is a test."}]
    estimate = cx._estimate_tokens(msgs)
    # "Hello world this is a test." = ~28 chars / 4 = ~7 tokens
    assert 4 <= estimate <= 20, f"Unexpected token estimate: {estimate}"
