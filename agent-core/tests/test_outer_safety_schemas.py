"""Phase 0 sanity tests for outer safety schemas.

These tests verify the typed contracts are correctly defined and
round-trip cleanly. They do NOT verify safety logic — implementation
tests land alongside Tier 1/2/3 in subsequent phases.

The most load-bearing test here is
`test_input_does_not_carry_normalized_query`, which encodes ADR 005 D7
(outer safety reads raw query + raw history, never coref-rewritten
content) as a Pydantic-shape invariant. If a future revision adds a
`user_query_normalized` field to `OuterSafetyInput`, this test fails
loudly.
"""

import pytest
from pydantic import ValidationError

from safety.outer.schemas import (
    OuterSafetyInput,
    OuterSafetyResult,
    SafetyDecision,
    SessionContext,
    TierName,
    TierResult,
)


def test_safety_decision_is_tri_state():
    """ALLOW / DENY / FLAG_FOR_REVIEW are the only legal values."""
    assert {d.value for d in SafetyDecision} == {"allow", "deny", "flag_for_review"}


def test_tier_result_round_trips_through_json():
    r = TierResult(
        tier=TierName.RBAC,
        decision=SafetyDecision.DENY,
        reason_code="role_lacks_action_grant",
        reason_human="Students cannot update grades.",
        latency_ms=1,
    )
    parsed = TierResult.model_validate(r.model_dump())
    assert parsed == r


def test_outer_safety_result_default_short_circuited_at_is_none():
    """All-tiers-ran is encoded as `None`, not a sentinel."""
    out = OuterSafetyResult(
        final_decision=SafetyDecision.ALLOW,
        tier_results=[],
        total_latency_ms=42,
        final_reason_code="all_tiers_passed",
        final_reason_human="",
    )
    assert out.short_circuited_at is None


def test_session_context_requires_user_role():
    """`user_role` is the anti-prompt-injection invariant — never optional.
    See ADR 005 D5.
    """
    with pytest.raises(ValidationError):
        SessionContext(user_id="u1", session_id="s1")  # type: ignore[call-arg]


def test_input_does_not_carry_normalized_query():
    """Encodes ADR 005 D7: outer safety reads raw query and raw conversation
    history, NEVER coref-rewritten content. If this test starts failing
    because someone added a `user_query_normalized` or `rewritten_query`
    field, the safety/coref ordering invariant is being violated.
    """
    fields = set(OuterSafetyInput.model_fields.keys())
    assert "user_query_normalized" not in fields, (
        "ADR 005 D7 violated: safety must not read coref-rewritten content"
    )
    assert "rewritten_query" not in fields, (
        "ADR 005 D7 violated: safety must not read coref-rewritten content"
    )
    assert "user_query_raw" in fields
    assert "conversation_history" in fields
