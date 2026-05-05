"""Phase 2 verification: Query Agent subgraph isolates internal state.

These tests assert the architectural property from Talking Point 2:
  - The Query Agent is a compiled `StateGraph`, not a plain async function
  - Internal working-memory fields (route_decision, raw_tool_result,
    execution_error, etc.) never appear in the boundary `QueryAgentOutput`
  - The subgraph has 3 internal nodes (route / execute / format) so
    LangSmith shows a real internal flow, not a single opaque span
"""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START

from agents.query_agent import (
    compile_query_agent,
    invoke_query_subgraph,
)
from agents.subgraph_states import (
    QUERY_INTERNAL_ONLY_KEYS,
    QueryAgentInput,
    QueryAgentOutput,
)


def _mock_llm(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke.return_value = AIMessage(content=content)
    return mock


# ---------------------------------------------------------------------------
# Subgraph topology
# ---------------------------------------------------------------------------


def test_query_subgraph_has_three_internal_nodes():
    compiled = compile_query_agent(_mock_llm("{}"))
    nodes = set(compiled.get_graph().nodes.keys())
    assert {"route_query", "execute_query_tool", "format_query_response"}.issubset(nodes)


def test_query_subgraph_edges_form_linear_pipeline():
    compiled = compile_query_agent(_mock_llm("{}"))
    edges = {(e.source, e.target) for e in compiled.get_graph().edges}
    assert (START, "route_query") in edges
    assert ("route_query", "execute_query_tool") in edges
    assert ("execute_query_tool", "format_query_response") in edges
    assert ("format_query_response", END) in edges


# ---------------------------------------------------------------------------
# Internal state isolation — the architectural deliverable
# ---------------------------------------------------------------------------


async def test_boundary_output_has_no_internal_keys():
    """`QueryAgentOutput` field set must not overlap internal-only keys."""
    output_fields = set(QueryAgentOutput.model_fields.keys())
    overlap = output_fields & QUERY_INTERNAL_ONLY_KEYS
    assert not overlap, f"internal keys leaked into Output: {overlap}"


async def test_invoke_returns_typed_output_only():
    """Caller receives a `QueryAgentOutput` Pydantic model — not a raw dict
    with internal keys.
    """
    llm = _mock_llm(json.dumps({
        "tool": "course_lookup",
        "arguments": {"course_id": "CS101"},
        "query_type": "deterministic",
    }))
    out = await invoke_query_subgraph(
        QueryAgentInput(user_message="Tell me about CS101", user_id="u1", session_id="s1"),
        llm,
    )

    assert isinstance(out, QueryAgentOutput)
    # Pydantic strict shape: dumping the model should not contain internal keys
    dumped = out.model_dump()
    leaked = QUERY_INTERNAL_ONLY_KEYS & set(dumped.keys())
    assert not leaked, f"internal keys present in boundary dump: {leaked}"
    assert out.success is True
    assert out.selected_tool == "course_lookup"


async def test_internal_state_visible_inside_subgraph_only():
    """Smoke-check: the *internal* invocation result (subgraph.ainvoke) DOES
    contain internal keys — proving they exist but are stripped at the
    adapter boundary by `invoke_query_subgraph`.
    """
    llm = _mock_llm(json.dumps({
        "tool": "course_lookup",
        "arguments": {"course_id": "CS101"},
        "query_type": "deterministic",
    }))
    compiled = compile_query_agent(llm)
    internal = await compiled.ainvoke({"user_message": "x"})

    # These DO appear inside the subgraph (proving isolation is real, not
    # a side effect of fields not existing).
    assert "route_decision" in internal
    assert "raw_tool_result" in internal


async def test_unknown_tool_does_not_leak_internal_error():
    """Even on failure path, boundary output has the strict shape."""
    llm = _mock_llm(json.dumps({
        "tool": "nonexistent_tool",
        "arguments": {},
        "query_type": "deterministic",
    }))
    out = await invoke_query_subgraph(
        QueryAgentInput(user_message="x", user_id="u1", session_id="s1"), llm,
    )
    assert isinstance(out, QueryAgentOutput)
    assert out.success is False
    assert "Unknown tool" in out.response
    # Confirm the internal `execution_error` isn't dragged into the dump
    assert "execution_error" not in out.model_dump()
