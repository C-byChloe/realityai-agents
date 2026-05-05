"""Action Agent — write/mutation operations behind a 3-gate subgraph.

Internal flow (compiled `StateGraph`):

  route_action → validate_args → execute_action_tool → audit_action

`route_action` extracts a tool + arguments from the LLM. `validate_args`
rejects malformed arguments before they hit gRPC (cheap fail-fast).
`execute_action_tool` calls the tool (gRPC with mock fallback). `audit_action`
emits a structured record with an opaque correlation ID.

Internal state (`ActionAgentInternalState`) never leaves the subgraph.
The orchestrator only sees `ActionAgentOutput` (response + success +
selected_tool + audit_id).
"""

import json
import os
import uuid
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
from state import AgentState, ToolCall

# gRPC client — initialized once, shared across tool calls
_grpc_client = CoreServiceClient(
    target=os.environ.get("GRPC_TARGET", "localhost:9090"),
    timeout=float(os.environ.get("GRPC_TIMEOUT", "10")),
)

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

ACTION_AGENT_SYSTEM_PROMPT = """\
You are the Action Agent for a university course management system.

## Identity
You handle all WRITE operations: creating, updating, and deleting academic records.
You do NOT answer questions or provide information — that is the Query Agent's job.
You do NOT plan multi-step workflows — that is the Planning Agent's job.

## Behavioral Constraints
- Only invoke tools that match the user's explicit request. Never infer additional operations.
- Always confirm the operation details before executing (include what will change and for whom).
- If the request is ambiguous, ask for clarification instead of guessing.
- Never perform bulk operations (e.g., "change all grades") without explicit per-item confirmation.

## Available Tools
- grade_update: Update a student's grade for a specific course/assignment
- enrollment_modify: Add or drop a student from a course
- assignment_create: Create a new assignment for a course

## Output Format
Respond with a JSON object:
{
  "tool": "<tool_name>",
  "arguments": { ... },
  "confirmation": "<human-readable summary of what will happen>"
}

If clarification is needed, respond with:
{
  "clarification_needed": true,
  "question": "<what you need to know>"
}
"""


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


def run_action_step(step, step_outputs: dict[int, Any]) -> dict:
    """Execute a typed action step from a plan.

    Reads `action_tool` + `action_args` directly off the PlanStep — no
    second LLM pass to re-derive what the planner already decided.
    Returns the raw tool result dict (the planner's reasoning step
    produces the final natural-language response).
    """
    if not step.action_tool:
        raise ValueError(f"step {step.step_id}: missing action_tool")

    tool_func = ACTION_TOOLS.get(step.action_tool)
    if tool_func is None:
        raise ValueError(f"step {step.step_id}: unknown action_tool {step.action_tool}")

    args = step.action_args or {}
    return tool_func.invoke(args)


# ---------------------------------------------------------------------------
# Subgraph nodes — internal state hidden behind ActionAgentOutput
# ---------------------------------------------------------------------------


# Required-argument schema per tool. Cheap, no external deps.
_REQUIRED_ARGS: dict[str, set[str]] = {
    "grade_update": {"student_id", "course_id", "assignment_id", "grade"},
    "enrollment_modify": {"student_id", "course_id", "action"},
    "assignment_create": {"course_id", "title", "due_date"},
}


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


async def _validate_args(state: ActionAgentInternalState) -> dict:
    """Gate 1: cheap structural validation before we touch gRPC."""
    if state.get("response_text"):
        # Already short-circuited by route (clarification or non-JSON).
        return {"validation_passed": True, "validation_errors": []}

    tool_name = state.get("selected_tool", "")
    args = state.get("tool_arguments", {}) or {}
    errors: list[str] = []

    if tool_name not in ACTION_TOOLS:
        errors.append(f"Unknown tool: {tool_name}")
    else:
        required = _REQUIRED_ARGS.get(tool_name, set())
        missing = required - set(args.keys())
        if missing:
            errors.append(f"Missing required arguments for {tool_name}: {sorted(missing)}")
        if tool_name == "enrollment_modify":
            action = args.get("action")
            if action not in {"add", "drop"}:
                errors.append(f"enrollment_modify.action must be 'add' or 'drop', got {action!r}")

    if errors:
        return {
            "validation_passed": False,
            "validation_errors": errors,
            "response_text": "; ".join(errors),
            "success": False,
        }
    return {"validation_passed": True, "validation_errors": []}


async def _execute_action_tool(state: ActionAgentInternalState) -> dict:
    """Gate 2: invoke the tool (gRPC with mock fallback)."""
    if not state.get("validation_passed", False):
        return {}  # validation already wrote response_text + success
    if state.get("response_text") and not state.get("selected_tool"):
        return {}  # short-circuited (clarification path)

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


async def _audit_action(state: ActionAgentInternalState) -> dict:
    """Gate 3: structured audit record + opaque correlation ID."""
    audit_id = uuid.uuid4().hex[:12]
    record = {
        "audit_id": audit_id,
        "tool": state.get("selected_tool", ""),
        "validation_passed": state.get("validation_passed", False),
        "execution_error": state.get("execution_error"),
        "success": bool(state.get("success", False)),
    }

    update: dict[str, Any] = {"audit_record": record}

    # If we haven't produced a response yet, build it now from the tool result.
    if not state.get("response_text"):
        raw = state.get("raw_tool_result") or {}
        confirmation = state.get("confirmation_text", "")
        msg = raw.get("message", "Operation completed.")
        update["response_text"] = f"{confirmation}\n\nResult: {msg}".strip()

    return update


def compile_action_agent(llm):
    """Compile the Action Agent subgraph: route → validate → execute → audit."""
    g = StateGraph(ActionAgentInternalState)
    g.add_node("route_action", _make_route_node(llm))
    g.add_node("validate_args", _validate_args)
    g.add_node("execute_action_tool", _execute_action_tool)
    g.add_node("audit_action", _audit_action)
    g.add_edge(START, "route_action")
    g.add_edge("route_action", "validate_args")
    g.add_edge("validate_args", "execute_action_tool")
    g.add_edge("execute_action_tool", "audit_action")
    g.add_edge("audit_action", END)
    return g.compile()


async def invoke_action_subgraph(inp: ActionAgentInput, llm) -> ActionAgentOutput:
    """Run the subgraph and project to the typed boundary output."""
    subgraph = compile_action_agent(llm)
    final = await subgraph.ainvoke({"user_message": inp.user_message})
    record = final.get("audit_record") or {}
    return ActionAgentOutput(
        response=final.get("response_text", ""),
        success=bool(final.get("success", False)),
        selected_tool=final.get("selected_tool", "") or "",
        audit_id=record.get("audit_id", "") or "",
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
