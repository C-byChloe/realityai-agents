# ADR 003: Subgraph internal-state isolation

**Status:** Accepted
**Date:** 2026-05

## Context

Sub-agents accumulate working memory during a turn — routing decisions,
raw tool results, validation flags, audit metadata. If that working
memory shares the same `TypedDict` as the orchestrator state:

1. Every sub-agent field becomes a top-level orchestrator-state key,
   even when it's noise to other layers.
2. LangSmith renders the parent graph and sub-agent state as one
   blob — the trace is hard to read.
3. There is no enforced boundary on what a sub-agent can leak upward.

## Decision

Query Agent and Action Agent are each compiled `StateGraph`s with a
private `TypedDict` (`QueryAgentInternalState`, `ActionAgentInternalState`
in `agents/subgraph_states.py`). The orchestrator boundary is a typed
Pydantic model (`QueryAgentOutput`, `ActionAgentOutput`) that contains
only what the parent graph needs to see (`response`, `success`,
`selected_tool`, `audit_id`).

The boundary is enforced at the adapter — `invoke_query_subgraph()` /
`invoke_action_subgraph()` extract the Output fields from the internal
final state and discard the rest. A frozenset
(`QUERY_INTERNAL_ONLY_KEYS`, `ACTION_INTERNAL_ONLY_KEYS`) names the
internal-only keys explicitly, and tests assert zero overlap with Output
model fields.

## Consequences

- Internal state appears inside the subgraph's LangSmith span; outside
  it, only the boundary model is visible.
- New internal fields never accidentally pollute orchestrator state.
- The Action Agent's 4-gate flow (route → validate → execute → audit)
  carries gate-specific state internally without changing the
  orchestrator-side ToolCall trace.

## Alternatives considered

- **Single shared `AgentState`.** Status quo before this ADR. Rejected
  for the reasons in Context.
- **Subgraphs as plain async functions.** Considered — simpler — but
  defeats the LangSmith trace-grouping benefit and the typed boundary.
