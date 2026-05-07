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
        # Plan-driven action steps run through inner safety Layer 1
        # RBAC against this role. Default to instructor so existing
        # action-step tests exercise the happy path; the
        # TestActionStepInnerSafetyRoleEnforcement class below sets
        # user_role="student" to lock the privilege-escalation guard.
        "user_role": "instructor",
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


# ---------------------------------------------------------------------------
# Inner safety on the plan path — closes the ADR 006 D6 bypass gap
# ---------------------------------------------------------------------------


class TestActionStepInnerSafety:
    """Phase 5 cutover: `run_action_step` now runs the same inner safety
    composer as the standalone subgraph. Before this, plan-driven action
    steps bypassed every gate including the prototype `_validate_args`
    in the subgraph — the largest defense-in-depth gap in the codebase.

    These tests pin the closed-gap behavior:
      - A plan step that would trip Layer 4 (live state, e.g., enrollment
        to a full course) surfaces as ToolCall(success=False, error=...);
        the gRPC call is never made.
      - Sibling steps in the same plan continue to execute — denial of
        one step does not abort the rest of the DAG.
    """

    async def test_action_step_denied_by_live_state_surfaces_as_failure(self):
        """CS401 is at capacity 30/30 in the mock world state. Layer 4
        denies the enrollment_modify add → ToolCall.success=False, no
        underlying tool invocation, no exception bubble.
        """
        plan = {
            "steps": [{
                "step_id": 1,
                "description": "enroll S100 in CS401 (full course)",
                "depends_on": [],
                "agent_type": "action",
                "action_tool": "enrollment_modify",
                "action_args": {
                    "student_id": "S100",
                    "course_id": "CS401",
                    "action": "add",
                },
            }],
            "reasoning": "Should be blocked at Layer 4.",
        }
        state = _make_state("enroll me in CS401")
        result = await run_planning_agent(state, _mock_llm(json.dumps(plan)))

        assert len(result["tool_calls"]) == 1
        tc = result["tool_calls"][0]
        assert tc.success is False
        # The reason text comes from check_live_state's "course_full" verdict.
        assert tc.error and "capacity" in tc.error.lower()

    async def test_denied_action_step_does_not_block_sibling_steps(self):
        """A plan with two independent steps — one action that gets
        denied at Layer 4, one query that should still run — must
        execute both in the same superstep. Denial cannot cascade.
        """
        plan = {
            "steps": [
                {
                    "step_id": 1,
                    "description": "denied action",
                    "depends_on": [],
                    "agent_type": "action",
                    "action_tool": "enrollment_modify",
                    "action_args": {
                        "student_id": "S100",
                        "course_id": "CS401",
                        "action": "add",
                    },
                },
                {
                    "step_id": 2,
                    "description": "independent query",
                    "depends_on": [],
                    "agent_type": "query",
                    "query_source": "canvas",
                    "query_params": {"user_id": "test-user"},
                },
            ],
            "reasoning": "Sibling step must still run.",
        }
        state = _make_state("do two things")
        result = await run_planning_agent(state, _mock_llm(json.dumps(plan)))

        assert len(result["tool_calls"]) == 2
        # The action step is failed; the query step is independent and
        # must produce its own ToolCall (success or otherwise — point is
        # the dispatcher reached it).
        labels = [tc.tool_name for tc in result["tool_calls"]]
        assert "enrollment_modify" in labels
        assert any("canvas" in l for l in labels)
        action_tc = next(tc for tc in result["tool_calls"] if tc.tool_name == "enrollment_modify")
        assert action_tc.success is False

    async def test_allowed_action_step_invokes_tool_and_records_audit(self):
        """Happy path: plan step passes all four layers → tool runs,
        audit is persisted with execution_attempted=True.
        """
        plan = {
            "steps": [{
                "step_id": 1,
                "description": "drop CS101",
                "depends_on": [],
                "agent_type": "action",
                "action_tool": "enrollment_modify",
                "action_args": {
                    "student_id": "S001",  # enrolled in CS101 per mock state
                    "course_id": "CS101",
                    "action": "drop",
                },
            }],
            "reasoning": "All four layers should pass.",
        }
        state = _make_state("drop CS101")
        result = await run_planning_agent(state, _mock_llm(json.dumps(plan)))

        assert len(result["tool_calls"]) == 1
        tc = result["tool_calls"][0]
        assert tc.success is True
        # The step output dict carries the audit_id correlator.
        step_outputs = result["step_outputs"]
        assert 1 in step_outputs
        assert "audit_id" in step_outputs[1]
        assert len(step_outputs[1]["audit_id"]) == 12


