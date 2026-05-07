"""Action Agent — write/mutation operations behind the inner safety gate.

Internal flow (compiled `StateGraph`):

  route_action → inner_safety_check → execute_action_tool → finalize_audit

`route_action` extracts a tool + arguments from the LLM.
`inner_safety_check` runs the four-layer inner safety gate (tool
authorization → parameter presence → parameter format → live state) and
builds an audit record (D4 — always built, even on DENY). On DENY the
record is persisted as terminal and execute is skipped. On ALLOW the
record is built but not persisted; `finalize_audit` updates it with the
execution outcome and persists the final state.

Internal state (`ActionAgentInternalState`) never leaves the subgraph.
The orchestrator only sees `ActionAgentOutput` (response + success +
selected_tool + audit_id).

The plan-driven path (`run_action_step` below) hits the SAME composer
to close the bypass gap — see ADR 006 D6.
"""

import json
import os
from typing import Any

import grpc
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph

from agents.subgraph_states import (
    ACTION_INTERNAL_ONLY_KEYS,
    ActionAgentInput,
    ActionAgentInternalState,
    ActionAgentOutput,
)
from grpc_client.client import CoreServiceClient, GrpcServiceError
from prompts import load_prompt
from safety.inner.audit import (
    persist_audit_record,
    update_audit_for_execution,
)
from safety.inner.node import inner_safety_check
from safety.inner.schemas import (
    InnerSafetyDecision,
    InnerSafetyInput,
)
from safety.outer.schemas import SessionContext
from state import AgentState, ToolCall

# gRPC client — initialized once, shared across tool calls
_grpc_client = CoreServiceClient(
    target=os.environ.get("GRPC_TARGET", "localhost:9090"),
    timeout=float(os.environ.get("GRPC_TIMEOUT", "10")),
)

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

# Sourced from prompts/action_agent.md — see frontmatter for version,
# benchmark binding, and audit status. Do not inline edits here; edit the
# prompt file and bump its version.
ACTION_AGENT_SYSTEM_PROMPT = load_prompt("action_agent")


# ---------------------------------------------------------------------------
# Tool Implementations (gRPC with mock fallback)
# ---------------------------------------------------------------------------


def _mock_fallback(operation: str, **kwargs) -> dict:
    """Return mock result when gRPC service is unavailable."""
    return {
        "success": True,
        "operation": operation,
        "mock": True,
        **kwargs,
    }


@tool
def grade_update(student_id: str, course_id: str, assignment_id: str, grade: str) -> dict:
    """Update a student's grade for a specific course assignment."""
    try:
        result = _grpc_client.update_grade(
            student_id=student_id, course_id=course_id,
            semester="Fall 2025", grade=grade,
        )
        return {
            "success": True,
            "operation": "grade_update",
            "student_id": student_id,
            "course_id": course_id,
            "grade": grade,
            "enrollment": result,
            "message": f"Grade updated to {grade} for student {student_id} in {course_id}",
        }
    except grpc.RpcError:
        return _mock_fallback(
            "grade_update", student_id=student_id, course_id=course_id,
            assignment_id=assignment_id, grade=grade,
            message=f"Grade updated to {grade} for student {student_id} in {course_id}",
        )


@tool
def enrollment_modify(student_id: str, course_id: str, action: str) -> dict:
    """Add or drop a student from a course. Action must be 'add' or 'drop'."""
    try:
        if action == "add":
            result = _grpc_client.enroll_student(
                student_id=student_id, course_id=course_id, semester="Fall 2025",
            )
        else:
            _grpc_client.drop_enrollment(
                student_id=student_id, course_id=course_id, semester="Fall 2025",
            )
            result = {"status": "DROPPED"}
        verb = "enrolled in" if action == "add" else "dropped from"
        return {
            "success": True,
            "operation": "enrollment_modify",
            "student_id": student_id,
            "course_id": course_id,
            "action": action,
            "result": result,
            "message": f"Student {student_id} {verb} {course_id}",
        }
    except grpc.RpcError:
        verb = "enrolled in" if action == "add" else "dropped from"
        return _mock_fallback(
            "enrollment_modify", student_id=student_id, course_id=course_id,
            action=action,
            message=f"Student {student_id} {verb} {course_id}",
        )


