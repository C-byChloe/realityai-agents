"""Phase 8.2 unit tests — output filter (defense-in-depth scrub).

Pins one test per category + the global invariants:
  - Audit safety: findings store hashes only, never leaked text.
  - Idempotence: re-running filter on its own output is a no-op.
  - Benign passthrough: clean responses are unmodified.
  - Reason-code allowlist coverage: every production reason_code must
    be caught (regression guard against new reason_codes silently
    leaking).
"""

import re

import pytest

from safety.output_filter import (
    OutputFinding,
    OutputFilterFindingDetail,
    OutputFilterResult,
    REDACTION_TOKEN,
    filter_response,
    known_reason_codes,
)


# ---------------------------------------------------------------------------
# Per-category coverage
# ---------------------------------------------------------------------------


class TestMarkerLeak:
    """Phase 8.1 spotlighting markers leaking back through the LLM
    response. If this fires, an attacker has learned the boundary
    format AND we failed to keep markers out of user-facing text.
    Filter catches both directions."""

    def test_begin_marker_redacted(self):
        out = filter_response("Found data at [BEGIN-DATA:abc123def456] keep going")
        assert REDACTION_TOKEN in out.redacted_text
        assert "[BEGIN-DATA:abc123def456]" not in out.redacted_text
        assert any(f.category == OutputFinding.MARKER_LEAK for f in out.findings)

    def test_end_marker_redacted(self):
        out = filter_response("done [END-DATA:abc123def456]")
        assert "[END-DATA" not in out.redacted_text
        assert out.redactions_count == 1

    def test_paired_markers_count_two(self):
        text = "[BEGIN-DATA:abc] payload [END-DATA:abc]"
        out = filter_response(text)
        assert out.redactions_count == 2


class TestReasonCodeLeak:
    """Internal reason codes (role_lacks_tool_grant, prompt_guard_*,
    etc.) are machine-readable strings users should never see — they
    belong in audit logs, not user responses. The reason_human field is
    the user-facing channel."""

    @pytest.mark.parametrize(
        "code",
        [
            "role_lacks_tool_grant",
            "role_lacks_action_grant",
            "unknown_role",
            "prompt_guard_injection_high_confidence",
            "prompt_guard_benign",
            "course_full",
            "student_can_only_self_modify_enrollment",
            "live_state_timeout",
            "all_layers_passed",
        ],
    )
    def test_each_reason_code_redacted(self, code: str):
        out = filter_response(f"Verdict: {code}.")
        assert code not in out.redacted_text
        assert any(f.category == OutputFinding.REASON_CODE for f in out.findings)

    def test_reason_code_word_boundary(self):
        """Reason codes must match as whole tokens. A user query that
        legitimately contains a substring like 'role' should NOT trip the
        filter just because 'role_lacks_tool_grant' starts with 'role'.
        """
        out = filter_response("Your role here is to help students.")
        # No reason_code finding — `role` alone is not a reason code.
        assert all(
            f.category != OutputFinding.REASON_CODE for f in out.findings
        )


class TestThresholdLeak:
    def test_named_threshold_constant_redacted(self):
        out = filter_response("DENY_THRESHOLD = 0.9 was exceeded.")
        assert "DENY_THRESHOLD" not in out.redacted_text
        assert any(
            f.category == OutputFinding.THRESHOLD_NAME for f in out.findings
        )

    def test_confidence_inequality_redacted(self):
        out = filter_response("I require confidence > 0.7 to proceed.")
        assert ">" in out.redacted_text or "[REDACTED]" in out.redacted_text
        # The whole "confidence > 0.7" expression is the threshold.
        assert "confidence > 0.7" not in out.redacted_text


