"""Tests for the typed plan-step schema."""

import pytest
from pydantic import ValidationError

from schemas.plan import AgentType, Plan, PlanStep, QuerySource


def test_query_step_requires_source_and_params():
    with pytest.raises(ValidationError):
        PlanStep(step_id=1, description="bad", agent_type=AgentType.QUERY)


def test_query_step_valid():
    s = PlanStep(
        step_id=1,
        description="get transcript",
        agent_type=AgentType.QUERY,
        query_source=QuerySource.CANVAS,
        query_params={"endpoint": "transcript", "user_id": "u1"},
    )
    assert s.query_source is QuerySource.CANVAS


def test_reasoning_step_requires_inputs_and_template():
    with pytest.raises(ValidationError):
        PlanStep(step_id=1, description="bad", agent_type=AgentType.REASONING)


def test_action_step_requires_tool():
    with pytest.raises(ValidationError):
        PlanStep(step_id=1, description="bad", agent_type=AgentType.ACTION)


def test_solver_step_requires_solver_type_and_inputs():
    with pytest.raises(ValidationError):
        PlanStep(step_id=1, description="bad", agent_type=AgentType.CONSTRAINT_SOLVER)


def test_no_self_dependency():
    with pytest.raises(ValidationError):
        PlanStep(
            step_id=2,
            description="x",
            agent_type=AgentType.QUERY,
            query_source=QuerySource.CANVAS,
            query_params={},
            depends_on=[2],
        )


def _q(i: int, deps: list[int] | None = None) -> PlanStep:
    return PlanStep(
        step_id=i,
        description=f"step {i}",
        agent_type=AgentType.QUERY,
        query_source=QuerySource.CANVAS,
        query_params={"i": i},
        depends_on=deps or [],
    )


def test_plan_rejects_dangling_dependency():
    with pytest.raises(ValidationError):
        Plan(steps=[_q(1, deps=[99])])


def test_plan_rejects_duplicate_step_ids():
    with pytest.raises(ValidationError):
        Plan(steps=[_q(1), _q(1)])


def test_plan_rejects_cycle():
    a = _q(1, deps=[2])
    b = _q(2, deps=[1])
    with pytest.raises(ValidationError):
        Plan(steps=[a, b])


def test_topological_layers_groups_independent_steps():
    """Steps 1 and 2 with no deps should be in the same layer."""
    plan = Plan(steps=[_q(1), _q(2), _q(3, deps=[1, 2])])
    layers = plan.topological_layers()
    assert len(layers) == 2
    assert {s.step_id for s in layers[0]} == {1, 2}
    assert [s.step_id for s in layers[1]] == [3]


def test_topological_layers_canonical_six_step_plan():
    """The 'avoid Fridays' example from the talking points."""
    plan = Plan(
        steps=[
            _q(1),                          # transcript
            _q(2),                          # degree program
            PlanStep(
                step_id=3, description="gap", agent_type=AgentType.REASONING,
                reasoning_inputs=[2], reasoning_template="extract_unsatisfied",
                depends_on=[2],
            ),
            _q(4, deps=[3]),                # catalog query
            PlanStep(
                step_id=5, description="solve", agent_type=AgentType.CONSTRAINT_SOLVER,
                solver_type="schedule_csp", solver_inputs={"k": "v"},
                depends_on=[1, 4],
            ),
            PlanStep(
                step_id=6, description="explain", agent_type=AgentType.REASONING,
                reasoning_inputs=[5], reasoning_template="explain_schedule",
                depends_on=[5],
            ),
        ]
    )
    layers = plan.topological_layers()
    # Layer 0: {1, 2} run in parallel
    assert {s.step_id for s in layers[0]} == {1, 2}
    # Layer ordering: 3 follows 2; 4 follows 3; 5 follows 1+4; 6 follows 5
    layer_for = {s.step_id: i for i, layer in enumerate(layers) for s in layer}
    assert layer_for[3] > layer_for[2]
    assert layer_for[4] > layer_for[3]
    assert layer_for[5] > layer_for[4] and layer_for[5] > layer_for[1]
    assert layer_for[6] > layer_for[5]
