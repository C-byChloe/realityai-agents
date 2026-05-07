"""Layers 2 + 3 of the inner safety gate.

Layer 2 — Parameter presence: required fields exist for the chosen tool.
Layer 3 — Parameter format: enum legality, regex format checks for
   structured fields (course_id, grade, due_date, action).

Both layers run on the LLM-emitted (tool_name, tool_args) tuple BEFORE
gRPC execution. They are deterministic, sub-millisecond, and have no
external dependencies — pure functions over the input.

Why split into two layers (and not one combined "validate" gate):
  - Presence is cheaper (set diff). Format is regex.
  - Short-circuit between them makes traces clearer:
    "DENY at parameter_presence, missing student_id" vs.
    "DENY at parameter_format, course_id 'cs101a' fails pattern"
  - Each can be extended independently as new tools land.

The format checks DO NOT re-flag missing fields (Layer 2 covers that).
Each format check guards `if field in args:` first.

See `docs/adr/006-inner-safety-layer.md` (D1 binary verdict).
"""

from __future__ import annotations

import re
from time import perf_counter
from typing import Callable

from safety.inner.schemas import (
    InnerLayerName,
    InnerSafetyDecision,
    InnerSafetyInput,
    LayerResult,
)

# ---------------------------------------------------------------------------
# Layer 2 — required-args table
# ---------------------------------------------------------------------------
# Self-contained copy; the duplicate in agents/action_agent.py:213-218 is
# retired during the Phase 5 cutover. Until then both copies exist and
# must be kept in sync.

_REQUIRED_ARGS: dict[str, set[str]] = {
    "grade_update": {"student_id", "course_id", "assignment_id", "grade"},
    "enrollment_modify": {"student_id", "course_id", "action"},
    "assignment_create": {"course_id", "title", "due_date"},
}


def check_parameter_presence(safety_input: InnerSafetyInput) -> LayerResult:
    """Layer 2 entry point: are all required fields present?"""
    t0 = perf_counter()
    tool_name = safety_input.tool_name
    args = safety_input.tool_args or {}

    required = _REQUIRED_ARGS.get(tool_name)
    if required is None:
        # Tool unknown to this layer's registry. Layer 1 (tool_authorization)
        # already enforces a known-tool invariant; reaching here would mean
        # config drift between the two layers. Fail-closed.
        return _result(
            InnerLayerName.PARAMETER_PRESENCE,
            decision=InnerSafetyDecision.DENY,
            reason_code="unknown_tool_for_presence_check",
            reason_human=(
                f"No parameter-presence policy configured for tool '{tool_name}'."
            ),
            metadata={"tool": tool_name},
            t0=t0,
        )

    missing = required - set(args.keys())
    if missing:
        return _result(
            InnerLayerName.PARAMETER_PRESENCE,
            decision=InnerSafetyDecision.DENY,
            reason_code="missing_required_args",
            reason_human=(
                f"Tool '{tool_name}' missing required args: {sorted(missing)}"
            ),
            metadata={"tool": tool_name, "missing": sorted(missing)},
            t0=t0,
        )

    return _result(
        InnerLayerName.PARAMETER_PRESENCE,
        decision=InnerSafetyDecision.ALLOW,
        reason_code="all_required_args_present",
        reason_human="",
        metadata={"tool": tool_name, "checked": sorted(required)},
        t0=t0,
    )


# ---------------------------------------------------------------------------
# Layer 3 — format validators (regex / enum checks)
# ---------------------------------------------------------------------------

_COURSE_ID_PATTERN = re.compile(r"^[A-Z]{2,4}\d{3,4}$")
_GRADE_PATTERN = re.compile(r"^[A-F][+-]?$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ENROLLMENT_ACTIONS = {"add", "drop"}


def _validate_grade_update_format(args: dict) -> list[str]:
    errors: list[str] = []
    if "course_id" in args and not _COURSE_ID_PATTERN.match(str(args["course_id"])):
        errors.append(
            f"course_id format invalid; expected e.g. 'CS101' or 'COMS4705', "
            f"got {args['course_id']!r}"
        )
    if "grade" in args and not _GRADE_PATTERN.match(str(args["grade"])):
        errors.append(
            f"grade must be letter grade (A–F, optional +/-), got {args['grade']!r}"
        )
    return errors


def _validate_enrollment_format(args: dict) -> list[str]:
    errors: list[str] = []
    if "course_id" in args and not _COURSE_ID_PATTERN.match(str(args["course_id"])):
        errors.append(
            f"course_id format invalid; expected e.g. 'CS101' or 'COMS4705', "
            f"got {args['course_id']!r}"
        )
    if "action" in args and args["action"] not in _ENROLLMENT_ACTIONS:
        errors.append(
            f"action must be 'add' or 'drop', got {args['action']!r}"
        )
    return errors


def _validate_assignment_format(args: dict) -> list[str]:
    errors: list[str] = []
    if "course_id" in args and not _COURSE_ID_PATTERN.match(str(args["course_id"])):
        errors.append(
            f"course_id format invalid; expected e.g. 'CS101' or 'COMS4705', "
            f"got {args['course_id']!r}"
        )
    if "title" in args and not str(args["title"]).strip():
        errors.append("title must be non-empty")
    if "due_date" in args and not _DATE_PATTERN.match(str(args["due_date"])):
        errors.append(
            f"due_date must be ISO date 'YYYY-MM-DD', got {args['due_date']!r}"
        )
    return errors


_FORMAT_VALIDATORS: dict[str, Callable[[dict], list[str]]] = {
    "grade_update": _validate_grade_update_format,
    "enrollment_modify": _validate_enrollment_format,
    "assignment_create": _validate_assignment_format,
}


def check_parameter_format(safety_input: InnerSafetyInput) -> LayerResult:
    """Layer 3 entry point: do present fields satisfy format rules?

    This layer assumes Layer 2 (presence) already passed — it only
    validates the format of fields that ARE present, never re-flags
    missing ones. Same defensive contract: unknown tool → DENY.
    """
    t0 = perf_counter()
    tool_name = safety_input.tool_name
    args = safety_input.tool_args or {}

    validator = _FORMAT_VALIDATORS.get(tool_name)
    if validator is None:
        return _result(
            InnerLayerName.PARAMETER_FORMAT,
            decision=InnerSafetyDecision.DENY,
            reason_code="unknown_tool_for_format_check",
            reason_human=(
                f"No format-validation policy configured for tool '{tool_name}'."
            ),
            metadata={"tool": tool_name},
            t0=t0,
        )

    errors = validator(args)
    if errors:
        return _result(
            InnerLayerName.PARAMETER_FORMAT,
            decision=InnerSafetyDecision.DENY,
            reason_code="invalid_arg_format",
            reason_human="; ".join(errors),
            metadata={"tool": tool_name, "errors": errors},
            t0=t0,
        )

    return _result(
        InnerLayerName.PARAMETER_FORMAT,
        decision=InnerSafetyDecision.ALLOW,
        reason_code="format_checks_passed",
        reason_human="",
        metadata={"tool": tool_name},
        t0=t0,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _result(
    layer: InnerLayerName,
    *,
    decision: InnerSafetyDecision,
    reason_code: str,
    reason_human: str,
    metadata: dict,
    t0: float,
) -> LayerResult:
    return LayerResult(
        layer=layer,
        decision=decision,
        reason_code=reason_code,
        reason_human=reason_human,
        latency_ms=int((perf_counter() - t0) * 1000),
        metadata=metadata,
    )
