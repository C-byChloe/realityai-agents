"""Schedule constraint solver — backtracking search, no LLM.

Implements the canonical "plan next semester avoiding Fridays" example.
LLM is responsible for *extracting* constraints from the user's natural
language; this solver is responsible for *satisfying* them.

Why this is not LLM work:
  - Interval overlap (schedule conflict): LLM hallucinates time math
  - Prereq chain (especially with OR branches): LLM accuracy drops fast
  - Credit counting: arithmetic over conditional rules is fragile
"""

from __future__ import annotations

from itertools import combinations

from schemas.query_outputs import CourseSection, MeetingTime
from schemas.solver import ScheduleConstraints, ScheduleOption


class ConstraintSolver:
    """Backtracking schedule solver.

    POC scale: candidates < ~50 sections, choose 3-5. Backtracking with
    early pruning is sufficient. For larger scale move to z3 / or-tools.
    """

    def solve(
        self,
        candidates: list[CourseSection],
        completed: set[str],
        constraints: ScheduleConstraints,
    ) -> list[ScheduleOption]:
        # 1. Hard-constraint filter: drop sections that violate excluded days
        #    or whose prereqs are not met.
        valid = [
            c for c in candidates
            if self._meets_hard_constraints(c, completed, constraints)
        ]

        # 2. Generate candidate combinations of size [min_courses, max_courses].
        results: list[ScheduleOption] = []
        sizes = range(constraints.min_courses, constraints.max_courses + 1)
        seen_combos: set[tuple[str, ...]] = set()

        for size in sizes:
            for combo in combinations(valid, size):
                key = tuple(sorted(c.course_code for c in combo))
                if key in seen_combos:
                    continue
                seen_combos.add(key)

                if self._has_time_conflict(combo):
                    continue
                option = ScheduleOption.from_courses(list(combo))
                if not self._meets_credit_bounds(option, constraints):
                    continue
                results.append(option)

        # 3. Rank by user preferences (deterministic; LLM may re-rank later).
        return self._rank(results, constraints)

    @staticmethod
    def _meets_hard_constraints(
        section: CourseSection,
        completed: set[str],
        constraints: ScheduleConstraints,
    ) -> bool:
        if section.has_meeting_on_days(constraints.days_excluded):
            return False
        if any(p not in completed for p in section.prereqs):
            return False
        if constraints.time_range is not None:
            lo, hi = constraints.time_range
            for m in section.meetings:
                if m.start < lo or m.end > hi:
                    return False
        return True

    @staticmethod
    def _has_time_conflict(combo: tuple[CourseSection, ...]) -> bool:
        meetings: list[MeetingTime] = [m for c in combo for m in c.meetings]
        for i in range(len(meetings)):
            for j in range(i + 1, len(meetings)):
                if meetings[i].overlaps(meetings[j]):
                    return True
        return False

    @staticmethod
    def _meets_credit_bounds(
        option: ScheduleOption,
        constraints: ScheduleConstraints,
    ) -> bool:
        if constraints.min_credits is not None and option.total_credits < constraints.min_credits:
            return False
        if constraints.max_credits is not None and option.total_credits > constraints.max_credits:
            return False
        return True

    @staticmethod
    def _rank(
        options: list[ScheduleOption],
        constraints: ScheduleConstraints,
    ) -> list[ScheduleOption]:
        # Default ranking: more courses = better, ties broken by total credits.
        for o in options:
            o.rank_score = float(len(o.courses)) + 0.01 * o.total_credits
        return sorted(options, key=lambda o: o.rank_score, reverse=True)
