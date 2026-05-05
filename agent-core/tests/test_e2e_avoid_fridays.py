"""End-to-end test for the canonical 'avoid Fridays' plan.

This is the example from the reasoning-layer talking points (Talking
Point 1). It exercises the full typed pipeline:

  Step 1: query canvas        → StudentTranscript                (parallel)
  Step 2: query degree_db     → DegreeProgram                    (parallel)
  Step 3: reasoning           → list[UnsatisfiedRequirement]
  Step 4: query catalog_db    → list[CourseSection]
  Step 5: constraint_solver   → list[ScheduleOption]
  Step 6: reasoning           → str (explanation)
"""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agents.planning_agent import run_planning_agent
from schemas.query_outputs import (
    CourseSection,
    DegreeProgram,
    StudentTranscript,
    UnsatisfiedRequirement,
)
from schemas.solver import ScheduleOption


def _mock_llm(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke.return_value = AIMessage(content=content)
    return mock


def _state(msg: str) -> dict:
    return {
        "messages": [HumanMessage(content=msg)],
        "intent": "planning",
        "intent_confidence": 0.95,
        "selected_agent": "planning_agent",
        "safety_result": None,
        "tool_calls": [],
        "response": "",
        "user_id": "u1",
        "session_id": "s1",
        "requires_approval": False,
        "approval_status": None,
    }


CANONICAL_PLAN = {
    "reasoning": "Plan next semester avoiding Friday classes.",
    "steps": [
        {
            "step_id": 1, "description": "Get completed courses",
            "depends_on": [], "agent_type": "query",
            "query_source": "canvas",
            "query_params": {"user_id": "u1"},
        },
        {
            "step_id": 2, "description": "Get AI track requirements",
            "depends_on": [], "agent_type": "query",
            "query_source": "degree_db",
            "query_params": {"major": "CS", "track": "AI", "cohort": "2024-2027"},
        },
        {
            "step_id": 3, "description": "Identify unsatisfied AI electives",
            "depends_on": [1, 2], "agent_type": "reasoning",
            "reasoning_inputs": [1, 2],
            "reasoning_template": "extract_unsatisfied_elective_pool",
        },
        {
            "step_id": 4, "description": "Find next-semester offerings",
            "depends_on": [3], "agent_type": "query",
            "query_source": "catalog_db",
            "query_params": {"term": "S26", "days_excluded": ["F"]},
        },
        {
            "step_id": 5, "description": "Solve schedule",
            "depends_on": [1, 4], "agent_type": "constraint_solver",
            "solver_type": "schedule_csp",
            "solver_inputs": {
                "candidates": "<from_step_4>",
                "completed": "<from_step_1>",
                "constraints": {
                    "days_excluded": ["F"],
                    "min_courses": 2,
                    "max_courses": 3,
                },
            },
        },
        {
            "step_id": 6, "description": "Explain recommendation",
            "depends_on": [5], "agent_type": "reasoning",
            "reasoning_inputs": [5],
            "reasoning_template": "explain_schedule_recommendation",
        },
    ],
}


async def test_canonical_avoid_fridays_plan_executes_end_to_end():
    state = _state("Plan my next semester for AI track avoiding Fridays")
    result = await run_planning_agent(state, _mock_llm(json.dumps(CANONICAL_PLAN)))

    # All 6 steps logged
    assert len(result["tool_calls"]) == 6
    assert all(tc.success for tc in result["tool_calls"]), \
        [tc for tc in result["tool_calls"] if not tc.success]

    outputs = result["step_outputs"]

    # Typed outputs at every step boundary
    assert isinstance(outputs[1], StudentTranscript)
    assert isinstance(outputs[2], DegreeProgram)
    assert isinstance(outputs[3], list)
    assert all(isinstance(u, UnsatisfiedRequirement) for u in outputs[3])
    assert isinstance(outputs[4], list)
    assert all(isinstance(c, CourseSection) for c in outputs[4])
    assert isinstance(outputs[5], list)
    assert all(isinstance(s, ScheduleOption) for s in outputs[5])
    assert isinstance(outputs[6], str)

    # Step 4 honored the days_excluded filter (no Friday courses)
    for section in outputs[4]:
        assert "F" not in {d for m in section.meetings for d in m.days}

    # Step 5 produced at least one valid schedule
    assert outputs[5], "solver returned no schedule options"
    top = outputs[5][0]
    for section in top.courses:
        assert "F" not in {d for m in section.meetings for d in m.days}

    # Step 6 explanation references real course codes from the recommendation
    explanation = outputs[6]
    assert any(c.course_code in explanation for c in top.courses)
