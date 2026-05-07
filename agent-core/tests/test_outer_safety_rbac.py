"""Phase 1 unit tests for outer safety Tier 1 — RBAC.

Locks the verdict cases that ADR 005 D5 + the rbac matrix YAML together
prescribe. Note that `(student, action)` ALLOWs at outer — outer is
intent-category granularity and cannot distinguish self-enroll
(legitimate) from grade_update (forbidden). Tool-level RBAC for student
writes lives at inner Layer 1; caller-identity (a student modifying
another student's record) lives at inner Layer 4.

  | role       | intent   | verdict            | reason_code                  |
  |------------|----------|--------------------|------------------------------|
  | student    | query    | ALLOW              | role_grants_action           |
  | student    | action   | ALLOW (defer)      | role_grants_action           |
  | instructor | action   | ALLOW              | role_grants_action           |
  | admin      | query    | DENY (fail-closed) | unknown_role                 |
  | student    | chitchat | FLAG_FOR_REVIEW    | unknown_intent_for_role      |
"""

from safety.outer.rbac import check_rbac
from safety.outer.schemas import (
    OuterSafetyInput,
    SafetyDecision,
    SessionContext,
    TierName,
)


def _inp(role: str, intent: str) -> OuterSafetyInput:
    return OuterSafetyInput(
        session=SessionContext(user_id="u1", session_id="s1", user_role=role),
        intent=intent,
        user_query_raw="anything",
    )


# ---------------------------------------------------------------------------
# Tier name + latency are always populated
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_result_carries_tier_name(self):
        out = check_rbac(_inp("student", "query"))
        assert out.tier == TierName.RBAC

    def test_result_records_latency(self):
        out = check_rbac(_inp("student", "query"))
        assert out.latency_ms >= 0  # non-negative; sub-ms is fine


# ---------------------------------------------------------------------------
# The four verdict cases
# ---------------------------------------------------------------------------


class TestVerdicts:
    def test_known_role_allowed_intent_allows(self):
        out = check_rbac(_inp("student", "query"))
        assert out.decision == SafetyDecision.ALLOW
        assert out.reason_code == "role_grants_action"

    def test_student_action_intent_allowed_defers_to_inner(self):
        """Outer is intent-category-level; cannot distinguish self-enroll
        from grade_update. Outer ALLOWs `(student, action)` and defers
        tool-level RBAC to inner Layer 1 + caller-identity to inner
        Layer 4. Pins the matrix decision against accidental regression
        back to the over-restrictive "student denied all action" rule
        that blocked legitimate self-enrollment.
        """
        out = check_rbac(_inp("student", "action"))
        assert out.decision == SafetyDecision.ALLOW
        assert out.reason_code == "role_grants_action"

    def test_instructor_can_perform_action(self):
        out = check_rbac(_inp("instructor", "action"))
        assert out.decision == SafetyDecision.ALLOW
        assert out.reason_code == "role_grants_action"

    def test_unknown_role_is_denied_fail_closed(self):
        """Fail-closed: any role not in the matrix gets DENY, not ALLOW.
        ADR 005 D5 — unconfigured principals are not implicitly trusted.
        """
        out = check_rbac(_inp("admin", "query"))
        assert out.decision == SafetyDecision.DENY
        assert out.reason_code == "unknown_role"

    def test_unknown_intent_for_known_role_flags_for_review(self):
        """Intent not in either allowed or denied → FLAG_FOR_REVIEW.
        Don't auto-deny a missing matrix entry; defer to later tiers / HiTL.
        """
        out = check_rbac(_inp("student", "chitchat"))
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "unknown_intent_for_role"


# ---------------------------------------------------------------------------
# Metadata is preserved for trace fidelity
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_metadata_records_role_and_intent(self):
        out = check_rbac(_inp("student", "action"))
        assert out.metadata["role"] == "student"
        assert out.metadata["intent"] == "action"