# ---------------------------------------------------------------------------
# Privilege escalation guard on the plan path — D5 invariant
# ---------------------------------------------------------------------------


class TestActionStepInnerSafetyRoleEnforcement:
    """Pin the closure of the privilege-escalation bug found in audit:
    a student-role caller cannot execute write tools by routing
    through the plan path, even when the LLM produces a valid action
    step that would pass Layers 2-4.

    Before the sec-fix, run_action_step's _default_plan_session
    hard-coded user_role="instructor" and PlanExecState had no
    `session` field, so any student-triggered planning intent could
    surface action steps that bypassed Layer 1 RBAC. These tests lock
    the closed gap.
    """

    async def test_student_cannot_execute_grade_update_via_plan(self):
        """student-role state → run_planning_agent dispatches an
        action step → run_action_step receives session.user_role=
        "student" via PlanExecState → Layer 1 denies. The tool is
        NEVER invoked.
        """
        plan = {
            "steps": [{
                "step_id": 1,
                "description": "student trying to update own grade",
                "depends_on": [],
                "agent_type": "action",
                "action_tool": "grade_update",
                "action_args": {
                    "student_id": "S001",
                    "course_id": "CS101",
                    "assignment_id": "A1",
                    "grade": "A",
                },
            }],
            "reasoning": "Student attempting privilege escalation.",
        }
        state = _make_state("give me an A", user_role="student")
        result = await run_planning_agent(state, _mock_llm(json.dumps(plan)))

        assert len(result["tool_calls"]) == 1
        tc = result["tool_calls"][0]
        assert tc.success is False
        # Layer 1's deny message names the role and tool.
        assert tc.error and "student" in tc.error.lower()
        assert tc.error and "grade_update" in tc.error

        # The step output is the structured denial dict; reason_code
        # confirms it was Layer 1 specifically (not Layer 2/3/4).
        step_output = result["step_outputs"][1]
        assert step_output["denied_by_inner_safety"] is True
        assert step_output["reason_code"] == "role_lacks_tool_grant"

    async def test_unknown_role_via_plan_fails_closed(self):
        """JWT carrying a role name not in the matrix → Layer 1
        DENY ("unknown_role") rather than fail-open. Same fail-closed
        posture as outer safety ADR 005 D5.
        """
        plan = {
            "steps": [{
                "step_id": 1,
                "description": "exotic role tries write",
                "depends_on": [],
                "agent_type": "action",
                "action_tool": "enrollment_modify",
                "action_args": {
                    "student_id": "S100",
                    "course_id": "CS101",
                    "action": "add",
                },
            }],
            "reasoning": "Unknown-role fail-closed test.",
        }
        state = _make_state("enroll me", user_role="phantom_role")
        result = await run_planning_agent(state, _mock_llm(json.dumps(plan)))

        tc = result["tool_calls"][0]
        assert tc.success is False
        step_output = result["step_outputs"][1]
        assert step_output["denied_by_inner_safety"] is True

    async def test_run_action_step_default_session_is_fail_closed(self):
        """Direct callers of run_action_step who omit `session=` must
        fall through to a student-role default — never instructor.
        Pins the contract on `_default_plan_session()`.
        """
        from agents.action_agent import _default_plan_session

        sess = _default_plan_session()
        assert sess.user_role == "student", (
            "Default plan session must be the most-restricted role so "
            "missing wiring fails closed at Layer 1."
        )