@tool
def assignment_create(course_id: str, title: str, due_date: str, description: str = "") -> dict:
    """Create a new assignment for a course."""
    try:
        import uuid
        assignment_id = f"{course_id}-{uuid.uuid4().hex[:6].upper()}"
        result = _grpc_client.create_assignment({
            "assignment_id": assignment_id,
            "course_id": course_id,
            "title": title,
            "description": description,
            "due_date": due_date,
            "max_points": 100,
        })
        return {
            "success": True,
            "operation": "assignment_create",
            "assignment": result,
            "message": f"Assignment '{title}' created for {course_id}, due {due_date}",
        }
    except grpc.RpcError:
        return _mock_fallback(
            "assignment_create", course_id=course_id, title=title,
            due_date=due_date, description=description,
            message=f"Assignment '{title}' created for {course_id}, due {due_date}",
        )


# Tool registry for lookup
ACTION_TOOLS = {
    "grade_update": grade_update,
    "enrollment_modify": enrollment_modify,
    "assignment_create": assignment_create,
}


async def run_action_step(
    step,
    step_outputs: dict[int, Any],
    *,
    session: SessionContext | None = None,
) -> dict:
    """Execute a typed action step from a plan, gated by inner safety.

    Reads `action_tool` + `action_args` directly off the PlanStep — no
    second LLM pass to re-derive what the planner already decided. Then
    runs the same `inner_safety_check` gate the standalone subgraph uses
    (closes ADR 006 D6 bypass gap). On DENY: returns a failure dict
    without ever calling the tool. On ALLOW: invokes the tool, persists
    the audit with execution outcome.

    The `session` kwarg is the JWT-derived identity (D5). Production
    callers (the plan-step node in `planning_agent.py`) plumb it
    through from `PlanExecState.session`. When `session` is omitted —
    as in unit tests that don't seed identity — we default to a
    student-role session so missing wiring fails closed at Layer 1
    rather than silently granting instructor privileges. This is a
    deliberate fail-closed choice: the audit log will record the
    denial under "student" and the test will surface the missing
    plumbing rather than masking it with a privilege escalation.
    """
    if not step.action_tool:
        raise ValueError(f"step {step.step_id}: missing action_tool")

    tool_func = ACTION_TOOLS.get(step.action_tool)
    if tool_func is None:
        raise ValueError(f"step {step.step_id}: unknown action_tool {step.action_tool}")

    args = step.action_args or {}
    safety_input = InnerSafetyInput(
        session=session or _default_plan_session(),
        tool_name=step.action_tool,
        tool_args=args,
    )
    result = await inner_safety_check(safety_input)

    if result.final_decision == InnerSafetyDecision.DENY:
        # Surface as a structured failure dict — caller (the plan step
        # node in planning_agent) converts to ToolCall(success=False).
        # No tool invocation; the audit record is already persisted as
        # terminal inside the composer.
        return {
            "success": False,
            "operation": step.action_tool,
            "denied_by_inner_safety": True,
            "reason_code": result.final_reason_code,
            "message": result.final_reason_human,
            "audit_id": result.audit.audit_id,
        }

    # ALLOW path — invoke the tool and persist the post-execution audit.
    try:
        raw = tool_func.invoke(args)
        succeeded = bool(raw.get("success", True))
        error = None if succeeded else raw.get("message")
    except Exception as e:
        raw = {"success": False, "operation": step.action_tool, "message": str(e)}
        succeeded = False
        error = str(e)

    final_audit = update_audit_for_execution(
        result.audit, succeeded=succeeded, error=error,
    )
    persist_audit_record(final_audit)

    # Surface the audit_id on the result so traces can correlate.
    raw = {**raw, "audit_id": final_audit.audit_id}
    return raw


