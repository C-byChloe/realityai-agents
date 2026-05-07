"""Planning Agent — emits a typed Plan DAG and executes it as a LangGraph subgraph.

Design (Phase 1):

  1. `make_plan()` — LLM emits a typed `Plan` (`schemas/plan.py`).
  2. `compile_plan_graph(plan)` — builds a fresh `StateGraph` where each
     `PlanStep` is its own node (`step_<id>_<agent_type>`). Edges come
     from `depends_on`. Steps with no deps wire from START; leaf steps
     wire to END.
  3. `run_planning_agent()` invokes the compiled subgraph.

LangGraph runs nodes with no inter-dependency in the same superstep, so
plan-level parallelism is the runtime's job, not ours. Each step shows
up as its own LangSmith span — no opaque "execution" blob.

Talking-Point alignment:
  - TP1 "plan steps lock plan-time decisions": dispatch by `agent_type`.
  - TP3 "LLM at the ends, symbolic in the middle": reasoning + solver
    nodes call deterministic code; LLM only runs at make_plan() and
    optionally inside the explain_schedule reasoning template.
"""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from typing_extensions import TypedDict

from agents.action_agent import ACTION_TOOLS, run_action_step
from agents.query_agent import run_query_step
from prompts import load_prompt
from reasoning.gap_analysis import compute_unsatisfied
from reasoning.solver import ConstraintSolver
from safety.outer.schemas import SessionContext
from schemas.plan import AgentType, Plan, PlanStep, QuerySource
from schemas.query_outputs import (
    CourseSection,
    DegreeProgram,
    StudentTranscript,
)
from schemas.solver import ScheduleConstraints, ScheduleOption
from state import AgentState, ToolCall

# ---------------------------------------------------------------------------
# System Prompt — typed Plan emission
# ---------------------------------------------------------------------------

# Sourced from prompts/planning_agent.md — see frontmatter for version,
# benchmark binding, and audit status. Do not inline edits here; edit the
# prompt file and bump its version.
PLANNING_AGENT_SYSTEM_PROMPT = load_prompt("planning_agent")


# ---------------------------------------------------------------------------
# Registries exposed for trace + tooling. Query dispatch is driven by the
# `QuerySource` enum (typed, plan-aligned); action dispatch by tool name.
# ---------------------------------------------------------------------------


QUERY_SOURCES: list[QuerySource] = list(QuerySource)


# ---------------------------------------------------------------------------
# Subgraph state with reducers (parallel-write safe)
# ---------------------------------------------------------------------------


def _merge_step_outputs(left: dict, right: dict) -> dict:
    """Reducer for parallel writes to step_outputs.

    LangGraph runs sibling nodes in the same superstep, and each node's
    return value is merged into state via this reducer. Without it,
    concurrent writes raise `InvalidUpdateError`.
    """
    return {**(left or {}), **(right or {})}


class PlanExecState(TypedDict, total=False):
    """State scoped to one plan execution. Lives only inside the compiled
    plan subgraph; never leaks to `AgentState`.

    `session` carries the JWT-derived identity (D5) into action steps so
    inner safety Layer 1 can RBAC against the actual caller's role
    rather than a placeholder. `run_planning_agent` builds it from the
    parent `AgentState`; without it, `run_action_step` falls through to
    a fail-closed student-role default.
    """

    plan: Plan
    step_outputs: Annotated[dict[int, Any], _merge_step_outputs]
    tool_calls: Annotated[list[ToolCall], operator.add]
    session: SessionContext


# ---------------------------------------------------------------------------
# Step-node factory
# ---------------------------------------------------------------------------


