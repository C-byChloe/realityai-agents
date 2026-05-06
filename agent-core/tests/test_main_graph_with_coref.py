"""Integration test: main graph with coref_resolver wired in.

Exercises the four scenarios from Task 10:
  1. First-turn query → coref skips, planning sees raw query
  2. Multi-turn pronoun → coref rewrites, planning sees resolved query
  3. Multi-turn Chinese ellipsis → coref expands, planning sees expanded query
  4. Coref LLM raises → graph completes, planning sees raw query
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from orchestrator import build_graph
from preprocessing.schemas import RewrittenQuery


# ---------------------------------------------------------------------------
# Mocking helpers — keeps every LLM call deterministic, no network.
# ---------------------------------------------------------------------------


def _mock_orchestrator_llm() -> MagicMock:
    """Mock for orchestrator._get_llm — returns canned JSON for the
    three LLM call sites along the ALLOW path:
      1. intent classifier
      2. outer safety Tier 3 (LLM intent analyzer)
      3. agent (query_agent's route_query)
    """
    llm = MagicMock()
    intent_response = AIMessage(content=json.dumps({"intent": "query", "confidence": 0.9}))
    analyzer_ok = AIMessage(content=json.dumps({
        "decision": "allow", "confidence": 0.92, "reason": "ok",
    }))
    query_response = AIMessage(content=json.dumps({
        "source": "catalog_db",
        "params": {"term": "F25", "course_codes": ["CS101"]},
        "query_type": "deterministic",
    }))
    state = {"calls": 0}

    async def ainvoke(messages, **kwargs):
        i = state["calls"]
        state["calls"] += 1
        if i == 0:
            return intent_response
        if i == 1:
            return analyzer_ok
        return query_response

    llm.ainvoke = ainvoke
    return llm


def _coref_llm_returning(rewrite: RewrittenQuery) -> MagicMock:
    structured = AsyncMock()
    structured.ainvoke = AsyncMock(return_value=rewrite)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    return llm


def _coref_llm_raising(exc: Exception) -> MagicMock:
    structured = AsyncMock()
    structured.ainvoke = AsyncMock(side_effect=exc)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    return llm


def _initial_state(messages: list) -> dict:
    return {
        "messages": messages,
        "intent": "",
        "intent_confidence": 0.0,
        "selected_agent": "",
        "user_role": "instructor",  # bypass RBAC for action-class routing
        "outer_safety_result": None,
        "tool_calls": [],
        "response": "",
        "user_id": "u1",
        "session_id": "s1",
        "requires_approval": False,
        "approval_status": None,
    }


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def test_first_turn_skips_coref_and_reaches_execution():
    """Empty history → gate skips → planning sees raw query."""
    coref_llm = MagicMock()
    coref_llm.with_structured_output = MagicMock()
    app = build_graph(coref_llm=coref_llm).compile()

    with patch("orchestrator._get_llm", return_value=_mock_orchestrator_llm()):
        final = await app.ainvoke(_initial_state(
            [HumanMessage(content="What are the prereqs for COMS3134?")]
        ))

    # Coref ran the gate but did not call the LLM
    coref_llm.with_structured_output.assert_not_called()
    # State carries no_rewrite record + the raw query as normalized
    assert final["rewritten_query"].rewrite_reason == "no_rewrite"
    assert final["user_query_normalized"] == "What are the prereqs for COMS3134?"
    # Execution actually ran — response is non-empty
    assert final["response"]


async def test_multi_turn_english_pronoun_resolves():
    """Multi-turn 'its' resolves to the COMS4705 from history."""
    rewrite = RewrittenQuery(
        original_query="What's its prereq?",
        rewritten_query="What's COMS4705's prereq?",
        resolved_entities={"its": "COMS4705"},
        rewrite_reason="coreference",
        confidence=0.92,
    )
    coref_llm = _coref_llm_returning(rewrite)
    app = build_graph(coref_llm=coref_llm).compile()

    with patch("orchestrator._get_llm", return_value=_mock_orchestrator_llm()):
        final = await app.ainvoke(_initial_state([
            HumanMessage(content="I want to take COMS4705"),
            AIMessage(content="OK"),
            HumanMessage(content="What's its prereq?"),
        ]))

    coref_llm.with_structured_output.assert_called_once()
    assert final["rewritten_query"].rewrite_reason == "coreference"
    assert final["user_query_normalized"] == "What's COMS4705's prereq?"
    assert "COMS4705" in final["rewritten_query"].rewritten_query


async def test_multi_turn_chinese_ellipsis_expands():
    """'再查一下避开周五的' should expand against Spring 2026 context."""
    rewrite = RewrittenQuery(
        original_query="再查一下避开周五的",
        rewritten_query="再查一下 Spring 2026 AI 选修中避开周五的课",
        resolved_entities={"再查一下": "Spring 2026 AI electives", "周五": "Friday"},
        rewrite_reason="ellipsis",
        confidence=0.85,
    )
    coref_llm = _coref_llm_returning(rewrite)
    app = build_graph(coref_llm=coref_llm).compile()

    with patch("orchestrator._get_llm", return_value=_mock_orchestrator_llm()):
        final = await app.ainvoke(_initial_state([
            HumanMessage(content="Show me Spring 2026 AI electives"),
            AIMessage(content="Found 5 courses"),
            HumanMessage(content="再查一下避开周五的"),
        ]))

    assert final["rewritten_query"].rewrite_reason == "ellipsis"
    assert "Spring 2026" in final["user_query_normalized"]
    assert "周五" in final["user_query_normalized"]


async def test_coref_llm_failure_does_not_break_graph():
    """LLM exception → fallback to raw query, graph still completes."""
    coref_llm = _coref_llm_raising(RuntimeError("rate limited"))
    app = build_graph(coref_llm=coref_llm).compile()

    with patch("orchestrator._get_llm", return_value=_mock_orchestrator_llm()):
        final = await app.ainvoke(_initial_state([
            HumanMessage(content="I want to take COMS4705"),
            AIMessage(content="OK"),
            HumanMessage(content="What's its prereq?"),
        ]))

    # Coref emitted the no_rewrite record with confidence 0.0
    assert final["rewritten_query"].rewrite_reason == "no_rewrite"
    assert final["rewritten_query"].confidence == 0.0
    # And the normalized query falls back to the original raw input
    assert final["user_query_normalized"] == "What's its prereq?"
    # Graph completed end-to-end
    assert final["response"]
