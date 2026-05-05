"""Typed query-agent output schemas.

Query Agent returns source-discriminated structured data so downstream
reasoning steps can operate on Pydantic objects without re-parsing strings.
This collapses the LLM's responsibility surface: structured operations
(set diff, conflict detection, prereq traversal) move to deterministic code.
"""

from __future__ import annotations

from datetime import time
from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Transcript (Canvas-sourced)
# ---------------------------------------------------------------------------


class TranscriptEntry(BaseModel):
    course_code: str
    grade: str
    credits: int
    term: str
    is_passed: bool


class StudentTranscript(BaseModel):
    user_id: str
    entries: list[TranscriptEntry] = Field(default_factory=list)

    @property
    def completed(self) -> set[str]:
        """Course codes that the student has passed."""
        return {e.course_code for e in self.entries if e.is_passed}

    @property
    def total_credits(self) -> int:
        return sum(e.credits for e in self.entries if e.is_passed)


# ---------------------------------------------------------------------------
# Degree program (degree-DB sourced)
# ---------------------------------------------------------------------------


class RequirementNode(BaseModel):
    """Tree node for degree requirements.

    kind=leaf: a concrete course pool with `need` courses required from `pool`.
    kind=and:  all `children` must be satisfied.
    kind=or:   at least one child must be satisfied.
    """

    requirement_id: str
    kind: Literal["leaf", "and", "or"]
    name: str = ""
    pool: list[str] = Field(default_factory=list)
    need: int = 0
    children: list["RequirementNode"] = Field(default_factory=list)

    @field_validator("children")
    @classmethod
    def _leaf_has_no_children(cls, v, info):
        kind = info.data.get("kind")
        if kind == "leaf" and v:
            raise ValueError("leaf requirement cannot have children")
        return v


class DegreeProgram(BaseModel):
    major: str
    track: str | None = None
    cohort: str
    root: RequirementNode


class UnsatisfiedRequirement(BaseModel):
    """Output of gap analysis: what the student still needs to take."""

    requirement_id: str
    name: str
    pool: list[str]
    need: int
    already_satisfied_by: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Course catalog (catalog-DB sourced)
# ---------------------------------------------------------------------------


class MeetingTime(BaseModel):
    days: list[Literal["M", "T", "W", "R", "F", "S", "U"]]
    start: time
    end: time

    def overlaps(self, other: "MeetingTime") -> bool:
        if not set(self.days) & set(other.days):
            return False
        return self.start < other.end and other.start < self.end


class CourseSection(BaseModel):
    course_code: str
    section: str
    term: str
    credits: int
    instructor: str = ""
    meetings: list[MeetingTime] = Field(default_factory=list)
    prereqs: list[str] = Field(default_factory=list)

    def has_meeting_on_days(self, excluded_days: list[str]) -> bool:
        excluded = set(excluded_days)
        return any(set(m.days) & excluded for m in self.meetings)


# ---------------------------------------------------------------------------
# Syllabus chunks (RAG-sourced) — non-structured by nature, but typed wrapper
# ---------------------------------------------------------------------------


class SyllabusChunk(BaseModel):
    chunk_id: str
    course_id: str
    content: str
    score: float
    source: str = "syllabus_rag"


# ---------------------------------------------------------------------------
# Discriminated union over all query-agent output shapes
# ---------------------------------------------------------------------------


QueryOutput = Union[
    StudentTranscript,
    DegreeProgram,
    list[CourseSection],
    list[UnsatisfiedRequirement],
    list[SyllabusChunk],
]


# Forward-ref resolution for self-referential RequirementNode
RequirementNode.model_rebuild()