def _make_step_node(step: PlanStep):
    """Build an async node function bound to a single PlanStep.

    Each call returns the partial state update for *only this step*:
    `step_outputs={step_id: result}` and `tool_calls=[ToolCall(...)]`.
    The reducers merge these with sibling nodes' updates.
    """

    async def node(state: PlanExecState) -> dict:
        try:
            result = await _dispatch_step(
                step,
                state.get("step_outputs", {}),
                session=state.get("session"),
            )
        except Exception as e:
            return {
                "step_outputs": {step.step_id: e},
                "tool_calls": [ToolCall(
                    tool_name=_step_label(step),
                    arguments=_step_args(step),
                    success=False,
                    error=str(e),
                )],
            }

        # Action steps denied by inner safety return a structured dict
        # rather than raising — surface them as failed ToolCalls so the
        # plan executor's success flag and trace UI both reflect the
        # denial without aborting sibling steps.
        if (
            step.agent_type is AgentType.ACTION
            and isinstance(result, dict)
            and result.get("denied_by_inner_safety")
        ):
            return {
                "step_outputs": {step.step_id: result},
                "tool_calls": [ToolCall(
                    tool_name=_step_label(step),
                    arguments=_step_args(step),
                    result=_summarize_for_trace(result),
                    success=False,
                    error=result.get("message") or result.get("reason_code"),
                )],
            }

        return {
            "step_outputs": {step.step_id: result},
            "tool_calls": [ToolCall(
                tool_name=_step_label(step),
                arguments=_step_args(step),
                result=_summarize_for_trace(result),
                success=True,
            )],
        }

    node.__name__ = step_node_name(step)
    return node


def step_node_name(step: PlanStep) -> str:
    """Stable node name used in graph + trace."""
    suffix = step.agent_type.value
    if step.agent_type is AgentType.QUERY and step.query_source is not None:
        suffix = f"query_{step.query_source.value}"
    elif step.agent_type is AgentType.CONSTRAINT_SOLVER and step.solver_type:
        suffix = f"solver_{step.solver_type}"
    elif step.agent_type is AgentType.REASONING and step.reasoning_template:
        suffix = f"reasoning_{step.reasoning_template}"
    elif step.agent_type is AgentType.ACTION and step.action_tool:
        suffix = f"action_{step.action_tool}"
    return f"step_{step.step_id}_{suffix}"


# ---------------------------------------------------------------------------
# Per-agent_type dispatch (sync handlers run in worker threads)
# ---------------------------------------------------------------------------


import asyncio


async def _dispatch_step(
    step: PlanStep,
    step_outputs: dict[int, Any],
    *,
    session: SessionContext | None = None,
) -> Any:
    if step.agent_type is AgentType.QUERY:
        return await asyncio.to_thread(run_query_step, step, step_outputs)
    if step.agent_type is AgentType.ACTION:
        # run_action_step is async (calls inner_safety_check internally
        # which awaits Layer 4 live-state). The tool_func.invoke inside
        # is sync and brief — running it on the event loop is fine.
        # Session plumbing (D5): pass the JWT-derived identity so inner
        # Layer 1 RBACs against the actual caller, not a placeholder.
        return await run_action_step(step, step_outputs, session=session)
    if step.agent_type is AgentType.REASONING:
        return await asyncio.to_thread(_run_reasoning_step, step, step_outputs)
    if step.agent_type is AgentType.CONSTRAINT_SOLVER:
        return await asyncio.to_thread(_run_solver_step, step, step_outputs)
    raise ValueError(f"unknown agent_type: {step.agent_type}")


# ---------------------------------------------------------------------------
# Reasoning step (template registry)
# ---------------------------------------------------------------------------


def _run_reasoning_step(step: PlanStep, step_outputs: dict[int, Any]) -> Any:
    template = step.reasoning_template
    inputs = [step_outputs[i] for i in (step.reasoning_inputs or [])]

    if template == "extract_unsatisfied_elective_pool":
        program = _expect(inputs, DegreeProgram)
        transcript = _expect(inputs, StudentTranscript)
        return compute_unsatisfied(program, transcript)

    if template == "explain_schedule_recommendation":
        options = _expect(inputs, list)
        if not options:
            return "No valid schedule options found."
        top = options[0]
        if not isinstance(top, ScheduleOption):
            raise ValueError("explain_schedule_recommendation expects list[ScheduleOption]")
        codes = ", ".join(c.course_code for c in top.courses)
        return (
            f"Recommended schedule: {codes} "
            f"({top.total_credits} credits). All listed courses have prereqs met "
            f"and no time conflicts."
        )

    raise ValueError(f"unknown reasoning_template: {template}")


def _expect(inputs: list[Any], cls: type) -> Any:
    for v in inputs:
        if isinstance(v, cls):
            return v
    raise ValueError(f"reasoning step missing input of type {cls.__name__}")


