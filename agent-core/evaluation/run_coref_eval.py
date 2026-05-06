"""Coref eval runner.

Run: ``python -m evaluation.run_coref_eval``

Iterates `coref_eval_set.jsonl`, runs `coref_resolver_node` on each
case, and reports:
  - % cases where every `expected_rewritten_contains` substring is
    present in the rewritten query
  - % cases with confidence ≥ 0.5
  - % false-positive guard cases that emit `no_rewrite` or unchanged query
  - % no-antecedent cases that produce confidence < 0.5

This is a methodology artifact, not a verdict — the LLM is real and
results vary run-to-run. Treat output as a development-time signal.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from preprocessing.coref_resolver import make_coref_resolver_node


EVAL_PATH = Path(__file__).parent / "coref_eval_set.jsonl"


def _load_cases() -> list[dict]:
    cases = []
    with open(EVAL_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _build_messages(case: dict) -> list:
    """Convert eval-case history + query into LangChain messages."""
    msgs = []
    for h in case.get("history", []):
        msgs.append(HumanMessage(content=h.get("content", "")))
    msgs.append(HumanMessage(content=case["query"]))
    return msgs


async def _run_case(node, case: dict) -> dict:
    state = {"messages": _build_messages(case)}
    out = await node(state)
    rewritten = out.get("rewritten_query")
    return {
        "id": case["id"],
        "category": case["category"],
        "rewritten_query": rewritten.rewritten_query if rewritten else "",
        "rewrite_reason": rewritten.rewrite_reason if rewritten else "",
        "confidence": rewritten.confidence if rewritten else 0.0,
        "expected_contains": case.get("expected_rewritten_contains", []),
        "no_rewrite_expected": case.get("no_rewrite_expected", False),
        "low_confidence_expected": case.get("low_confidence_expected", False),
    }


async def main() -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY not set. The coref eval requires a real LLM. "
            "Set the env var or use the unit tests for offline checks.",
            file=sys.stderr,
        )
        return 1

    cases = _load_cases()
    node = make_coref_resolver_node(llm=None)  # production LLM

    results = await asyncio.gather(*[_run_case(node, c) for c in cases])

    contains_hits = sum(
        1 for r in results
        if r["expected_contains"] and all(
            sub.lower() in r["rewritten_query"].lower()
            for sub in r["expected_contains"]
        )
    )
    high_conf_hits = sum(1 for r in results if r["confidence"] >= 0.5)
    fp_guard_results = [r for r in results if r["category"] == "false_positive_guard"]
    fp_guard_pass = sum(
        1 for r in fp_guard_results
        if r["rewrite_reason"] == "no_rewrite" or r["rewritten_query"] == ""
    )
    no_antec = [r for r in results if r["category"] == "no_antecedent"]
    no_antec_pass = sum(1 for r in no_antec if r["confidence"] < 0.5)

    n = len(results)
    print(f"Coref Eval Results — {n} cases")
    print("-" * 50)
    print(f"  contains-substring hits: {contains_hits}/{n} ({contains_hits/n*100:.1f}%)")
    print(f"  confidence ≥ 0.5:        {high_conf_hits}/{n} ({high_conf_hits/n*100:.1f}%)")
    print(f"  false-positive guards:   "
          f"{fp_guard_pass}/{len(fp_guard_results)} produced no_rewrite")
    print(f"  no-antecedent edge:      "
          f"{no_antec_pass}/{len(no_antec)} produced confidence < 0.5")
    print()
    print("Per-case detail:")
    for r in results:
        marker = "✓" if (
            (r["category"] == "false_positive_guard" and r["rewrite_reason"] == "no_rewrite")
            or (r["category"] == "no_antecedent" and r["confidence"] < 0.5)
            or (r["expected_contains"] and all(
                sub.lower() in r["rewritten_query"].lower()
                for sub in r["expected_contains"]))
        ) else "✗"
        print(
            f"  [{marker}] #{r['id']:>2} {r['category']:>22s} "
            f"conf={r['confidence']:.2f} reason={r['rewrite_reason']:>12s}"
        )
        if marker == "✗":
            print(f"        rewritten: {r['rewritten_query'][:80]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
