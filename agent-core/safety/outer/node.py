"""LangGraph node entry point for the outer safety gate.

Sequentially runs Tier 1 (RBAC) → Tier 2 (static rules) → Tier 3 (LLM
intent analyzer), short-circuiting on the first non-ALLOW decision.

A normal request walks all three tiers (small ms). A request blocked by
RBAC pays Tier 1 only and never burns Tier 3 token cost — that's the
point of cost-aware sequential ordering (ADR 005 D3).

Reads from state:
  - messages: list[BaseMessage]   — last entry is current turn
  - intent: str                   — already classified upstream
  - user_role: str                — JWT-derived (defaults to "student"
                                    until Phase -1 wires the gateway)

Writes to state:
  - outer_safety_result: OuterSafetyResult       (canonical)
  - requires_approval: bool                       (legacy alias, True iff FLAG)

The conditional edge `_route_after_outer_safety` reads
`outer_safety_result.final_decision` to dispatch to coref_resolver,
reject_node, or hitl_approval.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from safety.outer.intent_analyzer import analyze_intent
from safety.outer.rbac import check_rbac
from safety.outer.schemas import (
    OuterSafetyInput,
    OuterSafetyResult,
    SafetyDecision,
    SessionContext,
    TierName,
    TierResult,
)
from safety.outer.static_rules import check_static_rules


def _build_input(state: dict[str, Any]) -> OuterSafetyInput:
    """Project the LangGraph state into an OuterSafetyInput.

    `user_role` defaults to "student" when missing — this is the documented
    fallback until the API gateway emits a JWT role claim. ADR 005 D5
    requires that role NEVER come from messages; this function reads only
    from `state["user_role"]`.
    """
    messages = state.get("messages", []) or []
    last = messages[-1] if messages else None
    user_query_raw = (
        last.content if last is not None and hasattr(last, "content")
        else (str(last) if last is not None else "")
    )
    history = list(messages[:-1])

    return OuterSafetyInput(
        session=SessionContext(
            user_id=state.get("user_id", ""),
            session_id=state.get("session_id", ""),
            user_role=state.get("user_role", "student"),
        ),
        intent=state.get("intent", ""),
        user_query_raw=user_query_raw,
        conversation_history=history,
    )


def _short_circuit(
    tier_results: list[TierResult],
    blocking: TierResult,
    t_start: float,
) -> dict[str, Any]:
    """Build the state update for a non-ALLOW short-circuit."""
    final_result = OuterSafetyResult(
        final_decision=blocking.decision,
        short_circuited_at=blocking.tier,
        tier_results=tier_results,
        total_latency_ms=int((perf_counter() - t_start) * 1000),
        final_reason_code=blocking.reason_code,
        final_reason_human=blocking.reason_human,
    )
    return {
        "outer_safety_result": final_result,
        # Legacy alias for code that still reads `requires_approval` directly.
        "requires_approval": blocking.decision == SafetyDecision.FLAG_FOR_REVIEW,
    }


def _all_allowed(
    tier_results: list[TierResult],
    t_start: float,
) -> dict[str, Any]:
    final_result = OuterSafetyResult(
        final_decision=SafetyDecision.ALLOW,
        short_circuited_at=None,
        tier_results=tier_results,
        total_latency_ms=int((perf_counter() - t_start) * 1000),
        final_reason_code="all_tiers_passed",
        final_reason_human="",
    )
    return {
        "outer_safety_result": final_result,
        "requires_approval": False,
    }


async def outer_safety_check(state: dict[str, Any], llm) -> dict[str, Any]:
    """Run RBAC → static rules → LLM intent analyzer sequentially.

    The LLM is injected so callers (orchestrator at runtime, tests with
    mocks) can supply their own client. Tier 1+2 don't need it; only
    Tier 3 calls it.
    """
    safety_input = _build_input(state)
    tier_results: list[TierResult] = []
    t_start = perf_counter()

    # Tier 1 — RBAC (sub-ms, sync).
    r1 = check_rbac(safety_input)
    tier_results.append(r1)
    if r1.decision != SafetyDecision.ALLOW:
        return _short_circuit(tier_results, r1, t_start)

    # Tier 2 — static rules (low ms, sync).
    r2 = check_static_rules(safety_input)
    tier_results.append(r2)
    if r2.decision != SafetyDecision.ALLOW:
        return _short_circuit(tier_results, r2, t_start)

    # Tier 3 — LLM intent analyzer (~800ms cold, async).
    r3 = await analyze_intent(safety_input, llm)
    tier_results.append(r3)
    if r3.decision != SafetyDecision.ALLOW:
        return _short_circuit(tier_results, r3, t_start)

    return _all_allowed(tier_results, t_start)
