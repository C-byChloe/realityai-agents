# ADR 002: Reducer-equipped state for parallel plan-step writes

**Status:** Accepted
**Date:** 2026-05

## Context

The Plan DAG executor runs sibling steps (no inter-dependency) in the
same LangGraph superstep. Two parallel step nodes both write back to the
same state keys: `step_outputs` (per-step result, keyed by step_id) and
`tool_calls` (the trace list).

LangGraph requires reducer functions on any state key written by more
than one node in a superstep. Without them, concurrent writes raise
`InvalidUpdateError` at runtime. The first run of the canonical 6-step
"avoid Fridays" plan failed exactly this way before reducers were
added.

## Decision

The plan-execution subgraph state (`PlanExecState` in
`agents/planning_agent.py`) annotates each parallel-write key with a
reducer:

```python
class PlanExecState(TypedDict):
    plan: Plan
    step_outputs: Annotated[dict[int, Any], _merge_step_outputs]
    tool_calls:   Annotated[list[ToolCall],  operator.add]
```

`_merge_step_outputs` is a dict union (last-write-wins per step_id;
step_ids are unique by Plan validation). `operator.add` concatenates
tool-call lists.

For multi-parent join steps (e.g., a step with `depends_on=[1, 4]`),
the graph compiler uses LangGraph's list-source `add_edge([n1, n4],
target)` so the join fires *once* after all parents complete, not once
per incoming edge.

## Consequences

- Sibling parallelism is the runtime's job; the application code does
  not call `asyncio.gather` directly.
- The reducer choice (dict merge, list append) is the contract that
  must hold across all node implementations. Tests cover concurrent
  writes (`test_plan_steps_as_nodes.py`).

## Alternatives considered

- **No reducer, run sequentially.** Rejected — defeats the purpose of
  the DAG.
- **`asyncio.gather` inside a single node.** Rejected — LangSmith would
  see one opaque span instead of N.
