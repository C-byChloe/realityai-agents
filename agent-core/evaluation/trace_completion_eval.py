"""Trace-driven task completion evaluator.

Distinct from `e2e_runner.py` — that one is a pytest wrapper and judges
PASS/FAIL from pytest's exit signal. This module judges from the
LangGraph **final state** itself: tool_calls success flags, outer
safety verdict, plan/step_outputs, response presence.

This is what makes the resume claim "LangSmith-driven analysis"
defensible: the metric is computed from the same trace fields LangSmith
spans capture, not from pytest's view of the world.

Run:
    python -m evaluation.trace_completion_eval

Inputs:
    evaluation/trace_eval_set.jsonl — one scenario per line. Schema:
        {
          "id": int,
          "category": "query" | "action_blocked" | "planning"
                    | "safety_deny" | "coref_resolved",
          "user_role": "student" | "instructor" | "registrar",
          "history": [{"role": "user|assistant", "content": str}, ...],
          "query": str,
          "mock_llm_responses": [str, ...],
          "expected": { ...category-specific assertions... }
        }

Outputs:
    evaluation/trace_completion_report.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from orchestrator import build_graph
from preprocessing.schemas import RewrittenQuery
from safety.outer.schemas import SafetyDecision

EVAL_PATH = Path(__file__).parent / "trace_eval_set.jsonl"
REPORT_PATH = Path(__file__).parent / "trace_completion_report.json"


def _load_cases() -> list[dict]:
    cases: list[dict] = []
    with open(EVAL_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _build_messages(case: dict) -> list:
    msgs: list = []
    for h in case.get("history", []):
        if h.get("role") == "assistant":
            msgs.append(AIMessage(content=h.get("content", "")))
        else:
            msgs.append(HumanMessage(content=h.get("content", "")))
    msgs.append(HumanMessage(content=case["query"]))
    return msgs


def _build_coref_llm(case: dict):
    """Optional separate LLM for the coref node.

    When `coref_mock` is present in the case, build a coref-only mock
    that returns a specific RewrittenQuery. This lets us simulate
    high-confidence-but-wrong rewrites and low-confidence-but-correct
    rewrites — the two cases the threshold fix actually disambiguates.
    """
    spec = case.get("coref_mock")
    if not spec:
        return None

    rewrite = RewrittenQuery(
        original_query=case["query"],
        rewritten_query=spec["rewritten_query"],
        rewrite_reason=spec.get("rewrite_reason", "coreference"),
        confidence=float(spec["confidence"]),
        resolved_entities=spec.get("resolved_entities", {}),
    )
    structured = AsyncMock()
    structured.ainvoke = AsyncMock(return_value=rewrite)
    coref_llm = AsyncMock()
    coref_llm.with_structured_output = lambda _schema: structured
    return coref_llm


async def _run_one(case: dict) -> dict:
    """Invoke the graph with mocked LLM, return final state + per-case verdict."""
    main_llm = AsyncMock()
    main_llm.ainvoke.side_effect = [
        AIMessage(content=r) for r in case.get("mock_llm_responses", [])
    ]
    coref_llm = _build_coref_llm(case)

    state: dict[str, Any] = {
        "messages": _build_messages(case),
        "intent": "",
        "intent_confidence": 0.0,
        "selected_agent": "",
        "user_role": case.get("user_role", "instructor"),
        "outer_safety_result": None,
        "tool_calls": [],
        "response": "",
        "user_id": "trace-eval-user",
        "session_id": f"trace-eval-{case['id']}",
        "requires_approval": False,
        "approval_status": None,
    }

    try:
        with patch("orchestrator._get_llm", return_value=main_llm):
            graph = build_graph(coref_llm=coref_llm)
            app = graph.compile()
            final = await app.ainvoke(state)
    except Exception as e:
        return {
            "id": case["id"],
            "category": case["category"],
            "completed": False,
            "reason": f"graph_raised: {type(e).__name__}: {e}",
        }

    completed, reason = _judge(case, final)
    return {
        "id": case["id"],
        "category": case["category"],
        "completed": completed,
        "reason": reason,
    }


def _judge(case: dict, state: dict) -> tuple[bool, str]:
    """Apply category-specific completion rules to the final graph state.

    Each rule reads only fields that are visible in a LangSmith trace
    (tool_calls, outer_safety_result, plan, step_outputs, response).
    """
    cat = case["category"]
    expected = case.get("expected", {})

    if cat == "safety_deny":
        outer = state.get("outer_safety_result")
        if outer is None:
            return False, "no outer_safety_result"
        if outer.final_decision == SafetyDecision.ALLOW:
            return False, f"expected DENY/FLAG, got ALLOW"
        return True, f"blocked at {outer.short_circuited_at}"

    if cat == "action_blocked":
        outer = state.get("outer_safety_result")
        if outer is None or outer.final_decision == SafetyDecision.ALLOW:
            return False, "action reached execution without HiTL flag"
        return True, "outer safety routed to HiTL/deny"

    if cat == "query":
        tcs = state.get("tool_calls", [])
        if not tcs:
            return False, "no tool_calls in trace"
        success = any(getattr(t, "success", False) for t in tcs)
        if not success:
            return False, "all tool_calls failed"
        if "expected_tool" in expected:
            names = [getattr(t, "tool_name", "") for t in tcs]
            if expected["expected_tool"] not in names:
                return False, f"expected tool {expected['expected_tool']}, got {names}"
        return True, "query tool succeeded"

    if cat == "planning":
        plan = state.get("plan")
        if not plan:
            return False, "no plan emitted"
        tcs = state.get("tool_calls", [])
        if not tcs:
            return False, "plan emitted but no step tool_calls"
        all_ok = all(getattr(t, "success", False) for t in tcs)
        if not all_ok:
            failed = [getattr(t, "tool_name", "?") for t in tcs if not getattr(t, "success", False)]
            return False, f"plan steps failed: {failed}"
        return True, f"all {len(tcs)} plan steps succeeded"

    if cat == "coref_resolved":
        # The coref threshold's actual effect is on what the planning
        # agent SEES as its query — `state["user_query_normalized"]`.
        # Judging directly on this field isolates the threshold's effect
        # from any downstream noise (planning LLM, plan validation).
        # `normalized_must_contain` = correct rewrite passed through.
        # `normalized_must_not_contain` = bad low-confidence rewrite was
        #   caught by the threshold and the original was used instead.
        normalized = state.get("user_query_normalized") or ""
        for token in expected.get("normalized_must_contain", []):
            if token.lower() not in normalized.lower():
                return False, f"normalized missing {token!r}: {normalized!r}"
        for token in expected.get("normalized_must_not_contain", []):
            if token.lower() in normalized.lower():
                return False, (
                    f"normalized leaked forbidden {token!r}: {normalized!r}"
                )
        return True, f"normalized={normalized!r}"

    return False, f"unknown category {cat!r}"


async def _run_all() -> dict:
    cases = _load_cases()
    # Sequential, not asyncio.gather — `patch("orchestrator._get_llm")`
    # is a module-global patch and concurrent cases would cross-pollute
    # each other's mock side_effect queues.
    results: list[dict] = []
    for c in cases:
        results.append(await _run_one(c))

    total = len(results)
    passed = sum(1 for r in results if r["completed"])

    # Per-category breakdown — surfaces which slice the threshold fix
    # actually moves.
    by_cat: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_cat.setdefault(r["category"], {"total": 0, "passed": 0})
        bucket["total"] += 1
        if r["completed"]:
            bucket["passed"] += 1

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "coref_confidence_threshold": float(
                os.environ.get("COREF_CONFIDENCE_FALLBACK_THRESHOLD", "0.5")
            ),
        },
        "summary": {
            "total_scenarios": total,
            "passed": passed,
            "task_completion_rate": round(passed / total * 100, 2) if total else 0.0,
        },
        "by_category": {
            cat: {
                **bucket,
                "rate": round(bucket["passed"] / bucket["total"] * 100, 2)
                if bucket["total"] else 0.0,
            }
            for cat, bucket in sorted(by_cat.items())
        },
        "scenarios": results,
    }


def main() -> int:
    if not EVAL_PATH.exists():
        print(f"Eval set not found at {EVAL_PATH}", file=sys.stderr)
        return 2

    report = asyncio.run(_run_all())
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))

    s = report["summary"]
    print(f"Trace Completion Eval — {s['total_scenarios']} scenarios")
    print(f"  Threshold: {report['config']['coref_confidence_threshold']}")
    print(f"  Passed:    {s['passed']}/{s['total_scenarios']} ({s['task_completion_rate']}%)")
    print()
    print("By category:")
    for cat, bucket in report["by_category"].items():
        print(f"  {cat:>20s}: {bucket['passed']}/{bucket['total']} ({bucket['rate']}%)")
    print(f"\nReport written to {REPORT_PATH}")

    failed = [r for r in report["scenarios"] if not r["completed"]]
    if failed:
        print(f"\nFailures ({len(failed)}):")
        for r in failed:
            print(f"  #{r['id']:>2} [{r['category']:>16s}] {r['reason']}")

    return 0 if s["task_completion_rate"] == 100.0 else 1


if __name__ == "__main__":
    sys.exit(main())