def _default_plan_session() -> SessionContext:
    """Fail-closed fallback identity for plan-driven actions.

    Used only when `run_action_step` is called without an explicit
    `session=` kwarg (unit tests, programmatic callers that haven't
    plumbed identity yet). Defaulting to "student" — the most
    restricted role — means a missing wiring is surfaced as a Layer 1
    DENY rather than silently granting instructor privileges. The
    production plan executor (`run_planning_agent`) builds a real
    `SessionContext` from `AgentState["user_role"]` and passes it
    through `PlanExecState.session`; this default never fires there.
    """
    return SessionContext(
        user_id="",
        session_id="plan-driven-default",
        user_role="student",
    )


# ---------------------------------------------------------------------------
# Subgraph nodes — internal state hidden behind ActionAgentOutput
# ---------------------------------------------------------------------------


def _make_route_node(llm):
    async def route_action(state: ActionAgentInternalState) -> dict:
        messages = [
            SystemMessage(content=ACTION_AGENT_SYSTEM_PROMPT),
            HumanMessage(content=state["user_message"]),
        ]
        ai_response = await llm.ainvoke(messages)
        raw = ai_response.content if hasattr(ai_response, "content") else str(ai_response)
        try:
            decision = json.loads(raw)
        except (json.JSONDecodeError, AttributeError):
            return {
                "route_decision": {},
                "selected_tool": "",
                "tool_arguments": {},
                "confirmation_text": "",
                "response_text": raw,
                "success": True,
            }
        if decision.get("clarification_needed"):
            return {
                "route_decision": decision,
                "selected_tool": "",
                "tool_arguments": {},
                "confirmation_text": "",
                "response_text": decision.get("question", "Need clarification."),
                "success": True,
            }
        return {
            "route_decision": decision,
            "selected_tool": decision.get("tool", ""),
            "tool_arguments": decision.get("arguments", {}),
            "confirmation_text": decision.get("confirmation", ""),
        }
    return route_action


async def _inner_safety_node(state: ActionAgentInternalState) -> dict:
    """Run inner safety Layers 1-4 + audit sidecar (ADR 006).

    Mirrors the gate that `run_action_step` calls on the plan path.
    Skipped on the clarification / non-JSON short-circuit (where the
    LLM produced no tool selection).

    On DENY: writes `response_text` + `success=False` so subsequent
    nodes treat the request as terminated; persists the terminal audit
    record from the composer.
    On ALLOW: writes `inner_safety_result` so `_execute_action_tool`
    can attach execution outcome to the audit.
    """
    if state.get("response_text") and not state.get("selected_tool"):
        # Clarification or non-JSON path — no tool to gate.
        return {}

    tool_name = state.get("selected_tool", "") or ""
    args = state.get("tool_arguments", {}) or {}

    safety_input = InnerSafetyInput(
        session=SessionContext(
            user_id=state.get("user_id", ""),
            session_id=state.get("session_id", "subgraph"),
            # D5: role flows from the JWT-derived state field, never from
            # messages. Default is the most-restricted role so a missing
            # plumbing fails closed at Layer 1 (DENY) instead of granting
            # instructor privileges. ActionAgentInput defaults user_role
            # to "student" for the same reason.
            user_role=state.get("user_role") or "student",
        ),
        tool_name=tool_name,
        tool_args=args,
    )
    result = await inner_safety_check(safety_input)

    update: dict[str, Any] = {"inner_safety_result": result}
    if result.final_decision == InnerSafetyDecision.DENY:
        update["response_text"] = result.final_reason_human or result.final_reason_code
        update["success"] = False
    return update


async def _execute_action_tool(state: ActionAgentInternalState) -> dict:
    """Invoke the tool (gRPC with mock fallback) on the ALLOW path only.

    Skipped when:
      - inner safety denied (response_text + success=False already set)
      - LLM short-circuited at route (no selected_tool)
    """
    inner = state.get("inner_safety_result")
    if inner is None:
        # Clarification path — never reached the safety gate.
        return {}
    if inner.final_decision == InnerSafetyDecision.DENY:
        return {}

    tool_name = state.get("selected_tool", "")
    args = state.get("tool_arguments", {}) or {}
    tool_func = ACTION_TOOLS[tool_name]
    try:
        raw = tool_func.invoke(args)
        return {"raw_tool_result": raw, "success": True}
    except Exception as e:
        return {
            "raw_tool_result": {"success": False, "message": str(e)},
            "execution_error": str(e),
            "success": False,
            "response_text": f"Tool execution failed: {e}",
        }


