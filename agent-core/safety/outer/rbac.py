"""Tier 1 of the outer safety gate — RBAC.

Pure dict lookup of `(user_role, intent)` against the matrix in
`config/rbac_matrix.yaml`. Sub-millisecond hot path; no LLM, no I/O at
runtime (the YAML is parsed once at module import).

Verdicts:
  - intent in role's `allowed`        → ALLOW
  - intent in role's `denied`         → DENY ("role_lacks_action_grant")
  - intent unknown to a known role    → FLAG_FOR_REVIEW (defer)
  - role unknown                      → DENY ("unknown_role", fail-closed)

See docs/adr/005-outer-safety-layer.md (D5: user_role from SessionContext)
and the locked behavior table in test_outer_safety_rbac.py.
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TypedDict

import yaml

from safety.outer.schemas import (
    OuterSafetyInput,
    SafetyDecision,
    TierName,
    TierResult,
)

_MATRIX_PATH = Path(__file__).parent / "config" / "rbac_matrix.yaml"


class _RoleConfig(TypedDict):
    allowed: frozenset[str]
    denied: frozenset[str]


def _load_matrix() -> dict[str, _RoleConfig]:
    """Parse `rbac_matrix.yaml` into precomputed role → (allowed, denied) sets.

    Frozenset for hashability and to make accidental mutation impossible
    once the matrix is loaded.
    """
    with open(_MATRIX_PATH) as f:
        data = yaml.safe_load(f) or {}
    out: dict[str, _RoleConfig] = {}
    for role, cfg in (data.get("roles") or {}).items():
        out[role] = {
            "allowed": frozenset(cfg.get("allowed_action_categories", []) or []),
            "denied": frozenset(cfg.get("denied_action_categories", []) or []),
        }
    return out


# Module-load precompute. Reload not supported at runtime; redeploy the
# process to pick up matrix changes. (Hot reload is a Phase 6 concern.)
_ROLES: dict[str, _RoleConfig] = _load_matrix()


def check_rbac(safety_input: OuterSafetyInput) -> TierResult:
    """Tier 1 entry point.

    Pure function. No state, no I/O, no async — designed to be the
    cheapest possible gate before any expensive check runs.
    """
    t0 = perf_counter()
    role = safety_input.session.user_role
    intent = safety_input.intent

    config = _ROLES.get(role)

    if config is None:
        # Unknown role — fail-closed. ADR 005 D5: never trust unconfigured
        # principals just because the matrix forgot them.
        return _result(
            decision=SafetyDecision.DENY,
            reason_code="unknown_role",
            reason_human=f"Role '{role}' has no policy configured.",
            metadata={"role": role, "intent": intent},
            t0=t0,
        )

    if intent in config["allowed"]:
        return _result(
            decision=SafetyDecision.ALLOW,
            reason_code="role_grants_action",
            reason_human="",
            metadata={"role": role, "intent": intent},
            t0=t0,
        )

    if intent in config["denied"]:
        return _result(
            decision=SafetyDecision.DENY,
            reason_code="role_lacks_action_grant",
            reason_human=(
                f"Role '{role}' is not authorized for action category '{intent}'."
            ),
            metadata={"role": role, "intent": intent},
            t0=t0,
        )

    # Intent not in either list — known role, but unknown intent for it.
    # Defer to later tiers / HiTL rather than auto-deny: a missing matrix
    # entry should not block a request the static rules might still
    # legitimately ALLOW or FLAG.
    return _result(
        decision=SafetyDecision.FLAG_FOR_REVIEW,
        reason_code="unknown_intent_for_role",
        reason_human=(
            f"Role '{role}' has no policy for intent '{intent}'; routing to review."
        ),
        metadata={"role": role, "intent": intent},
        t0=t0,
    )


def _result(
    *,
    decision: SafetyDecision,
    reason_code: str,
    reason_human: str,
    metadata: dict,
    t0: float,
) -> TierResult:
    return TierResult(
        tier=TierName.RBAC,
        decision=decision,
        reason_code=reason_code,
        reason_human=reason_human,
        latency_ms=int((perf_counter() - t0) * 1000),
        metadata=metadata,
    )
