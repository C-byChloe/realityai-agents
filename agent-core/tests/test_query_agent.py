"""Unit tests for the Query Agent."""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agents.query_agent import (
    QUERY_TOOLS,
    _format_tool_result,
    course_lookup,
    run_query_agent,
    schedule_query,
    syllabus_retrieve,
)
from state import AgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(user_msg: str, **overrides) -> AgentState:
    base = {
        "messages": [HumanMessage(content=user_msg)],
        "intent": "query",
        "intent_confidence": 0.9,
        "selected_agent": "query_agent",
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
# Mock Tool Tests
# ---------------------------------------------------------------------------


class TestCourseLookup:
    def test_known_course(self):
        result = course_lookup.invoke({"course_id": "CS101"})
        assert result["success"] is True
        assert result["title"] == "Introduction to Computer Science"
        assert result["instructor"] == "Dr. Smith"

    def test_unknown_course(self):
        result = course_lookup.invoke({"course_id": "FAKE999"})
        assert result["success"] is False
        assert "not found" in result["message"]

    def test_case_insensitive(self):
        result = course_lookup.invoke({"course_id": "cs101"})
        assert result["success"] is True


class TestScheduleQuery:
    def test_known_schedule(self):
        result = schedule_query.invoke({"course_id": "CS101"})
        assert result["success"] is True
        assert result["days"] == "Mon/Wed/Fri"
        assert "10:00" in result["time"]

    def test_unknown_schedule(self):
        result = schedule_query.invoke({"course_id": "FAKE999"})
        assert result["success"] is False


class TestSyllabusRetrieve:
    def test_known_syllabus(self):
        result = syllabus_retrieve.invoke({"course_id": "CS101"})
        assert result["success"] is True
        assert len(result["topics"]) > 0
        assert "Textbook" in result["materials"]

    def test_unknown_syllabus(self):
        result = syllabus_retrieve.invoke({"course_id": "FAKE999"})
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Response Formatting Tests
# ---------------------------------------------------------------------------


class TestFormatToolResult:
    def test_format_course_lookup(self):
        result = {
            "course_id": "CS101",
            "title": "Intro to CS",
            "instructor": "Dr. Smith",
            "credits": 3,
            "description": "Learn programming.",
        }
        formatted = _format_tool_result("course_lookup", result)
        assert "Intro to CS" in formatted
        assert "Dr. Smith" in formatted
        assert "3" in formatted

    def test_format_schedule_query(self):
        result = {
            "course_id": "CS101",
            "days": "Mon/Wed/Fri",
            "time": "10:00 AM",
            "room": "Science Hall 201",
        }
        formatted = _format_tool_result("schedule_query", result)
        assert "Mon/Wed/Fri" in formatted
        assert "10:00 AM" in formatted
        assert "Science Hall 201" in formatted

    def test_format_syllabus_retrieve(self):
        result = {
            "course_id": "CS101",
            "topics": ["Week 1: Intro", "Week 2: Variables"],
            "materials": "Textbook: Think Python",
        }
        formatted = _format_tool_result("syllabus_retrieve", result)
        assert "Week 1: Intro" in formatted
        assert "Textbook" in formatted


# ---------------------------------------------------------------------------
# Agent Execution Tests
# ---------------------------------------------------------------------------


class TestRunQueryAgent:
    async def test_routes_to_course_lookup(self):
        llm_response = json.dumps({
            "tool": "course_lookup",
            "arguments": {"course_id": "CS101"},
            "query_type": "deterministic",
        })
        state = _make_state("Tell me about CS101")
        result = await run_query_agent(state, _mock_llm(llm_response))

        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0].tool_name == "course_lookup"
        assert result["tool_calls"][0].success is True
        assert "Introduction to Computer Science" in result["response"]

    async def test_routes_to_schedule_query(self):
        llm_response = json.dumps({
            "tool": "schedule_query",
            "arguments": {"course_id": "CS101"},
            "query_type": "deterministic",
        })
        state = _make_state("What time does CS101 meet?")
        result = await run_query_agent(state, _mock_llm(llm_response))

        assert result["tool_calls"][0].tool_name == "schedule_query"
        assert "Mon/Wed/Fri" in result["response"]

    async def test_routes_to_syllabus_retrieve(self):
        llm_response = json.dumps({
            "tool": "syllabus_retrieve",
            "arguments": {"course_id": "CS101"},
            "query_type": "tutoring",
        })
        state = _make_state("Show me the CS101 syllabus")
        result = await run_query_agent(state, _mock_llm(llm_response))

        assert result["tool_calls"][0].tool_name == "syllabus_retrieve"
        assert result["tool_calls"][0].success is True

    async def test_handles_not_found(self):
        llm_response = json.dumps({
            "tool": "course_lookup",
            "arguments": {"course_id": "FAKE999"},
            "query_type": "deterministic",
        })
        state = _make_state("Tell me about FAKE999")
        result = await run_query_agent(state, _mock_llm(llm_response))

        assert "not found" in result["response"]

    async def test_handles_unknown_tool(self):
        llm_response = json.dumps({
            "tool": "nonexistent_tool",
            "arguments": {},
            "query_type": "deterministic",
        })
        state = _make_state("Do something weird")
        result = await run_query_agent(state, _mock_llm(llm_response))

        assert "Unknown tool" in result["response"]
        assert result["tool_calls"][0].success is False

    async def test_handles_malformed_response(self):
        state = _make_state("What is CS101?")
        result = await run_query_agent(state, _mock_llm("Just a plain text answer"))

        assert result["response"] == "Just a plain text answer"
        assert result["tool_calls"] == []


class TestToolRegistry:
    def test_all_tools_registered(self):
        assert "course_lookup" in QUERY_TOOLS
        assert "schedule_query" in QUERY_TOOLS
        assert "syllabus_retrieve" in QUERY_TOOLS
        assert len(QUERY_TOOLS) == 3