# ---------------------------------------------------------------------------
# Constraint solver step
# ---------------------------------------------------------------------------


def _run_solver_step(step: PlanStep, step_outputs: dict[int, Any]) -> Any:
    if step.solver_type == "schedule_csp":
        inputs = _resolve_solver_inputs(step.solver_inputs or {}, step_outputs)
        candidates = inputs.get("candidates")
        completed = inputs.get("completed")
        constraints_raw = inputs.get("constraints")

        if not isinstance(candidates, list) or not all(isinstance(c, CourseSection) for c in candidates):
            raise ValueError("schedule_csp expects candidates: list[CourseSection]")

        if isinstance(completed, StudentTranscript):
            completed_set = completed.completed
        elif isinstance(completed, set):
            completed_set = completed
        elif isinstance(completed, list):
            completed_set = set(completed)
        else:
            completed_set = set()

        if isinstance(constraints_raw, ScheduleConstraints):
            constraints = constraints_raw
        else:
            constraints = ScheduleConstraints.model_validate(constraints_raw or {})

        return ConstraintSolver().solve(candidates, completed_set, constraints)

    if step.solver_type == "prereq_check":
        raise NotImplementedError("prereq_check solver is not implemented yet")

    raise ValueError(f"unknown solver_type: {step.solver_type}")


def _resolve_solver_inputs(inputs: dict, step_outputs: dict[int, Any]) -> dict:
    out = {}
    for k, v in inputs.items():
        if isinstance(v, str) and v.startswith("<from_step_") and v.endswith(">"):
            ref = int(v[len("<from_step_"):-1])
            out[k] = step_outputs[ref]
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------


def _step_label(step: PlanStep) -> str:
    if step.agent_type is AgentType.QUERY:
        return f"query/{step.query_source.value}" if step.query_source else "query"
    if step.agent_type is AgentType.ACTION:
        return step.action_tool or "action"
    if step.agent_type is AgentType.REASONING:
        return f"reasoning/{step.reasoning_template}"
    if step.agent_type is AgentType.CONSTRAINT_SOLVER:
        return f"solver/{step.solver_type}"
    return str(step.agent_type)


def _step_args(step: PlanStep) -> dict:
    if step.agent_type is AgentType.QUERY:
        return step.query_params or {}
    if step.agent_type is AgentType.ACTION:
        return step.action_args or {}
    if step.agent_type is AgentType.REASONING:
        return {
            "inputs": step.reasoning_inputs,
            "template": step.reasoning_template,
        }
    if step.agent_type is AgentType.CONSTRAINT_SOLVER:
        return step.solver_inputs or {}
    return {}


def _summarize_for_trace(result: Any) -> Any:
    if isinstance(result, StudentTranscript):
        return {"type": "StudentTranscript", "completed": sorted(result.completed)}
    if isinstance(result, DegreeProgram):
        return {"type": "DegreeProgram", "major": result.major, "track": result.track}
    if isinstance(result, list):
        if result and isinstance(result[0], CourseSection):
            return {"type": "list[CourseSection]", "codes": [c.course_code for c in result]}
        if result and isinstance(result[0], ScheduleOption):
            return {
                "type": "list[ScheduleOption]",
                "count": len(result),
                "top_courses": [c.course_code for c in result[0].courses] if result else [],
            }
    return result


# ---------------------------------------------------------------------------
# Dynamic graph compilation — Phase 1 deliverable
# ---------------------------------------------------------------------------


