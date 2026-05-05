"""Typed plan-step schema for the planning agent.

Plan time decisions (which agent runs the step, which data source to query,
which reasoning template to apply, which solver to invoke) are locked into
the schema so dispatch is deterministic. Sub-agents do not re-parse natural
language — they read structured fields off the PlanStep.

This is the contract referenced in the reasoning-layer talking points
(Talking Point 1).
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AgentType(str, Enum):
    QUERY = "query"
    REASONING = "reasoning"
    ACTION = "action"
    CONSTRAINT_SOLVER = "constraint_solver"


class QuerySource(str, Enum):
    CANVAS = "canvas"
    DEGREE_DB = "degree_db"
    CATALOG_DB = "catalog_db"
    SYLLABUS_RAG = "syllabus_rag"


SolverType = Literal["schedule_csp", "prereq_check"]


class PlanStep(BaseModel):
    """One node in the plan DAG.

    `description` is for traces and debugging only — it does not influence
    dispatch. The fields used at dispatch time depend on `agent_type`:

      QUERY  → query_source + query_params
      REASONING → reasoning_inputs + reasoning_template
      ACTION → action_tool + action_args
      CONSTRAINT_SOLVER → solver_type + solver_inputs
    """

    step_id: int = Field(ge=0)
    description: str
    depends_on: list[int] = Field(default_factory=list)
    agent_type: AgentType

    # Query
    query_source: QuerySource | None = None
    query_params: dict | None = None

    # Reasoning
    reasoning_inputs: list[int] | None = None
    reasoning_template: str | None = None

    # Action
    action_tool: str | None = None
    action_args: dict | None = None

    # Constraint solver
    solver_type: SolverType | None = None
    solver_inputs: dict | None = None

    @model_validator(mode="after")
    def _check_required_fields_per_agent_type(self) -> "PlanStep":
        if self.agent_type is AgentType.QUERY:
            if self.query_source is None or self.query_params is None:
                raise ValueError(
                    f"Step {self.step_id}: query agent_type requires "
                    "query_source + query_params"
                )
        elif self.agent_type is AgentType.REASONING:
            if not self.reasoning_inputs or not self.reasoning_template:
                raise ValueError(
                    f"Step {self.step_id}: reasoning agent_type requires "
                    "reasoning_inputs + reasoning_template"
                )
        elif self.agent_type is AgentType.ACTION:
            if not self.action_tool:
                raise ValueError(
                    f"Step {self.step_id}: action agent_type requires action_tool"
                )
        elif self.agent_type is AgentType.CONSTRAINT_SOLVER:
            if self.solver_type is None or self.solver_inputs is None:
                raise ValueError(
                    f"Step {self.step_id}: constraint_solver agent_type requires "
                    "solver_type + solver_inputs"
                )
        return self

    @model_validator(mode="after")
    def _no_self_dependency(self) -> "PlanStep":
        if self.step_id in self.depends_on:
            raise ValueError(
                f"Step {self.step_id}: cannot depend on itself"
            )
        return self


class Plan(BaseModel):
    """A DAG of plan steps. Validated for shape on construction."""

    steps: list[PlanStep] = Field(default_factory=list)
    reasoning: str = ""

    @model_validator(mode="after")
    def _validate_dag(self) -> "Plan":
        ids = {s.step_id for s in self.steps}
        if len(ids) != len(self.steps):
            raise ValueError("duplicate step_id in plan")
        for s in self.steps:
            for dep in s.depends_on:
                if dep not in ids:
                    raise ValueError(
                        f"Step {s.step_id} depends_on missing step {dep}"
                    )
        # Cycle detection (Kahn's algorithm)
        indeg = {sid: 0 for sid in ids}
        adj: dict[int, list[int]] = {sid: [] for sid in ids}
        for s in self.steps:
            for dep in s.depends_on:
                adj[dep].append(s.step_id)
                indeg[s.step_id] += 1
        ready = [sid for sid, d in indeg.items() if d == 0]
        visited = 0
        while ready:
            sid = ready.pop()
            visited += 1
            for nxt in adj[sid]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    ready.append(nxt)
        if visited != len(ids):
            raise ValueError("plan contains a cycle")
        return self

    def step(self, step_id: int) -> PlanStep:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        raise KeyError(f"no step {step_id}")

    def topological_layers(self) -> list[list[PlanStep]]:
        """Return steps grouped by dependency layer.

        Steps in the same layer have no dependencies on each other and can
        be executed concurrently (asyncio.gather). Layers run sequentially.
        """
        remaining = {s.step_id: set(s.depends_on) for s in self.steps}
        layers: list[list[PlanStep]] = []
        by_id = {s.step_id: s for s in self.steps}

        while remaining:
            ready_ids = [sid for sid, deps in remaining.items() if not deps]
            if not ready_ids:
                # Shouldn't happen — cycle detector ran in __post_init__
                raise ValueError("unreachable: cycle in topological_layers")
            ready_ids.sort()
            layers.append([by_id[sid] for sid in ready_ids])
            for sid in ready_ids:
                del remaining[sid]
            for deps in remaining.values():
                deps.difference_update(ready_ids)

        return layers
