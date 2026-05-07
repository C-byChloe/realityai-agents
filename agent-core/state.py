"""Agent state definitions for the LangGraph state machine."""

from typing import Annotated, Any, Literal

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from preprocessing.schemas import RewrittenQuery
from safety.inner.schemas import InnerSafetyResult
from safety.outer.schemas import OuterSafetyResult
from schemas.plan import PlanStep


class SafetyResult(BaseModel):
    """Result from the two-layer safety system."""

    flagged: bool = False
    reason: str | None = None
    static_risk: str | None = None  # "high" or "low"
    dynamic_flagged: bool = False


class ToolCall(BaseModel):
    """A tool call made by an agent."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    success: bool = True
    error: str | None = None


class AgentState(TypedDict, total=False):
    """State schema for the LangGraph state machine.

    Flows through: intent_classification → agent_routing → safety_check
                   → execution → response_generation
    """

    # User input
    messages: Annotated[list, add_messages]

    # Intent classification result
    intent: str  # "action", "query", "planning"
    intent_confidence: float

    # Agent routing
    selected_agent: str  # "action_agent", "query_agent", "planning_agent"

    # Identity (multi-tenant scope). `user_role` is the load-bearing
    # invariant for the outer safety layer — see ADR 005 D5. Production
    # API gateway should populate this from the JWT role claim; until
    # that wiring lands (Phase -1), consumers default to "student" at
    # call sites.
    user_role: str

    # Safety check (legacy 2-layer OR-merge, retired by outer-safety Phase 4)
    safety_result: SafetyResult | None

    # Outer safety result (3-tier sequential short-circuit — ADR 005).
    # Populated by `outer_safety_check` node; consumed by the
    # `_route_after_outer_safety` conditional edge.
    outer_safety_result: OuterSafetyResult | None

    # Inner safety result (4-tier sequential + audit sidecar — ADR 006).
    # Populated by inner_safety_check inside the action subgraph and the
    # plan-driven action path. Carries an audit_record that is always
    # populated even on DENY (D4 invariant).
    inner_safety_result: InnerSafetyResult | None

    # Coref resolver output (Layer 1 of query rewrite). Populated by
    # `coref_resolver_node` between safety_check and execution. Both fields
    # may be unset on flows that bypass coref (e.g., HiTL approval branch);
    # consumers must use the explicit fallback chain to messages[-1].content.
    rewritten_query: RewrittenQuery | None
    user_query_normalized: str | None

    # Execution
    tool_calls: list[ToolCall]

    # Planning agent state — typed DAG of plan steps + per-step outputs.
    # `plan` is the structured DAG produced by make_plan; `step_outputs` is
    # keyed by step_id and holds whatever typed object that step returned
    # (StudentTranscript, list[CourseSection], list[ScheduleOption], ...).
    plan: list[PlanStep]
    step_outputs: dict[int, Any]

    # Response
    response: str

    # Metadata
    user_id: str
    session_id: str
    requires_approval: bool
    approval_status: Literal["pending", "approved", "rejected"] | None