def compile_plan_graph(plan: Plan):
    """Build a `StateGraph` where each PlanStep is its own node.

    Edges:
      - START → step_<id>     for every root (depends_on == [])
      - step_<dep> → step_<id> for every dependency
      - step_<id> → END        for every leaf (no other step depends on it)

    Sibling steps (no inter-dependency) are scheduled in the same
    LangGraph superstep, so parallelism is the runtime's responsibility.
    """
    graph = StateGraph(PlanExecState)
    node_names = {s.step_id: step_node_name(s) for s in plan.steps}

    for step in plan.steps:
        graph.add_node(node_names[step.step_id], _make_step_node(step))

    dependants: dict[int, set[int]] = {s.step_id: set() for s in plan.steps}
    for step in plan.steps:
        for dep in step.depends_on:
            dependants[dep].add(step.step_id)

    for step in plan.steps:
        target = node_names[step.step_id]
        if not step.depends_on:
            graph.add_edge(START, target)
        elif len(step.depends_on) == 1:
            graph.add_edge(node_names[step.depends_on[0]], target)
        else:
            # Multiple parents — use list-source add_edge for AND-join.
            # Without this LangGraph fires the node once per incoming edge.
            graph.add_edge([node_names[d] for d in step.depends_on], target)
        if not dependants[step.step_id]:
            graph.add_edge(target, END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Plan emission (LLM call)
# ---------------------------------------------------------------------------


async def make_plan(user_query: str, llm) -> tuple[Plan | None, str, str]:
    """Ask the LLM for a typed Plan. Returns (plan, reasoning, raw_response).

    plan is None when the LLM produced no parseable plan or no steps.
    raw_response is returned for trace/error surfacing.
    """
    messages = [
        SystemMessage(content=PLANNING_AGENT_SYSTEM_PROMPT),
        HumanMessage(content=user_query),
    ]
    ai_response = await llm.ainvoke(messages)
    raw = ai_response.content if hasattr(ai_response, "content") else str(ai_response)

    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, AttributeError):
        return None, "", raw

    reasoning = result.get("reasoning", "")
    raw_steps = result.get("steps", [])

    if not raw_steps:
        return None, reasoning, raw

    try:
        plan = Plan(
            steps=[PlanStep.model_validate(s) for s in raw_steps],
            reasoning=reasoning,
        )
    except ValidationError as e:
        return None, f"Plan validation failed: {e}", raw

    return plan, reasoning, raw


# ---------------------------------------------------------------------------
# Agent entry point (intent-router)
# ---------------------------------------------------------------------------


async def run_planning_agent(state: AgentState, llm) -> dict:
    """Plan a multi-step request and execute the plan as a LangGraph subgraph."""
    # Explicit fallback chain — make trust order visible in code review.
    # `user_query_normalized` is set by `coref_resolver_node` on the execute
    # branch. Flows that bypass coref (e.g., HiTL approval) leave it unset,
    # so we fall back to the raw last user message.
    user_query = (
        state.get("user_query_normalized")
        or state["messages"][-1].content
    )
    plan, reasoning, raw = await make_plan(user_query, llm)

    if plan is None:
        if reasoning.startswith("Plan validation failed"):
            return {
                "response": reasoning,
                "tool_calls": [],
                "plan": [],
                "step_outputs": {},
            }
        return {
            "response": reasoning or raw,
            "tool_calls": [],
            "plan": [],
            "step_outputs": {},
        }

    # D5 — propagate the JWT-derived identity into PlanExecState so
    # action steps' inner Layer 1 RBAC checks the real caller's role.
    # Default to "student" when missing, mirroring run_action_agent's
    # fail-closed posture: a missing role claim must not silently grant
    # instructor privileges to plan-driven action steps.
    session = SessionContext(
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
        user_role=state.get("user_role") or "student",
    )

    subgraph = compile_plan_graph(plan)
    result = await subgraph.ainvoke({
        "plan": plan,
        "step_outputs": {},
        "tool_calls": [],
        "session": session,
    })

    step_outputs = result.get("step_outputs", {})
    tool_calls = result.get("tool_calls", [])

    response_parts = [f"**Plan:** {plan.reasoning}", ""]
    tc_by_label = {(tc.tool_name, json.dumps(tc.arguments, default=str)): tc for tc in tool_calls}
    for s in plan.steps:
        key = (_step_label(s), json.dumps(_step_args(s), default=str))
        tc = tc_by_label.get(key)
        if tc and tc.success:
            marker = "OK"
        elif tc:
            marker = f"FAILED ({tc.error})"
        else:
            marker = "?"
        response_parts.append(f"Step {s.step_id}: {s.description} — {marker}")

    return {
        "response": "\n".join(response_parts),
        "tool_calls": tool_calls,
        "plan": plan.steps,
        "step_outputs": step_outputs,
    }
