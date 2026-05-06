# Evaluation

This directory contains the eval harness and a checked-in baseline snapshot.
It is **not** a verdict on production behavior — it is a methodology
artifact and a regression anchor.

## What's in here

| File | Purpose |
|---|---|
| `ground_truth.json` | 100 self-curated query → relevant-doc annotations against the mock retrieval universe (`retrieval/hybrid.py:MOCK_DOCUMENTS`). |
| `precision_harness.py` | Computes Precision@5 / Recall@5 for vector-only vs. hybrid (RRF) retrieval. |
| `run.py` | CLI: `python -m evaluation.run`. Produces `baseline_metrics.json`. |
| `e2e_runner.py` | Wraps the pytest scenario suite (`tests/test_e2e_scenarios.py`) and emits a JSON report. |
| `coref_eval_set.jsonl` + `run_coref_eval.py` | 22-case smoke eval for the coref resolver (Layer 1 query rewrite). Real LLM, requires `ANTHROPIC_API_KEY`. |
| `outer_safety_smoke_eval.jsonl` + `run_outer_safety_eval.py` | 10-case smoke eval for the outer-safety 3-tier gate (ADR 005). Produces a per-tier confusion matrix in `outer_safety_smoke_baseline.md`. Real LLM for Tier 3, requires `ANTHROPIC_API_KEY`. |
| `baseline_metrics.json` | **Committed** snapshot of the retrieval/solver/plan-subgraph eval run. Diffs against this file flag regressions. |
| `outer_safety_smoke_baseline.md` | **Committed** snapshot of the outer-safety smoke eval. Read this for the per-tier attribution and defense-in-depth coverage. |

## How to run

```bash
cd agent-core
python -m evaluation.run
```

This regenerates `baseline_metrics.json` in place. Diff the file to see what
changed; commit the new file when the change is intentional.

## What the report contains

```jsonc
{
  "schema_version": 1,
  "generated_at": "<ISO-8601 UTC>",
  "git_sha": "<short SHA of repo HEAD>",
  "limitations": "<narrative — read this before quoting numbers>",
  "retrieval":     { "vector_only": {...}, "hybrid": {...}, "improvement": {...} },
  "solver":        { "runtime_ms_p50": ..., "runtime_ms_p95": ..., ... },
  "plan_subgraph": { "runtime_ms_p50": ..., "runtime_ms_p95": ..., ... }
}
```

- **retrieval** — Precision@5 and Recall@5 across all 100 ground-truth queries.
- **solver** — Wall-clock runtime of the schedule-CSP solver on the canonical
  avoid-Fridays scenario (n=50 trials, deterministic, no LLM).
- **plan_subgraph** — End-to-end LangGraph plan-execution latency on the
  canonical 6-step plan, using a **no-network mock LLM**. Reflects
  orchestration cost only — real API roundtrips dominate when Claude is
  wired in.

## Limitations (read this before quoting any number)

These are the honest weaknesses of this eval. They are not production-grade.

1. **Mock retrieval universe is 10 documents.** Both vector-only and hybrid
   saturate at 100% P@5 / R@5 because course-id filtering already narrows the
   candidate pool to ~3 docs per course. The eval cannot differentiate
   retrieval techniques on this corpus. **Hybrid's value will only show on a
   larger, less-filtered corpus** — that's a partnership-stage workload, not
   a POC one.

2. **Single annotator.** I wrote the queries and the ground-truth labels.
   No inter-annotator agreement, no held-out test set. Selection bias is
   real — I unconsciously avoided queries the system would obviously fail.

3. **No real production traffic.** No user logs, no failure modes from the
   wild, no distribution shift. The 100 queries are synthesized from the
   mock universe.

4. **No LLM-judge metrics.** The eval scores retrieval by exact doc-ID match.
   It does not score whether the agent's final natural-language answer is
   *correct* or *helpful*. That requires either human evaluation or a
   judge-LLM rubric, neither of which is in scope.

5. **Latency is mock-LLM only.** `plan_subgraph.runtime_ms_*` measures
   only LangGraph orchestration overhead. With the real Anthropic API, the
   end-to-end p50 will be dominated by 2–6 model roundtrips
   (~1–3 seconds each). Treat the local number as a lower bound on
   orchestration cost, not a real-world latency target.

6. **Solver runtime scales with candidates.** The committed solver
   benchmark uses 5 candidate sections. Production-realistic course catalogs
   are 50–200 sections per term; backtracking with the current pruning will
   need a profile-driven rewrite (or move to a SAT solver) at that scale.

## What this baseline IS good for

- **Regression detection.** A diff that drops Recall@5 or doubles solver
  p95 is a flag worth investigating before merge.
- **Establishing a methodology.** The ADR-grade claim is: "we measure
  Precision/Recall on a curated set, plus deterministic component
  benchmarks, with a committed snapshot." The methodology generalizes; the
  current numbers are toy-corpus numbers.
- **Self-honesty during interviews.** Pointing at this README is more
  credible than vague claims about retrieval quality.

## What this baseline is NOT

Not a verdict. Not a production SLO. Not evidence the system is correct on
real Columbia course data. Phase 5 (Canvas PAT connector) was descoped, so
no live-data evaluation was performed.
