"""Phase 4 unit tests for inner safety audit sidecar.

Locks ADR 006 D4 invariants:
  - Audit records get built EVEN on DENY (not just ALLOW). Catches
    permission-scan attack patterns where an attacker probes for what
    they can't access; if audit only ran on ALLOW, denials would leave
    no trace.
  - args_keys are captured but argument VALUES never are (PII safety).
    The schema enforces this at the field level (Phase 0 sanity test);
    these tests verify the build function honors it.
  - audit_id is opaque (12-char uuid hex slice) — sortable enough for
    trace correlation, short enough for log greppability.
  - persist_audit_record is idempotent (same audit_id twice = no-op).
"""

import re

import pytest

from safety.inner.audit import (
    build_audit_record,
    persist_audit_record,
    update_audit_for_execution,
    _persisted_audit_ids,
    _reset_persisted_audit_ids_for_testing,
)
from safety.inner.schemas import (
    AuditRecord,
    InnerLayerName,
    InnerSafetyDecision,
    InnerSafetyInput,
)
from safety.outer.schemas import SessionContext


@pytest.fixture(autouse=True)
def _reset_persisted_set():
    """Each test starts with a clean dedup set."""
    _reset_persisted_audit_ids_for_testing()
    yield
    _reset_persisted_audit_ids_for_testing()


def _inp(args: dict | None = None) -> InnerSafetyInput:
    return InnerSafetyInput(
        session=SessionContext(
            user_id="u1", session_id="s1", user_role="instructor", tenant_id="t1"
        ),
        tool_name="grade_update",
        tool_args=args
        or {"student_id": "S001", "course_id": "CS101",
            "assignment_id": "A1", "grade": "A"},
    )


# ---------------------------------------------------------------------------
# Build phase — pre-execution audit construction
# ---------------------------------------------------------------------------


class TestBuildOnDeny:
    """ADR 006 D4: audit ALWAYS runs, even on DENY. Locks the invariant
    that build_audit_record returns a fully populated record regardless
    of decision."""

    def test_build_on_deny_at_layer_1_succeeds(self):
        record = build_audit_record(
            _inp(),
            decision=InnerSafetyDecision.DENY,
            short_circuited_at=InnerLayerName.TOOL_AUTHORIZATION,
            final_reason_code="role_lacks_tool_grant",
        )
        assert record.decision == InnerSafetyDecision.DENY
        assert record.short_circuited_at == InnerLayerName.TOOL_AUTHORIZATION
        assert record.final_reason_code == "role_lacks_tool_grant"

    def test_deny_record_has_execution_attempted_false(self):
        """If gates denied, execution was never attempted — record reflects that."""
        record = build_audit_record(
            _inp(),
            decision=InnerSafetyDecision.DENY,
            short_circuited_at=InnerLayerName.LIVE_STATE,
            final_reason_code="course_full",
        )
        assert record.execution_attempted is False
        assert record.execution_succeeded is None


class TestBuildOnAllow:
    def test_build_on_allow_short_circuited_at_is_none(self):
        record = build_audit_record(
            _inp(),
            decision=InnerSafetyDecision.ALLOW,
            short_circuited_at=None,
            final_reason_code="all_layers_passed",
        )
        assert record.decision == InnerSafetyDecision.ALLOW
        assert record.short_circuited_at is None


# ---------------------------------------------------------------------------
# Args privacy — keys captured, values NEVER captured (D4)
# ---------------------------------------------------------------------------


class TestArgsPrivacy:
    def test_args_keys_captured_sorted(self):
        record = build_audit_record(
            _inp({"grade": "A", "student_id": "S001", "course_id": "CS101"}),
            decision=InnerSafetyDecision.ALLOW,
            short_circuited_at=None,
            final_reason_code="x",
        )
        # Sorted for deterministic audit output
        assert record.args_keys == ["course_id", "grade", "student_id"]

    def test_args_values_never_in_dump(self):
        """Values like the actual grade ('A') or the student_id ('S001')
        must not appear ANYWHERE in the AuditRecord serialization. The
        schema doesn't have a field for them, but a defensive sweep
        guards against future regressions (e.g., someone adding an
        `args` blob)."""
        record = build_audit_record(
            _inp({"grade": "PII_GRADE_VALUE", "student_id": "PII_STUDENT_VALUE"}),
            decision=InnerSafetyDecision.DENY,
            short_circuited_at=InnerLayerName.PARAMETER_PRESENCE,
            final_reason_code="missing_required_args",
        )
        dumped = record.model_dump_json()
        assert "PII_GRADE_VALUE" not in dumped
        assert "PII_STUDENT_VALUE" not in dumped


