"""Outer-safety smoke eval runner.

Run: ``python -m evaluation.run_outer_safety_eval``

Iterates `outer_safety_smoke_eval.jsonl`, calls `outer_safety_check`
on each case, and produces a per-tier confusion matrix in
`evaluation/outer_safety_smoke_baseline.md`.

The report answers three questions in addition to overall accuracy:

  1. **Per-tier attribution** — Of the cases that should be caught at
     each tier (RBAC / static rules / injection guard), how many were
     actually caught at the expected tier? A misalignment (e.g., the
     injection guard catching a lexical pattern that should have been a
     static rule) is a signal that the rule set has gaps.

  2. **Defense-in-depth coverage** — Of the cases that should NOT be
     ALLOW, how many got blocked by *any* tier? This is the safety-net
     metric: as long as something catches each violation, the layered
     design is doing its job.

  3. **Throughput** — Of the cases that SHOULD be ALLOW, how many were
     correctly allowed? FLAG-on-allow is a false positive that costs
     human review; DENY-on-allow is a hard failure.

Phase 7 made this runner **deterministic**: Tier 3 is now Prompt Guard
(a specialized injection classifier) instead of Claude. The runner uses
`get_default_prompt_guard()` which falls back to the heuristic client
when transformers isn't installed — so this script can run in CI
without an `ANTHROPIC_API_KEY` and without GPU. Suitable as a CI gate.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from safety.outer.node import outer_safety_check
from safety.outer.prompt_guard import get_default_prompt_guard
from safety.outer.schemas import SafetyDecision

EVAL_PATH = Path(__file__).parent / "outer_safety_smoke_eval.jsonl"
REPORT_PATH = Path(__file__).parent / "outer_safety_smoke_baseline.md"


def _load_cases() -> list[dict]:
    cases: list[dict] = []
    with open(EVAL_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _make_state(case: dict) -> dict:
    """Project an eval case into the AgentState shape outer_safety_check reads."""
    return {
        "messages": [HumanMessage(content=case["user_query"])],
        "intent": case["intent"],
        "user_role": case["user_role"],
        "user_id": "eval-user",
        "session_id": f"eval-case-{case['id']}",
    }


async def _run_case(case: dict, prompt_guard) -> dict[str, Any]:
    state = _make_state(case)
    out = await outer_safety_check(state, prompt_guard=prompt_guard)
    result = out["outer_safety_result"]
    return {
        "id": case["id"],
        "category": case["category"],
        "user_query": case["user_query"],
        "expected_decision": case["expected_decision"],
        "expected_short_circuit_at": case["expected_short_circuit_at"],
        "actual_decision": result.final_decision.value,
        "actual_short_circuit_at": (
            result.short_circuited_at.value if result.short_circuited_at else None
        ),
        "actual_reason_code": result.final_reason_code,
        "tier_count_run": len(result.tier_results),
        "total_latency_ms": result.total_latency_ms,
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


def _tier_attribution_table(results: list[dict]) -> str:
    """Per-tier table: cases short-circuited there + were they expected to be?"""
    tiers = ["rbac", "static_rules", "injection_guard"]
    lines = [
        "| Tier | Caught (actual SC) | Expected to catch | Aligned (caught + expected here) |",
        "|------|:------------------:|:-----------------:|:--------------------------------:|",
    ]
    for tier in tiers:
        caught = sum(1 for r in results if r["actual_short_circuit_at"] == tier)
        expected = sum(1 for r in results if r["expected_short_circuit_at"] == tier)
        aligned = sum(
            1
            for r in results
            if r["actual_short_circuit_at"] == tier
            and r["expected_short_circuit_at"] == tier
        )
        lines.append(f"| {tier} | {caught} | {expected} | {aligned} |")
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
        "| ID | Category | Expected | Actual | SC at (exp → act) | ✓ |",
        "|----|----------|----------|--------|-------------------|:-:|",
    ]
    for r in results:
        decision_match = r["actual_decision"] == r["expected_decision"]
        sc_match = r["actual_short_circuit_at"] == r["expected_short_circuit_at"]
        check = "✓" if decision_match and sc_match else "✗"
        sc_str = f"{r['expected_short_circuit_at'] or '∅'} → {r['actual_short_circuit_at'] or '∅'}"
        # Truncate query for table width
        cat = r["category"][:24]
        lines.append(
            f"| {r['id']} | {cat} | {r['expected_decision']} | "
            f"{r['actual_decision']} | {sc_str} | {check} |"
        )
    return "\n".join(lines)


def _build_report(results: list[dict]) -> str:
    n = len(results)
    allow_correct, allow_total = _decision_match_rate(results, "allow")
    deny_correct, deny_total = _decision_match_rate(results, "deny")
    flag_correct, flag_total = _decision_match_rate(results, "flag_for_review")

    not_allow = [r for r in results if r["expected_decision"] != "allow"]
    blocked = sum(1 for r in not_allow if r["actual_decision"] != "allow")

    avg_latency = sum(r["total_latency_ms"] for r in results) / n if n else 0

    return f"""# Outer Safety Smoke Eval — Baseline

