"""Integration tests for the two-layer safety merge and HiTL flow."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from orchestrator import (
    _infer_tool_from_intent,
    hitl_approval,
    safety_check,
)
from safety.merge import run_safety_check
from state import AgentState, SafetyResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_llm(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke.return_value = AIMessage(content=content)
    return mock


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


# ---------------------------------------------------------------------------
# OR Merge Policy Tests
# ---------------------------------------------------------------------------


class TestMergePolicy:
    """Test the OR merge: either layer flagging → flagged."""

    async def test_static_high_dynamic_pass(self):
        """Static flags (high-risk tool), dynamic passes → flagged."""
        llm = _mock_llm(json.dumps({"flagged": False, "reason": None}))
        result = await run_safety_check("Update grade", "grade_update", llm)

        assert result.flagged is True
        assert result.static_risk == "high"
        assert result.dynamic_flagged is False
        assert "Static" in result.reason

    async def test_static_low_dynamic_flags(self):
        """Static passes (low-risk tool), dynamic flags → flagged."""
        llm = _mock_llm(json.dumps({"flagged": True, "reason": "Bulk operation"}))
        result = await run_safety_check("Look up all grades", "course_lookup", llm)

        assert result.flagged is True
        assert result.static_risk == "low"
        assert result.dynamic_flagged is True
        assert "Dynamic" in result.reason

    async def test_both_flag(self):
        """Both layers flag → flagged with combined reasons."""
        llm = _mock_llm(json.dumps({"flagged": True, "reason": "Bulk grade change"}))
        result = await run_safety_check("Change all grades to A", "grade_update", llm)

        assert result.flagged is True
        assert result.static_risk == "high"
        assert result.dynamic_flagged is True
        assert "Static" in result.reason
        assert "Dynamic" in result.reason

    async def test_both_pass(self):
        """Both layers pass → not flagged."""
        llm = _mock_llm(json.dumps({"flagged": False, "reason": None}))
        result = await run_safety_check("What time does CS101 meet?", "course_lookup", llm)

        assert result.flagged is False
        assert result.static_risk == "low"
        assert result.dynamic_flagged is False

    async def test_no_tool_name_passes_static(self):
        """No tool name → static passes (low), only dynamic matters."""
        llm = _mock_llm(json.dumps({"flagged": False, "reason": None}))
        result = await run_safety_check("Hello", None, llm)

        assert result.flagged is False
        assert result.static_risk == "low"


# ---------------------------------------------------------------------------
# Parallel Execution Tests
# ---------------------------------------------------------------------------


class TestParallelExecution:
    async def test_both_layers_called(self):
        """Verify both static and dynamic layers are invoked."""
        llm = _mock_llm(json.dumps({"flagged": False, "reason": None}))
        result = await run_safety_check("test", "course_lookup", llm)

        # LLM was called (dynamic analyzer)
        llm.ainvoke.assert_called_once()
        # Static result is present
        assert result.static_risk is not None


# ---------------------------------------------------------------------------
# HiTL Approval Node Tests
# ---------------------------------------------------------------------------


class TestHiTLApproval:
    async def test_returns_pending_status(self):
        state = _make_state(
            "Change all grades",
            safety_result=SafetyResult(flagged=True, reason="Bulk operation"),
        )
        result = await hitl_approval(state)

        assert result["approval_status"] == "pending"
        assert "instructor approval" in result["response"]
        assert "Bulk operation" in result["response"]

    async def test_includes_safety_reason(self):
        state = _make_state(
            "Delete student",
            safety_result=SafetyResult(
                flagged=True,
                reason="Static: high-risk | Dynamic: scope mismatch",
            ),
        )
        result = await hitl_approval(state)

        assert "Static" in result["response"]
        assert "Dynamic" in result["response"]


# ---------------------------------------------------------------------------
# Intent-to-Tool Mapping Tests
# ---------------------------------------------------------------------------


class TestInferToolFromIntent:
    def test_action_maps_to_high_risk_tool(self):
        result = _infer_tool_from_intent("action")
        assert result == "grade_update"

    def test_query_maps_to_none(self):
        assert _infer_tool_from_intent("query") is None

    def test_planning_maps_to_none(self):
        assert _infer_tool_from_intent("planning") is None


# ---------------------------------------------------------------------------
# Integration: "Change all grades to A" triggers both layers and blocks
# ---------------------------------------------------------------------------


class TestEndToEndSafetyBlocking:
    async def test_change_all_grades_triggers_both_layers(self):
        """E2E: 'Change all grades to A' should be flagged by both layers."""
        llm = _mock_llm(json.dumps({
            "flagged": True,
            "reason": "Bulk operation affecting all students",
        }))
        result = await run_safety_check(
            "Change all grades to A",
            "grade_update",
            llm,
        )

        assert result.flagged is True
        assert result.static_risk == "high"
        assert result.dynamic_flagged is True
        assert "Static" in result.reason
        assert "Dynamic" in result.reason