# ---------------------------------------------------------------------------
# audit_id format
# ---------------------------------------------------------------------------


class TestAuditIdFormat:
    def test_audit_id_is_12_char_hex(self):
        record = build_audit_record(
            _inp(),
            decision=InnerSafetyDecision.ALLOW,
            short_circuited_at=None,
            final_reason_code="x",
        )
        assert len(record.audit_id) == 12
        assert re.fullmatch(r"[0-9a-f]{12}", record.audit_id), (
            f"audit_id should be 12-char lowercase hex, got {record.audit_id!r}"
        )

    def test_audit_ids_are_unique_per_call(self):
        ids = {
            build_audit_record(
                _inp(),
                decision=InnerSafetyDecision.ALLOW,
                short_circuited_at=None,
                final_reason_code="x",
            ).audit_id
            for _ in range(50)
        }
        # Vanishingly unlikely to collide on 12-char uuid hex (48 bits)
        assert len(ids) == 50


# ---------------------------------------------------------------------------
# Update phase — post-execution outcome
# ---------------------------------------------------------------------------


class TestUpdateForExecution:
    def test_update_on_success_marks_attempted_and_succeeded(self):
        original = build_audit_record(
            _inp(),
            decision=InnerSafetyDecision.ALLOW,
            short_circuited_at=None,
            final_reason_code="all_layers_passed",
        )
        updated = update_audit_for_execution(original, succeeded=True)
        assert updated.execution_attempted is True
        assert updated.execution_succeeded is True
        assert updated.execution_error is None
        # audit_id preserved across update
        assert updated.audit_id == original.audit_id

    def test_update_on_failure_records_error(self):
        original = build_audit_record(
            _inp(),
            decision=InnerSafetyDecision.ALLOW,
            short_circuited_at=None,
            final_reason_code="all_layers_passed",
        )
        updated = update_audit_for_execution(
            original, succeeded=False, error="gRPC unavailable"
        )
        assert updated.execution_attempted is True
        assert updated.execution_succeeded is False
        assert updated.execution_error == "gRPC unavailable"

    def test_update_returns_new_record_not_mutation(self):
        """Pydantic-immutable contract: update returns a new record;
        original is unchanged. Lets callers compare pre/post snapshots."""
        original = build_audit_record(
            _inp(),
            decision=InnerSafetyDecision.ALLOW,
            short_circuited_at=None,
            final_reason_code="x",
        )
        updated = update_audit_for_execution(original, succeeded=True)
        assert original.execution_attempted is False  # unchanged
        assert updated.execution_attempted is True


# ---------------------------------------------------------------------------
# Persistence — idempotent stub
# ---------------------------------------------------------------------------


class TestPersistIdempotency:
    def test_persist_writes_unique_id(self):
        record = build_audit_record(
            _inp(),
            decision=InnerSafetyDecision.ALLOW,
            short_circuited_at=None,
            final_reason_code="x",
        )
        persist_audit_record(record)
        assert record.audit_id in _persisted_audit_ids()

    def test_persist_same_id_twice_is_noop(self):
        record = build_audit_record(
            _inp(),
            decision=InnerSafetyDecision.ALLOW,
            short_circuited_at=None,
            final_reason_code="x",
        )
        persist_audit_record(record)
        persist_audit_record(record)  # duplicate — must not raise, must not double-write
        assert len(_persisted_audit_ids()) == 1

    def test_persist_different_ids_accumulate(self):
        for _ in range(5):
            persist_audit_record(
                build_audit_record(
                    _inp(),
                    decision=InnerSafetyDecision.ALLOW,
                    short_circuited_at=None,
                    final_reason_code="x",
                )
            )
        assert len(_persisted_audit_ids()) == 5
