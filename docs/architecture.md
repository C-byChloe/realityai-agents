# RealityAI Agent Architecture

This document describes how the agent layer (`agent-core/`) is wired.
It is the engineering reference; for design rationale see the ADRs in
`docs/adr/`.

## High-level flow

```
user message
   │
   ▼
┌──────────────────────────────────────────────────────────────┐
│  LangGraph state machine (orchestrator.py)                   │
│                                                              │
│  intent_classification → agent_routing → safety_check        │
│                                              │               │
│                  ┌───────────────────────────┴───────────┐   │
│                  ▼                                       ▼   │
│           coref_resolver                       hitl_approval │
│                  │                                       │   │
│                  ▼                                       │   │
│          execution (chooses one):                        │   │
│            • Action  Agent subgraph                      │   │
│            • Query   Agent subgraph                      │   │
│            • Planning Agent (typed Plan DAG)             │   │
│                  │                                       │   │
│                  └───────────────┬───────────────────────┘   │
│                                  ▼                           │
│                        response_generation → END             │
└──────────────────────────────────────────────────────────────┘
```

`coref_resolver` runs only on the execute branch — the `hitl_approval`
branch bypasses it so HiTL approval always sees raw user input. See
the Query Rewrite Layer section below.

## State schemas

| Schema | Where | Purpose |
|---|---|---|
| `AgentState` | `state.py` | Top-level LangGraph state. Carries messages, intent, safety result, the typed `plan`, `step_outputs`, the optional `rewritten_query` + `user_query_normalized`, and the final response. |
| `RewrittenQuery` | `preprocessing/schemas.py` | Output of the Layer 1 coref resolver. Carries original + rewritten query, resolved-entity map, rewrite reason, and confidence. Low-confidence rewrites fall back to the original. |
| `QueryAgentInternalState` | `agents/subgraph_states.py` | Working memory of the Query subgraph (route decision, raw tool result, error). Internal-only. |
| `ActionAgentInternalState` | `agents/subgraph_states.py` | Working memory of the Action subgraph (route decision, validation flags, raw result, audit record). Internal-only. |
| `PlanExecState` | `agents/planning_agent.py` | State of the dynamically-compiled plan-execution subgraph. Has reducers on `step_outputs` (dict merge) and `tool_calls` (list append) for parallel-write safety. |

## Query Rewrite Layer

A two-layer query rewrite design sits between user input and plan execution.

### Layer 1 — Coreference resolution (main graph)

Multi-turn queries often contain pronouns ("its prereq?") or ellipsis
("再查一下避开周五的"). The `coref_resolver` node turns these into
self-contained queries before the planning agent reads them.

**Placement**: between `safety_check` and `execution`, on the execute
branch only. Safety operates on raw user input — coref output never
reaches safety or HiTL approval. The trust boundary is documented in
`preprocessing/coref_resolver.py`'s module docstring.

**Conditional execution**: a deterministic regex gate
(`preprocessing/coref_gate.py`) skips the LLM call for first-turn
queries and queries with no detectable referential expressions. Most
turns pay zero LLM cost.

**Fallback**: if the LLM call fails or returns confidence < 0.5, the
node emits `RewrittenQuery(rewrite_reason="no_rewrite", confidence=…)`
and downstream consumers use the raw query. No exception propagates.

### Layer 2 — Source-level reformulation (PlanStep fields)

For `syllabus_rag` plan steps, `make_plan` populates two optional
fields on `PlanStep`:

- `semantic_query` — reformulated retrieval query for vector search
- `query_expansion` — optional list of paraphrases for multi-query retrieval

Other query sources (`canvas`, `degree_db`, `catalog_db`) absorb their
reformulation into the existing typed `query_params` fields — no
separate field needed since they are already structured.

**Implementation**: `agents/query_agent.py:_query_syllabus_rag` reads
these via underscore-prefixed keys (`_semantic_query`,
`_query_expansion`) that `run_query_step` injects from the `PlanStep`.
This keeps all source handlers on a uniform
`(params: dict) -> result` signature; the underscore prefix encodes
"reasoning-layer metadata, not user-supplied params" and is documented
at the dispatch site.

## Sub-agent subgraphs

### Query Agent (3-node linear pipeline)

```
START → route_query → execute_query_tool → format_query_response → END
```

`route_query` runs the LLM, parses the JSON tool/arguments decision.
`execute_query_tool` invokes the tool from `QUERY_TOOLS`. `format_query_response`
projects the raw tool dict into a human-readable string.

External boundary: `QueryAgentInput` → `QueryAgentOutput` (Pydantic).
The internal fields (`route_decision`, `raw_tool_result`,
`execution_error`, `tool_arguments`) never appear at the boundary —
see ADR 003.

### Action Agent (4-gate flow)

```
START → route_action → validate_args → execute_action_tool → audit_action → END
```

