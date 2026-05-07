"""Typed contracts for the inner safety layer.

Phase 0 deliverable. The layer implementations
(`tool_authorization.py` / `parameter_validation.py` / `live_state.py` /
`audit.py` / `node.py`) land in subsequent phases — this module locks
the I/O shapes so downstream code can integrate against stable contracts.

See `docs/adr/006-inner-safety-layer.md` for the design rationale,
including:
  - D1: binary `ALLOW / DENY` (no FLAG_FOR_REVIEW — outer handles ambiguity)
  - D2: sequential short-circuit, cheap → expensive
  - D3: Layer 4 unavailable → fail closed (DENY), never silently skipped
  - D4: audit is a sidecar, ALWAYS populated, even on DENY
  - D5: `user_role` from `SessionContext`, never from messages
        (invariant inherited from outer safety ADR 005 D5)
  - D6: action path only in v1 (query path is Phase 7 deferred)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from safety.outer.schemas import SessionContext  # D5 — same JWT-derived role channel


class InnerSafetyDecision(str, Enum):
    """Binary verdict — see ADR 006 D1.

    No FLAG_FOR_REVIEW: inner safety runs on concrete (tool, args) tuples
    that are already past outer's ambiguity gate. Layers are deterministic
    checks (dict lookup, regex, DB read); their output is black/white.
    """

    ALLOW = "allow"
    DENY = "deny"


class InnerLayerName(str, Enum):
    """The four layers of the inner safety node, in evaluation order.

    Same terminology convention as outer (ADR 005): "layer" here is
    intra-node; the stack-wide "layer" for outer/inner gate distinction
    is reserved for that broader picture.
    """

    TOOL_AUTHORIZATION = "tool_authorization"
    PARAMETER_PRESENCE = "parameter_presence"
    PARAMETER_FORMAT = "parameter_format"
    LIVE_STATE = "live_state"


class LayerResult(BaseModel):
    """One layer's verdict on a single tool invocation."""

    layer: InnerLayerName
    decision: InnerSafetyDecision
    reason_code: str = Field(
        description="Machine-readable identifier (e.g. 'role_lacks_tool_grant')."
    )
    reason_human: str = Field(
        description="Operator-facing explanation; surfaced in trace and audit_record."
    )
    latency_ms: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class InnerSafetyInput(BaseModel):
    """Input carried into `inner_safety_check`.

    Schema deliberately omits `messages` and any free-form user text —
    inner safety operates ONLY on the typed (tool, args, session) tuple
    that the LLM produced. This is the D5 invariant: identity comes from
    `SessionContext.user_role` (JWT-derived), tool decision is already
    made, args are concrete. Anything user-controlled (messages, raw
    query) has been processed upstream.
    """

    session: SessionContext
    tool_name: str  # the tool the LLM picked (e.g., "grade_update")
    tool_args: dict[str, Any]  # the args the LLM emitted


class AuditRecord(BaseModel):
    """Sidecar audit emitted for every attempted action — D4.

    Two-phase population:
      1. After inner gates run: decision + layer attribution + execution_attempted
      2. After tool execution (if attempted): execution_succeeded + result_summary

    Args VALUES are NOT stored — only `args_keys` — because args may
    contain PII (student_id, grade values). For compliance forensics, the
    audit_id correlates back to the LangSmith trace which has full args.
    """

    audit_id: str = Field(description="Opaque correlation ID; 12-char uuid hex slice.")
    timestamp_iso: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    tool_name: str
    args_keys: list[str] = Field(
        description="Names of args passed; values omitted for PII safety."
    )
    user_id: str
    session_id: str
    user_role: str
    tenant_id: str | None = None

    # Inner safety verdict
    decision: InnerSafetyDecision
    short_circuited_at: InnerLayerName | None = None
    final_reason_code: str = ""

    # Execution attempt (set after gRPC call if all layers ALLOW)
    execution_attempted: bool = False
    execution_succeeded: bool | None = None
    execution_error: str | None = None


class InnerSafetyResult(BaseModel):
    """Aggregate verdict written to `AgentState.inner_safety_result`.

    `audit` is ALWAYS populated, regardless of `final_decision` — see D4.
    """

    final_decision: InnerSafetyDecision
    short_circuited_at: InnerLayerName | None = Field(
        default=None,
        description="None means all 4 layers ran and all returned ALLOW.",
    )
    layer_results: list[LayerResult] = Field(
        default_factory=list,
        description="Always populated; ordered by evaluation sequence.",
    )
    total_latency_ms: int
    final_reason_code: str
    final_reason_human: str

    # Sidecar audit record — populated even when short-circuited (D4).
    audit: AuditRecord
