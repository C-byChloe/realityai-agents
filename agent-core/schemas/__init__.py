"""Typed schemas for the reasoning layer.

These schemas form the explicit contracts between planning, query, reasoning,
and constraint-solver steps. They lock in plan-time decisions so dispatch is
deterministic and reasoning steps can operate on structured objects rather
than re-parsing natural-language strings.
"""

from schemas.plan import (
    AgentType,
    Plan,
    PlanStep,
    QuerySource,
    SolverType,
)
from schemas.query_outputs import (
    CourseSection,
    DegreeProgram,
    MeetingTime,
    QueryOutput,
    RequirementNode,
    StudentTranscript,
    SyllabusChunk,
    TranscriptEntry,
    UnsatisfiedRequirement,
)
from schemas.solver import (
    ScheduleConstraints,
    ScheduleOption,
)

__all__ = [
    "AgentType",
    "CourseSection",
    "DegreeProgram",
    "MeetingTime",
    "Plan",
    "PlanStep",
    "QueryOutput",
    "QuerySource",
    "RequirementNode",
    "ScheduleConstraints",
    "ScheduleOption",
    "SolverType",
    "StudentTranscript",
    "SyllabusChunk",
    "TranscriptEntry",
    "UnsatisfiedRequirement",
]
