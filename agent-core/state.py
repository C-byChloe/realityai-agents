"""Agent state definitions for the LangGraph state machine."""

from typing import Annotated, Any, Literal

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


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


class AgentState(TypedDict):
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

    # Execution
    tool_calls: list[ToolCall]

    # Response
    response: str

    # Metadata
    user_id: str
    session_id: str
    requires_approval: bool
    approval_status: Literal["pending", "approved", "rejected"] | None
