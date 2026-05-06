"""Phase 3 unit tests for outer safety Tier 3 — LLM intent analyzer.

LLM is mocked throughout — no network calls.

Locks ADR 005 D8 (the universal fallback): timeout / parse-error /
unexpected exception / low-confidence ALL fall back to FLAG_FOR_REVIEW,
NEVER to DENY. This is the availability-correctness invariant the whole
analyzer is designed around.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from safety.outer.intent_analyzer import (
    CONFIDENCE_FALLBACK_THRESHOLD,
    analyze_intent,
)
from safety.outer.schemas import (
    OuterSafetyInput,
    SafetyDecision,
    SessionContext,
    TierName,
)


def _inp(query: str = "show me my grades", role: str = "student",
         intent: str = "query", history: list | None = None) -> OuterSafetyInput:
    return OuterSafetyInput(
        session=SessionContext(user_id="u1", session_id="s1", user_role=role),
        intent=intent,
        user_query_raw=query,
        conversation_history=history or [],
    )


def _llm_returning(content: str) -> AsyncMock:
    m = AsyncMock()
    m.ainvoke = AsyncMock(return_value=AIMessage(content=content))
    return m


def _llm_raising(exc: Exception) -> AsyncMock:
    m = AsyncMock()
    m.ainvoke = AsyncMock(side_effect=exc)
    return m


def _llm_hanging() -> AsyncMock:
    """An LLM that never returns — simulates timeout."""

    async def hang(*_a, **_kw):
        await asyncio.sleep(10)

    m = AsyncMock()
    m.ainvoke = hang
    return m


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


class TestResultShape:
    async def test_result_carries_tier_name(self):
        llm = _llm_returning(
            json.dumps({"decision": "allow", "confidence": 0.9, "reason": "ok"})
        )
        out = await analyze_intent(_inp(), llm)
        assert out.tier == TierName.INTENT_ANALYZER

    async def test_result_records_latency(self):
        llm = _llm_returning(
            json.dumps({"decision": "allow", "confidence": 0.9, "reason": "ok"})
        )
        out = await analyze_intent(_inp(), llm)
        assert out.latency_ms >= 0


# ---------------------------------------------------------------------------
# Trusted-verdict path: high-confidence LLM output is honored as-is
# ---------------------------------------------------------------------------


class TestTrustedVerdicts:
    async def test_high_confidence_allow_is_honored(self):
        llm = _llm_returning(
            json.dumps({"decision": "allow", "confidence": 0.92, "reason": "benign"})
        )
        out = await analyze_intent(_inp(), llm)
        assert out.decision == SafetyDecision.ALLOW
        assert out.reason_code == "analyzer_allow"
        assert out.metadata["confidence"] == 0.92

    async def test_high_confidence_deny_is_honored(self):
        llm = _llm_returning(
            json.dumps({
                "decision": "deny",
                "confidence": 0.88,
                "reason": "prompt injection detected",
            })
        )
        out = await analyze_intent(_inp(), llm)
        assert out.decision == SafetyDecision.DENY
        assert out.reason_code == "analyzer_deny"

    async def test_high_confidence_flag_is_honored(self):
        llm = _llm_returning(
            json.dumps({
                "decision": "flag_for_review",
                "confidence": 0.75,
                "reason": "ambiguous",
            })
        )
        out = await analyze_intent(_inp(), llm)
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "analyzer_flag_for_review"


# ---------------------------------------------------------------------------
# ADR D8 invariant: every failure mode → FLAG_FOR_REVIEW, never DENY
# ---------------------------------------------------------------------------


class TestFallbackToFlag:
    async def test_low_confidence_falls_back_to_flag_even_when_llm_said_allow(self):
        llm = _llm_returning(
            json.dumps({"decision": "allow", "confidence": 0.3, "reason": "low"})
        )
        out = await analyze_intent(_inp(), llm)
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "low_confidence"
        # The original LLM verdict + confidence preserved in metadata for trace
        assert out.metadata["raw_decision"] == "allow"
        assert out.metadata["confidence"] == 0.3

    async def test_low_confidence_falls_back_to_flag_even_when_llm_said_deny(self):
        """If the analyzer can't say with confidence even when leaning DENY,
        we don't trust it. FLAG instead — let HiTL or a cheaper layer decide.
        """
        llm = _llm_returning(
            json.dumps({"decision": "deny", "confidence": 0.4, "reason": "weak"})
        )
        out = await analyze_intent(_inp(), llm)
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "low_confidence"

    async def test_threshold_boundary_is_inclusive(self):
        """Confidence == threshold (0.7) is trusted, not flagged."""
        llm = _llm_returning(
            json.dumps({
                "decision": "allow",
                "confidence": CONFIDENCE_FALLBACK_THRESHOLD,
                "reason": "borderline",
            })
        )
        out = await analyze_intent(_inp(), llm)
        assert out.decision == SafetyDecision.ALLOW
        assert out.reason_code == "analyzer_allow"

    async def test_parse_error_falls_back_to_flag(self):
        out = await analyze_intent(_inp(), _llm_returning("not valid json"))
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "llm_parse_error"

    async def test_missing_decision_field_falls_back_to_flag(self):
        out = await analyze_intent(
            _inp(), _llm_returning(json.dumps({"confidence": 0.9}))
        )
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "llm_parse_error"

    async def test_invalid_decision_label_falls_back_to_flag(self):
        out = await analyze_intent(
            _inp(),
            _llm_returning(
                json.dumps({"decision": "maybe", "confidence": 0.9, "reason": "?"})
            ),
        )
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "llm_parse_error"

    async def test_llm_timeout_falls_back_to_flag(self):
        out = await analyze_intent(_inp(), _llm_hanging(), timeout_s=0.05)
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "llm_timeout"

    async def test_llm_exception_falls_back_to_flag(self):
        out = await analyze_intent(_inp(), _llm_raising(RuntimeError("boom")))
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "llm_exception"
        assert "boom" in out.metadata["error"]

    async def test_no_failure_mode_returns_deny(self):
        """The most important invariant test: across ALL failure modes
        explored above, NONE returns DENY. DENY is reserved for trusted
        high-confidence verdicts only.
        """
        # timeout
        out = await analyze_intent(_inp(), _llm_hanging(), timeout_s=0.05)
        assert out.decision != SafetyDecision.DENY

        # exception
        out = await analyze_intent(_inp(), _llm_raising(ValueError("x")))
        assert out.decision != SafetyDecision.DENY

        # parse error
        out = await analyze_intent(_inp(), _llm_returning(""))
        assert out.decision != SafetyDecision.DENY

        # low confidence DENY → FLAG, not DENY
        out = await analyze_intent(
            _inp(),
            _llm_returning(
                json.dumps({"decision": "deny", "confidence": 0.2, "reason": "?"})
            ),
        )
        assert out.decision != SafetyDecision.DENY


# ---------------------------------------------------------------------------
# Multi-turn history is forwarded to the LLM (D7: raw history, not coref-rewritten)
# ---------------------------------------------------------------------------


class TestHistoryHandling:
    async def test_conversation_history_appears_in_prompt(self):
        """The analyzer must see prior turns to detect multi-turn injection.
        Verify the LLM input includes formatted history.
        """
        captured: dict = {}

        async def capturing_invoke(messages, *_a, **_kw):
            captured["messages"] = messages
            return AIMessage(
                content=json.dumps({
                    "decision": "allow", "confidence": 0.9, "reason": "ok"
                })
            )

        llm = AsyncMock()
        llm.ainvoke = capturing_invoke

        history = [
            {"role": "user", "content": "I want to know about COMS3134"},
            {"role": "assistant", "content": "It's a data structures course."},
        ]
        await analyze_intent(_inp(history=history), llm)

        # System message (the analyzer prompt) should include rendered history
        sys_msg_content = captured["messages"][0].content
        assert "COMS3134" in sys_msg_content
        assert "data structures course" in sys_msg_content

    async def test_empty_history_is_handled(self):
        llm = _llm_returning(
            json.dumps({"decision": "allow", "confidence": 0.9, "reason": "ok"})
        )
        out = await analyze_intent(_inp(history=[]), llm)
        assert out.decision == SafetyDecision.ALLOW
