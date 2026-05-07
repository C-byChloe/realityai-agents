"""Phase 3 unit tests for inner safety Layer 4 — live world-state check.

Locks ADR 006 D3 invariant: every failure mode → DENY, never silently
ALLOW. The DB-fail / timeout / not-found / config-drift paths all
fall closed because verifying world state is the layer's whole purpose;
unverified state cannot be safely committed.

Race-condition coverage: capacity full, grade window closed, student
not enrolled, wrong instructor — these are the canonical examples for
why a write path cannot trust the read path's view.
"""

import asyncio
from unittest.mock import patch

import pytest

from safety.inner.live_state import check_live_state
from safety.inner.schemas import (
    InnerLayerName,
    InnerSafetyDecision,
    InnerSafetyInput,
)
from safety.outer.schemas import SessionContext


def _inp(
    tool: str,
    args: dict,
    *,
    user_id: str = "instructor_smith",
    user_role: str = "instructor",
) -> InnerSafetyInput:
    return InnerSafetyInput(
        session=SessionContext(
            user_id=user_id, session_id="s1", user_role=user_role
        ),
        tool_name=tool,
        tool_args=args,
    )


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


class TestResultShape:
    async def test_result_carries_layer_name(self):
        out = await check_live_state(
            _inp("enrollment_modify",
                 {"student_id": "S100", "course_id": "CS101", "action": "add"})
        )
        assert out.layer == InnerLayerName.LIVE_STATE

    async def test_result_records_latency(self):
        out = await check_live_state(
            _inp("enrollment_modify",
                 {"student_id": "S100", "course_id": "CS101", "action": "add"})
        )
        assert out.latency_ms >= 0


# ---------------------------------------------------------------------------
# Happy paths — one per tool
# ---------------------------------------------------------------------------


class TestHappyPaths:
    async def test_enrollment_add_with_capacity_allows(self):
        out = await check_live_state(
            _inp("enrollment_modify",
                 {"student_id": "S100", "course_id": "CS101", "action": "add"})
        )
        assert out.decision == InnerSafetyDecision.ALLOW
        assert out.reason_code == "enrollment_capacity_ok"

    async def test_enrollment_drop_when_enrolled_allows(self):
        out = await check_live_state(
            _inp("enrollment_modify",
                 {"student_id": "S001", "course_id": "CS101", "action": "drop"})
        )
        assert out.decision == InnerSafetyDecision.ALLOW
        assert out.reason_code == "enrollment_record_exists"

    async def test_grade_update_when_window_open_allows(self):
        out = await check_live_state(
            _inp("grade_update",
                 {"student_id": "S001", "course_id": "CS101",
                  "assignment_id": "A1", "grade": "A"})
        )
        assert out.decision == InnerSafetyDecision.ALLOW
        assert out.reason_code == "grade_update_preconditions_met"

    async def test_assignment_create_by_course_instructor_allows(self):
        out = await check_live_state(
            _inp(
                "assignment_create",
                {"course_id": "CS101", "title": "HW1", "due_date": "2026-05-15"},
                user_id="instructor_smith",  # CS101's instructor in mock
            )
        )
        assert out.decision == InnerSafetyDecision.ALLOW
        assert out.reason_code == "course_ownership_confirmed"


# ---------------------------------------------------------------------------
# Race-condition denials — the whole reason this layer exists
# ---------------------------------------------------------------------------


