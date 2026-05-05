"""Unified evaluation CLI.

Run: ``python -m evaluation.run``

Outputs:
  evaluation/baseline_metrics.json — committed snapshot of the current baseline.

Sections:
  retrieval     — Precision@5 / Recall@5 over `ground_truth.json` for
                  vector-only vs. hybrid (RRF) retrieval. No LLM calls.
  solver        — Wall-clock runtime of the schedule-CSP solver on the
                  canonical avoid-Fridays scenario (n=50 trials).
  plan_subgraph — End-to-end latency of the LangGraph plan-step subgraph
                  on the canonical 6-step plan, using a no-network mock
                  LLM (so latency reflects orchestration cost only,
                  not API roundtrip).

Methodology and limitations live in `evaluation/README.md`.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage, HumanMessage

from evaluation.precision_harness import run_baseline_vs_hybrid


CANONICAL_AVOID_FRIDAYS_PLAN: dict = {
    "reasoning": "Plan next semester avoiding Friday classes.",
    "steps": [
        {"step_id": 1, "description": "Get completed courses", "depends_on": [],
         "agent_type": "query", "query_source": "canvas",
         "query_params": {"user_id": "u1"}},
        {"step_id": 2, "description": "Get AI track requirements", "depends_on": [],
         "agent_type": "query", "query_source": "degree_db",
         "query_params": {"major": "CS", "track": "AI", "cohort": "2024-2027"}},
        {"step_id": 3, "description": "Identify unsatisfied AI electives",
         "depends_on": [1, 2], "agent_type": "reasoning",
         "reasoning_inputs": [1, 2],
         "reasoning_template": "extract_unsatisfied_elective_pool"},
        {"step_id": 4, "description": "Find next-semester offerings",
         "depends_on": [3], "agent_type": "query", "query_source": "catalog_db",
         "query_params": {"term": "S26", "days_excluded": ["F"]}},
        {"step_id": 5, "description": "Solve schedule",
         "depends_on": [1, 4], "agent_type": "constraint_solver",
         "solver_type": "schedule_csp",
         "solver_inputs": {
             "candidates": "<from_step_4>",
             "completed": "<from_step_1>",
             "constraints": {"days_excluded": ["F"], "min_courses": 2, "max_courses": 3},
         }},
        {"step_id": 6, "description": "Explain recommendation",
         "depends_on": [5], "agent_type": "reasoning",
         "reasoning_inputs": [5],
         "reasoning_template": "explain_schedule_recommendation"},
    ],
}


# ---------------------------------------------------------------------------
# Section: retrieval
# ---------------------------------------------------------------------------


def section_retrieval() -> dict:
    return run_baseline_vs_hybrid()


# ---------------------------------------------------------------------------
# Section: solver runtime (deterministic, no LLM)
# ---------------------------------------------------------------------------


def section_solver(trials: int = 50) -> dict:
    from datetime import time as dtime

    from reasoning.solver import ConstraintSolver
    from schemas.query_outputs import CourseSection, MeetingTime
    from schemas.solver import ScheduleConstraints

    def _mt(days, h1, m1, h2, m2):
        return MeetingTime(days=days, start=dtime(h1, m1), end=dtime(h2, m2))

    candidates = [
        CourseSection(course_code="CS401", section="001", term="S26", credits=3,
                      meetings=[_mt(["T", "R"], 11, 40, 12, 55)]),
        CourseSection(course_code="CS402", section="001", term="S26", credits=3,
                      meetings=[_mt(["F"], 10, 0, 12, 30)]),
        CourseSection(course_code="CS403", section="001", term="S26", credits=3,
                      meetings=[_mt(["M", "W"], 13, 0, 14, 15)]),
        CourseSection(course_code="CS404", section="001", term="S26", credits=3,
                      meetings=[_mt(["T", "R"], 14, 30, 15, 45)]),
        CourseSection(course_code="MATH210", section="001", term="S26", credits=4,
                      meetings=[_mt(["M", "W", "F"], 9, 0, 9, 50)]),
    ]
    constraints = ScheduleConstraints(
        days_excluded=["F"], min_courses=2, max_courses=3,
    )

    solver = ConstraintSolver()
    runtimes_ms: list[float] = []
    for _ in range(trials):
        t0 = time.perf_counter()
        options = solver.solve(candidates, completed=set(), constraints=constraints)
        runtimes_ms.append((time.perf_counter() - t0) * 1000)

    return {
        "trials": trials,
        "candidates": len(candidates),
        "options_found": len(options),
        "runtime_ms_p50": round(statistics.median(runtimes_ms), 3),
        "runtime_ms_p95": round(_percentile(runtimes_ms, 95), 3),
        "runtime_ms_mean": round(statistics.mean(runtimes_ms), 3),
    }


def _percentile(xs: list[float], pct: float) -> float:
    if not xs:
        return 0.0
    xs_sorted = sorted(xs)
    k = (len(xs_sorted) - 1) * (pct / 100)
    f = int(k)
    c = min(f + 1, len(xs_sorted) - 1)
    if f == c:
        return xs_sorted[f]
    return xs_sorted[f] * (c - k) + xs_sorted[c] * (k - f)


# ---------------------------------------------------------------------------
# Section: plan subgraph end-to-end latency (mock LLM, no network)
# ---------------------------------------------------------------------------


def _mock_llm(content: str):
    mock = AsyncMock()
    mock.ainvoke.return_value = AIMessage(content=content)
    return mock


async def _measure_plan_latency(trials: int) -> list[float]:
    from agents.planning_agent import run_planning_agent

    plan_json = json.dumps(CANONICAL_AVOID_FRIDAYS_PLAN)
    runtimes_ms: list[float] = []

    for _ in range(trials):
        state = {
            "messages": [HumanMessage(content="Plan next semester avoiding Fridays")],
            "intent": "planning",
            "intent_confidence": 0.95,
            "selected_agent": "planning_agent",
            "safety_result": None,
            "tool_calls": [],
            "response": "",
            "user_id": "u1",
            "session_id": "s1",
            "requires_approval": False,
            "approval_status": None,
        }
        t0 = time.perf_counter()
        result = await run_planning_agent(state, _mock_llm(plan_json))
        runtimes_ms.append((time.perf_counter() - t0) * 1000)
        # Sanity: the plan must produce 6 successful tool calls per run.
        assert len(result["tool_calls"]) == 6
        assert all(tc.success for tc in result["tool_calls"])

    return runtimes_ms


def section_plan_subgraph(trials: int = 20) -> dict:
    runtimes = asyncio.run(_measure_plan_latency(trials))
    return {
        "trials": trials,
        "plan_steps": 6,
        "runtime_ms_p50": round(statistics.median(runtimes), 3),
        "runtime_ms_p95": round(_percentile(runtimes, 95), 3),
        "runtime_ms_mean": round(statistics.mean(runtimes), 3),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).parent.parent.parent),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def build_report() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "limitations": (
            "Self-curated annotations against a 10-doc mock universe. "
            "Single annotator. No production traffic. No LLM-judge metrics. "
            "Subgraph latency uses a no-network mock LLM; real Anthropic API "
            "roundtrips dominate when wired in. See evaluation/README.md."
        ),
        "retrieval": section_retrieval(),
        "solver": section_solver(),
        "plan_subgraph": section_plan_subgraph(),
    }


def main() -> int:
    report = build_report()
    out = Path(__file__).parent / "baseline_metrics.json"
    out.write_text(json.dumps(report, indent=2) + "\n")

    print(f"Baseline metrics: {out}")
    print(f"  generated_at: {report['generated_at']}")
    print(f"  git_sha: {report['git_sha']}")
    r = report["retrieval"]
    print(f"  retrieval (vector-only):  P@5={r['vector_only']['precision_at_5']:.1%}, "
          f"R@5={r['vector_only']['recall_at_5']:.1%}")
    print(f"  retrieval (hybrid RRF):   P@5={r['hybrid']['precision_at_5']:.1%}, "
          f"R@5={r['hybrid']['recall_at_5']:.1%}")
    print(f"  retrieval (improvement):  P@5{r['improvement']['precision_at_5']:+.1%}, "
          f"R@5{r['improvement']['recall_at_5']:+.1%}")
    s = report["solver"]
    print(f"  solver runtime (n={s['trials']}): "
          f"p50={s['runtime_ms_p50']}ms, p95={s['runtime_ms_p95']}ms")
    p = report["plan_subgraph"]
    print(f"  plan subgraph (n={p['trials']}, mock LLM): "
          f"p50={p['runtime_ms_p50']}ms, p95={p['runtime_ms_p95']}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