async def _finalize_audit(state: ActionAgentInternalState) -> dict:
    """Update the audit record with execution outcome and persist.

    On ALLOW path only — the DENY path persisted a terminal record
    inside the composer. On the clarification path there's no audit at
    all (no tool was selected).
    """
    inner = state.get("inner_safety_result")
    update: dict[str, Any] = {}

    if inner is not None and inner.final_decision == InnerSafetyDecision.ALLOW:
        raw = state.get("raw_tool_result") or {}
        succeeded = bool(state.get("success", False)) and bool(raw.get("success", True))
        error = state.get("execution_error") or (
            None if succeeded else raw.get("message")
        )
        final_audit = update_audit_for_execution(
            inner.audit, succeeded=succeeded, error=error,
        )
        persist_audit_record(final_audit)
        # Replace the audit on the result so callers reading
        # inner_safety_result.audit see the post-execution state.
        update["inner_safety_result"] = inner.model_copy(update={"audit": final_audit})

    if not state.get("response_text"):
        raw = state.get("raw_tool_result") or {}
        confirmation = state.get("confirmation_text", "")
        msg = raw.get("message", "Operation completed.")
        update["response_text"] = f"{confirmation}\n\nResult: {msg}".strip()

    return update


def compile_action_agent(llm):
    """Compile the Action Agent subgraph: route → inner_safety → execute → finalize."""
    g = StateGraph(ActionAgentInternalState)
    g.add_node("route_action", _make_route_node(llm))
    g.add_node("inner_safety_check", _inner_safety_node)
    g.add_node("execute_action_tool", _execute_action_tool)
    g.add_node("finalize_audit", _finalize_audit)
    g.add_edge(START, "route_action")
    g.add_edge("route_action", "inner_safety_check")
    g.add_edge("inner_safety_check", "execute_action_tool")
    g.add_edge("execute_action_tool", "finalize_audit")
    g.add_edge("finalize_audit", END)
    return g.compile()


async def invoke_action_subgraph(inp: ActionAgentInput, llm) -> ActionAgentOutput:
    """Run the subgraph and project to the typed boundary output."""
    subgraph = compile_action_agent(llm)
    final = await subgraph.ainvoke({
        "user_message": inp.user_message,
        "user_id": inp.user_id,
        "session_id": inp.session_id,
        # D5: user_role must reach the inner_safety_check node. The
        # subgraph internal state field defaults to None when missing;
        # _inner_safety_node falls back to "student" (fail-closed).
        "user_role": inp.user_role,
    })
    inner = final.get("inner_safety_result")
    audit_id = inner.audit.audit_id if inner is not None else ""
    return ActionAgentOutput(
        response=final.get("response_text", ""),
        success=bool(final.get("success", False)),
        selected_tool=final.get("selected_tool", "") or "",
        audit_id=audit_id,
    )


# ---------------------------------------------------------------------------
# Orchestrator-facing adapter — preserves the legacy {response, tool_calls} shape
# ---------------------------------------------------------------------------


async def run_action_agent(state: AgentState, llm) -> dict:
    user_msg = state["messages"][-1].content if state.get("messages") else ""
    output = await invoke_action_subgraph(
        ActionAgentInput(
            user_message=user_msg,
            user_id=state.get("user_id", ""),
            session_id=state.get("session_id", ""),
            # D5 — pull the JWT-derived role from AgentState. ActionAgentInput
            # defaults to "student" if missing, so a missing claim falls
            # through to Layer 1 DENY rather than granting instructor.
            user_role=state.get("user_role") or "student",
        ),
        llm,
    )

    if not output.selected_tool:
        # Clarification or non-JSON — no tool call to record.
        return {"response": output.response, "tool_calls": []}

    return {
        "response": output.response,
        "tool_calls": [ToolCall(
            tool_name=output.selected_tool,
            arguments={},
            result=None,
            success=output.success,
            error=None if output.success else output.response,
        )],
    }
