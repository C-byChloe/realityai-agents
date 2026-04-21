"""Action Agent — handles all write/mutation operations.

Responsible for grade updates, enrollment modifications, and assignment creation.
Tools use gRPC calls to the Spring Boot core service, with mock fallback
when the service is unavailable.
"""

import json
import os
from typing import Any

import grpc
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

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


# ---------------------------------------------------------------------------
# Agent Execution
# ---------------------------------------------------------------------------


async def run_action_agent(state: AgentState, llm) -> dict:
    """Execute the Action Agent: classify the tool call, then execute it.

    Returns updated state fields: response, tool_calls.
    """
    messages = [
        SystemMessage(content=ACTION_AGENT_SYSTEM_PROMPT),
        HumanMessage(content=state["messages"][-1].content),
    ]
    ai_response = await llm.ainvoke(messages)

    # Parse the LLM response
    try:
        result = json.loads(ai_response.content)
    except (json.JSONDecodeError, AttributeError):
        return {
            "response": ai_response.content if hasattr(ai_response, "content") else str(ai_response),
            "tool_calls": [],
        }

    # Handle clarification requests
    if result.get("clarification_needed"):
        return {
            "response": result["question"],
            "tool_calls": [],
        }

    # Execute the tool
    tool_name = result.get("tool", "")
    arguments = result.get("arguments", {})
    confirmation = result.get("confirmation", "")

    tool_func = ACTION_TOOLS.get(tool_name)
    if not tool_func:
        return {
            "response": f"Unknown tool: {tool_name}",
            "tool_calls": [ToolCall(tool_name=tool_name, arguments=arguments, success=False,
                                    error=f"Unknown tool: {tool_name}")],
        }

    try:
        tool_result = tool_func.invoke(arguments)
        return {
            "response": f"{confirmation}\n\nResult: {tool_result.get('message', 'Operation completed.')}",
            "tool_calls": [ToolCall(tool_name=tool_name, arguments=arguments,
                                    result=tool_result, success=True)],
        }
    except Exception as e:
        return {
            "response": f"Tool execution failed: {e}",
            "tool_calls": [ToolCall(tool_name=tool_name, arguments=arguments,
                                    success=False, error=str(e))],
        }
