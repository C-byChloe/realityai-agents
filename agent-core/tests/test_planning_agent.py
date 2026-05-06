"""Unit tests for the Planning Agent (typed Plan path)."""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agents.action_agent import ACTION_TOOLS
from agents.planning_agent import QUERY_SOURCES, run_planning_agent
from schemas.plan import QuerySource
from state import AgentState


def _make_state(user_msg: str, **overrides) -> AgentState:
    base = {
        "messages": [HumanMessage(content=user_msg)],
        "intent": "planning",
        "intent_confidence": 0.9,
        "selected_agent": "planning_agent",
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


def _mock_llm(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke.return_value = AIMessage(content=content)
    return mock


# ---------------------------------------------------------------------------
# Typed Plan execution
# ---------------------------------------------------------------------------


class TestTypedPlanExecution:
    async def test_two_independent_query_steps_run(self):
        """Two query steps with no dependencies should both execute."""
        plan = {
            "steps": [
                {
                    "step_id": 1,
                    "description": "Get transcript",
                    "depends_on": [],
                    "agent_type": "query",
                    "query_source": "canvas",
                    "query_params": {"user_id": "u1"},
                },
                {
                    "step_id": 2,
                    "description": "Get degree program",
                    "depends_on": [],
                    "agent_type": "query",
                    "query_source": "degree_db",
                    "query_params": {"major": "CS", "track": "AI", "cohort": "2024-2027"},
                },
            ],
            "reasoning": "Independent reads in parallel.",
        }
        state = _make_state("Plan my degree")
        result = await run_planning_agent(state, _mock_llm(json.dumps(plan)))

        assert len(result["tool_calls"]) == 2
        assert all(tc.success for tc in result["tool_calls"])
        assert 1 in result["step_outputs"]
        assert 2 in result["step_outputs"]
        # Outputs are typed Pydantic objects
        from schemas.query_outputs import DegreeProgram, StudentTranscript
        assert isinstance(result["step_outputs"][1], StudentTranscript)
        assert isinstance(result["step_outputs"][2], DegreeProgram)

    async def test_action_step_dispatches_to_action_tool(self):
        plan = {
            "steps": [
                {
                    "step_id": 1,
                    "description": "Drop CS101",
                    "depends_on": [],
                    "agent_type": "action",
                    "action_tool": "enrollment_modify",
                    "action_args": {"student_id": "S001", "course_id": "CS101", "action": "drop"},
                },
            ],
            "reasoning": "Single drop.",
        }
        state = _make_state("Drop CS101")
        result = await run_planning_agent(state, _mock_llm(json.dumps(plan)))

        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0].success is True
        assert result["tool_calls"][0].tool_name == "enrollment_modify"

    async def test_reasoning_step_consumes_typed_upstream(self):
        """Gap-analysis reasoning step uses typed transcript + degree program."""
        plan = {
            "steps": [
                {"step_id": 1, "description": "transcript", "depends_on": [],
                 "agent_type": "query", "query_source": "canvas",
                 "query_params": {"user_id": "u1"}},
                {"step_id": 2, "description": "degree", "depends_on": [],
                 "agent_type": "query", "query_source": "degree_db",
                 "query_params": {"major": "CS", "track": "AI", "cohort": "2024-2027"}},
                {"step_id": 3, "description": "gap analysis", "depends_on": [1, 2],
                 "agent_type": "reasoning",
                 "reasoning_inputs": [1, 2],
                 "reasoning_template": "extract_unsatisfied_elective_pool"},
            ],
            "reasoning": "Find unsatisfied requirements.",
        }
        state = _make_state("What do I still need to take?")
        result = await run_planning_agent(state, _mock_llm(json.dumps(plan)))

        assert all(tc.success for tc in result["tool_calls"])
        assert 3 in result["step_outputs"]
        unmet = result["step_outputs"][3]
        assert isinstance(unmet, list)
        assert all(hasattr(u, "requirement_id") for u in unmet)


# ---------------------------------------------------------------------------
# DAG validation errors
# ---------------------------------------------------------------------------


class TestPlanValidation:
    async def test_query_step_without_source_rejected(self):
        plan = {
            "steps": [{
                "step_id": 1, "description": "bad", "depends_on": [],
                "agent_type": "query",
            }],
            "reasoning": "Missing source.",
        }
        state = _make_state("x")
        result = await run_planning_agent(state, _mock_llm(json.dumps(plan)))
        assert "validation failed" in result["response"].lower()
        assert result["tool_calls"] == []

    async def test_dangling_dependency_rejected(self):
        plan = {
            "steps": [{
                "step_id": 1, "description": "bad", "depends_on": [99],
                "agent_type": "query", "query_source": "canvas",
                "query_params": {"user_id": "u1"},
            }],
            "reasoning": "Bad deps.",
        }
        state = _make_state("x")
        result = await run_planning_agent(state, _mock_llm(json.dumps(plan)))
        assert "validation failed" in result["response"].lower()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_empty_plan(self):
        plan = {"steps": [], "reasoning": "Cannot plan this."}
        state = _make_state("x")
        result = await run_planning_agent(state, _mock_llm(json.dumps(plan)))
        assert result["tool_calls"] == []
        assert "Cannot plan" in result["response"]

    async def test_malformed_llm_response(self):
        state = _make_state("x")
        result = await run_planning_agent(state, _mock_llm("not JSON"))
        assert result["response"] == "not JSON"
        assert result["tool_calls"] == []

    async def test_query_step_runtime_error_marks_failure(self):
        """Query handler raises (e.g., missing user_id) → step marked failed."""
        plan = {
            "steps": [{
                "step_id": 1, "description": "bad params", "depends_on": [],
                "agent_type": "query", "query_source": "canvas",
                "query_params": {},  # missing user_id
            }],
            "reasoning": "Should fail at runtime.",
        }
        state = _make_state("x")
        result = await run_planning_agent(state, _mock_llm(json.dumps(plan)))
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0].success is False


# ---------------------------------------------------------------------------
# Dispatch registries — query (4 typed sources) + action (3 tools)
# ---------------------------------------------------------------------------


class TestDispatchRegistries:
    """Locks the planning agent's known dispatch surface.

    Query dispatch is by typed `QuerySource` enum; action dispatch is by
    tool name in `ACTION_TOOLS`. Both registries are shared with the
    standalone agents — single source of truth for what the planner can
    actually emit.
    """

    def test_all_four_query_sources_registered(self):
        assert set(QUERY_SOURCES) == {
            QuerySource.CANVAS,
            QuerySource.DEGREE_DB,
            QuerySource.CATALOG_DB,
            QuerySource.SYLLABUS_RAG,
        }

    def test_all_three_action_tools_registered(self):
        assert "grade_update" in ACTION_TOOLS
        assert "enrollment_modify" in ACTION_TOOLS
        assert "assignment_create" in ACTION_TOOLS
        assert len(ACTION_TOOLS) == 3
