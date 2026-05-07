# Prompt Library — Changelog

Cross-prompt, chronological. Per-prompt history also lives in each
prompt's frontmatter `changelog` field.

Entries newest first. One line per prompt change; rationale + eval
delta for non-trivial edits.

---

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
