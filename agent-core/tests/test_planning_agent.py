"""Unit tests for the Planning Agent."""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agents.planning_agent import (
    PLANNING_TOOLS,
    run_planning_agent,
)
from state import AgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(user_msg: str, **overrides) -> AgentState:
    base = {
        "messages": [HumanMessage(content=user_msg)],
        "intent": "planning",
        "intent_confidence": 0.9,
        "selected_agent": "planning_agent",
        "safety_result": None,
        "tool_calls": [],
        "response": "",
        "user_id": "test-user",
        "session_id": "test-session",
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
# Task Decomposition Tests
# ---------------------------------------------------------------------------


class TestTaskDecomposition:
    async def test_multi_step_query_plan(self):
        """Planning Agent decomposes a query into multiple lookup steps."""
        llm_response = json.dumps({
            "plan": [
                {
                    "step": 1,
                    "description": "Look up CS201 details",
                    "tool": "course_lookup",
                    "arguments": {"course_id": "CS201"},
                },
                {
                    "step": 2,
                    "description": "Check CS201 schedule",
                    "tool": "schedule_query",
                    "arguments": {"course_id": "CS201"},
                },
            ],
            "reasoning": "Need to check both course info and schedule.",
        })
        state = _make_state("Tell me everything about CS201")
        result = await run_planning_agent(state, _mock_llm(llm_response))

        assert len(result["tool_calls"]) == 2
        assert result["tool_calls"][0].tool_name == "course_lookup"
        assert result["tool_calls"][1].tool_name == "schedule_query"
        assert all(tc.success for tc in result["tool_calls"])
        assert "Step 1" in result["response"]
        assert "Step 2" in result["response"]

    async def test_multi_step_with_write_operations(self):
        """Planning Agent can execute write operations as sub-steps."""
        llm_response = json.dumps({
            "plan": [
                {
                    "step": 1,
                    "description": "Look up CS201",
                    "tool": "course_lookup",
                    "arguments": {"course_id": "CS201"},
                },
                {
                    "step": 2,
                    "description": "Drop CS101",
                    "tool": "enrollment_modify",
                    "arguments": {"student_id": "S001", "course_id": "CS101", "action": "drop"},
                },
                {
                    "step": 3,
                    "description": "Enroll in CS201",
                    "tool": "enrollment_modify",
                    "arguments": {"student_id": "S001", "course_id": "CS201", "action": "add"},
                },
            ],
            "reasoning": "Switching enrollment from CS101 to CS201.",
        })
        state = _make_state("Drop CS101 and enroll me in CS201")
        result = await run_planning_agent(state, _mock_llm(llm_response))

        assert len(result["tool_calls"]) == 3
        assert result["tool_calls"][0].tool_name == "course_lookup"
        assert result["tool_calls"][1].tool_name == "enrollment_modify"
        assert result["tool_calls"][2].tool_name == "enrollment_modify"
        assert all(tc.success for tc in result["tool_calls"])

    async def test_single_step_plan(self):
        """Planning Agent handles a plan with only one step."""
        llm_response = json.dumps({
            "plan": [
                {
                    "step": 1,
                    "description": "Look up course info",
                    "tool": "course_lookup",
                    "arguments": {"course_id": "CS101"},
                },
            ],
            "reasoning": "Simple lookup needed.",
        })
        state = _make_state("What is CS101?")
        result = await run_planning_agent(state, _mock_llm(llm_response))

        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0].success is True


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_unknown_tool_in_plan(self):
        """Unknown tool in a step is marked as failed."""
        llm_response = json.dumps({
            "plan": [
                {
                    "step": 1,
                    "description": "Do something unknown",
                    "tool": "nonexistent_tool",
                    "arguments": {},
                },
            ],
            "reasoning": "Testing unknown tool.",
        })
        state = _make_state("Do something impossible")
        result = await run_planning_agent(state, _mock_llm(llm_response))

        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0].success is False
        assert "FAILED" in result["response"]

    async def test_partial_failure(self):
        """Plan continues executing even if one step fails."""
        llm_response = json.dumps({
            "plan": [
                {
                    "step": 1,
                    "description": "Unknown step",
                    "tool": "bad_tool",
                    "arguments": {},
                },
                {
                    "step": 2,
                    "description": "Look up CS101",
                    "tool": "course_lookup",
                    "arguments": {"course_id": "CS101"},
                },
            ],
            "reasoning": "Testing partial failure.",
        })
        state = _make_state("Do two things")
        result = await run_planning_agent(state, _mock_llm(llm_response))

        assert len(result["tool_calls"]) == 2
        assert result["tool_calls"][0].success is False
        assert result["tool_calls"][1].success is True

    async def test_empty_plan(self):
        """Empty plan returns reasoning only."""
        llm_response = json.dumps({
            "plan": [],
            "reasoning": "I can't create a plan for this.",
        })
        state = _make_state("Do nothing")
        result = await run_planning_agent(state, _mock_llm(llm_response))

        assert result["tool_calls"] == []
        assert "can't create a plan" in result["response"]

    async def test_malformed_llm_response(self):
        """Non-JSON response is returned as-is."""
        state = _make_state("Something complex")
        result = await run_planning_agent(state, _mock_llm("I don't understand the request"))

        assert result["response"] == "I don't understand the request"
        assert result["tool_calls"] == []

    async def test_no_plan_key(self):
        """JSON without plan key returns fallback."""
        llm_response = json.dumps({"reasoning": "Not sure what to do."})
        state = _make_state("Something vague")
        result = await run_planning_agent(state, _mock_llm(llm_response))

        assert result["tool_calls"] == []


# ---------------------------------------------------------------------------
# Tool Registry Tests
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def test_has_all_query_tools(self):
        assert "course_lookup" in PLANNING_TOOLS
        assert "schedule_query" in PLANNING_TOOLS
        assert "syllabus_retrieve" in PLANNING_TOOLS

    def test_has_all_action_tools(self):
        assert "grade_update" in PLANNING_TOOLS
        assert "enrollment_modify" in PLANNING_TOOLS
        assert "assignment_create" in PLANNING_TOOLS

    def test_total_tool_count(self):
        assert len(PLANNING_TOOLS) == 6
