"""Phase 0 sanity tests for inner safety schemas.

These tests verify the typed contracts. They do NOT verify safety
logic — implementation tests land alongside Layer 1/2/3/4 + audit in
subsequent phases.

Two load-bearing invariants are encoded as schema-shape assertions:

  - `test_decision_is_binary` — encodes ADR 006 D1 (no FLAG_FOR_REVIEW
    inside inner; if a future revision adds it, the test fails loudly
    so the design conversation re-opens).
  - `test_input_does_not_carry_messages` — encodes ADR 006 D5 (inner
    reads tool + args + session; never raw user messages).
"""

import pytest
from pydantic import ValidationError

from safety.inner.schemas import (
    AuditRecord,
    InnerLayerName,
    InnerSafetyDecision,
    InnerSafetyInput,
    InnerSafetyResult,
    LayerResult,
)
from safety.outer.schemas import SessionContext


def test_decision_is_binary():
    """ADR 006 D1: ALLOW / DENY are the only legal values.

    No FLAG_FOR_REVIEW. If a future revision adds it, this test will fail
    and force the design conversation back open: ambiguity should be
    handled by outer (which has FLAG/HiTL), not by inner (deterministic
    checks on concrete tool calls).
    """
    assert {d.value for d in InnerSafetyDecision} == {"allow", "deny"}


def test_layer_result_round_trips_through_json():
    r = LayerResult(
        layer=InnerLayerName.TOOL_AUTHORIZATION,
        decision=InnerSafetyDecision.DENY,
        reason_code="role_lacks_tool_grant",
        reason_human="Student is not authorized to invoke grade_update.",
        latency_ms=1,
    )
    parsed = LayerResult.model_validate(r.model_dump())
    assert parsed == r


def test_input_does_not_carry_messages():
    """ADR 006 D5: inner reads (tool, args, session). NEVER messages.

    If this test starts failing because someone added a `messages` or
    `user_query` field, the anti-prompt-injection invariant is being
    violated — inner safety must never inspect user-controlled content
    to make decisions about an already-emitted tool call.
    """
    fields = set(InnerSafetyInput.model_fields.keys())
    assert "messages" not in fields, (
        "ADR 006 D5 violated: inner safety must read tool+args, never raw messages"
    )
    assert "user_query" not in fields and "user_query_raw" not in fields
    assert {"session", "tool_name", "tool_args"}.issubset(fields)


def test_audit_record_args_keys_field_not_args_values():
    """ADR 006 D4: audit stores args KEYS, not VALUES (PII safety).

    The schema enforces this — there is no `args` or `args_values`
    field. If a future change adds one, this test forces a security
    review.
    """
    fields = set(AuditRecord.model_fields.keys())
    assert "args_keys" in fields
    assert "args" not in fields
    assert "args_values" not in fields
    assert "tool_args" not in fields


def test_inner_safety_result_requires_audit():
    """ADR 006 D4: audit is ALWAYS populated, even on DENY.

    `InnerSafetyResult.audit` has no default — instantiating without
    one raises ValidationError, ensuring code paths cannot accidentally
    produce a result without an audit trail.
    """
    sess = SessionContext(user_id="u1", session_id="s1", user_role="instructor")
    audit = AuditRecord(
        audit_id="abc123def456",
        tool_name="grade_update",
        args_keys=["student_id", "course_id", "assignment_id", "grade"],
        user_id=sess.user_id,
        session_id=sess.session_id,
        user_role=sess.user_role,
        decision=InnerSafetyDecision.DENY,  # audit even on DENY
    )
    # Valid: audit provided
    InnerSafetyResult(
        final_decision=InnerSafetyDecision.DENY,
        layer_results=[],
        total_latency_ms=1,
        final_reason_code="role_lacks_tool_grant",
        final_reason_human="Student cannot grade_update.",
        audit=audit,
    )
    # Invalid: audit omitted
    with pytest.raises(ValidationError):
        InnerSafetyResult(  # type: ignore[call-arg]
            final_decision=InnerSafetyDecision.DENY,
            layer_results=[],
            total_latency_ms=1,
            final_reason_code="x",
            final_reason_human="x",
        )
