"""Internal state schemas for Query/Action subgraphs.

These TypedDicts live ONLY inside their respective compiled subgraphs.
The orchestrator never sees them — it sees `QueryAgentOutput` /
`ActionAgentOutput` Pydantic models at the subgraph boundary.

This separation is the architectural claim from Talking Point 2: the
sub-agent's working memory (route decisions, raw tool results,
intermediate validation flags, audit metadata) does not pollute the
parent state, and does not appear at the parent's LangSmith trace level.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Query Agent — internal state and I/O models
# ---------------------------------------------------------------------------


class QueryAgentInternalState(TypedDict, total=False):
    """Working memory for the Query Agent subgraph. Internal-only."""

    user_message: str

    # Routing
    route_decision: dict           # parsed LLM JSON: {"tool", "arguments", "query_type"}
    selected_tool: str
    tool_arguments: dict
    query_type: str                 # "deterministic" | "tutoring"

    # Execution
    raw_tool_result: dict
    execution_error: str | None

    # Output (carried forward into the boundary model)
    response_text: str
    success: bool


class QueryAgentInput(BaseModel):
    """What the orchestrator hands to the Query Agent subgraph."""

    user_message: str
    user_id: str
    session_id: str


class QueryAgentOutput(BaseModel):
    """What the Query Agent returns to the orchestrator. Strict shape — no
    internal fields leak through.
    """

    response: str
    success: bool
    selected_tool: str = ""
    query_type: str = ""           # "deterministic" | "tutoring"


QUERY_INTERNAL_ONLY_KEYS: frozenset[str] = frozenset({
    "route_decision",
    "tool_arguments",
    "raw_tool_result",
    "execution_error",
    "response_text",
})


# ---------------------------------------------------------------------------
# Action Agent — internal state and I/O models
# ---------------------------------------------------------------------------


class ActionAgentInternalState(TypedDict, total=False):
    """Working memory for the Action Agent subgraph. Internal-only.

    The 3-gate flow (validate → execute → audit) leaves typed traces
    on this state so each gate can be inspected during debug. None of
    these fields appear in `ActionAgentOutput`.
    """

    user_message: str

    # Routing
    route_decision: dict
    selected_tool: str
    tool_arguments: dict
    confirmation_text: str

    # Validate gate
    validation_passed: bool
    validation_errors: list[str]

    # Execute gate
    raw_tool_result: dict
    execution_error: str | None

    # Audit gate
    audit_record: dict

    # Output
    response_text: str
    success: bool


class ActionAgentInput(BaseModel):
    user_message: str
    user_id: str
    session_id: str


class ActionAgentOutput(BaseModel):
    """Strict boundary model — internal gate fields are not exposed."""

    response: str
    success: bool
    selected_tool: str = ""
    audit_id: str = ""             # opaque correlation ID; full audit_record stays internal


ACTION_INTERNAL_ONLY_KEYS: frozenset[str] = frozenset({
    "route_decision",
    "tool_arguments",
    "confirmation_text",
    "validation_passed",
    "validation_errors",
    "raw_tool_result",
    "execution_error",
    "audit_record",
    "response_text",
})
