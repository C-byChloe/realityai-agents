# ADR 006: Inner safety layer — four-tier sequential gate + audit sidecar

**Status:** Designed (Phase 0). Layer implementations land in Phases 1–6.
**Date:** 2026-05
**Pairs with:** ADR 005 (outer safety layer)

## Context

### Two independent gates, not two layers of one gate

The agent loop has two safety-relevant time points:

  - **Outer** (ADR 005) — before the LLM picks a tool.
    Inputs known: `intent`, `user_query_raw`, `user_role`.
  - **Inner** (this ADR) — after the LLM has picked a tool and emitted
    args, before execution.
    Inputs known: `tool_name`, `tool_args`, current world state.

These are **independent gates** that catch different failure modes:

| Failure mode | Caught by |
|---|---|
| Bad request intent (bulk modifier, prompt injection lexical) | Outer Tier 2 |
| LLM judgment errors (semantic injection, scope mismatch) | Outer Tier 3 |
| **Prompt injection that flipped tool selection between intent and execution** | **Inner Layer 1** |
| LLM hallucinated parameter shape (missing field, illegal enum value) | Inner Layer 2 + 3 |
| **Stale read-path cache → write commits against an inconsistent world** | **Inner Layer 4** |

Inner is not a redundant copy of outer. Outer can predict tool surface
but cannot see the actual tool the LLM picked; inner sees the concrete
selection. Outer trusts the read path; inner re-reads world state at
write time.

### Current state on `main`

Action Agent's standalone subgraph (`compile_action_agent` in
`agents/action_agent.py`) ships a 4-gate flow:
`route_action → validate_args → execute_action_tool → audit_action`.

Mapping that to this ADR's design:

  - `validate_args`: implements **Layer 2 (parameter presence)** + **half
    of Layer 3 (enum legality for `enrollment_modify.action`)**.
  - `audit_action`: implements the **audit sidecar**.
  - **Layer 1 (tool authorization re-check)** is missing.
  - **Layer 3 (parameter format)**: only enum check, no semester window
    or course_id format.
  - **Layer 4 (live state)** is missing.

The plan-driven action path (`run_action_step`) is a 5-line wrapper over
`tool_func.invoke(args)` and **bypasses every gate including the
prototype**. This is the largest gap.

## Decision

Replace the prototype with a proper inner safety module that runs four
layers sequentially and emits an audit sidecar that is always populated.

### Topology

```
                                        ┌─ Layer 1: Tool authorization re-check    (μs)
                                        │   role × actual tool name
                                        ↓
LLM emits (tool, args)  ──→  Layer 2: Parameter presence                   (μs)
                                        │   required-field check
                                        ↓
                            Layer 3: Parameter format                       (ms)
                                        │   regex / enum / semester window
                                        ↓
                            Layer 4: Live state (cache-bypass Postgres)    (10s of ms)
                                        │   read current world state
                                        ↓
                            Tool execution (gRPC) ──→ result

  All layers also emit data into a sidecar AuditRecord that is
  always written, regardless of decision.
```

Tier 1–4 short-circuit on first DENY. Audit always runs.

### Locked design choices

**D1: Binary verdict (`ALLOW / DENY`).** Inner runs on concrete
`(tool, args)` tuples that already passed outer's ambiguity gate. Each
layer is deterministic (dict lookup, regex, DB read). FLAG_FOR_REVIEW
has no semantic role here; if a request needs human review, outer
should have routed it to HiTL upstream.

**D2: Sequential short-circuit, cheap → expensive.** Layer 1 (μs dict
lookup) gates Layer 4 (10s of ms gRPC read). A request denied at Layer 1
never pays the live-state DB cost.

