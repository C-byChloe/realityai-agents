"""Phase 1 verification: each PlanStep becomes its own LangGraph node.

This is the test that proves the architectural claim in the talking
points: plan-and-execute with LangGraph means LangSmith sees N+1 nodes
for an N-step plan, not one opaque "execution" blob.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START

from agents.planning_agent import (
    compile_plan_graph,
    run_planning_agent,
    step_node_name,
)
from schemas.plan import AgentType, Plan, PlanStep, QuerySource


def _q(i: int, deps=None) -> PlanStep:
    return PlanStep(
        step_id=i,
        description=f"step {i}",
        agent_type=AgentType.QUERY,
        query_source=QuerySource.CANVAS,
        query_params={"user_id": "u1"},
        depends_on=deps or [],
    )


# ---------------------------------------------------------------------------
# Topology — the architectural deliverable
# ---------------------------------------------------------------------------


def test_compiled_graph_has_one_node_per_plan_step():
    """6-step plan → ≥6 step nodes in the compiled graph."""
    plan = Plan(
        steps=[
            _q(1),
            _q(2),
            PlanStep(step_id=3, description="reasoning",
                     depends_on=[1, 2], agent_type=AgentType.REASONING,
                     reasoning_inputs=[1, 2],
                     reasoning_template="extract_unsatisfied_elective_pool"),
            _q(4, deps=[3]),
            PlanStep(step_id=5, description="solver",
                     depends_on=[1, 4], agent_type=AgentType.CONSTRAINT_SOLVER,
                     solver_type="schedule_csp",
                     solver_inputs={"k": "v"}),
            PlanStep(step_id=6, description="explain",
                     depends_on=[5], agent_type=AgentType.REASONING,
                     reasoning_inputs=[5],
                     reasoning_template="explain_schedule_recommendation"),
        ]
    )
    compiled = compile_plan_graph(plan)
    nodes = set(compiled.get_graph().nodes.keys())

    # LangGraph adds START + END as virtual nodes.
    expected_step_nodes = {step_node_name(s) for s in plan.steps}
    assert expected_step_nodes.issubset(nodes), (
        f"missing step nodes: {expected_step_nodes - nodes}"
    )
    # Total = 6 step nodes + START + END = 8
    assert len(nodes) >= len(plan.steps) + 2


def test_step_node_names_carry_agent_type_and_source():
    """Node name pattern is `step_<id>_<agent_type_or_source>` for LangSmith readability."""
    plan = Plan(steps=[
        _q(1),
        PlanStep(step_id=2, description="solve",
                 depends_on=[], agent_type=AgentType.CONSTRAINT_SOLVER,
                 solver_type="schedule_csp", solver_inputs={}),
    ])
    assert step_node_name(plan.steps[0]) == "step_1_query_canvas"
    assert step_node_name(plan.steps[1]) == "step_2_solver_schedule_csp"


def test_compiled_graph_edges_match_depends_on():
    """Every depends_on entry produces an edge in the compiled graph."""
    plan = Plan(steps=[
        _q(1),
        _q(2),
        _q(3, deps=[1, 2]),
    ])
    compiled = compile_plan_graph(plan)
    edges = compiled.get_graph().edges

    # (source, target) pairs — LangGraph internal edge representation
    edge_pairs = {(e.source, e.target) for e in edges}
    n1, n2, n3 = (step_node_name(s) for s in plan.steps)
    # Roots edge from START
    assert (START, n1) in edge_pairs
    assert (START, n2) in edge_pairs
    # Step 3 has both parents wired in
    assert (n1, n3) in edge_pairs
    assert (n2, n3) in edge_pairs
    # Leaf wires to END
    assert (n3, END) in edge_pairs


# ---------------------------------------------------------------------------
# Parallelism — sibling steps run in the same superstep
# ---------------------------------------------------------------------------


async def test_independent_steps_execute_in_parallel():
    """Two independent steps with sleep(50ms) each finish in <100ms total.

    If LangGraph serialized them, total time would be ~100ms. Parallel
    execution finishes in ~50ms. We assert <80ms with margin.
    """
    from agents.planning_agent import PlanExecState
    from agents import planning_agent as pa

    sleep_ms = 50
    start_times: list[float] = []

    def slow_query(step, step_outputs):
        start_times.append(time.perf_counter())
        time.sleep(sleep_ms / 1000)
        return f"result-{step.step_id}"

    original = pa.run_query_step
    pa.run_query_step = slow_query
    try:
        plan = Plan(steps=[_q(1), _q(2)])
        compiled = compile_plan_graph(plan)
        t0 = time.perf_counter()
        result = await compiled.ainvoke({
            "plan": plan,
            "step_outputs": {},
            "tool_calls": [],
        })
        elapsed_ms = (time.perf_counter() - t0) * 1000
    finally:
        pa.run_query_step = original

    assert len(start_times) == 2, "both steps should have run once"
    # Both nodes started within ~10ms of each other → truly parallel
    assert abs(start_times[1] - start_times[0]) < 0.020, \
        f"steps did not start in parallel: gap={start_times[1] - start_times[0]:.3f}s"
    # Total wall time well under 2*sleep_ms
    assert elapsed_ms < sleep_ms * 1.6, \
        f"plan took {elapsed_ms:.0f}ms; expected <{sleep_ms*1.6:.0f}ms (parallel)"
    assert result["step_outputs"] == {1: "result-1", 2: "result-2"}


async def test_join_node_fires_exactly_once():
    """A step with two parents must fire once, not once per incoming edge."""
    from agents import planning_agent as pa

    fire_count = {1: 0, 2: 0, 3: 0}

    def counting_query(step, step_outputs):
        fire_count[step.step_id] += 1
        return f"result-{step.step_id}"

    original = pa.run_query_step
    pa.run_query_step = counting_query
    try:
        plan = Plan(steps=[
            _q(1),
            _q(2),
            _q(3, deps=[1, 2]),  # depends on both
        ])
        compiled = compile_plan_graph(plan)
        await compiled.ainvoke({"plan": plan, "step_outputs": {}, "tool_calls": []})
    finally:
        pa.run_query_step = original

    assert fire_count == {1: 1, 2: 1, 3: 1}, \
        f"join node fired more than once: {fire_count}"


# ---------------------------------------------------------------------------
# End-to-end via run_planning_agent — should still produce typed outputs
# ---------------------------------------------------------------------------


def _mock_llm(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke.return_value = AIMessage(content=content)
    return mock


def _state(msg: str) -> dict:
    return {
        "messages": [HumanMessage(content=msg)],
        "intent": "planning",
        "intent_confidence": 0.9,
        "selected_agent": "planning_agent",
        "safety_result": None,
        "tool_calls": [],
        "response": "",
        "user_id": "u1",
        "session_id": "s1",
        "requires_approval": False,
        "approval_status": None,
    }


async def test_run_planning_agent_invokes_compiled_subgraph():
    plan_json = {
        "reasoning": "Two parallel reads.",
        "steps": [
            {"step_id": 1, "description": "transcript", "depends_on": [],
             "agent_type": "query", "query_source": "canvas",
             "query_params": {"user_id": "u1"}},
            {"step_id": 2, "description": "degree", "depends_on": [],
             "agent_type": "query", "query_source": "degree_db",
             "query_params": {"major": "CS", "track": "AI", "cohort": "2024-2027"}},
        ],
    }
    state = _state("plan")
    result = await run_planning_agent(state, _mock_llm(json.dumps(plan_json)))

    assert len(result["tool_calls"]) == 2
    assert all(tc.success for tc in result["tool_calls"])
    assert {1, 2} == set(result["step_outputs"].keys())
