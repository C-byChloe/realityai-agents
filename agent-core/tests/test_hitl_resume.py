"""Tests for the HiTL pause/resume contract.

Scenarios (matching the architecture diagram and interview script):

  1. Flagged request → graph pauses BEFORE hitl_approval; ainvoke returns
     with __interrupt__ payload; checkpointer holds state.
  2. Resume with {"approved": True} → graph continues into coref_resolver
     → execution → response_generation. Response is the executed result.
  3. Resume with {"approved": False} → graph routes to response_generation
     with a rejection message; execution never runs.
  4. Two flagged requests on different thread_ids → checkpoints isolated;
     resuming one does not affect the other.

The graph compiles with `MemorySaver` + `interrupt_before=["hitl_approval"]`
via `create_app()`. Tests inject mock LLMs through `patch("orchestrator._get_llm")`.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from orchestrator import create_app
from safety.outer.schemas import SafetyDecision


# ---------------------------------------------------------------------------
# Mocking helpers
# ---------------------------------------------------------------------------


def _state(msg: str) -> dict:
    return {
        "messages": [HumanMessage(content=msg)],
        "intent": "",
        "intent_confidence": 0.0,
        "selected_agent": "",
        # role=instructor so RBAC allows action; Tier 3 below produces the
        # FLAG verdict that triggers the HiTL pause.
        "user_role": "instructor",
        "outer_safety_result": None,
        "tool_calls": [],
        "response": "",
        "user_id": "u1",
        "session_id": "s1",
        "requires_approval": False,
        "approval_status": None,
    }


def _flagged_action_llm() -> AsyncMock:
    """LLM script for the post-pause path only. Phase 7 retired the
    Tier 3 LLM analyzer in favor of Prompt Guard, so the LLM queue no
    longer includes a tier3 response. Tier 3 FLAG is injected via
    `_flag_prompt_guard()` instead — see `_patch_for_flagged_action`.

    Sequence consumed:
      1. classify_intent
      2. coref_resolver gate (only if pronouns; usually no-op)
      3. (after resume) action_agent route_action
      4. (defensive duplicate in case route is re-entered)
    """
    intent = AIMessage(content=json.dumps({"intent": "action", "confidence": 0.95}))
    action_response = AIMessage(content=json.dumps({
        "tool": "enrollment_modify",
        "arguments": {"student_id": "S001", "course_id": "CS201", "action": "add"},
        "confirmation": "Enroll in CS201",
    }))
    mock = AsyncMock()
    mock.ainvoke.side_effect = [intent, action_response, action_response]
    return mock


def _flag_prompt_guard() -> AsyncMock:
    """Fake Prompt Guard returning a borderline-injection score (0.7),
    which `injection_guard` maps to FLAG_FOR_REVIEW. Used to deterministically
    trigger HiTL pause without depending on the heuristic's exact
    threshold for any specific input phrasing.
    """
    from safety.outer.prompt_guard import PromptGuardLabel, PromptGuardResult

    fake = AsyncMock()
    fake.classify.return_value = PromptGuardResult(
        label=PromptGuardLabel.INJECTION, score=0.7
    )
    return fake


def _patch_for_flagged_action(llm_mock: AsyncMock):
    """Helper context manager that patches BOTH _get_llm (for downstream
    agent calls) and the default Prompt Guard (for outer Tier 3 FLAG).
    Tests use it instead of two stacked `with` blocks.
    """
    import contextlib

    @contextlib.contextmanager
    def _patcher():
        with patch("orchestrator._get_llm", return_value=llm_mock), patch(
            "safety.outer.node.get_default_prompt_guard",
            return_value=_flag_prompt_guard(),
        ):
            yield

    return _patcher()


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


# ---------------------------------------------------------------------------
# Scenario 1: pause
# ---------------------------------------------------------------------------


async def test_flagged_request_pauses_before_hitl_approval():
    """A flagged request must pause and not write a final response yet."""
    app = create_app()
    cfg = _config("hitl-pause-1")

    with _patch_for_flagged_action(_flagged_action_llm()):
        result = await app.ainvoke(_state("Enroll me in CS201"), config=cfg)

    # The graph paused — final response is not generated yet.
    assert result["outer_safety_result"].final_decision == SafetyDecision.FLAG_FOR_REVIEW
    assert result["requires_approval"] is True
    # No response from response_generation has been written.
    assert result["response"] == ""

    # Checkpointer holds the snapshot — next node should be hitl_approval.
    snap = await app.aget_state(cfg)
    assert "hitl_approval" in snap.next, f"expected hitl_approval pending, got {snap.next}"


# ---------------------------------------------------------------------------
# Scenario 2: resume approved → continues
# ---------------------------------------------------------------------------


async def test_resume_approved_continues_into_execution():
    app = create_app()
    cfg = _config("hitl-approve-2")

    with _patch_for_flagged_action(_flagged_action_llm()):
        # 1. Initial invocation pauses before hitl_approval
        await app.ainvoke(_state("Enroll me in CS201"), config=cfg)
        # 2. Resume with approval — graph runs hitl_approval, then routes to
        # coref_resolver → execution → response_generation.
        final = await app.ainvoke(Command(resume={"approved": True}), config=cfg)

    assert final["approval_status"] == "approved"
    # Execution actually ran; we expect a non-empty response.
    assert final["response"], f"expected response after approval, got {final!r}"


# ---------------------------------------------------------------------------
# Scenario 3: resume rejected → no execution
# ---------------------------------------------------------------------------


async def test_resume_rejected_does_not_execute():
    app = create_app()
    cfg = _config("hitl-reject-3")

    with _patch_for_flagged_action(_flagged_action_llm()):
        await app.ainvoke(_state("Enroll me in CS201"), config=cfg)
        final = await app.ainvoke(Command(resume={"approved": False}), config=cfg)

    assert final["approval_status"] == "rejected"
    assert "rejected" in final["response"].lower()
    # No tool calls landed because execution was bypassed.
    assert final.get("tool_calls", []) == []


# ---------------------------------------------------------------------------
# Scenario 4: thread_id isolation across pauses
# ---------------------------------------------------------------------------


async def test_two_threads_have_isolated_pauses():
    """Resuming thread A must not affect thread B's pause."""
    app = create_app()
    cfg_a = _config("hitl-iso-a")
    cfg_b = _config("hitl-iso-b")

    # Need separate LLM scripts because the side_effect list is consumed.
    with _patch_for_flagged_action(_flagged_action_llm()):
        await app.ainvoke(_state("Enroll A in CS201"), config=cfg_a)
    with _patch_for_flagged_action(_flagged_action_llm()):
        await app.ainvoke(_state("Enroll B in MATH200"), config=cfg_b)

    # Both paused independently
    snap_a = await app.aget_state(cfg_a)
    snap_b = await app.aget_state(cfg_b)
    assert "hitl_approval" in snap_a.next
    assert "hitl_approval" in snap_b.next

    # Resume thread A only
    with _patch_for_flagged_action(_flagged_action_llm()):
        await app.ainvoke(Command(resume={"approved": False}), config=cfg_a)

    # Thread B is still paused
    snap_b_after = await app.aget_state(cfg_b)
    assert "hitl_approval" in snap_b_after.next, \
        f"thread B should still be paused, got next={snap_b_after.next}"
