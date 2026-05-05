"""Phase 2 verification: Action Agent subgraph gate flow.

  route → validate → execute → audit

Internal state isolation works the same way as Query Agent. This test
file additionally proves the gate ordering: validation rejects bad args
*before* execute touches gRPC, and audit always fires.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START

from agents.action_agent import (
    compile_action_agent,
    invoke_action_subgraph,
)
from agents.subgraph_states import (
    ACTION_INTERNAL_ONLY_KEYS,
    ActionAgentInput,
    ActionAgentOutput,
)


def _mock_llm(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke.return_value = AIMessage(content=content)
    return mock


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


def test_action_subgraph_has_four_gates():
    compiled = compile_action_agent(_mock_llm("{}"))
    nodes = set(compiled.get_graph().nodes.keys())
    assert {"route_action", "validate_args", "execute_action_tool", "audit_action"}.issubset(nodes)


def test_action_subgraph_edges_enforce_gate_order():
    """validate must come before execute; execute before audit. Gate skipping
    would defeat the safety claim, so the graph itself enforces it.
    """
    compiled = compile_action_agent(_mock_llm("{}"))
    edges = {(e.source, e.target) for e in compiled.get_graph().edges}
    assert (START, "route_action") in edges
    assert ("route_action", "validate_args") in edges
    assert ("validate_args", "execute_action_tool") in edges
    assert ("execute_action_tool", "audit_action") in edges
    assert ("audit_action", END) in edges
    # No shortcut from route to execute
    assert ("route_action", "execute_action_tool") not in edges


# ---------------------------------------------------------------------------
# Internal state isolation
# ---------------------------------------------------------------------------


def test_boundary_output_has_no_internal_keys():
    output_fields = set(ActionAgentOutput.model_fields.keys())
    overlap = output_fields & ACTION_INTERNAL_ONLY_KEYS
    assert not overlap, f"internal keys leaked into Output: {overlap}"


# ---------------------------------------------------------------------------
# Validation gate behavior
# ---------------------------------------------------------------------------


async def test_validate_rejects_missing_required_args_before_execute():
    """grade_update requires student_id/course_id/assignment_id/grade.
    Missing them → validation fails → execute short-circuits (no raw_tool_result).
    """
    llm = _mock_llm(json.dumps({
        "tool": "grade_update",
        "arguments": {"student_id": "S001"},  # missing 3 required fields
        "confirmation": "Updating grade",
    }))

    # Inspect internal state to prove the execute gate did not produce a result
    compiled = compile_action_agent(llm)
    internal = await compiled.ainvoke({"user_message": "Update grade"})
    assert internal.get("validation_passed") is False
    assert "Missing required" in internal.get("validation_errors", [""])[0]
    # Execute gate short-circuits when validation fails — no raw_tool_result written
    assert "raw_tool_result" not in internal

    out = await invoke_action_subgraph(
        ActionAgentInput(user_message="Update grade", user_id="u1", session_id="s1"), llm,
    )
    assert out.success is False
    assert "Missing required" in out.response


async def test_validate_rejects_bad_enum_value():
    """enrollment_modify.action must be 'add' or 'drop'."""
    llm = _mock_llm(json.dumps({
        "tool": "enrollment_modify",
        "arguments": {"student_id": "S001", "course_id": "CS101", "action": "swap"},
        "confirmation": "ok",
    }))
    compiled = compile_action_agent(llm)
    internal = await compiled.ainvoke({"user_message": "x"})
    assert internal.get("validation_passed") is False
    assert "raw_tool_result" not in internal

    out = await invoke_action_subgraph(
        ActionAgentInput(user_message="x", user_id="u1", session_id="s1"), llm,
    )
    assert out.success is False
    assert "must be 'add' or 'drop'" in out.response


# ---------------------------------------------------------------------------
# Audit gate behavior — always fires, leaves a correlation ID
# ---------------------------------------------------------------------------


async def test_audit_emits_correlation_id_on_success_path():
    llm = _mock_llm(json.dumps({
        "tool": "enrollment_modify",
        "arguments": {"student_id": "S001", "course_id": "CS101", "action": "drop"},
        "confirmation": "Dropping CS101",
    }))
    out = await invoke_action_subgraph(
        ActionAgentInput(user_message="Drop CS101", user_id="u1", session_id="s1"), llm,
    )
    assert out.success is True
    assert out.audit_id, "audit_id should be set on the boundary output"
    assert len(out.audit_id) == 12  # uuid hex slice


async def test_audit_record_stays_internal():
    """The full `audit_record` (with execution_error, validation flags) is
    internal; only the opaque audit_id leaks to the boundary.
    """
    llm = _mock_llm(json.dumps({
        "tool": "enrollment_modify",
        "arguments": {"student_id": "S001", "course_id": "CS101", "action": "drop"},
        "confirmation": "ok",
    }))
    compiled = compile_action_agent(llm)
    internal = await compiled.ainvoke({"user_message": "x"})

    assert "audit_record" in internal
    assert isinstance(internal["audit_record"], dict)
    assert "execution_error" in internal["audit_record"]

    # But the boundary output has only audit_id
    out = await invoke_action_subgraph(
        ActionAgentInput(user_message="x", user_id="u1", session_id="s1"), llm,
    )
    dumped = out.model_dump()
    assert "audit_record" not in dumped
    assert "execution_error" not in dumped


# ---------------------------------------------------------------------------
# Clarification short-circuit still works
# ---------------------------------------------------------------------------


async def test_clarification_skips_execute():
    """Clarification path leaves selected_tool empty and never produces raw_tool_result."""
    llm = _mock_llm(json.dumps({
        "clarification_needed": True,
        "question": "Which course do you mean?",
    }))
    compiled = compile_action_agent(llm)
    internal = await compiled.ainvoke({"user_message": "Drop the class"})
    assert internal.get("selected_tool", "") == ""
    assert "raw_tool_result" not in internal

    out = await invoke_action_subgraph(
        ActionAgentInput(user_message="Drop the class", user_id="u1", session_id="s1"), llm,
    )
    assert out.selected_tool == ""
    assert "Which course" in out.response
