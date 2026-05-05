# ADR 004: LLM at the ends, symbolic in the middle

**Status:** Accepted
**Date:** 2026-05

## Context

Several reasoning steps in the planner — schedule conflict detection,
prereq chain validation, credit counting, set-diff over degree
requirements — are tasks LLMs are systematically unreliable at:

- **Interval overlap** ("Tuesday 11:40–12:55 vs Tuesday 12:00–13:15"):
  LLMs confidently miss the 55-minute conflict.
- **Prereq DAG with OR branches** ("CS3134 OR CS3210 as substitute"):
  accuracy drops sharply past 2–3 hops.
- **Conditional credit counting** ("4 credits in CS, 3 in
  Engineering"): LLM arithmetic is fragile.

These are five-line algorithms. Delegating them to a probabilistic model
trades hallucination risk for flexibility we don't actually need.

## Decision

The planner places the LLM at the **two ends** of the pipeline:

- **Front:** `make_plan()` — natural-language → typed `Plan` DAG
- **Back:** `explain_schedule_recommendation` reasoning template —
  typed schedule → human-readable explanation

The middle is **deterministic symbolic code**:

| Task | Module |
|---|---|
| Set diff over degree requirements | `reasoning/gap_analysis.py:compute_unsatisfied()` |
| Interval overlap conflict detection | `schemas/query_outputs.py:MeetingTime.overlaps()` |
| Schedule CSP (backtracking + filters) | `reasoning/solver.py:ConstraintSolver` |
| Prereq filter | `ConstraintSolver._meets_hard_constraints()` |

The plan-step schema makes this routing explicit: a step with
`agent_type=constraint_solver` dispatches to the solver, not an LLM.

## Consequences

- Hallucination surface area collapses to two LLM calls per planning
  request. Both are observable; both have typed boundaries.
- Adding a new constraint type adds ~10 lines to the solver, not a new
  prompt to a model.
- The "right tool for the right job" claim has a concrete file mapping
  any reviewer can verify.

## Alternatives considered

- **LLM with function calling for the symbolic work.** Rejected — moves
  the routing decision back into the LLM (which may decline to call the
  function and just guess).
- **Pure symbolic, no LLM.** Rejected — language understanding and
  explanation generation are real LLM strengths; abandoning them for
  consistency would be over-correction.
