"""Unit tests for the LangGraph state machine and intent classifier."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from orchestrator import (
    AGENT_MAP,
    _route_by_agent,
    build_graph,
    classify_intent,
    create_app,
    execute_agent,
    generate_response,
    route_to_agent,
    safety_check,
)
from state import AgentState, SafetyResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(user_msg: str, **overrides) -> AgentState:
    """Create a minimal AgentState for testing."""
    base = {
        "messages": [HumanMessage(content=user_msg)],
        "intent": "",
        "intent_confidence": 0.0,
        "selected_agent": "",
        "safety_result": None,
        "tool_calls": [],
        "response": "",
        "user_id": "test-user",
        "session_id": "test-session",
        "requires_approval": False,
        "approval_status": None,
    }
    base.update(overrides)
    return base


def _mock_llm_response(content: str) -> AsyncMock:
    """Create a mock LLM that returns the given content."""
    mock = AsyncMock()
    mock.ainvoke.return_value = AIMessage(content=content)
    return mock


# ---------------------------------------------------------------------------
# Intent Classification
# ---------------------------------------------------------------------------


class TestClassifyIntent:
    """Tests for the intent classification node."""

    @pytest.mark.parametrize(
        "intent,user_msg",
        [
            ("action", "Change my enrollment to CS201"),
            ("action", "Update the grade for student 12345 to A"),
            ("action", "Create a new assignment due Friday"),
            ("query", "What time does CS101 meet?"),
            ("query", "Show me the syllabus for Biology 200"),
            ("query", "Who is the instructor for Math 301?"),
            ("planning", "Plan my next semester avoiding Friday classes"),
            ("planning", "What prerequisites do I need for the CS degree?"),
            ("planning", "Help me build a 4-year graduation plan"),
        ],
    )
    async def test_classifies_intent_correctly(self, intent, user_msg):
        state = _make_state(user_msg)
        llm_response = json.dumps({"intent": intent, "confidence": 0.95})

        with patch("orchestrator._get_llm", return_value=_mock_llm_response(llm_response)):
            result = await classify_intent(state)

        assert result["intent"] == intent
        assert result["intent_confidence"] == 0.95

    async def test_defaults_to_query_on_malformed_json(self):
        state = _make_state("some question")
        with patch("orchestrator._get_llm", return_value=_mock_llm_response("not json")):
            result = await classify_intent(state)

        assert result["intent"] == "query"
        assert result["intent_confidence"] == 0.5

    async def test_defaults_to_query_on_missing_intent_field(self):
        state = _make_state("some question")
        response = json.dumps({"something": "else"})
        with patch("orchestrator._get_llm", return_value=_mock_llm_response(response)):
            result = await classify_intent(state)

        assert result["intent"] == "query"


# ---------------------------------------------------------------------------
# Agent Routing
# ---------------------------------------------------------------------------


class TestRouteToAgent:
    """Tests for the agent routing node."""

    @pytest.mark.parametrize(
        "intent,expected_agent",
        [
            ("action", "action_agent"),
            ("query", "query_agent"),
            ("planning", "planning_agent"),
        ],
    )
    async def test_routes_to_correct_agent(self, intent, expected_agent):
        state = _make_state("test", intent=intent)
        result = await route_to_agent(state)
        assert result["selected_agent"] == expected_agent

    async def test_defaults_to_query_agent_for_unknown_intent(self):
        state = _make_state("test", intent="unknown")
        result = await route_to_agent(state)
        assert result["selected_agent"] == "query_agent"


# ---------------------------------------------------------------------------
# Safety Check (placeholder)
# ---------------------------------------------------------------------------


class TestSafetyCheck:
    """Tests for the placeholder safety check node."""

    async def test_returns_safe_by_default(self):
        state = _make_state("anything")
        result = await safety_check(state)
        assert result["safety_result"].flagged is False
        assert result["requires_approval"] is False


# ---------------------------------------------------------------------------
# Execution (placeholder)
# ---------------------------------------------------------------------------


class TestExecuteAgent:
    """Tests for agent execution routing."""

    async def test_routes_unknown_agent_to_placeholder(self):
        state = _make_state("hello", selected_agent="unknown_agent")

        with patch("orchestrator._get_llm", return_value=_mock_llm_response("{}")):
            result = await execute_agent(state)

        assert "unknown_agent" in result["response"]
        assert "hello" in result["response"]
        assert result["tool_calls"] == []

    async def test_routes_query_agent(self):
        query_response = json.dumps({
            "tool": "course_lookup",
            "arguments": {"course_id": "CS101"},
            "query_type": "deterministic",
        })
        state = _make_state("Tell me about CS101", selected_agent="query_agent")

        with patch("orchestrator._get_llm", return_value=_mock_llm_response(query_response)):
            result = await execute_agent(state)

        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0].tool_name == "course_lookup"


# ---------------------------------------------------------------------------
# Response Generation
# ---------------------------------------------------------------------------


class TestGenerateResponse:
    """Tests for the response generation node."""

    async def test_returns_existing_response(self):
        state = _make_state("test", response="The answer is 42")
        result = await generate_response(state)
        assert result["response"] == "The answer is 42"

    async def test_returns_existing_empty_response(self):
        state = _make_state("test")
        # generate_response preserves whatever is in state; empty string stays empty
        result = await generate_response(state)
        assert result["response"] == ""

    async def test_returns_fallback_when_response_key_missing(self):
        state = _make_state("test")
        del state["response"]
        result = await generate_response(state)
        assert "not sure" in result["response"]


# ---------------------------------------------------------------------------
# Conditional Routing
# ---------------------------------------------------------------------------


class TestConditionalRouting:
    """Tests for the _route_by_agent conditional edge."""

    def test_routes_to_execute_when_safe(self):
        state = _make_state("test", requires_approval=False)
        assert _route_by_agent(state) == "execute"

    def test_routes_to_awaiting_approval_when_flagged(self):
        state = _make_state(
            "test",
            requires_approval=True,
            safety_result=SafetyResult(flagged=True, reason="high risk"),
        )
        assert _route_by_agent(state) == "awaiting_approval"

    def test_routes_to_execute_when_not_flagged_despite_approval_required(self):
        state = _make_state(
            "test",
            requires_approval=True,
            safety_result=SafetyResult(flagged=False),
        )
        assert _route_by_agent(state) == "execute"


# ---------------------------------------------------------------------------
# Graph Construction
# ---------------------------------------------------------------------------


class TestBuildGraph:
    """Tests for graph construction and compilation."""

    def test_graph_builds_without_error(self):
        graph = build_graph()
        assert graph is not None

    def test_graph_compiles_without_error(self):
        app = create_app()
        assert app is not None

    async def test_full_graph_execution(self):
        """End-to-end test: a query message flows through all nodes."""
        intent_response = json.dumps({"intent": "query", "confidence": 0.9})
        query_response = json.dumps({
            "tool": "schedule_query",
            "arguments": {"course_id": "CS101"},
            "query_type": "deterministic",
        })

        # Mock LLM returns intent classification first, then query agent response
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = [
            AIMessage(content=intent_response),
            AIMessage(content=query_response),
        ]

        with patch("orchestrator._get_llm", return_value=mock_llm):
            app = create_app()
            result = await app.ainvoke(
                _make_state("What time does CS101 meet?"),
            )

        assert result["intent"] == "query"
        assert result["selected_agent"] == "query_agent"
        assert result["safety_result"].flagged is False
        assert "Mon/Wed/Fri" in result["response"]
