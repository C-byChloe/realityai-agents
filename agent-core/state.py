"""Agent state definitions for the LangGraph state machine."""

from typing import Annotated, Any, Literal

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from preprocessing.schemas import RewrittenQuery
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

    # Safety check
    safety_result: SafetyResult | None

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