**Generated:** {datetime.now(timezone.utc).isoformat()}
**Git SHA:** {_git_sha()}
**Cases:** {n}
**Avg total latency:** {avg_latency:.1f} ms

## Decision accuracy

| Expected | Correct / Total | Match rate |
|----------|:---------------:|:----------:|
| ALLOW | {allow_correct}/{allow_total} | {allow_correct / max(allow_total, 1) * 100:.0f}% |
| DENY | {deny_correct}/{deny_total} | {deny_correct / max(deny_total, 1) * 100:.0f}% |
| FLAG_FOR_REVIEW | {flag_correct}/{flag_total} | {flag_correct / max(flag_total, 1) * 100:.0f}% |

## Defense-in-depth coverage

Of the {len(not_allow)} cases that should NOT be ALLOW, **{blocked} got
blocked** (by any tier). The layered design is working iff this number
== {len(not_allow)}.

  - Coverage: **{blocked}/{len(not_allow)}** ({blocked / max(len(not_allow), 1) * 100:.0f}%)

## Per-tier attribution

{_tier_attribution_table(results)}

A row's "Aligned" column shows how many cases were caught **at the
expected tier**. A misalignment usually signals a rule-set gap: e.g., if
RBAC caught 0 cases but 2 were expected, the matrix is wrong; if Tier 3
caught a case the static rules should have caught, the static rules
need a new pattern.

## Per-case detail

{_per_case_table(results)}

## Methodology + honest limitations

- 10 cases, hand-curated to cover each tier's expected catch + the
  Phase 4 (student-action defer-to-inner) and Phase 7 (Prompt Guard)
  architectural decisions.
- Tier 3 is Prompt Guard (heuristic fallback in this dev env;
  `LocalPromptGuardClient` with the real model in production). With the
  heuristic, the runner is fully deterministic and suitable as a CI
  gate. With the real model, verdicts may vary slightly run-to-run.
- ALLOW cases are benign by construction; we do NOT have adversarial
  cases that should be ALLOW (i.e., injection-resistant authentic
  requests). Production-grade adversarial eval is out of scope here.
- Tier 1 + Tier 2 are deterministic; their alignment column should
  always be 100% on a stable rule set. Drift indicates a config edit.
- Coverage less than 100% means a violation reached the agent — that
  is the metric to watch first.
"""


async def main() -> int:
    cases = _load_cases()
    prompt_guard = get_default_prompt_guard()
    results = await asyncio.gather(*[_run_case(c, prompt_guard) for c in cases])

    report = _build_report(list(results))
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
    pg_kind = type(prompt_guard).__name__

    print(f"Outer-safety smoke eval — {n} cases (deterministic, {pg_kind})")
    print(f"  Decision match: {correct_decision}/{n}")
    print(f"  Tier attribution match: {correct_sc}/{n}")
    print(f"  Report: {REPORT_PATH}")

    # Non-zero exit on any case failure — usable as a CI gate. Tier 1 + 2
    # are deterministic by definition; Tier 3's heuristic fallback is also
    # deterministic. With LocalPromptGuardClient (real model) verdicts can
    # vary slightly run-to-run; the report doc notes that limitation.
    return 0 if (correct_decision == n and correct_sc == n) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
