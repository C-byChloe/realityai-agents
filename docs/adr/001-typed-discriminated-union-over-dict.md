# ADR 001: Typed discriminated outputs over generic dicts

**Status:** Accepted
**Date:** 2026-05

## Context

Plan steps and query-agent results travel between LangGraph nodes. The
naive shape is a generic `dict` with a `description` string and ad-hoc
keys. We hit two problems with that shape:

1. **Double inference at dispatch.** If a plan step carries only natural
   language (`"get next-semester offerings for AI track avoiding Fridays"`),
   the sub-agent has to re-parse it with another LLM call to decide which
   data source to hit and which filters to apply — work the planner
   already did.
2. **Re-parse hallucinations.** When a reasoning step receives an
   upstream query result as a stringified blob, the LLM has to extract
   structured fields (course codes, prereqs) from it. That's the failure
   mode LLMs are weakest at, and it's silent when it fails.

## Decision

Plan steps are typed `PlanStep` Pydantic models with per-`agent_type`
required fields (`schemas/plan.py`). Query agent outputs are a
discriminated union of source-specific Pydantic models —
`StudentTranscript` for canvas, `DegreeProgram` for degree_db, etc.
(`schemas/query_outputs.py`). Reasoning steps consume typed objects, not
strings. Set diff and conflict detection happen in deterministic Python,
not LLM prompts.

## Consequences

- Dispatch is a switch on `agent_type` / `query_source` — no second LLM
  call. Faster, cheaper, more predictable.
- Pydantic validators reject malformed plans at construction time, so
  the executor never runs a step with missing required fields.
- Boilerplate cost: every new query source adds a model. Worth it at
  the current 4-source scale.

## Alternatives considered

- **Generic dict with `description`.** Rejected — see Context.
- **Polymorphic class hierarchy** (one `PlanStep` subclass per
  agent_type). Considered, rejected for ergonomics: Pydantic + LangGraph
  are easier with one class + Optional fields + validators than multi-class
  serialization. Revisit if `agent_type` count exceeds 6–7.
