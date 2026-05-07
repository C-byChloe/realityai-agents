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
    """Working memory for the Query Agent subgraph. Internal-only.

    The standalone path uses the same `QuerySource` vocabulary as the
    plan-driven path — single source of truth for "what data layer is
    being read". The dispatch flow differs (single-shot LLM pick vs.
    plan-DAG executor), but both call into `_SOURCE_HANDLERS` underneath.
    """

    user_message: str

    # Routing — LLM picks a typed source + params (NOT a tool name)
    route_decision: dict           # parsed LLM JSON: {"source", "params", "query_type"}
    selected_source: str            # one of QuerySource enum values
    source_params: dict
    query_type: str                 # "deterministic" | "tutoring"

    # Execution — handler returns typed Pydantic, NOT a dict
    raw_typed_result: object        # StudentTranscript / DegreeProgram / list[CourseSection] / list[SyllabusChunk]
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
    selected_source: str = ""       # one of QuerySource enum values, or "" if LLM short-circuited
    query_type: str = ""            # "deterministic" | "tutoring"


QUERY_INTERNAL_ONLY_KEYS: frozenset[str] = frozenset({
    "route_decision",
    "source_params",
    "raw_typed_result",
    "execution_error",
    "response_text",
})


# ---------------------------------------------------------------------------
# Action Agent — internal state and I/O models
# ---------------------------------------------------------------------------


class ActionAgentInternalState(TypedDict, total=False):
    """Working memory for the Action Agent subgraph. Internal-only.

    The 4-gate flow (route → inner_safety → execute → finalize_audit)
    leaves typed traces on this state so each gate can be inspected
    during debug. None of these fields appear in `ActionAgentOutput`.
    """

    user_message: str
    user_id: str
    session_id: str
    user_role: str

    # Routing
    route_decision: dict
    selected_tool: str
    tool_arguments: dict
    confirmation_text: str

    # Inner safety gate (ADR 006) — replaces legacy validation_passed/errors.
    # Carries layer_results + audit; final_decision is the gate outcome.
    inner_safety_result: object  # InnerSafetyResult; typed via runtime check

    # Execute gate
    raw_tool_result: dict
    execution_error: str | None

    # Output
    response_text: str
    success: bool


class ActionAgentInput(BaseModel):
    user_message: str
    user_id: str
    session_id: str
    # JWT-derived role (D5) — load-bearing for inner Layer 1's tool
    # authorization re-check. The default is the most-restricted role
    # so a missing wiring fails closed: forgetting to populate this
    # makes Layer 1 deny write tools rather than silently grant them.
    user_role: str = "student"


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
    "inner_safety_result",
    "raw_tool_result",
    "execution_error",
    "response_text",
})
