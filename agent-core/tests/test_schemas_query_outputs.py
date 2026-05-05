"""Tests for the typed query-output schemas."""

from datetime import time

from schemas.query_outputs import (
    CourseSection,
    DegreeProgram,
    MeetingTime,
    RequirementNode,
    StudentTranscript,
    TranscriptEntry,
)


def _mt(days: list[str], start: tuple[int, int], end: tuple[int, int]) -> MeetingTime:
    return MeetingTime(days=days, start=time(*start), end=time(*end))


def test_meeting_overlap_same_day_overlapping_times():
    a = _mt(["T"], (11, 40), (12, 55))
    b = _mt(["T"], (12, 0), (13, 15))
    assert a.overlaps(b)


def test_meeting_overlap_disjoint_days():
    a = _mt(["T", "R"], (11, 40), (12, 55))
    b = _mt(["M", "W"], (11, 40), (12, 55))
    assert not a.overlaps(b)


def test_meeting_overlap_same_day_disjoint_times():
    a = _mt(["T"], (9, 0), (10, 0))
    b = _mt(["T"], (10, 0), (11, 0))
    assert not a.overlaps(b)  # touching but not overlapping


def test_transcript_completed_set():
    t = StudentTranscript(
        user_id="u1",
        entries=[
            TranscriptEntry(course_code="CS101", grade="A", credits=3, term="F24", is_passed=True),
            TranscriptEntry(course_code="CS102", grade="F", credits=3, term="F24", is_passed=False),
            TranscriptEntry(course_code="MATH100", grade="B", credits=4, term="F24", is_passed=True),
        ],
    )
    assert t.completed == {"CS101", "MATH100"}
    assert t.total_credits == 7


def test_course_section_excluded_days():
    c = CourseSection(
        course_code="CS300",
        section="001",
        term="S26",
        credits=3,
        meetings=[_mt(["F"], (10, 0), (11, 0))],
    )
    assert c.has_meeting_on_days(["F"])
    assert not c.has_meeting_on_days(["M"])


def test_degree_program_round_trip():
    p = DegreeProgram(
        major="CS",
        track="AI",
        cohort="2024-2027",
        root=RequirementNode(
            requirement_id="root",
            kind="and",
            children=[
                RequirementNode(
                    requirement_id="ai_electives",
                    kind="leaf",
                    pool=["CS401", "CS402", "CS403"],
                    need=2,
                ),
            ],
        ),
    )
    # Round-trip through JSON to confirm forward refs resolve
    p2 = DegreeProgram.model_validate(p.model_dump())
    assert p2.root.children[0].pool == ["CS401", "CS402", "CS403"]
