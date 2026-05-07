"""Phase 2 unit tests for inner safety Layer 2 + Layer 3.

Layer 2 (parameter presence): required args exist for the tool.
Layer 3 (parameter format): present args match regex / enum rules.

Locks the contract that Layer 3 NEVER re-flags missing fields — it only
validates what's there. Layer 2 catches absence; Layer 3 catches
malformed values. Cleaner trace attribution this way.
"""

from safety.inner.parameter_validation import (
    check_parameter_format,
    check_parameter_presence,
)
from safety.inner.schemas import (
    InnerLayerName,
    InnerSafetyDecision,
    InnerSafetyInput,
)
from safety.outer.schemas import SessionContext


def _inp(tool: str, args: dict) -> InnerSafetyInput:
    return InnerSafetyInput(
        session=SessionContext(user_id="u1", session_id="s1", user_role="instructor"),
        tool_name=tool,
        tool_args=args,
    )


# ---------------------------------------------------------------------------
# Result shape contracts (both layers)
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_presence_carries_layer_name(self):
        out = check_parameter_presence(
            _inp("enrollment_modify", {"student_id": "S1", "course_id": "CS101", "action": "add"})
        )
        assert out.layer == InnerLayerName.PARAMETER_PRESENCE

    def test_format_carries_layer_name(self):
        out = check_parameter_format(
            _inp("enrollment_modify", {"course_id": "CS101", "action": "add"})
        )
        assert out.layer == InnerLayerName.PARAMETER_FORMAT

    def test_both_layers_record_latency(self):
        a = check_parameter_presence(
            _inp("enrollment_modify", {"student_id": "S1", "course_id": "CS101", "action": "add"})
        )
        b = check_parameter_format(
            _inp("enrollment_modify", {"course_id": "CS101", "action": "add"})
        )
        assert a.latency_ms >= 0 and b.latency_ms >= 0


# ---------------------------------------------------------------------------
# Layer 2 — parameter presence
# ---------------------------------------------------------------------------


class TestParameterPresence:
    def test_all_required_present_allows(self):
        out = check_parameter_presence(
            _inp(
                "grade_update",
                {"student_id": "S1", "course_id": "CS101",
                 "assignment_id": "A1", "grade": "A"},
            )
        )
        assert out.decision == InnerSafetyDecision.ALLOW
        assert out.reason_code == "all_required_args_present"

    def test_missing_required_arg_denies(self):
        out = check_parameter_presence(
            _inp(
                "grade_update",
                {"student_id": "S1", "course_id": "CS101", "grade": "A"},
                # missing assignment_id
            )
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "missing_required_args"
        assert out.metadata["missing"] == ["assignment_id"]

    def test_multiple_missing_args_listed(self):
        out = check_parameter_presence(_inp("grade_update", {}))
        assert out.decision == InnerSafetyDecision.DENY
        # All four required fields missing
        assert set(out.metadata["missing"]) == {
            "student_id", "course_id", "assignment_id", "grade",
        }

    def test_unknown_tool_denies(self):
        """Tool not in _REQUIRED_ARGS — fail-closed (Layer 1 should have caught
        this; reaching here means config drift)."""
        out = check_parameter_presence(_inp("fake_tool", {"x": 1}))
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "unknown_tool_for_presence_check"


# ---------------------------------------------------------------------------
# Layer 3 — parameter format
# ---------------------------------------------------------------------------


class TestCourseIdFormat:
    def test_valid_course_id_passes(self):
        out = check_parameter_format(
            _inp("enrollment_modify", {"course_id": "CS101", "action": "add"})
        )
        assert out.decision == InnerSafetyDecision.ALLOW

    def test_invalid_course_id_lowercase_denies(self):
        out = check_parameter_format(
            _inp("enrollment_modify", {"course_id": "cs101", "action": "add"})
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "invalid_arg_format"
        assert "course_id" in out.reason_human

    def test_long_course_code_passes(self):
        """COMS4705 should pass: 4-letter prefix + 4-digit number."""
        out = check_parameter_format(
            _inp("enrollment_modify", {"course_id": "COMS4705", "action": "add"})
        )
        assert out.decision == InnerSafetyDecision.ALLOW


class TestGradeFormat:
    def test_valid_grades(self):
        for grade in ["A", "A+", "B-", "C", "D+", "F"]:
            out = check_parameter_format(
                _inp("grade_update", {"course_id": "CS101", "grade": grade})
            )
            assert out.decision == InnerSafetyDecision.ALLOW, f"failed on {grade!r}"

    def test_invalid_grade_denies(self):
        out = check_parameter_format(
            _inp("grade_update", {"course_id": "CS101", "grade": "Z"})
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert "grade" in out.reason_human


class TestEnrollmentActionEnum:
    def test_valid_enum_passes(self):
        for action in ["add", "drop"]:
            out = check_parameter_format(
                _inp("enrollment_modify", {"course_id": "CS101", "action": action})
            )
            assert out.decision == InnerSafetyDecision.ALLOW

    def test_invalid_enum_denies(self):
        out = check_parameter_format(
            _inp("enrollment_modify", {"course_id": "CS101", "action": "swap"})
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert "action" in out.reason_human


class TestAssignmentDateAndTitle:
    def test_valid_date_passes(self):
        out = check_parameter_format(
            _inp(
                "assignment_create",
                {"course_id": "CS101", "title": "HW 1", "due_date": "2026-05-15"},
            )
        )
        assert out.decision == InnerSafetyDecision.ALLOW

    def test_invalid_date_denies(self):
        out = check_parameter_format(
            _inp(
                "assignment_create",
                {"course_id": "CS101", "title": "HW 1", "due_date": "next Friday"},
            )
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert "due_date" in out.reason_human

    def test_empty_title_denies(self):
        out = check_parameter_format(
            _inp(
                "assignment_create",
                {"course_id": "CS101", "title": "   ", "due_date": "2026-05-15"},
            )
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert "title" in out.reason_human


# ---------------------------------------------------------------------------
# Layer 3 does NOT re-flag missing fields (that's Layer 2's job)
# ---------------------------------------------------------------------------


class TestLayerSeparation:
    def test_format_layer_skips_missing_fields(self):
        """If grade is absent, Layer 3 should NOT report a grade-format
        error — Layer 2 already handles absence. This keeps trace
        attribution clean: missing → Layer 2 catches; malformed → Layer 3.
        """
        # Provide course_id (valid) but no grade. Layer 3 should ALLOW
        # because no field-format violation in what's present.
        out = check_parameter_format(
            _inp("grade_update", {"course_id": "CS101"})
        )
        assert out.decision == InnerSafetyDecision.ALLOW
