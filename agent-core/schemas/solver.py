"""Schemas for the constraint solver — input constraints and output options.

Pure schema. Solver logic lives in `reasoning/solver.py` so the schema can
be imported without pulling in the algorithm.
"""

from __future__ import annotations

from datetime import time

from pydantic import BaseModel, Field

from schemas.query_outputs import CourseSection


class ScheduleConstraints(BaseModel):
    """Hard + soft constraints for the schedule CSP.

    Hard constraints are enforced by the solver (filter + backtracking).
    Soft preferences are passed through and used for ranking.
    """

    days_excluded: list[str] = Field(default_factory=list)
    time_range: tuple[time, time] | None = None
    min_courses: int = 3
    max_courses: int = 5
    min_credits: int | None = None
    max_credits: int | None = None
    preferences: dict = Field(default_factory=dict)


class ScheduleOption(BaseModel):
    """One valid schedule produced by the solver."""

    courses: list[CourseSection]
    total_credits: int
    rank_score: float = 0.0

    @classmethod
    def from_courses(cls, courses: list[CourseSection]) -> "ScheduleOption":
        return cls(
            courses=courses,
            total_credits=sum(c.credits for c in courses),
        )
