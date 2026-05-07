"""Unit tests for the Action Agent."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agents.action_agent import (
    ACTION_TOOLS,
    assignment_create,
    enrollment_modify,
    grade_update,
    run_action_agent,
)
from state import AgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(user_msg: str, **overrides) -> AgentState:
    base = {
        "messages": [HumanMessage(content=user_msg)],
        "intent": "action",
        "intent_confidence": 0.95,
        "selected_agent": "action_agent",
        "safety_result": None,
        "tool_calls": [],
        "response": "",
        "user_id": "test-user",
        "session_id": "test-session",
        # Inner safety Layer 1 RBACs against this role. Tests in this
        # module exercise instructor-permitted flows; tests that need
        # to verify student-role denial set user_role explicitly.
        "user_role": "instructor",
        "requires_approval": False,
        "approval_status": None,
    }
    base.update(overrides)
    return base


def _mock_llm(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke.return_value = AIMessage(content=content)
    return mock


# ---------------------------------------------------------------------------
# Tool Tests (mock fallback when gRPC service is unavailable)
# ---------------------------------------------------------------------------


class TestGradeUpdateTool:
    def test_returns_success(self):
        result = grade_update.invoke({
            "student_id": "S001",
            "course_id": "CS101",
            "assignment_id": "A1",
            "grade": "A",
        })
        assert result["success"] is True
        assert result["operation"] == "grade_update"
        assert "S001" in result["message"]

    def test_contains_all_fields(self):
        result = grade_update.invoke({
            "student_id": "S002",
            "course_id": "MATH200",
            "assignment_id": "HW3",
            "grade": "B+",
        })
        assert result["student_id"] == "S002"
        assert result["course_id"] == "MATH200"


class TestEnrollmentModifyTool:
    def test_add_enrollment(self):
        result = enrollment_modify.invoke({
            "student_id": "S001",
            "course_id": "CS201",
            "action": "add",
        })
        assert result["success"] is True
        assert result["action"] == "add"
        assert "enrolled in" in result["message"]

    def test_drop_enrollment(self):
        result = enrollment_modify.invoke({
            "student_id": "S001",
            "course_id": "CS201",
            "action": "drop",
        })
        assert result["success"] is True
        assert "dropped from" in result["message"]


class TestAssignmentCreateTool:
    def test_creates_assignment(self):
        result = assignment_create.invoke({
            "course_id": "CS101",
            "title": "Midterm Exam",
            "due_date": "2026-04-20",
            "description": "Covers chapters 1-5",
        })
        assert result["success"] is True
        assert "CS101" in result["message"]

    def test_creates_without_description(self):
        result = assignment_create.invoke({
            "course_id": "CS101",
            "title": "Quiz 1",
            "due_date": "2026-04-15",
        })
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Agent Execution Tests
# ---------------------------------------------------------------------------


class TestRunActionAgent:
    async def test_routes_to_grade_update(self):
        llm_response = json.dumps({
            "tool": "grade_update",
            "arguments": {
                "student_id": "S001",
                "course_id": "CS101",
                "assignment_id": "A1",
                "grade": "A",
            },
            "confirmation": "Updating grade to A for student S001 in CS101",
        })
        state = _make_state("Change student S001's grade to A in CS101")
        result = await run_action_agent(state, _mock_llm(llm_response))

        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0].tool_name == "grade_update"
        assert result["tool_calls"][0].success is True
        assert "A" in result["response"]

    async def test_routes_to_enrollment_modify(self):
        llm_response = json.dumps({
            "tool": "enrollment_modify",
            "arguments": {
                "student_id": "S001",
                "course_id": "CS201",
                "action": "add",
            },
            "confirmation": "Enrolling student S001 in CS201",
        })
        state = _make_state("Enroll me in CS201")
        result = await run_action_agent(state, _mock_llm(llm_response))

        assert result["tool_calls"][0].tool_name == "enrollment_modify"
        assert result["tool_calls"][0].success is True

    async def test_routes_to_assignment_create(self):
        llm_response = json.dumps({
            "tool": "assignment_create",
            "arguments": {
                "course_id": "CS101",
                "title": "Final Project",
                "due_date": "2026-05-15",
                "description": "Build a web app",
            },
            "confirmation": "Creating assignment 'Final Project' for CS101",
        })
        # Inner safety Layer 4 (live state) verifies the caller is the
        # course's instructor before allowing assignment_create. The mock
        # course state lists `instructor_smith` as CS101's instructor.
        state = _make_state(
            "Create a final project assignment for CS101 due May 15",
            user_id="instructor_smith",
        )
        result = await run_action_agent(state, _mock_llm(llm_response))

        assert result["tool_calls"][0].tool_name == "assignment_create"
        assert result["tool_calls"][0].success is True

    async def test_handles_clarification_request(self):
        llm_response = json.dumps({
            "clarification_needed": True,
            "question": "Which course do you want to update the grade for?",
        })
        state = _make_state("Change my grade")
        result = await run_action_agent(state, _mock_llm(llm_response))

        assert "Which course" in result["response"]
        assert result["tool_calls"] == []

    async def test_handles_unknown_tool(self):
        llm_response = json.dumps({
            "tool": "delete_student",
            "arguments": {"student_id": "S001"},
            "confirmation": "Deleting student",
        })
        state = _make_state("Delete student S001")
        result = await run_action_agent(state, _mock_llm(llm_response))

        # Inner safety Layer 1 (tool_authorization) catches unknown tools
        # via the role × tool matrix. The denial message names the tool
        # that was rejected; the success flag is False.
        assert "delete_student" in result["response"]
        assert result["tool_calls"][0].success is False

    async def test_handles_malformed_llm_response(self):
        state = _make_state("Do something")
        result = await run_action_agent(state, _mock_llm("I don't understand"))

        assert result["response"] == "I don't understand"
        assert result["tool_calls"] == []


class TestToolRegistry:
    def test_all_tools_registered(self):
        assert "grade_update" in ACTION_TOOLS
        assert "enrollment_modify" in ACTION_TOOLS
        assert "assignment_create" in ACTION_TOOLS
        assert len(ACTION_TOOLS) == 3