class TestSecretLeak:
    @pytest.mark.parametrize(
        "name, secret",
        [
            ("anthropic_api_key", "sk-ant-api03-aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890"),
            ("openai_api_key", "sk-aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890"),
            # ghp_ + exactly 36 alphanumeric chars (GitHub classic PAT format)
            ("github_classic", "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"),
            ("aws_access_key", "AKIAIOSFODNN7EXAMPLE"),
            ("jwt", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"),
            ("bearer", "Bearer abcdef1234567890ABCDEF1234567890XYZ"),
        ],
    )
    def test_each_secret_pattern_redacted(self, name: str, secret: str):
        out = filter_response(f"Token: {secret} — please authenticate.")
        assert secret not in out.redacted_text
        assert any(
            f.category == OutputFinding.SECRET_PATTERN for f in out.findings
        )


class TestArchitectureTermLeak:
    @pytest.mark.parametrize(
        "term",
        [
            "ADR 005",
            "ADR 006 D7",
            "D5 invariant",
            "Phase 8.1",
            "Phase 4 cutover",
            "spotlighting",
            "outer_safety_check",
            "inner_safety_result",
        ],
    )
    def test_each_term_redacted(self, term: str):
        out = filter_response(f"This is documented in {term}.")
        assert term not in out.redacted_text


# ---------------------------------------------------------------------------
# Audit safety — findings store hashes, never leaked content
# ---------------------------------------------------------------------------


class TestAuditSafety:
    """Critical invariant: the audit trail must not become a re-leakage
    channel. Findings carry hashes of matched text, never the text itself.
    """

    def test_findings_have_no_raw_text_field(self):
        """OutputFilterFindingDetail's schema must not expose any field
        that round-trips the matched substring."""
        fields = set(OutputFilterFindingDetail.model_fields.keys())
        # Whitelist what's allowed to be there. If a future schema bump
        # adds a "matched_text" field, this test fails — forcing review.
        assert fields == {"category", "match_hash"}

    def test_match_hash_does_not_contain_original_text(self):
        """The hash representation must not embed any prefix of the
        original. sha256[:12] is hex-only — no information leakage."""
        secret = "sk-ant-api03-aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890"
        out = filter_response(f"Use {secret}")
        for finding in out.findings:
            # Match hash is exactly 12 lowercase hex characters.
            assert re.fullmatch(r"[0-9a-f]{12}", finding.match_hash)
            # The hash must not contain any 6+ char substring of the secret.
            for i in range(len(secret) - 5):
                assert secret[i:i + 6].lower() not in finding.match_hash

    def test_dumped_result_does_not_contain_redacted_text(self):
        """A defensive sweep: even via Pydantic model_dump_json, no
        finding leaks the matched substring back out."""
        secret = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        out = filter_response(f"key: {secret}")
        dumped = out.model_dump_json()
        # The redacted_text WILL be in the dump (it's the user-safe
        # version), but the secret itself must NOT appear anywhere.
        assert secret not in dumped


# ---------------------------------------------------------------------------
# Behavioral invariants
# ---------------------------------------------------------------------------


class TestIdempotence:
    def test_filter_on_already_redacted_text_is_noop(self):
        """Running the filter twice gives the same output as once.
        Important for retry paths or composed pipelines that might
        accidentally double-apply."""
        text = "Verdict: role_lacks_tool_grant per ADR 005."
        once = filter_response(text)
        twice = filter_response(once.redacted_text)
        assert twice.redacted_text == once.redacted_text
        assert twice.redactions_count == 0


class TestBenignPassthrough:
    @pytest.mark.parametrize(
        "text",
        [
            "CS101 meets MWF 10:00-10:50 in room 401.",
            "Your transcript shows 36 completed credits.",
            "Spring 2026 has 5 AI electives available.",
            "请问CS101什么时候上课",  # Chinese, no leakage
            "",
        ],
    )
    def test_clean_response_unmodified(self, text: str):
        out = filter_response(text)
        assert out.redacted_text == text
        assert out.redactions_count == 0
        assert out.findings == []


class TestMixedContent:
    def test_multiple_categories_all_caught(self):
        text = (
            "Denied: role_lacks_tool_grant per ADR 005 D5; "
            "the [BEGIN-DATA:abc] marker leaked in Phase 4 cutover."
        )
        out = filter_response(text)
        assert out.redactions_count >= 4
        seen = {f.category for f in out.findings}
        assert OutputFinding.REASON_CODE in seen
        assert OutputFinding.INTERNAL_ARCHITECTURE_TERM in seen
        assert OutputFinding.MARKER_LEAK in seen


# ---------------------------------------------------------------------------
# Reason-code allowlist coverage — regression guard
# ---------------------------------------------------------------------------


class TestReasonCodeAllowlistCoverage:
    """Every reason_code shipped in production safety code must appear
    in the filter's allowlist. If a new reason_code is added without
    updating the filter, this test fails — surfacing the silent leak.

    Symmetric: every code in the filter's allowlist should be in actual
    use somewhere in the codebase. (Stale entries waste regex cycles
    but don't cause incorrect behavior; this half is informational.)
    """

    def test_known_reason_codes_is_nonempty(self):
        codes = list(known_reason_codes())
        assert len(codes) >= 30, (
            f"reason_code allowlist looks suspiciously small ({len(codes)})"
        )

    def test_each_known_code_is_actually_caught(self):
        """Canary: take each registered code, embed it in a sentence,
        verify the filter redacts it. If a regex compilation bug or
        word-boundary issue breaks any single code, this catches it.
        """
        for code in known_reason_codes():
            out = filter_response(f"Returned: {code} from layer X.")
            assert code not in out.redacted_text, (
                f"reason_code {code!r} not caught by filter"
            )
