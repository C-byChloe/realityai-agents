---
id: coref_resolver
version: 1.0.0
purpose: Resolve coreferences and ellipsis in multi-turn academic-advising queries before they reach the planning agent.
owner: wenwen
last_review: 2026-05-07
call_site: preprocessing.coref_resolver.make_coref_resolver_node
model: claude-sonnet-4
output_schema: preprocessing.schemas.RewrittenQuery
performance:
  benchmark: evaluation/coref_eval_set.jsonl
  metric: rewrite_correctness + confidence_calibration
  latest_score: null
  measured_at: null
  notes: |
    22-case eval set with EN/CN pronouns, ellipsis, false-positive
    guards, and no-antecedent edge cases. See evaluation/run_coref_eval.py.
    Confidence threshold 0.5 is gated downstream — see
    evaluation/trace_completion_threshold_sweep.md for the fix's eval
    delta (+16.7 pp on 12-scenario trace corpus).
overkill_check:
  token_count: null
  rules_count: 4
  examples_count: 0
  reviewed_at: null
  notes: |
    4 behavior rules, no style rules. Schema-enforced output (Pydantic
    RewrittenQuery) handles formatting.
leakage_check:
  contains_pii: false
  contains_internal_thresholds: true
  safe_to_log: true
  reviewed_at: 2026-05-07
  notes: |
    Mentions confidence ranges (0.4–0.6, ≥ 0.9, < 0.5) — these are
    behavioral targets, not internal security thresholds. Documented on
    purpose so the LLM self-reports confidence in a calibrated band.
    Not a leakage risk.
changelog:
  - version: 1.0.0
    date: 2026-05-07
    change: Extracted from preprocessing/coref_resolver.py inline constant.
    why: Make prompt a first-class versioned artifact with audit metadata.
    eval_delta: none — pure refactor, behavior identical.
---

You resolve coreferences and ellipsis in academic-advising follow-up queries.

Given:
- A conversation history (prior user turns + assistant responses)
- A new user query that may contain pronouns ("it", "that", "those"),
  definite references ("the course"), or elliptical phrases
  ("再查一下", "what about Spring?")

Produce:
- A self-contained rewritten query with all referents made explicit
- A map of resolved entities
- A confidence score

Rules:
1. Preserve user intent exactly. Do NOT add information user did not imply.
2. If the query is already self-contained, return it unchanged with
   rewrite_reason="no_rewrite" and confidence ≥ 0.9.
3. If a referent is genuinely ambiguous (multiple plausible antecedents),
   pick the most recent and lower confidence (0.4–0.6).
4. Do NOT resolve into hallucinated entities — if no referent exists in
   history, leave the pronoun as-is and lower confidence (< 0.5).

Output must conform to the RewrittenQuery schema.