**D3: Layer 4 unavailable → fail closed.** When Postgres is unreachable
or times out, Layer 4 returns DENY (`reason_code="live_state_unavailable"`).
Rationale: write path cannot trust unverified world state. A DB blip
should temporarily reject writes, not silently let them through. The
explicit availability-correctness trade-off opposite of outer ADR 005 D8
(where outer's LLM analyzer falls back to FLAG): outer absorbs LLM
failure into FLAG because LLM-down ≠ malicious; inner absorbs DB
failure into DENY because we can't confirm safety to commit.

**D4: Audit is a sidecar, ALWAYS populated.** The audit record is
written even when Layer 1 short-circuits with DENY. This catches scan
patterns where an attacker probes for permissions; if audit only ran on
ALLOW, denied attempts would leave no trace. Audit records args **keys
only**, not values, because args may contain PII; the LangSmith trace
correlates back via `audit_id`.

**D5: `user_role` from `SessionContext`, never from messages.**
Identical invariant to outer ADR 005 D5. Inner reads the same JWT-derived
`user_role` field that outer Tier 1 (RBAC) consumed; the matrix is
finer-grained (role × specific tool name) but the channel is the same.
The schema enforces this: `InnerSafetyInput` has `session: SessionContext`
and `tool_name: str` + `tool_args: dict`, but **no `messages` field**.

**D6: Action path only in v1.** Both action dispatch paths (standalone
`compile_action_agent` subgraph and plan-driven `run_action_step`) go
through inner safety in Phase 5. Query path is deferred to Phase 7
because its threat model is different:

  - Action's Layer 1 = role × tool name (e.g., student cannot grade_update)
  - Query's Layer 1 = user_id × source isolation (e.g., student u1 cannot
    fetch transcript for u2)
  - Layer 4 (live state) doesn't apply to reads — caches are by design
    for the read path

These warrant a separate ADR rather than overloading this one.

## Consequences

### What changes on `main`

  - New module `safety/inner/` mirroring `safety/outer/` structure:
    `schemas.py` (this Phase 0), then `tool_authorization.py` /
    `parameter_validation.py` / `live_state.py` / `audit.py` / `node.py`.
  - `agents/action_agent.py:_validate_args` is retired; its logic moves
    into `safety/inner/parameter_validation.py` and is reachable from
    both action paths.
  - `agents/action_agent.py:_audit_action` is retired; its logic moves
    into `safety/inner/audit.py` (and is upgraded to populate an
    `AuditRecord` schema rather than a free-form dict).
  - `agents/action_agent.py:run_action_step` (plan-driven path) gains
    a call to `inner_safety_check` before invoking the tool, closing
    the prototype-bypass gap.
  - `state.py` gains `inner_safety_result: InnerSafetyResult | None`.
    Single-write per action invocation.

### Production gap

  - **Layer 1 matrix** (role × tool name) needs YAML config like outer's
    RBAC matrix. Same gateway-prerequisite caveat: production needs JWT
    `role` claim wired (Phase −1 outside this ADR's scope).
  - **Layer 4 live-state checks** require real Postgres access. v1 ships
    a stub backed by the existing gRPC `_grpc_client` mock fallback; a
    production swap-in is straightforward (the source handler interface
    stays the same).
  - **Audit persistence** — v1 emits records into LangSmith trace and
    `inner_safety_result.audit`. A long-term audit log requires
    durable sink (append-only DB, S3, or vendor logging). Phase 4
    documents the seam.

### Operating envelope

  - YAML matrix for tool-authorization acceptable up to ~20 tools. Past
    that, migrate to OPA / cedar / dedicated rule DSL.
  - Layer 4 cost: a single Postgres read per write attempt (~10s of ms).
    Acceptable for write-path; would not be acceptable on the hot read
    path, which is why query inner safety is deferred and uses different
    Layer 4 semantics.

## Alternatives considered

  - **Skip inner entirely; trust outer + Pydantic validation on PlanStep.**
    Rejected. PlanStep validates structure (`action_tool` is non-empty)
    but not business rules (`grade_update` actually has all 4 required
    args). And outer cannot see the LLM's tool selection — only inner
    can re-check role × actual tool.

  - **Single LLM judge replacing all four layers.** Rejected. Same
    reasoning as outer ADR 005: Layer 1 (role match) and Layer 4 (live
    state) are deterministic; making them probabilistic is a pure
    downgrade.

  - **Use a fifth layer for "intent-action mismatch detection" (was the
    LLM tricked into the wrong tool?).** Rejected. That signal is a
    semantic judgment and belongs in outer Tier 3 (LLM intent analyzer),
    not in inner. Inner stays deterministic.

  - **Tri-state inner verdicts.** Rejected — see D1.

  - **Audit as 5th sequential layer.** Rejected — see D4. Audit must
    survive short-circuits.

  - **Inner safety on query path simultaneously.** Rejected — see D6.
    Query threat model warrants its own ADR.
