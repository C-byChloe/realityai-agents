"""Phase 1 unit tests for inner safety Layer 1 — tool authorization re-check.

Locks the four verdict cases that ADR 006 D1 + D5 + the tool_auth_matrix
YAML together prescribe:

  | role        | tool             | verdict | reason_code             |
  |-------------|------------------|---------|-------------------------|
  | instructor  | grade_update     | ALLOW   | role_grants_tool        |
  | student     | grade_update     | DENY    | role_lacks_tool_grant   |
  | admin       | grade_update     | DENY    | unknown_role            |
  | instructor  | fake_tool        | DENY    | unknown_tool_for_role   |
  | registrar   | assignment_create| ALLOW   | role_grants_tool        |

Note D1: this layer is BINARY. Unknown-tool-for-known-role is DENY
(not FLAG), unlike outer RBAC's unknown-intent-for-known-role which
was FLAG_FOR_REVIEW. The reason: inner's tool name is bounded by the
action tool registry, so an unknown tool is either config drift or
LLM hallucination — neither of which should silently pass through.
"""

from safety.inner.schemas import (
    InnerLayerName,
    InnerSafetyDecision,
    InnerSafetyInput,
)
from safety.inner.tool_authorization import check_tool_authorization
from safety.outer.schemas import SessionContext


def _inp(role: str, tool: str) -> InnerSafetyInput:
    return InnerSafetyInput(
        session=SessionContext(user_id="u1", session_id="s1", user_role=role),
        tool_name=tool,
        tool_args={},
    )


# ---------------------------------------------------------------------------
# Tier name + latency are always populated
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_result_carries_layer_name(self):
        out = check_tool_authorization(_inp("instructor", "grade_update"))
        assert out.layer == InnerLayerName.TOOL_AUTHORIZATION

    def test_result_records_latency(self):
        out = check_tool_authorization(_inp("instructor", "grade_update"))
        assert out.latency_ms >= 0


# ---------------------------------------------------------------------------
# The four verdict cases
# ---------------------------------------------------------------------------


class TestVerdicts:
    def test_known_role_with_granted_tool_allows(self):
        out = check_tool_authorization(_inp("instructor", "grade_update"))
        assert out.decision == InnerSafetyDecision.ALLOW
        assert out.reason_code == "role_grants_tool"

    def test_known_role_with_denied_tool_denies(self):
        """Student's denied_tools list contains all action tools — the
        post-LLM re-check that matters even if outer RBAC didn't fire
        (e.g., outer's intent classifier returned 'query' but the LLM
        emitted an action tool anyway, possibly due to prompt injection).
        """
        out = check_tool_authorization(_inp("student", "grade_update"))
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "role_lacks_tool_grant"
        assert "student" in out.reason_human
        assert "grade_update" in out.reason_human

    def test_registrar_can_create_assignment(self):
        """Registrar has all three action tools granted."""
        out = check_tool_authorization(_inp("registrar", "assignment_create"))
        assert out.decision == InnerSafetyDecision.ALLOW
        assert out.reason_code == "role_grants_tool"

    def test_unknown_role_is_denied_fail_closed(self):
        """Fail-closed: any role not in the matrix gets DENY, not ALLOW.
        ADR 006 D5 — same invariant as outer RBAC (ADR 005 D5).
        """
        out = check_tool_authorization(_inp("admin", "grade_update"))
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "unknown_role"

    def test_unknown_tool_for_known_role_denies(self):
        """Tool not in either granted or denied → DENY (NOT FLAG, unlike
        outer RBAC's unknown-intent-for-known-role behavior).
        ADR 006 D1: inner is binary; the tool name is bounded by the
        action tool registry, so an unknown tool is config drift or LLM
        hallucination — fail-closed.
        """
        out = check_tool_authorization(_inp("instructor", "fake_tool"))
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "unknown_tool_for_role"


# ---------------------------------------------------------------------------
# Metadata is preserved for trace fidelity
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_metadata_records_role_and_tool(self):
        out = check_tool_authorization(_inp("student", "grade_update"))
        assert out.metadata["role"] == "student"
        assert out.metadata["tool"] == "grade_update"