class TestRaceConditions:
    async def test_enrollment_add_to_full_course_denies(self):
        """CS401 is at capacity 30/30 in mock state. Outer / static rules
        cannot detect this — only a live read can. Layer 4 catches it."""
        out = await check_live_state(
            _inp("enrollment_modify",
                 {"student_id": "S100", "course_id": "CS401", "action": "add"})
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "course_full"
        assert out.metadata["current_enrollment"] == 30
        assert out.metadata["capacity"] == 30

    async def test_drop_when_not_enrolled_denies(self):
        out = await check_live_state(
            _inp("enrollment_modify",
                 {"student_id": "S999", "course_id": "CS101", "action": "drop"})
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "not_enrolled"

    async def test_grade_update_when_window_closed_denies(self):
        """MATH200 has grade_window_open=False in mock state."""
        out = await check_live_state(
            _inp("grade_update",
                 {"student_id": "S001", "course_id": "MATH200",
                  "assignment_id": "A1", "grade": "A"})
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "grade_window_closed"

    async def test_assignment_create_by_wrong_instructor_denies(self):
        out = await check_live_state(
            _inp(
                "assignment_create",
                {"course_id": "CS101", "title": "HW1", "due_date": "2026-05-15"},
                user_id="instructor_impostor",
            )
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "not_course_instructor"
        assert out.metadata["course_instructor"] == "instructor_smith"


# ---------------------------------------------------------------------------
# Failure modes — D3 fail-closed invariant
# ---------------------------------------------------------------------------


class TestFailureModes:
    async def test_course_not_found_denies(self):
        out = await check_live_state(
            _inp("enrollment_modify",
                 {"student_id": "S1", "course_id": "NOTACOURSE", "action": "add"})
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "course_not_found"

    async def test_db_unavailable_denies(self):
        """Any unexpected exception from _read_course_state → DENY."""

        async def boom(*_a, **_kw):
            raise ConnectionError("postgres unreachable")

        with patch("safety.inner.live_state._read_course_state", boom):
            out = await check_live_state(
                _inp("enrollment_modify",
                     {"student_id": "S1", "course_id": "CS101", "action": "add"})
            )
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "live_state_unavailable"
        assert "postgres unreachable" in out.metadata["error"]

    async def test_timeout_denies(self):
        """A slow read → asyncio.TimeoutError → DENY (not silent ALLOW)."""

        async def hang(*_a, **_kw):
            await asyncio.sleep(10)

        with patch("safety.inner.live_state._read_course_state", hang):
            out = await check_live_state(
                _inp("enrollment_modify",
                     {"student_id": "S1", "course_id": "CS101", "action": "add"}),
                timeout_s=0.05,
            )
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "live_state_timeout"

    async def test_unknown_tool_denies(self):
        """Tool with no handler in _dispatch → DENY (config drift)."""
        out = await check_live_state(
            _inp("fake_tool", {"x": 1})
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "unknown_tool_for_live_state"

    async def test_no_failure_mode_returns_allow(self):
        """Cross-cutting invariant: across ALL failure modes above, no
        path returns ALLOW. Layer 4 is fail-closed (D3); ALLOW only
        comes from confirmed-safe state."""
        # Simulated DB failure
        async def boom(*_a, **_kw):
            raise RuntimeError("x")
        with patch("safety.inner.live_state._read_course_state", boom):
            out = await check_live_state(
                _inp("grade_update",
                     {"student_id": "S1", "course_id": "CS101",
                      "assignment_id": "A1", "grade": "A"})
            )
            assert out.decision != InnerSafetyDecision.ALLOW

        # Unknown course
        out = await check_live_state(
            _inp("grade_update",
                 {"student_id": "S1", "course_id": "FAKE",
                  "assignment_id": "A1", "grade": "A"})
        )
        assert out.decision != InnerSafetyDecision.ALLOW

        # Unknown tool
        out = await check_live_state(_inp("nonexistent_tool", {}))
        assert out.decision != InnerSafetyDecision.ALLOW


# ---------------------------------------------------------------------------
# Caller-identity guard for enrollment_modify (sec-fix from outer matrix
# loosening). Granting students access to enrollment_modify at Layer 1
# would silently allow one student to drop or enroll another; this guard
# enforces self-only modifications for student-role callers without
# blocking instructor / registrar from doing it on behalf of others.
# ---------------------------------------------------------------------------


class TestStudentSelfModifyEnrollment:
    async def test_student_can_self_enroll_in_open_course(self):
        """Student S100 enrolls themselves in CS101 (capacity 25/30).
        caller_id == student_id → identity check passes → capacity check
        passes → ALLOW.
        """
        out = await check_live_state(
            _inp(
                "enrollment_modify",
                {"student_id": "S100", "course_id": "CS101", "action": "add"},
                user_id="S100",
                user_role="student",
            )
        )
        assert out.decision == InnerSafetyDecision.ALLOW
        assert out.reason_code == "enrollment_capacity_ok"

    async def test_student_cannot_enroll_other_student(self):
        """Student kim tries to enroll S100 (a different student) into
        CS101. Caller-identity check denies BEFORE the capacity check
        runs — a student cannot modify another student's enrollment.
        """
        out = await check_live_state(
            _inp(
                "enrollment_modify",
                {"student_id": "S100", "course_id": "CS101", "action": "add"},
                user_id="kim",
                user_role="student",
            )
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "student_can_only_self_modify_enrollment"
        assert out.metadata["caller_user_id"] == "kim"
        assert out.metadata["target_student_id"] == "S100"

    async def test_student_cannot_drop_another_student(self):
        """Symmetric guard for drop action — student cannot drop another
        student's enrollment, even if that student is enrolled.
        """
        out = await check_live_state(
            _inp(
                "enrollment_modify",
                {"student_id": "S001", "course_id": "CS101", "action": "drop"},
                user_id="kim",
                user_role="student",
            )
        )
        assert out.decision == InnerSafetyDecision.DENY
        assert out.reason_code == "student_can_only_self_modify_enrollment"

    async def test_instructor_can_still_enroll_any_student(self):
        """Caller-identity guard fires only on student role; instructors
        retain the ability to enroll anyone (capacity gate still applies).
        """
        out = await check_live_state(
            _inp(
                "enrollment_modify",
                {"student_id": "S100", "course_id": "CS101", "action": "add"},
                user_id="instructor_smith",
                user_role="instructor",
            )
        )
        assert out.decision == InnerSafetyDecision.ALLOW
        assert out.reason_code == "enrollment_capacity_ok"
