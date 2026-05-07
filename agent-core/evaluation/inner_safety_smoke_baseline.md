# Inner Safety Smoke Eval — Baseline

**Generated:** 2026-05-07T23:06:40.221138+00:00
**Git SHA:** b539e04
**Cases:** 10
**Avg total latency:** 0.40 ms

## Decision accuracy

| Expected | Correct / Total | Match rate |
|----------|:---------------:|:----------:|
| ALLOW | 3/3 | 100% |
| DENY | 7/7 | 100% |

**Decision-perfect:** 10/10
**Short-circuit-perfect (caught at expected layer):** 10/10

## Defense-in-depth coverage

Of the 7 cases that should NOT be ALLOW, **7 got
blocked** (by any layer). The layered design is working iff this
number == 7.

  - Coverage: **7/7** (100%)

## Audit invariant (ADR 006 D4)

Every case must produce a non-empty audit_id, regardless of decision.

  - Cases with audit_id: **10/10**

## Per-layer attribution

| Layer | Caught (actual SC) | Expected to catch | Aligned (caught + expected here) |
|-------|:------------------:|:-----------------:|:--------------------------------:|
| tool_authorization | 2 | 2 | 2 |
| parameter_presence | 2 | 2 | 2 |
| parameter_format | 1 | 1 | 1 |
| live_state | 2 | 2 | 2 |
| (no short-circuit, all-allow) | 3 | 3 | 3 |

A row's "Aligned" column shows how many cases were caught **at the
expected layer**. Inner safety is fully deterministic, so misalignment
indicates a real bug:
  - "Aligned" < "Caught" means the wrong layer is firing (e.g., Layer 3
    reports "missing field" when Layer 2 should have).
  - "Caught" < "Expected" means the layer is missing a check that
    should fire.

## Per-case detail

| ID | Category | Tool | Expected | Actual | SC at (exp → act) | OK |
|----|----------|------|----------|--------|-------------------|:--:|
| 1 | allow_instructor_grade_update | grade_update | allow | allow | - -> - | PASS |
| 2 | allow_instructor_enrollment_add | enrollment_modify | allow | allow | - -> - | PASS |
| 3 | allow_instructor_assignment_crea | assignment_create | allow | allow | - -> - | PASS |
| 4 | deny_l1_student_grade_update | grade_update | deny | deny | tool_authorization -> tool_authorization | PASS |
| 5 | deny_l1_unknown_role | grade_update | deny | deny | tool_authorization -> tool_authorization | PASS |
| 6 | deny_l2_grade_update_missing_fie | grade_update | deny | deny | parameter_presence -> parameter_presence | PASS |
| 7 | deny_l2_assignment_missing_due_d | assignment_create | deny | deny | parameter_presence -> parameter_presence | PASS |
| 8 | deny_l3_enrollment_action_invali | enrollment_modify | deny | deny | parameter_format -> parameter_format | PASS |
| 9 | deny_l4_enrollment_to_full_cours | enrollment_modify | deny | deny | live_state -> live_state | PASS |
| 10 | deny_l4_db_unavailable_fails_clo | enrollment_modify | deny | deny | live_state -> live_state | PASS |

## Methodology + honest limitations

- 10 cases, hand-curated to cover each layer's expected catch + the
  fail-closed path (D3) and audit invariant (D4).
- Fully deterministic — no LLM call. Mock world state in
  `safety/inner/live_state.py:_MOCK_COURSE_STATE` is the single source
  of truth for Layer 4 race-condition cases.
- Case 10 monkeypatches `_read_course_state` per-case to simulate a
  ConnectionError; this exercises the fail-closed branch without
  needing a real DB outage.
- Layer 4 covers tool-specific live-state checks
  (capacity / enrollment record / instructor ownership). Cases 2 + 3 +
  9 cover the per-tool happy + race-condition variants. A more
  exhaustive eval would add a grade_window_closed case (MATH200) and
  a drop-when-not-enrolled case; deferred to Phase 7.
- Coverage less than 100% means a violation reached gRPC — that is
  the metric to watch first. Treat it as a CI gate (not a soft signal
  like the outer-safety eval, which has LLM variance).
