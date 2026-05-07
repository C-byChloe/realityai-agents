"""Layer 4 of the inner safety gate — live world-state check.

Reads CURRENT world state (capacity, enrollment, grade window, etc.)
for the entity affected by the action — bypassing any read-path cache.
This is the race-condition guard: turn 1 may have recommended CS401
based on a stale "5 seats open" cache; turn 2 commits an enrollment,
but in the interim the last seat got taken. Without this layer, the
write commits against an inconsistent world.

ADR 006 D3: when the world-state read is unavailable (timeout, DB
unreachable, etc.), this layer returns DENY ("live_state_unavailable").
Fail-closed because we cannot confirm safety to commit; a write should
fail temporarily rather than silently proceed against unknown state.
This is the OPPOSITE trade-off from outer's Tier 3 (which fell back to
FLAG): inner is the last gate before gRPC, so unverified state is more
dangerous than refused writes.

Per-tool dispatch:
  enrollment_modify (add)  → check course exists + capacity not full
  enrollment_modify (drop) → check student is currently enrolled
  grade_update             → check student is enrolled + grade window open
  assignment_create        → check course exists + caller owns it

Production swap point: `_read_course_state` is currently backed by an
in-memory mock. Replace with a real Postgres / gRPC read in production;
the rest of the layer (per-tool dispatch + timeout + verdict logic)
stays unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any, Callable, Coroutine

from safety.inner.schemas import (
    InnerLayerName,
    InnerSafetyDecision,
    InnerSafetyInput,
    LayerResult,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S: float = 2.0


# ---------------------------------------------------------------------------
# Mock world-state store. Production replaces with real Postgres / gRPC.
# ---------------------------------------------------------------------------

_MOCK_COURSE_STATE: dict[str, dict] = {
    "CS101": {
        "exists": True,
        "instructor_user_id": "instructor_smith",
        "current_enrollment": 25,
        "capacity": 30,
        "grade_window_open": True,
        "enrolled_students": {"S001", "S002", "S010"},
    },
    "CS201": {
        "exists": True,
        "instructor_user_id": "instructor_johnson",
        "current_enrollment": 28,
        "capacity": 30,
        "grade_window_open": True,
        "enrolled_students": {"S001", "S005"},
    },
    "CS401": {
        "exists": True,
        "instructor_user_id": "instructor_lee",
        "current_enrollment": 30,
        "capacity": 30,  # FULL — used by race-condition tests
        "grade_window_open": True,
        "enrolled_students": {"S001"},
    },
    "MATH200": {
        "exists": True,
        "instructor_user_id": "instructor_lee",
        "current_enrollment": 15,
        "capacity": 20,
        "grade_window_open": False,  # CLOSED — used by grade-update race tests
        "enrolled_students": {"S001"},
    },
}


async def _read_course_state(course_id: str) -> dict:
    """Read current course state. Production swap point.

    Currently reads from `_MOCK_COURSE_STATE` (in-memory). Production
    replaces this with a real Postgres query or gRPC call to the core
    service. The interface contract:
      - returns a dict with keys matching the mock structure above
      - raises KeyError when the course does not exist
      - raises any other Exception when the underlying store is
        unreachable (network error, DB down, etc.)

    Timeout enforcement is done by the layer entry point (`check_live_state`)
    via `asyncio.wait_for` wrapping the entire dispatch — this lets the
    timeout invariant survive even when tests monkeypatch this function.
    """
    # Simulated I/O delay so timeout tests can exercise the path.
    await asyncio.sleep(0.001)
    if course_id not in _MOCK_COURSE_STATE:
        raise KeyError(f"course not found: {course_id}")
    return _MOCK_COURSE_STATE[course_id]


# ---------------------------------------------------------------------------
# Per-tool live-state validators
# ---------------------------------------------------------------------------


async def _check_enrollment_modify(
    safety_input: InnerSafetyInput,
) -> tuple[InnerSafetyDecision, str, str, dict]:
    args = safety_input.tool_args
    course_id = args["course_id"]
    student_id = args["student_id"]
    action = args["action"]
    caller_role = safety_input.session.user_role
    caller_id = safety_input.session.user_id

    # Caller-identity guard: students may modify only their OWN enrollment
    # record. Instructors / registrars retain the ability to enroll or
    # drop any student. Without this check, granting students access to
    # `enrollment_modify` at Layer 1 would silently allow one student to
    # drop another. The check runs BEFORE the world-state read so a
    # spoofed student_id never hits the DB.
    if caller_role == "student" and student_id != caller_id:
        return (
            InnerSafetyDecision.DENY,
            "student_can_only_self_modify_enrollment",
            (
                f"User {caller_id} (role=student) cannot modify enrollment "
                f"for {student_id}; students may only modify their own "
                f"enrollment record."
            ),
            {"caller_user_id": caller_id, "target_student_id": student_id},
        )

    state = await _read_course_state(course_id)

    if action == "add":
        if state["current_enrollment"] >= state["capacity"]:
            return (
                InnerSafetyDecision.DENY,
                "course_full",
                (
                    f"Course {course_id} is at capacity "
                    f"({state['current_enrollment']}/{state['capacity']}); "
                    f"cannot enroll {student_id}."
                ),
                {"current_enrollment": state["current_enrollment"],
                 "capacity": state["capacity"]},
            )
        return (
            InnerSafetyDecision.ALLOW,
            "enrollment_capacity_ok",
            "",
            {"current_enrollment": state["current_enrollment"]},
        )

    # action == "drop"
    if student_id not in state["enrolled_students"]:
        return (
            InnerSafetyDecision.DENY,
            "not_enrolled",
            (
                f"Student {student_id} is not enrolled in {course_id}; "
                f"cannot drop."
            ),
            {"enrolled_count": len(state["enrolled_students"])},
        )
    return (
        InnerSafetyDecision.ALLOW,
        "enrollment_record_exists",
        "",
        {"enrolled_count": len(state["enrolled_students"])},
    )


async def _check_grade_update(
    safety_input: InnerSafetyInput,
) -> tuple[InnerSafetyDecision, str, str, dict]:
    args = safety_input.tool_args
    course_id = args["course_id"]
    student_id = args["student_id"]

    state = await _read_course_state(course_id)

    if not state["grade_window_open"]:
        return (
            InnerSafetyDecision.DENY,
            "grade_window_closed",
            (
                f"Grade window for {course_id} is closed; "
                f"updates require registrar override."
            ),
            {},
        )
    if student_id not in state["enrolled_students"]:
        return (
            InnerSafetyDecision.DENY,
            "student_not_enrolled",
            (
                f"Student {student_id} is not enrolled in {course_id}; "
                f"cannot grade."
            ),
            {},
        )
    return (
        InnerSafetyDecision.ALLOW,
        "grade_update_preconditions_met",
        "",
        {},
    )


async def _check_assignment_create(
    safety_input: InnerSafetyInput,
) -> tuple[InnerSafetyDecision, str, str, dict]:
    args = safety_input.tool_args
    course_id = args["course_id"]
    caller_id = safety_input.session.user_id

    state = await _read_course_state(course_id)

    # Authorization at the course level: only the listed instructor
    # may create assignments for their course. (Registrars / admins
    # would be handled by a richer matrix; not in scope here.)
    if state["instructor_user_id"] != caller_id:
        return (
            InnerSafetyDecision.DENY,
            "not_course_instructor",
            (
                f"User {caller_id} is not the instructor of {course_id}; "
                f"cannot create assignment."
            ),
            {"course_instructor": state["instructor_user_id"]},
        )
    return (
        InnerSafetyDecision.ALLOW,
        "course_ownership_confirmed",
        "",
        {},
    )


_LIVE_STATE_HANDLERS: dict[
    str,
    Callable[[InnerSafetyInput], Coroutine[Any, Any, tuple[InnerSafetyDecision, str, str, dict]]],
] = {
    # NOTE: the dict signature above is approximate; actual calls also
    # take timeout_s. The handlers are awaited via _dispatch.
}


async def _dispatch(
    safety_input: InnerSafetyInput,
) -> tuple[InnerSafetyDecision, str, str, dict]:
    tool_name = safety_input.tool_name
    if tool_name == "enrollment_modify":
        return await _check_enrollment_modify(safety_input)
    if tool_name == "grade_update":
        return await _check_grade_update(safety_input)
    if tool_name == "assignment_create":
        return await _check_assignment_create(safety_input)
    raise ValueError(f"no live-state handler for tool {tool_name!r}")


# ---------------------------------------------------------------------------
# Layer 4 entry point
# ---------------------------------------------------------------------------


async def check_live_state(
    safety_input: InnerSafetyInput, *, timeout_s: float = DEFAULT_TIMEOUT_S
) -> LayerResult:
    """Read current world state and verdict.

    Failure modes (D3 fail-closed):
      - asyncio.TimeoutError → DENY ("live_state_timeout")
      - KeyError (course not found) → DENY ("course_not_found")
      - any other Exception (DB unreachable, etc.) → DENY ("live_state_unavailable")
      - tool not in _LIVE_STATE_HANDLERS → DENY ("unknown_tool_for_live_state")
    """
    t0 = perf_counter()
    tool_name = safety_input.tool_name

    try:
        decision, reason_code, reason_human, metadata = await asyncio.wait_for(
            _dispatch(safety_input), timeout=timeout_s
        )
        return _result(
            decision=decision,
            reason_code=reason_code,
            reason_human=reason_human,
            metadata={"tool": tool_name, **metadata},
            t0=t0,
        )

    except asyncio.TimeoutError:
        return _result(
            decision=InnerSafetyDecision.DENY,
            reason_code="live_state_timeout",
            reason_human=(
                f"World-state read timed out after {timeout_s}s; cannot "
                f"verify preconditions for {tool_name}."
            ),
            metadata={"tool": tool_name, "failure_mode": "timeout"},
            t0=t0,
        )

    except KeyError as e:
        return _result(
            decision=InnerSafetyDecision.DENY,
            reason_code="course_not_found",
            reason_human=str(e),
            metadata={"tool": tool_name, "failure_mode": "not_found"},
            t0=t0,
        )

    except ValueError as e:
        # Raised by _dispatch for unknown tools.
        return _result(
            decision=InnerSafetyDecision.DENY,
            reason_code="unknown_tool_for_live_state",
            reason_human=str(e),
            metadata={"tool": tool_name, "failure_mode": "config_drift"},
            t0=t0,
        )

    except Exception as e:
        logger.warning("Live-state read failed for %s: %s", tool_name, e)
        return _result(
            decision=InnerSafetyDecision.DENY,
            reason_code="live_state_unavailable",
            reason_human=(
                f"World-state read failed for {tool_name}; failing closed. "
                f"Underlying error: {e}"
            ),
            metadata={
                "tool": tool_name,
                "failure_mode": "exception",
                "error": str(e),
            },
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
        layer=InnerLayerName.LIVE_STATE,
        decision=decision,
        reason_code=reason_code,
        reason_human=reason_human,
        latency_ms=int((perf_counter() - t0) * 1000),
        metadata=metadata,
    )
