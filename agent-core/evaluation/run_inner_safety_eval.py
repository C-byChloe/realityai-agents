"""Inner-safety smoke eval runner.

Run: ``python -m evaluation.run_inner_safety_eval``

Iterates `inner_safety_smoke_eval.jsonl`, calls `inner_safety_check`
on each case, and produces a per-layer confusion matrix in
`evaluation/inner_safety_smoke_baseline.md`.

Unlike the outer-safety runner, **this one is fully deterministic** —
inner safety is dict lookup + regex + DB read; no LLM in the loop.
That means:
  - No `ANTHROPIC_API_KEY` requirement.
  - Verdicts are reproducible run-to-run; alignment columns should
    always show 100% on a stable rule set / mock world state.
  - Drift indicates a config edit (matrix, validators) or world-state
    change. Useful as a CI gate, not just a development signal.

Cases cover:
  - 3 ALLOW: instructor + valid grade_update / enrollment_add /
    assignment_create against the in-memory mock world state.
  - 2 Layer 1 DENY: student + grade_update; unknown-role + any tool.
  - 2 Layer 2 DENY: instructor + missing-arg grade_update / assignment.
  - 1 Layer 3 DENY: instructor + enrollment_modify with invalid action.
  - 1 Layer 4 DENY (race): instructor + add to CS401 (capacity 30/30).
  - 1 Layer 4 DENY (fail-closed): simulated DB-unavailable per ADR 006 D3.

The `simulate_db_unavailable` flag on a case triggers a monkeypatch of
`safety.inner.live_state._read_course_state` to raise ConnectionError
for that single case — exercises the D3 fail-closed path without
needing a real network failure.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from safety.inner.audit import _reset_persisted_audit_ids_for_testing
from safety.inner.node import inner_safety_check
from safety.inner.schemas import InnerSafetyDecision, InnerSafetyInput
from safety.outer.schemas import SessionContext

EVAL_PATH = Path(__file__).parent / "inner_safety_smoke_eval.jsonl"
REPORT_PATH = Path(__file__).parent / "inner_safety_smoke_baseline.md"

_LAYERS = [
    "tool_authorization",
    "parameter_presence",
    "parameter_format",
    "live_state",
]


def _load_cases() -> list[dict]:
    cases: list[dict] = []
    with open(EVAL_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _make_input(case: dict) -> InnerSafetyInput:
    return InnerSafetyInput(
        session=SessionContext(
            user_id=case["user_id"],
            session_id=f"eval-case-{case['id']}",
            user_role=case["user_role"],
        ),
        tool_name=case["tool_name"],
        tool_args=case.get("tool_args", {}),
    )


async def _run_case(case: dict) -> dict[str, Any]:
    safety_input = _make_input(case)

    # Per-case patch for fail-closed simulation. Outside this branch,
    # the real (mock) world-state store is used.
    if case.get("simulate_db_unavailable"):
        async def _fail(*_a, **_kw):
            raise ConnectionError("simulated DB outage for fail-closed eval")

        with patch("safety.inner.live_state._read_course_state", _fail):
            result = await inner_safety_check(safety_input)
    else:
        result = await inner_safety_check(safety_input)

    return {
        "id": case["id"],
        "category": case["category"],
        "tool_name": case["tool_name"],
        "expected_decision": case["expected_decision"],
        "expected_short_circuit_at": case["expected_short_circuit_at"],
        "actual_decision": result.final_decision.value,
        "actual_short_circuit_at": (
            result.short_circuited_at.value if result.short_circuited_at else None
        ),
        "actual_reason_code": result.final_reason_code,
        "layer_count_run": len(result.layer_results),
        "total_latency_ms": result.total_latency_ms,
        "audit_id": result.audit.audit_id,
        "audit_decision": result.audit.decision.value,
    }


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).parent.parent.parent),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def _decision_match_rate(results: list[dict], expected: str) -> tuple[int, int]:
    cases = [r for r in results if r["expected_decision"] == expected]
    correct = sum(1 for r in cases if r["actual_decision"] == expected)
    return correct, len(cases)


def _layer_attribution_table(results: list[dict]) -> str:
    """Per-layer table: cases short-circuited there + were they expected to be?"""
    lines = [
        "| Layer | Caught (actual SC) | Expected to catch | Aligned (caught + expected here) |",
        "|-------|:------------------:|:-----------------:|:--------------------------------:|",
    ]
    for layer in _LAYERS:
        caught = sum(1 for r in results if r["actual_short_circuit_at"] == layer)
        expected = sum(1 for r in results if r["expected_short_circuit_at"] == layer)
        aligned = sum(
            1
            for r in results
            if r["actual_short_circuit_at"] == layer
            and r["expected_short_circuit_at"] == layer
        )
        lines.append(f"| {layer} | {caught} | {expected} | {aligned} |")
    no_sc_actual = sum(1 for r in results if r["actual_short_circuit_at"] is None)
    no_sc_expected = sum(1 for r in results if r["expected_short_circuit_at"] is None)
    no_sc_aligned = sum(
        1
        for r in results
        if r["actual_short_circuit_at"] is None
        and r["expected_short_circuit_at"] is None
    )
    lines.append(
        f"| (no short-circuit, all-allow) | {no_sc_actual} | {no_sc_expected} | {no_sc_aligned} |"
    )
    return "\n".join(lines)


def _per_case_table(results: list[dict]) -> str:
    lines = [
        "| ID | Category | Tool | Expected | Actual | SC at (exp → act) | OK |",
        "|----|----------|------|----------|--------|-------------------|:--:|",
    ]
    for r in results:
        decision_match = r["actual_decision"] == r["expected_decision"]
        sc_match = r["actual_short_circuit_at"] == r["expected_short_circuit_at"]
        check = "PASS" if decision_match and sc_match else "FAIL"
        sc_str = f"{r['expected_short_circuit_at'] or '-'} -> {r['actual_short_circuit_at'] or '-'}"
        cat = r["category"][:32]
        lines.append(
            f"| {r['id']} | {cat} | {r['tool_name']} | "
            f"{r['expected_decision']} | {r['actual_decision']} | {sc_str} | {check} |"
        )
    return "\n".join(lines)


def _audit_invariant_check(results: list[dict]) -> tuple[int, int]:
    """ADR 006 D4: every case must have an audit_id, even on DENY."""
    total = len(results)
    with_audit = sum(1 for r in results if r["audit_id"])
    return with_audit, total


def _build_report(results: list[dict]) -> str:
    n = len(results)
    allow_correct, allow_total = _decision_match_rate(results, "allow")
    deny_correct, deny_total = _decision_match_rate(results, "deny")

    not_allow = [r for r in results if r["expected_decision"] != "allow"]
    blocked = sum(1 for r in not_allow if r["actual_decision"] != "allow")

    audit_present, audit_total = _audit_invariant_check(results)

    avg_latency = sum(r["total_latency_ms"] for r in results) / n if n else 0

    decision_perfect = sum(
        1 for r in results if r["actual_decision"] == r["expected_decision"]
    )
    sc_perfect = sum(
        1
        for r in results
        if r["actual_short_circuit_at"] == r["expected_short_circuit_at"]
    )

    return f"""# Inner Safety Smoke Eval — Baseline

