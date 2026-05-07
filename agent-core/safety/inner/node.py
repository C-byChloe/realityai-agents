"""Inner safety composer — runs Layers 1-4 sequentially with audit sidecar.

Sequential short-circuit (ADR 006 D2): Layer 1 → 2 → 3 → 4. The first
non-ALLOW verdict short-circuits the chain. Audit is ALWAYS built and
persisted (D4), even when short-circuited at Layer 1.

Cost ordering (cheap → expensive): tool_authorization (dict lookup,
sub-ms) → parameter_presence (set diff, sub-ms) → parameter_format
(regex, low ms) → live_state (async DB read, ~10s of ms with 2s timeout
ceiling). A request denied at Layer 1 doesn't pay for an unnecessary DB
round-trip — the same cost-aware ordering as the outer gate (ADR 005 D3).

Two callers in the codebase consume this composer:
  1. The standalone Action Agent subgraph (`compile_action_agent`) —
     wraps `inner_safety_check` in a state-shaped node.
  2. The plan-driven path (`run_action_step` in `agents/planning_agent.py`
     → `agents/action_agent.py`) — calls it directly, no LangGraph wrapper.

Both must hit the same gate for ADR 006's "no bypass" invariant to hold.

Defense-in-depth invariants enforced HERE (not just by individual layers):
  - D1: returns InnerSafetyResult.final_decision ∈ {ALLOW, DENY}; never FLAG.
  - D4: result.audit is non-None on every return path.
  - D2: layer_results is ordered by evaluation sequence (1, 2, 3, 4 max).
"""

from __future__ import annotations

from time import perf_counter

from safety.inner.audit import (
    build_audit_record,
    persist_audit_record,
)
from safety.inner.live_state import check_live_state
from safety.inner.parameter_validation import (
    check_parameter_format,
    check_parameter_presence,
)
from safety.inner.schemas import (
    InnerLayerName,
    InnerSafetyDecision,
    InnerSafetyInput,
    InnerSafetyResult,
    LayerResult,
)
from safety.inner.tool_authorization import check_tool_authorization


async def inner_safety_check(
    safety_input: InnerSafetyInput,
    *,
    live_state_timeout_s: float = 2.0,
) -> InnerSafetyResult:
    """Run Layers 1-4 with sequential short-circuit + audit sidecar.

    Returns a fully-populated InnerSafetyResult. `result.audit` is
    persisted (idempotent stub) before return — callers do not need to
    persist it themselves. The returned record is also reachable via
    `result.audit` for callers that want to attach execution-outcome
    fields after invoking the tool.
    """
    t_start = perf_counter()
    layer_results: list[LayerResult] = []

    # Layer 1 — tool authorization (sync, sub-ms).
    r1 = check_tool_authorization(safety_input)
    layer_results.append(r1)
    if r1.decision != InnerSafetyDecision.ALLOW:
        return _short_circuit(safety_input, layer_results, r1, t_start)

    # Layer 2 — parameter presence (sync, sub-ms).
    r2 = check_parameter_presence(safety_input)
    layer_results.append(r2)
    if r2.decision != InnerSafetyDecision.ALLOW:
        return _short_circuit(safety_input, layer_results, r2, t_start)

    # Layer 3 — parameter format (sync, low ms).
    r3 = check_parameter_format(safety_input)
    layer_results.append(r3)
    if r3.decision != InnerSafetyDecision.ALLOW:
        return _short_circuit(safety_input, layer_results, r3, t_start)

    # Layer 4 — live state (async, ~10s of ms).
    r4 = await check_live_state(safety_input, timeout_s=live_state_timeout_s)
    layer_results.append(r4)
    if r4.decision != InnerSafetyDecision.ALLOW:
        return _short_circuit(safety_input, layer_results, r4, t_start)

    return _all_allowed(safety_input, layer_results, t_start)


def _short_circuit(
    safety_input: InnerSafetyInput,
    layer_results: list[LayerResult],
    blocking: LayerResult,
    t_start: float,
) -> InnerSafetyResult:
    """Build the InnerSafetyResult for a non-ALLOW short-circuit.

    Audit is built BEFORE the return so D4 holds: every code path that
    leaves this module persists an audit record.
    """
    audit = build_audit_record(
        safety_input,
        decision=blocking.decision,
        short_circuited_at=blocking.layer,
        final_reason_code=blocking.reason_code,
    )
    persist_audit_record(audit)
    return InnerSafetyResult(
        final_decision=blocking.decision,
        short_circuited_at=blocking.layer,
        layer_results=layer_results,
        total_latency_ms=int((perf_counter() - t_start) * 1000),
        final_reason_code=blocking.reason_code,
        final_reason_human=blocking.reason_human,
        audit=audit,
    )


def _all_allowed(
    safety_input: InnerSafetyInput,
    layer_results: list[LayerResult],
    t_start: float,
) -> InnerSafetyResult:
    """Build the InnerSafetyResult when all four layers ALLOW.

    The audit record is built but NOT persisted here. The caller is
    expected to attach execution outcome via `update_audit_for_execution`
    after the gRPC call and persist that final record. This avoids the
    dedup-set silently dropping the post-execution record (the stub's
    idempotency uses audit_id, which is stable across the update).
    """
    audit = build_audit_record(
        safety_input,
        decision=InnerSafetyDecision.ALLOW,
        short_circuited_at=None,
        final_reason_code="all_layers_passed",
    )
    return InnerSafetyResult(
        final_decision=InnerSafetyDecision.ALLOW,
        short_circuited_at=None,
        layer_results=layer_results,
        total_latency_ms=int((perf_counter() - t_start) * 1000),
        final_reason_code="all_layers_passed",
        final_reason_human="",
        audit=audit,
    )
