"""Typed contracts for the outer safety layer.

Phase 0 deliverable. The tier implementations
(`rbac.py` / `static_rules.py` / `intent_analyzer.py`) land in subsequent
phases — this module locks the input/output shapes so downstream code
can integrate against stable contracts.

See `docs/adr/005-outer-safety-layer.md` for the design rationale,
including:
  - D3: three tiers, sequential short-circuit (not parallel OR-merge)
  - D4: tri-state ALLOW / DENY / FLAG_FOR_REVIEW (FLAG → HiTL, not direct reject)
  - D5: `user_role` from SessionContext, never from messages
  - D7: outer safety reads RAW query + RAW history, never coref-rewritten content
  - D8: layer-3 LLM fallback to FLAG_FOR_REVIEW, not DENY
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SafetyDecision(str, Enum):
    """Tri-state output of every outer-safety tier.

    `FLAG_FOR_REVIEW` is the deliberate fallback for ambiguous cases and
    for tier failures (LLM timeout, parse error, low confidence). The
    request goes to HiTL approval instead of being rejected outright —
    this trades throughput for accuracy on ambiguous traffic. See ADR 005 D4.
    """

    ALLOW = "allow"
    DENY = "deny"
    FLAG_FOR_REVIEW = "flag_for_review"


class TierName(str, Enum):
    """The three tiers of the outer safety node, in evaluation order.

    Note on terminology: "tier" is intra-node language; "layer" is
    stack-wide (gateway / app / vendor backend). ADR 005 uses "tier"
    exclusively for the intra-node concept to avoid confusion with the
    three-stack-layer auth model.

    Tier 3 was renamed from `intent_analyzer` (Claude Sonnet, generic
    safety reasoning) to `injection_guard` (Meta Prompt Guard 2 86M, a
    specialized injection classifier) — see Phase 7 in the changelog.
    The stable string value is `injection_guard`; older traces / audit
    log entries with the legacy `intent_analyzer` value are accepted by
    Pydantic via the migration alias below if needed.
    """

    RBAC = "rbac"
    STATIC_RULES = "static_rules"
    INJECTION_GUARD = "injection_guard"


class TierResult(BaseModel):
    """One tier's verdict on a single request."""

    tier: TierName
    decision: SafetyDecision
    reason_code: str = Field(
        description="Machine-readable identifier (e.g. 'role_lacks_action_grant')."
    )
    reason_human: str = Field(
        description="User-facing explanation; surfaced in trace and reject_node response."
    )
    latency_ms: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionContext(BaseModel):
    """Subset of session attributes that outer safety reads.

    `user_role` lives here and is JWT-derived at the API gateway.
    Outer safety NEVER reads user identity from messages — see ADR 005 D5
    (anti-prompt-injection invariant).

    Production gap: the API gateway does not currently emit a `role` JWT
    claim. Phase 0 unblocks development with a default of "student" at
    consumer call sites; full gateway wiring is tracked as Phase -1
    outside this layer's scope.
    """

    user_id: str
    session_id: str
    user_role: str  # "student" | "instructor" | "registrar" | ...
    tenant_id: str | None = None


class OuterSafetyInput(BaseModel):
    """Input carried into outer_safety_check.

    Schema deliberately omits `user_query_normalized` / `rewritten_query`
    or anything that could be confused with coref-rewritten content.
    Outer safety operates on RAW user input plus RAW conversation history
    — see ADR 005 D7. Tier-3 LLM analyzer reads history to detect
    multi-turn injection patterns directly, without trusting the coref
    resolver's rewrite.
    """

    session: SessionContext
    intent: str  # already classified by upstream intent_classification
    user_query_raw: str  # latest user message, verbatim
    conversation_history: list[Any] = Field(
        default_factory=list,
        description=(
            "Prior messages in the thread, raw. Used by Tier 3 LLM "
            "analyzer to detect multi-turn injection patterns. Coref-"
            "rewritten content NEVER appears here."
        ),
    )


class OuterSafetyResult(BaseModel):
    """Aggregate verdict written to `AgentState.outer_safety_result`.

    `tier_results` is always populated even when short-circuited — this
    preserves trace fidelity so per-tier confusion matrices can be
    computed from logs.
    """

    final_decision: SafetyDecision
    short_circuited_at: TierName | None = Field(
        default=None,
        description="None means all 3 tiers ran and all returned ALLOW.",
    )
    tier_results: list[TierResult] = Field(
        default_factory=list,
        description="Always populated; ordered by evaluation sequence.",
    )
    total_latency_ms: int
    final_reason_code: str
    final_reason_human: str
