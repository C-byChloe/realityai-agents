"""User-scope isolation tests.

Asserts the boundary between user-scoped and shared data sources, so a
session for user A cannot see user B's transcript.

Honest scope (encoded as tests below):
  - canvas (transcript)        — MUST be user-scoped
  - degree_db, catalog_db,
    syllabus_rag               — INTENTIONALLY shared (catalog/program
                                  data is not user-private)

If you add a new query_source that handles user-private data, add a test
to `TestCanvasUserScope` for it. Adding a non-user-scoped source goes
under `TestSharedSourcesAreShared` so the contract is explicit.
"""

import pytest

from agents.query_agent import run_query_step
from schemas.plan import AgentType, PlanStep, QuerySource
from schemas.query_outputs import DegreeProgram, StudentTranscript


def _canvas_step(user_id: str) -> PlanStep:
    return PlanStep(
        step_id=1,
        description=f"transcript for {user_id}",
        agent_type=AgentType.QUERY,
        query_source=QuerySource.CANVAS,
        query_params={"user_id": user_id},
    )


def _degree_step(major: str, track: str, cohort: str) -> PlanStep:
    return PlanStep(
        step_id=2,
        description="degree program",
        agent_type=AgentType.QUERY,
        query_source=QuerySource.DEGREE_DB,
        query_params={"major": major, "track": track, "cohort": cohort},
    )


# ---------------------------------------------------------------------------
# Canvas (transcript) MUST be user-scoped
# ---------------------------------------------------------------------------


class TestCanvasUserScope:
    def test_user_a_gets_only_user_a_transcript(self):
        result = run_query_step(_canvas_step("u1"), step_outputs={})
        assert isinstance(result, StudentTranscript)
        assert result.user_id == "u1"
        # u1's mock transcript has CS201; u2's does not.
        assert "CS201" in result.completed
        # u2's mock transcript has ENG101; u1's must not include it.
        assert "ENG101" not in result.completed

    def test_user_b_gets_only_user_b_transcript(self):
        result = run_query_step(_canvas_step("u2"), step_outputs={})
        assert isinstance(result, StudentTranscript)
        assert result.user_id == "u2"
        assert "ENG101" in result.completed
        assert "CS201" not in result.completed

    def test_canvas_query_requires_user_id(self):
        """Canvas without user_id is a contract violation, not an empty result."""
        bad_step = PlanStep(
            step_id=1,
            description="missing user_id",
            agent_type=AgentType.QUERY,
            query_source=QuerySource.CANVAS,
            query_params={},  # empty — no user_id
        )
        with pytest.raises(ValueError, match="user_id"):
            run_query_step(bad_step, step_outputs={})

    def test_unknown_user_returns_empty_transcript_not_someone_elses(self):
        """An unknown user_id returns an empty transcript — never falls
        back to another user's data.
        """
        result = run_query_step(_canvas_step("u-does-not-exist"), step_outputs={})
        assert isinstance(result, StudentTranscript)
        assert result.user_id == "u-does-not-exist"
        assert result.entries == []


# ---------------------------------------------------------------------------
# Cross-step contamination: planning subgraph must not bleed user contexts
# ---------------------------------------------------------------------------


async def test_two_concurrent_planning_runs_have_isolated_step_outputs():
    """Two simultaneous planning_agent invocations with different users
    must produce separate `step_outputs` — there is no shared mutable
    state in the plan executor.
    """
    import asyncio
    import json
    from unittest.mock import AsyncMock

    from langchain_core.messages import AIMessage, HumanMessage

    from agents.planning_agent import run_planning_agent

    def _state(user_id: str) -> dict:
        return {
            "messages": [HumanMessage(content="get my transcript")],
            "intent": "planning",
            "intent_confidence": 0.95,
            "selected_agent": "planning_agent",
            "safety_result": None,
            "tool_calls": [],
            "response": "",
            "user_id": user_id,
            "session_id": f"sess-{user_id}",
            "requires_approval": False,
            "approval_status": None,
        }

    def _plan(user_id: str) -> dict:
        return {
            "reasoning": f"Get transcript for {user_id}",
            "steps": [{
                "step_id": 1, "description": "transcript", "depends_on": [],
                "agent_type": "query", "query_source": "canvas",
                "query_params": {"user_id": user_id},
            }],
        }

    def _llm_for(user_id: str):
        m = AsyncMock()
        m.ainvoke.return_value = AIMessage(content=json.dumps(_plan(user_id)))
        return m

    # Run both planners concurrently
    r_a, r_b = await asyncio.gather(
        run_planning_agent(_state("u1"), _llm_for("u1")),
        run_planning_agent(_state("u2"), _llm_for("u2")),
    )

    t_a = r_a["step_outputs"][1]
    t_b = r_b["step_outputs"][1]
    assert isinstance(t_a, StudentTranscript) and t_a.user_id == "u1"
    assert isinstance(t_b, StudentTranscript) and t_b.user_id == "u2"
    assert "CS201" in t_a.completed and "CS201" not in t_b.completed
    assert "ENG101" in t_b.completed and "ENG101" not in t_a.completed


# ---------------------------------------------------------------------------
# Shared sources — by design, NOT user-scoped
# ---------------------------------------------------------------------------


class TestSharedSourcesAreShared:
    """These sources serve catalog/program data that is not user-private.
    Locking these into tests so a future change that makes them suddenly
    user-scoped is a deliberate decision, not a silent regression.
    """

    def test_degree_program_is_user_independent(self):
        result_a = run_query_step(_degree_step("CS", "AI", "2024-2027"), step_outputs={})
        result_b = run_query_step(_degree_step("CS", "AI", "2024-2027"), step_outputs={})
        assert isinstance(result_a, DegreeProgram)
        # Same query → same data, regardless of caller. (No user_id in params.)
        assert result_a.model_dump() == result_b.model_dump()

    def test_degree_query_does_not_accept_user_id(self):
        """Make the contract loud: degree_db queries don't take a user_id
        parameter. If you find yourself wanting to add one, you're probably
        moving program metadata into a user-scoped store, which is a design
        change worth discussing.
        """
        # The signature accepts a dict — but adding user_id is silently
        # ignored. Test that the result doesn't change based on user_id.
        params_a = {"major": "CS", "track": "AI", "cohort": "2024-2027", "user_id": "u1"}
        params_b = {"major": "CS", "track": "AI", "cohort": "2024-2027", "user_id": "u2"}
        step_a = PlanStep(step_id=1, description="x", agent_type=AgentType.QUERY,
                          query_source=QuerySource.DEGREE_DB, query_params=params_a)
        step_b = PlanStep(step_id=1, description="x", agent_type=AgentType.QUERY,
                          query_source=QuerySource.DEGREE_DB, query_params=params_b)
        a = run_query_step(step_a, step_outputs={})
        b = run_query_step(step_b, step_outputs={})
        assert a.model_dump() == b.model_dump()