**Generated:** {datetime.now(timezone.utc).isoformat()}
**Git SHA:** {_git_sha()}
**Cases:** {n}
**Avg total latency:** {avg_latency:.2f} ms

## Decision accuracy

| Expected | Correct / Total | Match rate |
|----------|:---------------:|:----------:|
| ALLOW | {allow_correct}/{allow_total} | {allow_correct / max(allow_total, 1) * 100:.0f}% |
| DENY | {deny_correct}/{deny_total} | {deny_correct / max(deny_total, 1) * 100:.0f}% |

**Decision-perfect:** {decision_perfect}/{n}
**Short-circuit-perfect (caught at expected layer):** {sc_perfect}/{n}

## Defense-in-depth coverage

Of the {len(not_allow)} cases that should NOT be ALLOW, **{blocked} got
blocked** (by any layer). The layered design is working iff this
number == {len(not_allow)}.

  - Coverage: **{blocked}/{len(not_allow)}** ({blocked / max(len(not_allow), 1) * 100:.0f}%)

## Audit invariant (ADR 006 D4)

Every case must produce a non-empty audit_id, regardless of decision.

  - Cases with audit_id: **{audit_present}/{audit_total}**

## Per-layer attribution

{_layer_attribution_table(results)}

A row's "Aligned" column shows how many cases were caught **at the
expected layer**. Inner safety is fully deterministic, so misalignment
indicates a real bug:
  - "Aligned" < "Caught" means the wrong layer is firing (e.g., Layer 3
    reports "missing field" when Layer 2 should have).
  - "Caught" < "Expected" means the layer is missing a check that
    should fire.

## Per-case detail

{_per_case_table(results)}

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
"""


async def main() -> int:
    cases = _load_cases()

    # Reset audit dedup set so each run starts clean (otherwise a prior
    # interactive session's persisted IDs collide with case IDs).
    _reset_persisted_audit_ids_for_testing()

    # Run sequentially — cases are tiny and the simulate_db_unavailable
    # patch context manager doesn't compose with parallel tasks safely.
    results: list[dict] = []
    for case in cases:
        results.append(await _run_case(case))

    report = _build_report(results)
    REPORT_PATH.write_text(report)

    n = len(results)
    correct_decision = sum(
        1 for r in results if r["actual_decision"] == r["expected_decision"]
    )
    correct_sc = sum(
        1
        for r in results
        if r["actual_short_circuit_at"] == r["expected_short_circuit_at"]
    )
    not_allow = [r for r in results if r["expected_decision"] != "allow"]
    blocked = sum(1 for r in not_allow if r["actual_decision"] != "allow")

    print(f"Inner-safety smoke eval — {n} cases (deterministic)")
    print(f"  Decision match: {correct_decision}/{n}")
    print(f"  Layer attribution match: {correct_sc}/{n}")
    print(f"  Defense-in-depth coverage: {blocked}/{len(not_allow)}")
    print(f"  Report: {REPORT_PATH}")

    # Non-zero exit if any case failed — useful as a CI gate.
    return 0 if (correct_decision == n and correct_sc == n) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
