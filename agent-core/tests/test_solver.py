"""Tests for the schedule constraint solver."""

from datetime import time

from reasoning.gap_analysis import compute_unsatisfied
from reasoning.solver import ConstraintSolver
from schemas.query_outputs import (
    CourseSection,
    DegreeProgram,
    MeetingTime,
    RequirementNode,
    StudentTranscript,
    TranscriptEntry,
)
from schemas.solver import ScheduleConstraints


def _mt(days, hh1, mm1, hh2, mm2):
    return MeetingTime(days=days, start=time(hh1, mm1), end=time(hh2, mm2))


def _sec(code, days, start, end, prereqs=None, credits=3):
    return CourseSection(
        course_code=code,
        section="001",
        term="S26",
        credits=credits,
        meetings=[_mt(days, *start, *end)],
        prereqs=prereqs or [],
    )


def test_solver_excludes_friday_courses():
    candidates = [
        _sec("CS401", ["T", "R"], (11, 0), (12, 15)),
        _sec("CS402", ["F"], (10, 0), (11, 0)),
        _sec("CS403", ["M", "W"], (13, 0), (14, 15)),
    ]
    solver = ConstraintSolver()
    constraints = ScheduleConstraints(days_excluded=["F"], min_courses=2, max_courses=2)
    options = solver.solve(candidates, completed=set(), constraints=constraints)

    assert options
    for opt in options:
        codes = {c.course_code for c in opt.courses}
        assert "CS402" not in codes


def test_solver_drops_combinations_with_time_conflicts():
    a = _sec("CS401", ["T"], (11, 40), (12, 55))
    b = _sec("CS402", ["T"], (12, 0), (13, 15))  # conflicts with a
    c = _sec("CS403", ["W"], (11, 0), (12, 15))  # different day
    solver = ConstraintSolver()
    constraints = ScheduleConstraints(min_courses=2, max_courses=2)
    options = solver.solve([a, b, c], completed=set(), constraints=constraints)

    combos = [tuple(sorted(s.course_code for s in o.courses)) for o in options]
    assert ("CS401", "CS402") not in combos
    assert ("CS401", "CS403") in combos


def test_solver_filters_by_unmet_prereqs():
    a = _sec("CS401", ["T"], (11, 0), (12, 15), prereqs=["CS201"])
    b = _sec("CS402", ["W"], (11, 0), (12, 15), prereqs=[])
    solver = ConstraintSolver()
    constraints = ScheduleConstraints(min_courses=1, max_courses=2)

    no_prereq = solver.solve([a, b], completed=set(), constraints=constraints)
    codes_no = {c.course_code for opt in no_prereq for c in opt.courses}
    assert "CS401" not in codes_no
    assert "CS402" in codes_no

    with_prereq = solver.solve([a, b], completed={"CS201"}, constraints=constraints)
    codes_with = {c.course_code for opt in with_prereq for c in opt.courses}
    assert "CS401" in codes_with


def test_gap_analysis_simple():
    transcript = StudentTranscript(
        user_id="u1",
        entries=[
            TranscriptEntry(course_code="CS401", grade="A", credits=3, term="F25", is_passed=True),
        ],
    )
    program = DegreeProgram(
        major="CS",
        track="AI",
        cohort="2024-2027",
        root=RequirementNode(
            requirement_id="root",
            kind="leaf",
            name="AI electives",
            pool=["CS401", "CS402", "CS403"],
            need=2,
        ),
    )
    unmet = compute_unsatisfied(program, transcript)
    assert len(unmet) == 1
    assert unmet[0].need == 1
    assert unmet[0].already_satisfied_by == ["CS401"]


def test_gap_analysis_satisfied_requirement_not_returned():
    transcript = StudentTranscript(
        user_id="u1",
        entries=[
            TranscriptEntry(course_code="CS401", grade="A", credits=3, term="F25", is_passed=True),
            TranscriptEntry(course_code="CS402", grade="A", credits=3, term="F25", is_passed=True),
        ],
    )
    program = DegreeProgram(
        major="CS",
        track="AI",
        cohort="2024-2027",
        root=RequirementNode(
            requirement_id="root",
            kind="leaf",
            pool=["CS401", "CS402", "CS403"],
            need=2,
        ),
    )
    unmet = compute_unsatisfied(program, transcript)
    assert unmet == []
