"""Phase 5 verification: Action Agent subgraph gate flow.

  route → inner_safety_check → execute_action_tool → finalize_audit

Internal state isolation works the same way as Query Agent. This test
file additionally proves the gate ordering: inner safety rejects bad args
*before* execute touches gRPC, and the audit record always lands on the
boundary output (regardless of decision).

Updated for Phase 5 cutover (ADR 006): the legacy 3-gate flow
(validate → execute → audit) was replaced by the 4-layer inner safety
composer. Assertions now read from `inner_safety_result.layer_results`
and `inner_safety_result.audit` instead of the retired flat keys.
"""

import json
from unittest.mock import AsyncMock

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
from safety.inner.schemas import InnerLayerName, InnerSafetyDecision


def _mock_llm(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke.return_value = AIMessage(content=content)
    return mock


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


def test_action_subgraph_has_four_gates():
    """Cutover: validate_args + audit_action retired in favor of the
    composer node `inner_safety_check` and the post-execution
    `finalize_audit` step.
    """
    compiled = compile_action_agent(_mock_llm("{}"))
    nodes = set(compiled.get_graph().nodes.keys())
    assert {
        "route_action",
        "inner_safety_check",
        "execute_action_tool",
        "finalize_audit",
    }.issubset(nodes)


def test_action_subgraph_edges_enforce_gate_order():
    """Inner safety must come before execute; execute before finalize_audit.
    Gate skipping would defeat the safety claim, so the graph itself
    enforces it. There must be no shortcut from route directly to execute.
    """
    compiled = compile_action_agent(_mock_llm("{}"))
    edges = {(e.source, e.target) for e in compiled.get_graph().edges}
    assert (START, "route_action") in edges
    assert ("route_action", "inner_safety_check") in edges
    assert ("inner_safety_check", "execute_action_tool") in edges
    assert ("execute_action_tool", "finalize_audit") in edges
    assert ("finalize_audit", END) in edges
    # No shortcut bypassing the safety gate.
    assert ("route_action", "execute_action_tool") not in edges


# ---------------------------------------------------------------------------
# Internal state isolation
# ---------------------------------------------------------------------------


def test_boundary_output_has_no_internal_keys():
    output_fields = set(ActionAgentOutput.model_fields.keys())
    overlap = output_fields & ACTION_INTERNAL_ONLY_KEYS
    assert not overlap, f"internal keys leaked into Output: {overlap}"


# ---------------------------------------------------------------------------
# Inner safety gate behavior — Layer 2 catches missing fields
# ---------------------------------------------------------------------------


async def test_inner_safety_rejects_missing_required_args_before_execute():
    """grade_update requires student_id/course_id/assignment_id/grade.
    Missing them → inner safety DENY at parameter_presence → execute
    short-circuits (no raw_tool_result).
    """
    llm = _mock_llm(json.dumps({
        "tool": "grade_update",
        "arguments": {"student_id": "S001"},  # missing 3 required fields
        "confirmation": "Updating grade",
    }))

    compiled = compile_action_agent(llm)
    internal = await compiled.ainvoke({"user_message": "Update grade"})

    inner = internal.get("inner_safety_result")
    assert inner is not None
    assert inner.final_decision == InnerSafetyDecision.DENY
    assert inner.short_circuited_at == InnerLayerName.PARAMETER_PRESENCE
    # Execute gate short-circuits on DENY — no raw_tool_result written.
    assert "raw_tool_result" not in internal

    out = await invoke_action_subgraph(
        ActionAgentInput(user_message="Update grade", user_id="u1", session_id="s1"), llm,
    )
    assert out.success is False
    assert "missing required" in out.response.lower()


async def test_inner_safety_rejects_bad_enum_value():
    """enrollment_modify.action must be 'add' or 'drop' — Layer 3 catches
    malformed enum values.
    """
    llm = _mock_llm(json.dumps({
        "tool": "enrollment_modify",
        "arguments": {"student_id": "S001", "course_id": "CS101", "action": "swap"},
        "confirmation": "ok",
    }))
    compiled = compile_action_agent(llm)
    internal = await compiled.ainvoke({"user_message": "x"})

    inner = internal.get("inner_safety_result")
    assert inner is not None
    assert inner.final_decision == InnerSafetyDecision.DENY
    assert inner.short_circuited_at == InnerLayerName.PARAMETER_FORMAT
    assert "raw_tool_result" not in internal

    out = await invoke_action_subgraph(
        ActionAgentInput(user_message="x", user_id="u1", session_id="s1"), llm,
    )
    assert out.success is False
    assert "'add'" in out.response and "'drop'" in out.response


# ---------------------------------------------------------------------------
# Audit sidecar — D4: always populated, even on DENY (composer guarantee)
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
    """The full audit record (with execution_error, decision history, etc.) is
    internal; only the opaque audit_id leaks to the boundary.
    """
    llm = _mock_llm(json.dumps({
        "tool": "enrollment_modify",
        "arguments": {"student_id": "S001", "course_id": "CS101", "action": "drop"},
        "confirmation": "ok",
    }))
    compiled = compile_action_agent(llm)
    internal = await compiled.ainvoke({"user_message": "x"})

    # The full inner_safety_result + audit lives on internal state.
    inner = internal.get("inner_safety_result")
    assert inner is not None
    assert inner.audit is not None
    assert inner.audit.execution_attempted is True
    assert inner.audit.execution_succeeded is True

    # But the boundary output exposes only the audit_id correlator.
    out = await invoke_action_subgraph(
        ActionAgentInput(user_message="x", user_id="u1", session_id="s1"), llm,
    )
    dumped = out.model_dump()
    assert "inner_safety_result" not in dumped
    assert "audit" not in dumped
    assert "execution_error" not in dumped
    assert dumped["audit_id"]  # opaque correlator only


async def test_audit_built_on_deny_path_too():
    """ADR 006 D4: even when inner safety denies (no execute, no
    finalize update), the audit record exists and carries the layer
    attribution. Catches "permission scan" patterns where attackers
    probe what they can't access.
    """
    llm = _mock_llm(json.dumps({
        "tool": "grade_update",
        "arguments": {"student_id": "S001"},  # missing fields → Layer 2 deny
        "confirmation": "ok",
    }))
    compiled = compile_action_agent(llm)
    internal = await compiled.ainvoke({"user_message": "x"})

    inner = internal.get("inner_safety_result")
    assert inner is not None
    audit = inner.audit
    assert audit.decision == InnerSafetyDecision.DENY
    assert audit.short_circuited_at == InnerLayerName.PARAMETER_PRESENCE
    assert audit.execution_attempted is False
    assert audit.execution_succeeded is None


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
    # Clarification path bypasses inner safety entirely (no tool to gate).
    assert internal.get("inner_safety_result") is None

    out = await invoke_action_subgraph(
        ActionAgentInput(user_message="Drop the class", user_id="u1", session_id="s1"), llm,
    )
    assert out.selected_tool == ""
    assert "Which course" in out.response
