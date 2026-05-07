# Coref confidence threshold — before/after eval

Trace-driven task completion sweep over the
`evaluation/trace_eval_set.jsonl` corpus (12 scenarios). Judges
completion from the LangGraph final state — `tool_calls` success
flags, `outer_safety_result.final_decision`, `plan` shape, and
`user_query_normalized` content — not pytest assertions.

## Result

| `COREF_CONFIDENCE_FALLBACK_THRESHOLD` | task_completion_rate | coref_resolved subset |
|---|---|---|
| 0.0 (no fallback — pre-fix) | **83.3%** (10/12) | 50% (2/4) |
| 0.5 (current default — post-fix) | **100%** (12/12) | 100% (4/4) |

Reports preserved as
`trace_completion_report_threshold_0.0.json` and
`trace_completion_report_threshold_0.5.json`.

## X / Y / Z

**X — what the trace showed.**
LangSmith spans on the planning path showed two distinct shapes for
multi-turn ambiguous-coref scenarios: in some traces, the planning
agent's input (`user_query_normalized`) referenced a fabricated course
code that never appeared in the user's history. The coref resolver
span had `confidence < 0.4` on those exact cases — a confident-looking
rewrite written by an under-grounded LLM.

**Y — root cause.**
`coref_resolver.py` always forwarded `rewritten_query` regardless of
the LLM's self-reported confidence. Low-confidence rewrites — the
exact cases where the LLM is most likely to fabricate referents —
silently propagated into planning, causing wrong-tool selections.

**Z — fix.**
Confidence-gated fallback at `coref_resolver.py:153` — when
`rewritten.confidence < CONFIDENCE_FALLBACK_THRESHOLD` (default 0.5),
use `original_query` verbatim instead. This trades silent wrong action
for honest failure: planning sees the raw ambiguous query and fails
visibly (no plan, or asks for clarification) rather than executing
against a fabricated entity.

The threshold is read at call time from
`COREF_CONFIDENCE_FALLBACK_THRESHOLD` env var so the eval harness can
sweep without re-importing.

## Re-eval

Same 12-scenario corpus, threshold flipped from 0.0 to 0.5:
- 4 coref scenarios cover (correct-rewrite, high-conf) × 2 +
  (wrong-rewrite, low-conf) × 2.
- The 8 non-coref scenarios are threshold-independent controls and
  pass at 100% on both sweeps — confirming the delta is attributable
  to the threshold, not eval noise.
- Net delta: **+16.7 percentage points** task_completion_rate; **+50
  pp** on the coref subset specifically.

## Honest caveats

- 12 scenarios is a smoke set, not a production eval. The corpus needs
  expansion (more wrong+low cases, plus the symmetric correct+low
  cases where 0.5 actually *hurts*) before claiming the optimum is at
  0.5.
- Planning LLM is mocked. The harness measures whether the threshold
  controls what the planning agent *receives* (`user_query_normalized`),
  not whether a real planning LLM would produce a correct plan from
  that input.
- Mock LLMs sidestep network jitter; `runtime_ms_p50/p95` numbers from
  this run are not representative of production latency.
