# ADR 005: Outer safety layer — three-tier sequential gate

**Status:** Designed (Phase 0). Tier implementations land in Phases 1–5.
**Date:** 2026-05

## Context

### Three layers of the auth stack

Authorization is enforced at three points across the entire stack:

  - **Stack Layer A — OAuth scope** at the API gateway (vendor concern, not in this ADR)
  - **Stack Layer B — Application-level outer safety** (this ADR)
  - **Stack Layer C — Vendor server-side enforcement** (e.g., Canvas itself rejects unauthorized API calls; not in this ADR)

This ADR scopes Stack Layer B. Each stack layer guards a different time
point and trust assumption; B cannot replace A or C, and vice versa.

### "Tier" inside outer safety vs. "layer" stack-wide

The outer safety node has three internal tiers that run sequentially.
To avoid confusion with the stack layers above:

  - **Layer** = a level of the auth stack (A / B / C).
  - **Tier** = a stage inside the outer safety node (1 / 2 / 3).

This document uses "tier" exclusively for the intra-node concept.

### Two safety gates, not two layers

The agent loop has two safety-relevant time points:

  - **Outer** — before the LLM picks a tool. Inputs known: `intent`, `user_query_raw`, `user_role`.
  - **Inner** — after the LLM picks a tool, before execution. Inputs known: tool name, tool args, current world state.

This ADR covers the outer gate only. The inner gate is a separate
concern (separate ADR, separate implementation in each agent subgraph).
The two are independent gates, not two layers of one gate — each catches
failure modes the other cannot see.

### Current state on `main`

`safety/risk_classifier.py` + `safety/intent_analyzer.py` + `safety/merge.py`
implement a binary OR-merge of two parallel classifiers. This is a
basic safety net but has three gaps:

  1. **No role-aware authorization (RBAC).** A student request to update
     a grade is rejected on tool risk, not on the structural fact that
     the role has no grant.
  2. **Binary `flagged: bool` conflates "deny" with "needs review".**
     Ambiguous cases get rejected outright, hurting useful traffic that
     should route to HiTL approval.
  3. **OR-merge erases attribution.** Cannot answer "which tier caught
     this" — defense-in-depth is invisible.

## Decision

Replace the current `safety_check` node with a three-tier sequential
short-circuit gate. Tiers run cheapest-first and short-circuit on the
first non-`ALLOW` decision.

**Tier 1 — RBAC.** Pure dict lookup of `(user_role, action_category)`
against a YAML matrix. Sub-millisecond. Cheapest.

**Tier 2 — Static rules.** YAML rule engine over `(intent,
user_query_raw, user_role)`. Catches lexical patterns: bulk modifiers,
prompt-injection signals, sensitive keywords, intent-vs-lexicon mismatch.
Low ms.

**Tier 3 — Dynamic LLM intent analyzer.** Independent LLM call with its
own prompt and (future) cache. Inputs include raw query AND raw
conversation history (NOT coref-rewritten content). Detects multi-turn
injection chains, scope mismatch, data exfiltration patterns. ~800 ms cold.

Every tier returns `ALLOW` / `DENY` / `FLAG_FOR_REVIEW`. Final routing:

  - `ALLOW` → `coref_resolver` → `execution`
  - `DENY` → `reject_node` (terminates with reason)
  - `FLAG_FOR_REVIEW` → existing `hitl_approval` (LangGraph interrupt → resume)

### Locked design choices

**D1: No tool surface predictor.** Considered an `intent → tool set`
front-end where Tier 2 rules fire on `(predicted_tools, query)`.
**Rejected**: rules in practice fire on `(intent, query, role)` directly;
the predictor adds indirection without changing verdicts. Two YAML files
collapse to one.

**D2: Tier 3 is an independent LLM call** with its own prompt, its own
cache key (`hash(query + intent + role)`), and its own timeout. Not
piggy-backed on intent classification.

**D3: Sequential short-circuit, NOT parallel.** Cheap deterministic
checks gate expensive probabilistic checks. Tier 3's token cost is paid
only when Tiers 1 + 2 both `ALLOW`. A normal planning request typically
exits at Tier 1.

