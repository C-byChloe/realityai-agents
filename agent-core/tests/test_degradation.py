"""Integration tests for graceful degradation: retry and escalation."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from agents.degradation import (
    EscalationTicket,
    FailureType,
    detect_hallucination,
    detect_refusal,
    detect_tool_error,
    execute_with_degradation,
    simplify_prompt,
)


# ---------------------------------------------------------------------------
# Failure Detection Tests
# ---------------------------------------------------------------------------


class TestDetectRefusal:
    def test_detects_cant_help(self):
        assert detect_refusal("I can't help with that request") is True

    def test_detects_unable_to(self):
        assert detect_refusal("I'm unable to perform that action") is True

    def test_detects_sorry_cant(self):
        assert detect_refusal("Sorry, I can't do that for you") is True

    def test_detects_as_an_ai(self):
        assert detect_refusal("As an AI, I can't modify grades") is True

    def test_passes_normal_response(self):
        assert detect_refusal("CS101 meets Mon/Wed/Fri at 10am") is False

    def test_passes_empty_response(self):
        assert detect_refusal("") is False


class TestDetectHallucination:
    def test_detects_unknown_course(self):
        context = {"CS101", "CS201"}
        assert detect_hallucination("You should take PHYS999", context) is True

    def test_passes_known_courses(self):
        context = {"CS101", "CS201"}
        assert detect_hallucination("CS101 meets on Monday", context) is False

    def test_empty_context_passes(self):
        assert detect_hallucination("Take CS999", set()) is False

    def test_no_course_ids_in_response(self):
        context = {"CS101"}
        assert detect_hallucination("Hello, how can I help?", context) is False


class TestDetectToolError:
    def test_detects_failed_tool(self):
        tools = [{"success": False, "tool_name": "grade_update", "error": "gRPC timeout"}]
        result = detect_tool_error(tools)
        assert result is not None
        assert result.failure_type == FailureType.TOOL_ERROR

    def test_passes_successful_tools(self):
        tools = [{"success": True, "tool_name": "course_lookup", "error": None}]
        assert detect_tool_error(tools) is None

    def test_passes_empty_tools(self):
        assert detect_tool_error([]) is None


# ---------------------------------------------------------------------------
# Simplified Prompt Tests
# ---------------------------------------------------------------------------


class TestSimplifyPrompt:
    def test_strips_few_shot_examples(self):
        prompt = """You are an agent.

## Instructions
Do stuff.

## Few-Shot Examples

### Example 1:
User: do thing
Agent: did thing

### Example 2:
User: do other thing
Agent: did other thing

## Output Format
JSON only."""
        simplified = simplify_prompt(prompt)
        assert "Few-Shot" not in simplified
        assert "Example 1" not in simplified
        assert "## Output Format" in simplified
        assert "## Instructions" in simplified

    def test_no_examples_returns_unchanged(self):
        prompt = "Simple prompt without examples"
        assert simplify_prompt(prompt) == prompt


# ---------------------------------------------------------------------------
# Retry + Escalation Integration Tests
# ---------------------------------------------------------------------------


class TestRetryFlow:
    async def test_success_on_first_attempt(self):
        agent = AsyncMock(return_value={"response": "CS101 meets MWF", "tool_calls": []})
        result = await execute_with_degradation(
            agent, {}, "query_agent", "What time does CS101 meet?"
        )
        assert "CS101" in result["response"]
        assert "escalation" not in result
        assert agent.call_count == 1

    async def test_retry_on_refusal(self):
        agent = AsyncMock(side_effect=[
            {"response": "I can't help with that", "tool_calls": []},
            {"response": "CS101 meets MWF 10am", "tool_calls": []},
        ])
        result = await execute_with_degradation(
            agent, {}, "query_agent", "CS101 schedule"
        )
        assert "CS101" in result["response"]
        assert agent.call_count == 2

    async def test_escalation_after_two_failures(self):
        agent = AsyncMock(return_value={
            "response": "I can't help with that",
            "tool_calls": [],
        })
        result = await execute_with_degradation(
            agent, {}, "query_agent", "CS101 schedule"
        )
        assert "forwarded" in result["response"]
        assert "instructor" in result["response"]
        assert "escalation" in result
        assert isinstance(result["escalation"], EscalationTicket)
        assert len(result["escalation"].errors) == 2

    async def test_timeout_triggers_retry(self):
        async def slow_agent(**kwargs):
            await asyncio.sleep(60)  # Will timeout
            return {"response": "late", "tool_calls": []}

        result = await execute_with_degradation(
            slow_agent, {}, "action_agent", "update grade",
        )
        assert "forwarded" in result["response"]
        assert result["escalation"].errors[0].failure_type == FailureType.TIMEOUT

    async def test_escalation_includes_context(self):
        agent = AsyncMock(return_value={
            "response": "Sorry, I can't do that for you",
            "tool_calls": [],
        })
        result = await execute_with_degradation(
            agent, {}, "action_agent", "Change my grade to A"
        )
        ticket = result["escalation"]
        assert ticket.user_query == "Change my grade to A"
        assert ticket.agent_type == "action_agent"

    async def test_hallucination_triggers_retry(self):
        agent = AsyncMock(side_effect=[
            {"response": "You should take PHYS999 next", "tool_calls": []},
            {"response": "CS101 is available next semester", "tool_calls": []},
        ])
        result = await execute_with_degradation(
            agent, {}, "query_agent", "What should I take?",
            context_ids={"CS101", "CS201"},
        )
        assert "CS101" in result["response"]
        assert agent.call_count == 2