- **route_action** — LLM extracts tool + arguments + confirmation text
- **validate_args** — structural pre-flight: required arguments present,
  enum values legal. Rejects bad calls before they reach gRPC.
- **execute_action_tool** — short-circuits when validation failed;
  otherwise invokes the tool (gRPC with mock fallback).
- **audit_action** — emits a structured `audit_record` and an opaque
  `audit_id`; always runs.

External boundary: `ActionAgentInput` → `ActionAgentOutput`. The full
`audit_record` stays internal; only `audit_id` is exposed.

## Planning Agent — typed Plan DAG executor

The planner does not run a hard-coded sub-graph. It dynamically
**compiles a fresh `StateGraph` per request** from the LLM-produced
`Plan`:

1. `make_plan(user_query, llm)` — LLM emits a typed `Plan` (validated
   against `schemas/plan.py`). For `syllabus_rag` steps it also
   populates the Layer 2 fields (`semantic_query`, `query_expansion`).
2. `compile_plan_graph(plan)` — for each `PlanStep`, add a node named
   `step_<id>_<agent_type_or_source>`. Wire edges from `depends_on`:
   - Single-parent → `add_edge(parent, child)`
   - Multi-parent → `add_edge([p1, p2], child)` (AND-join — child fires once)
   - No parents → `add_edge(START, child)`
   - No dependants → `add_edge(child, END)`
3. `subgraph.ainvoke(...)` — LangGraph runs sibling nodes (no
   inter-dependency) in the same superstep, in parallel.

Each step node dispatches by `agent_type`:

| `agent_type` | Handler |
|---|---|
| `query` | `agents/query_agent.py:run_query_step` — branches on `query_source` (canvas / degree_db / catalog_db / syllabus_rag), returns a typed Pydantic object |
| `action` | `agents/action_agent.py:run_action_step` — invokes `ACTION_TOOLS[action_tool]` with `action_args` |
| `reasoning` | `agents/planning_agent.py:_run_reasoning_step` — template registry: `extract_unsatisfied_elective_pool` (set diff), `explain_schedule_recommendation` |
| `constraint_solver` | `agents/planning_agent.py:_run_solver_step` — currently `schedule_csp` (backtracking) |

## Symbolic vs. LLM placement

The LLM runs at three places per planning turn:

1. **coref_resolver** (Layer 1, conditional) — multi-turn coref /
   ellipsis resolution. Skipped by the regex gate for self-contained
   queries, so most turns pay zero LLM cost here.
2. **make_plan()** — natural-language → typed `Plan`. Also fills the
   Layer 2 fields (`semantic_query`, `query_expansion`) for
   `syllabus_rag` steps.
3. **explain_schedule_recommendation** — typed schedule → explanation.

Everything in between (gap analysis, schedule CSP, prereq filter,
interval-overlap conflict detection) is deterministic Python in
`reasoning/`. See ADR 004.

## Safety

`safety/` runs two independent classifiers (static rules,
dynamic LLM intent analyzer) on each turn and merges with OR semantics
in `safety/merge.py`. A flag from either layer routes through
`hitl_approval` instead of execution.

Safety always operates on raw user input. The coref resolver sits
*after* `safety_check` on the execute branch precisely so that no
LLM-rewritten content can reach the safety pipeline or the HiTL
approval surface.

## What is and isn't wired up

| Subsystem | Status |
|---|---|
| Multi-agent orchestration | Wired |
| Typed plan DAG with parallel execution | Wired |
| Subgraph internal-state isolation | Wired |
| Two-layer safety + HiTL | Wired |
| Query rewrite Layer 1 (coref resolver + gate) | Wired |
| Query rewrite Layer 2 (PlanStep `semantic_query` / `query_expansion`) | Wired |
| Hybrid retrieval (RRF over vector + keyword) | Implementation wired; data source is mock |
| ChromaDB vector store | **Not wired** — mock keyword-overlap stub stands in |
| PostgreSQL keyword search | **Not wired** — mock |
| Redis caching | **Not wired** — in-memory dict |
| Spring Boot core service (gRPC) | Wired |
| Canvas / live data | Not wired (mock transcripts in `agents/query_agent.py`) |

The mocks return typed Pydantic objects that match the production
contract, so swapping in a real source only changes data origin.

## Observability

`observability/tracing.py` wraps LangSmith. Each LangGraph node is its
own span; the dynamic plan subgraph produces N step spans + a parent
span per request. The `coref_resolver` node produces its own span when
the gate fires; gate-skipped turns emit a no-op span with
`rewrite_reason="no_rewrite"` for trace consistency.

## Testing

332 unit tests + 14 gRPC integration tests (5 require a live Spring
service). Eval harness lives in `evaluation/`; baseline metrics are
checked in (`evaluation/baseline_metrics.json`). Coref-specific eval
set is at `evaluation/coref_eval_set.jsonl`. See
`evaluation/README.md` for methodology and limitations.