**D4: Tri-state, not binary.** `FLAG_FOR_REVIEW` routes to HiTL approval
instead of being rejected. This trades throughput for accuracy on
ambiguous cases — false positives become approval cards, not rejections.

**D5: `user_role` reads from `SessionContext`, never from messages.**
Messages are user-controlled and prompt-injectable. Role is JWT-derived
at the API gateway. **This is the single load-bearing invariant of the
entire safety design.** It is enforced by Pydantic schema shape:
`OuterSafetyInput.session.user_role` exists; `OuterSafetyInput` has no
field that could carry message-derived identity.

**D6: Failed safety check short-circuits.** A non-`ALLOW` verdict from
any tier writes a structured `TierResult` to
`outer_safety_result.tier_results` (for trace fidelity), and the
conditional routing terminates plan execution. No fall-through to
planning.

**D7: Outer safety runs BEFORE `coref_resolver`.** Coref is an LLM call;
its rewrite cannot sit between user input and the safety pipeline. Tier
3's analyzer reads raw query and raw conversation history — NOT
`user_query_normalized`. This preserves the multi-turn injection threat
model: an attack chain split across turns must remain visible to Tier 3
in raw form.

**D8: Tier 3 fallback is `FLAG_FOR_REVIEW`, not `DENY`.** Timeout, parse
error, or confidence < 0.7 → `FLAG_FOR_REVIEW`. `DENY`-on-failure would
amplify "LLM slowness" into "product unavailability"; `FLAG` trades
throughput for availability. This is the explicit availability-correctness
trade-off.

## Consequences

### What changes on `main`

- `safety/risk_classifier.py`, `safety/intent_analyzer.py`,
  `safety/merge.py` are deleted (hard cutover). Their logic is ported:
    - `risk_classifier.py` → a rule in `safety/outer/static_rules.py`
    - `intent_analyzer.py` → `safety/outer/intent_analyzer.py`
      (with tri-state output, confidence threshold, future cache)
- `tests/test_safety_merge.py` is rewritten as
  `tests/test_outer_safety_node.py` plus tier-specific unit tests.
- Orchestrator routing changes from 2-way (`execute / awaiting_approval`)
  to 3-way (`execute / hitl_approval / reject_node`).
- `AgentState` gains `user_role: str` and
  `outer_safety_result: OuterSafetyResult | None`.
- A new `reject_node` materializes the rejection response from
  `outer_safety_result.final_reason_human`.

### Production gap

Outer safety reads `user_role` from `SessionContext`, but the API
gateway JWT does not yet emit a `role` claim. For Phase 0, code defaults
to `"student"` when missing. **This ADR explicitly does not claim
production-readiness until Phase −1 (gateway → JWT role claim →
SessionContext propagation) ships.**

### Eval expectations

Phase 5 ships a 10-case smoke eval producing per-tier confusion matrix.
Cases distribute: 4 `ALLOW`, 2 `DENY`-by-RBAC, 2 `DENY`-by-static-rules,
2 `FLAG`-by-LLM-analyzer. The matrix validates that defense-in-depth is
measurable: each tier should catch cases its predecessors cannot.

### Operating envelope

  - YAML rules engine acceptable up to ~30 rules. Past that, migrate to
    OPA / cedar / dedicated rule DSL.
  - In-memory Tier-3 cache acceptable for single-process. Multi-worker
    deployment requires Redis. Production hardening lives in a future ADR.

## Alternatives considered

  - **Keep the current OR-merge.** Rejected: no role-awareness, no tier
    attribution, no false-positive recourse via `FLAG`.
  - **Three tiers in parallel for latency.** Rejected: every request
    pays Tier 3 token cost even when RBAC alone would deny. Sequential
    short-circuit is cost-aware design.
  - **Single LLM judge replacing all three tiers.** Rejected:
    deterministic RBAC + lexical rules give explainable traces (a
    compliance and debugging requirement) and microsecond paths for
    the common case.
  - **Tool surface predictor in front of Tier 2.** Rejected — see D1.
  - **Coref resolver before safety.** Rejected — see D7.
