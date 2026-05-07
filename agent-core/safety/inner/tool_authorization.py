"""Layer 1 of the inner safety gate — tool authorization re-check.

Pure dict lookup of `(user_role, tool_name)` against the matrix in
`config/tool_auth_matrix.yaml`. Sub-millisecond hot path; no I/O at
runtime (the YAML is parsed once at module import).

Why this layer exists (the prompt-injection-defense argument):
  - Outer RBAC checks `(role, action_category)` at request entry.
  - Tool selection happens AFTER outer (the LLM picks the actual tool).
  - Between outer and execution, prompt injection could flip the tool
    selection. Inner Layer 1 is the only defense against that flip.

Verdicts:
  - tool in role's `granted_tools`  → ALLOW
  - tool in role's `denied_tools`   → DENY ("role_lacks_tool_grant")
  - tool unknown to a known role    → DENY ("unknown_tool_for_role")
  - role unknown                    → DENY ("unknown_role", fail-closed)

The verdict shape is binary (ADR 006 D1) — no FLAG_FOR_REVIEW.

See `docs/adr/006-inner-safety-layer.md` (D1 binary, D5 user_role
from SessionContext) and the locked behavior table in
`tests/test_inner_safety_tool_authorization.py`.
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TypedDict

import yaml

from safety.inner.schemas import (
    InnerLayerName,
    InnerSafetyDecision,
    InnerSafetyInput,
    LayerResult,
)

_MATRIX_PATH = Path(__file__).parent / "config" / "tool_auth_matrix.yaml"


class _RoleConfig(TypedDict):
    granted: frozenset[str]
    denied: frozenset[str]


def _load_matrix() -> dict[str, _RoleConfig]:
    """Parse `tool_auth_matrix.yaml` into precomputed role → (granted, denied) sets.

    Frozenset for hashability and to make accidental mutation impossible
    once the matrix is loaded.
    """
    with open(_MATRIX_PATH) as f:
        data = yaml.safe_load(f) or {}
    out: dict[str, _RoleConfig] = {}
    for role, cfg in (data.get("roles") or {}).items():
        out[role] = {
            "granted": frozenset(cfg.get("granted_tools", []) or []),
            "denied": frozenset(cfg.get("denied_tools", []) or []),
        }
    return out


# Module-load precompute. Reload not supported at runtime; redeploy the
# process to pick up matrix changes.
_ROLES: dict[str, _RoleConfig] = _load_matrix()


def check_tool_authorization(safety_input: InnerSafetyInput) -> LayerResult:
    """Layer 1 entry point.

    Pure function. No state, no I/O, no async — designed to be the
    cheapest possible gate before any expensive check runs (Layer 4
    live-state in particular).
    """
    t0 = perf_counter()
    role = safety_input.session.user_role
    tool_name = safety_input.tool_name

    config = _ROLES.get(role)

    if config is None:
        # Unknown role — fail-closed. ADR 006 D5: never trust unconfigured
        # principals just because the matrix forgot them.
        return _result(
            decision=InnerSafetyDecision.DENY,
            reason_code="unknown_role",
            reason_human=f"Role '{role}' has no tool-authorization policy configured.",
            metadata={"role": role, "tool": tool_name},
            t0=t0,
        )

    if tool_name in config["granted"]:
        return _result(
            decision=InnerSafetyDecision.ALLOW,
            reason_code="role_grants_tool",
            reason_human="",
            metadata={"role": role, "tool": tool_name},
            t0=t0,
        )

    if tool_name in config["denied"]:
        return _result(
            decision=InnerSafetyDecision.DENY,
            reason_code="role_lacks_tool_grant",
            reason_human=(
                f"Role '{role}' is not authorized to invoke tool '{tool_name}'."
            ),
            metadata={"role": role, "tool": tool_name},
            t0=t0,
        )

    # Tool is in neither granted nor denied list for this known role.
    # Unlike outer RBAC (which FLAGs unknown intent because intent enum
    # is open-ended), inner's tool name is bounded by the action tool
    # registry. An unconfigured tool for a known role is a config gap
    # OR an LLM hallucinating a tool name — either way, fail-closed.
    return _result(
        decision=InnerSafetyDecision.DENY,
        reason_code="unknown_tool_for_role",
        reason_human=(
            f"Role '{role}' has no policy for tool '{tool_name}'; denying."
        ),
        metadata={"role": role, "tool": tool_name},
        t0=t0,
    )


def _result(
    *,
    decision: InnerSafetyDecision,
    reason_code: str,
    reason_human: str,
    metadata: dict,
    t0: float,
) -> LayerResult:
    return LayerResult(
        layer=InnerLayerName.TOOL_AUTHORIZATION,
        decision=decision,
        reason_code=reason_code,
        reason_human=reason_human,
        latency_ms=int((perf_counter() - t0) * 1000),
        metadata=metadata,
    )
