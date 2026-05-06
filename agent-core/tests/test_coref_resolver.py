"""Unit tests for the coref_resolver_node — LLM is mocked."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from preprocessing.coref_resolver import make_coref_resolver_node
from preprocessing.schemas import RewrittenQuery


def _llm_returning(rewrite: RewrittenQuery) -> MagicMock:
    structured = AsyncMock()
    structured.ainvoke = AsyncMock(return_value=rewrite)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    return llm


def _llm_raising(exc: Exception) -> MagicMock:
    structured = AsyncMock()
    structured.ainvoke = AsyncMock(side_effect=exc)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    return llm


# ---------------------------------------------------------------------------
# Gate-skip path: no LLM call, no_rewrite emitted
# ---------------------------------------------------------------------------


class TestGateSkip:
    async def test_first_turn_emits_no_rewrite_with_full_confidence(self):
        llm = MagicMock()
        node = make_coref_resolver_node(llm=llm)
        out = await node({"messages": [HumanMessage(content="Hello")]})
        assert out["rewritten_query"].rewrite_reason == "no_rewrite"
        assert out["rewritten_query"].confidence == 1.0
        assert out["user_query_normalized"] == "Hello"
        # LLM not invoked
        llm.with_structured_output.assert_not_called()

    async def test_gate_skip_does_not_call_llm(self):
        llm = MagicMock()
        node = make_coref_resolver_node(llm=llm)
        await node({"messages": [HumanMessage(content="What time does CS101 meet?")]})
        llm.with_structured_output.assert_not_called()


# ---------------------------------------------------------------------------
# Gate-fire path: LLM called, output handled
# ---------------------------------------------------------------------------


class TestGateFireLLMSucceeds:
    async def test_high_confidence_uses_rewritten_query(self):
        rewrite = RewrittenQuery(
            original_query="What is its prereq?",
            rewritten_query="What is COMS4705's prereq?",
            resolved_entities={"its": "COMS4705"},
            rewrite_reason="coreference",
            confidence=0.9,
        )
        node = make_coref_resolver_node(llm=_llm_returning(rewrite))
        out = await node({
            "messages": [
                HumanMessage(content="Tell me about COMS4705"),
                HumanMessage(content="What is its prereq?"),
            ],
        })
        assert out["user_query_normalized"] == "What is COMS4705's prereq?"
        assert out["rewritten_query"].confidence == 0.9
        assert out["rewritten_query"].rewrite_reason == "coreference"

    async def test_low_confidence_falls_back_to_original(self):
        """Below CONFIDENCE_FALLBACK_THRESHOLD (0.5), use original."""
        rewrite = RewrittenQuery(
            original_query="What is its prereq?",
            rewritten_query="WRONG GUESS",
            resolved_entities={},
            rewrite_reason="coreference",
            confidence=0.3,
        )
        node = make_coref_resolver_node(llm=_llm_returning(rewrite))
        out = await node({
            "messages": [
                HumanMessage(content="Tell me about COMS4705"),
                HumanMessage(content="What is its prereq?"),
            ],
        })
        assert out["user_query_normalized"] == "What is its prereq?"
        # The typed record still carries the LLM's attempt (for trace)
        assert out["rewritten_query"].rewritten_query == "WRONG GUESS"
        assert out["rewritten_query"].confidence == 0.3

    async def test_threshold_boundary_exact_zero_point_five_uses_rewrite(self):
        """At the threshold, prefer the rewritten form (>= comparison)."""
        rewrite = RewrittenQuery(
            original_query="its prereq",
            rewritten_query="COMS4705 prereq",
            rewrite_reason="coreference",
            confidence=0.5,
        )
        node = make_coref_resolver_node(llm=_llm_returning(rewrite))
        out = await node({
            "messages": [
                HumanMessage(content="COMS4705"),
                HumanMessage(content="its prereq"),
            ],
        })
        assert out["user_query_normalized"] == "COMS4705 prereq"


# ---------------------------------------------------------------------------
# Gate-fire path: LLM raises → graceful fallback
# ---------------------------------------------------------------------------


class TestGateFireLLMFails:
    async def test_llm_exception_emits_no_rewrite_zero_confidence(self):
        node = make_coref_resolver_node(llm=_llm_raising(RuntimeError("boom")))
        out = await node({
            "messages": [
                HumanMessage(content="COMS4705"),
                HumanMessage(content="its prereq?"),
            ],
        })
        assert out["rewritten_query"].rewrite_reason == "no_rewrite"
        assert out["rewritten_query"].confidence == 0.0
        # Use original (zero confidence is below threshold)
        assert out["user_query_normalized"] == "its prereq?"

    async def test_no_exception_propagates_to_caller(self):
        """The node must absorb LLM errors — caller should not see them."""
        node = make_coref_resolver_node(llm=_llm_raising(ValueError("rate limited")))
        # If this raises, the test fails by exception propagation.
        out = await node({
            "messages": [
                HumanMessage(content="COMS4705"),
                HumanMessage(content="its prereq?"),
            ],
        })
        assert out is not None


# ---------------------------------------------------------------------------
# Empty messages — nothing to do, no crash
# ---------------------------------------------------------------------------


class TestEdge:
    async def test_empty_messages_returns_empty_dict(self):
        node = make_coref_resolver_node(llm=MagicMock())
        out = await node({"messages": []})
        assert out == {}
