# Prompt Library — Changelog

Cross-prompt, chronological. Per-prompt history also lives in each
prompt's frontmatter `changelog` field.

Entries newest first. One line per prompt change; rationale + eval
delta for non-trivial edits.

---

## 2026-05-08 — Phase 8.1: Indirect-injection content isolation (spotlighting)

| Prompt | Change | Reason |
|---|---|---|
| `query_agent` | 1.0.0 → 1.1.0 | Added "Untrusted retrieved content" disclosure section. The Query Agent receives RAG-retrieved syllabus chunks (and surfaces them in conversation_history); without this disclosure a future LLM consumer can't distinguish retrieved data from instructions. |
| `planning_agent` | 1.0.0 → 1.1.0 | Same disclosure added. Reasoning steps may consume `step_outputs` from upstream `syllabus_rag` query steps — without this, a planted directive in retrieved content could hijack plan shape or step parameters. |

**Architectural defense (Hines et al. 2024 — spotlighting):**
- New module: `safety/content_isolation.py` — `wrap_untrusted(content, nonce)` produces marker-bounded text; `new_nonce()` makes per-request nonces (cryptographically random, 64 bits).
- Wire-in: `agents/query_agent.py` formatter wraps every `SyllabusChunk.content` in `[BEGIN-DATA:nonce]` / `[END-DATA:nonce]` before including it in `response_text`.
- Defense scope: any LLM call that consumes the wrapped content gets the disclosure-driven instruction to treat marker-bounded text as data.

**Eval deltas:**
- trace_eval_set scores unchanged (12/12) — no current case includes an injected syllabus chunk; defense is forward-looking.
- TODO Phase 8.2: nonce-aware Prompt Guard heuristic (so Tier 3 also honors markers when scanning conversation_history) + dedicated indirect-injection eval case.

---

## 2026-05-07 — Phase 7: Tier 3 architecture swap (intent_analyzer retired)

| Prompt | Change | Reason |
|---|---|---|
| `intent_analyzer` | **Retired** (moved to `_retired/intent_analyzer.md`) | Tier 3 of outer safety swapped from Claude Sonnet to Meta Prompt Guard 2 86M. Tier 3's job is binary injection classification, not general safety reasoning — a 70B+ general LLM was overkill (~800ms, ~$0.003/call, 5 distinct API failure modes all routing to FLAG). Replaced by `safety/outer/prompt_guard.py` (real model in production, deterministic heuristic fallback in dev/CI) + `safety/outer/injection_guard.py` (Tier 3 entry point). |

**Eval deltas:**
- Outer safety smoke eval becomes **deterministic** — no `ANTHROPIC_API_KEY` required, suitable as a CI gate.
- 5 failure modes → 2: classifier failures collapse from {timeout, generic exception, JSONDecodeError, schema violation, low confidence} down to {timeout, exception}. JSON parsing + schema negotiation eliminated because the classifier returns typed Pydantic.
- Latency: ~800ms → ~50ms (real model on M-series CPU; ~sub-ms on heuristic).

**Registry:** dropped `intent_analyzer` row.
**Loader:** refuses to load anything from `_retired/` with a retirement-specific FileNotFoundError.

---

## 2026-05-07 — First bench cycle

`tools/prompt_bench.py` shipped. First run wires two of the four bench
sources (`trace_eval_set.jsonl` and `inner_safety_smoke_eval.jsonl`),
producing the first official `latest_score` numbers in each prompt's
frontmatter:

| Prompt | Bench | Score |
|---|---|---|
| `intent_classifier` | trace_eval_set | 12/12 (100%) |
| `query_agent` | trace_eval_set | 12/12 (100%) |
| `planning_agent` | trace_eval_set | 12/12 (100%) |
| `action_agent` | inner_safety_smoke_eval | 10/10 (100%) |

Two prompts (`coref_resolver`, `intent_analyzer`) are bound to real-LLM
eval sets and remain unwired pending API budget. The bench runner exits
2 with an explicit reason for those rather than silently passing.

Frontmatter writeback uses surgical regex on `latest_score:` and
`measured_at:` lines (not full YAML round-trip) to keep diffs to the
two fields that actually changed; `notes: |` block scalars preserved
byte-for-byte.

Bench history at `bench_history/<id>.jsonl` is the append-only audit
trail; safe to run on every dev loop. Frontmatter `latest_score`
updates only on `--write-frontmatter`, so daily bench runs don't churn
the prompt files.

## 2026-05-07 — Library bootstrap

Extracted all 6 production system prompts into versioned `prompts/<id>.md`
files. No behavioral change — every call site loads the same prompt body
it previously held inline.

| Prompt | New version | Action |
|---|---|---|
| `intent_classifier` | 1.0.0 | Extracted from `orchestrator.py:36` |
| `intent_analyzer` | 1.0.0 | Extracted from `safety/outer/intent_analyzer.py:44` |
| `coref_resolver` | 1.0.0 | Extracted from `preprocessing/coref_resolver.py:48` |
| `query_agent` | 1.0.0 | Extracted from `agents/query_agent.py:58` |
| `action_agent` | 1.0.0 | Extracted from `agents/action_agent.py:61` |
| `planning_agent` | 1.0.0 | Extracted from `agents/planning_agent.py:52` |

**Why**: prompts are production artifacts on hot request paths. Inlining
them in Python modules made them invisible to PR review (drowned by
unrelated diff hunks) and impossible to audit at scale. The library
layer gives each prompt:
- A standalone file diff reviewers can read in isolation
- YAML frontmatter carrying owner / version / benchmark binding /
  audit state / changelog
- A registry landing page (`REGISTRY.md`) so anyone joining the team
  sees the full surface in one table
- A surface for `tools/prompt_audit.py` to run overkill / leakage
  checks deterministically

**Eval delta**: none — pure refactor. Verified by 416-test regression
suite (excluding gRPC integration tests that require a running server)
and the 12-scenario trace completion eval (12/12 pass on threshold 0.5).

**Follow-on work**:
- `tools/prompt_audit.py` — overkill (token count, rule count, shouting
  ratio) and leakage (hardcoded thresholds, PII regex, internal
  identifiers) checks. Frontmatter `overkill_check` and `leakage_check`
  fields populated by the audit, not hand-edited.
- `tools/prompt_bench.py` — per-prompt benchmark runner that writes
  `latest_score` + `measured_at` back to frontmatter.
