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
│          execution (chooses one):              hitl_approval │
│            • Action  Agent subgraph                          │
│            • Query   Agent subgraph                          │
│            • Planning Agent (typed Plan DAG)                 │
│                  │                                       │   │
│                  └───────────────┬───────────────────────┘   │
│                                  ▼                           │
│                        response_generation → END             │
└──────────────────────────────────────────────────────────────┘
```

## State schemas

| Schema | Where | Purpose |
|---|---|---|
| `AgentState` | `state.py` | Top-level LangGraph state. Carries messages, intent, safety result, the typed `plan`, `step_outputs`, and the final response. |
| `QueryAgentInternalState` | `agents/subgraph_states.py` | Working memory of the Query subgraph (route decision, raw tool result, error). Internal-only. |
| `ActionAgentInternalState` | `agents/subgraph_states.py` | Working memory of the Action subgraph (route decision, validation flags, raw result, audit record). Internal-only. |
| `PlanExecState` | `agents/planning_agent.py` | State of the dynamically-compiled plan-execution subgraph. Has reducers on `step_outputs` (dict merge) and `tool_calls` (list append) for parallel-write safety. |

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
   against `schemas/plan.py`).
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

The LLM runs at exactly two places per planning turn:

1. **make_plan()** — natural-language → typed Plan
2. **explain_schedule_recommendation** — typed schedule → explanation

Everything in between (gap analysis, schedule CSP, prereq filter,
interval-overlap conflict detection) is deterministic Python in
`reasoning/`. See ADR 004.

## Safety

`safety/` runs two independent classifiers (static rules,
dynamic LLM intent analyzer) on each turn and merges with OR semantics
in `safety/merge.py`. A flag from either layer routes through
`hitl_approval` instead of execution.

## What is and isn't wired up

| Subsystem | Status |
|---|---|
| Multi-agent orchestration | Wired |
| Typed plan DAG with parallel execution | Wired |
| Subgraph internal-state isolation | Wired |
| Two-layer safety + HiTL | Wired |
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
span per request.

## Testing

308 unit tests + 14 gRPC integration tests (5 require a live Spring
service). Eval harness lives in `evaluation/`; baseline metrics are
checked in (`evaluation/baseline_metrics.json`). See
`evaluation/README.md` for methodology and limitations.
