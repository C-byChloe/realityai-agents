"""Phase 7 unit tests — Tier 3 injection_guard entry point.

Locks the verdict mapping that ADR 005 D8 + the threshold constants
together prescribe:

  | PromptGuard score | Decision         | reason_code                            |
  |-------------------|------------------|----------------------------------------|
  | >= 0.9 INJECTION  | DENY             | prompt_guard_injection_high_confidence |
  | 0.5–0.9 INJECTION | FLAG_FOR_REVIEW  | prompt_guard_injection_uncertain       |
  | < 0.5             | ALLOW            | prompt_guard_benign                    |

Plus the 2 fail-modes (timeout / exception) and 1 defensive guard
(malformed score) — all → FLAG_FOR_REVIEW per D8 fail-open invariant.
"""

import asyncio

from safety.outer.injection_guard import (
    DENY_THRESHOLD,
    FLAG_THRESHOLD,
    check_injection,
)
from safety.outer.prompt_guard import (
    PromptGuardClient,
    PromptGuardLabel,
    PromptGuardResult,
)
from safety.outer.schemas import (
    OuterSafetyInput,
    SafetyDecision,
    SessionContext,
    TierName,
)


def _inp(query: str = "anything") -> OuterSafetyInput:
    return OuterSafetyInput(
        session=SessionContext(user_id="u1", session_id="s1", user_role="student"),
        intent="query",
        user_query_raw=query,
    )


class _FakeClient:
    """Synchronous test double — returns whatever PromptGuardResult is
    pre-loaded. Keeps tests deterministic without monkeypatching the
    real heuristic client."""

    def __init__(self, result: PromptGuardResult | None = None, raise_exc: Exception | None = None, sleep_s: float = 0.0):
        self._result = result
        self._raise = raise_exc
        self._sleep_s = sleep_s

    async def classify(self, text, history=None):
        if self._sleep_s:
            await asyncio.sleep(self._sleep_s)
        if self._raise:
            raise self._raise
        assert self._result is not None
        return self._result


# ---------------------------------------------------------------------------
# Verdict mapping by score
# ---------------------------------------------------------------------------


class TestVerdictByScore:
    async def test_high_confidence_injection_denies(self):
        client = _FakeClient(PromptGuardResult(label=PromptGuardLabel.INJECTION, score=0.95))
        out = await check_injection(_inp(), client)
        assert out.decision == SafetyDecision.DENY
        assert out.tier == TierName.INJECTION_GUARD
        assert out.reason_code == "prompt_guard_injection_high_confidence"
        assert out.metadata["score"] == 0.95

    async def test_borderline_injection_flags(self):
        """0.5 <= score < 0.9 → FLAG. The user's HiTL setup means the
        end-user gets a step-up confirmation prompt."""
        client = _FakeClient(PromptGuardResult(label=PromptGuardLabel.INJECTION, score=0.7))
        out = await check_injection(_inp(), client)
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "prompt_guard_injection_uncertain"

    async def test_below_flag_threshold_allows(self):
        client = _FakeClient(PromptGuardResult(label=PromptGuardLabel.BENIGN, score=0.95))
        out = await check_injection(_inp(), client)
        assert out.decision == SafetyDecision.ALLOW
        assert out.reason_code == "prompt_guard_benign"

    async def test_threshold_constants_are_what_we_documented(self):
        """Pin DENY_THRESHOLD and FLAG_THRESHOLD against accidental
        regression. If you genuinely want to adjust them, update this
        test alongside the constant — the test's purpose is to make
        the change visible in code review.
        """
        assert DENY_THRESHOLD == 0.9
        assert FLAG_THRESHOLD == 0.5


# ---------------------------------------------------------------------------
# D8 fail-open: every failure mode routes to FLAG, never DENY
# ---------------------------------------------------------------------------


class TestFailureModes:
    async def test_classifier_timeout_flags(self):
        """Real model exceeding 1s → FLAG (not DENY)."""
        client = _FakeClient(sleep_s=10.0)  # never returns
        out = await check_injection(_inp(), client, timeout_s=0.05)
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "prompt_guard_timeout"
        assert out.metadata["failure_mode"] == "timeout"

    async def test_classifier_exception_flags(self):
        """Any classifier exception (model crash, OOM, etc.) → FLAG."""
        client = _FakeClient(raise_exc=RuntimeError("model OOM"))
        out = await check_injection(_inp(), client)
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "prompt_guard_exception"
        assert "model OOM" in out.metadata["error"]

    async def test_malformed_score_flags(self):
        """Defensive guard: a future client that returns a bogus score
        (NaN, inf, -1, 2.5, non-numeric) routes to FLAG instead of
        crashing or trusting the bad number."""
        client = _FakeClient(PromptGuardResult(label=PromptGuardLabel.INJECTION, score=float("nan")))
        out = await check_injection(_inp(), client)
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "prompt_guard_malformed_result"

    async def test_out_of_range_score_flags(self):
        client = _FakeClient(PromptGuardResult(label=PromptGuardLabel.INJECTION, score=1.5))
        out = await check_injection(_inp(), client)
        assert out.decision == SafetyDecision.FLAG_FOR_REVIEW
        assert out.reason_code == "prompt_guard_malformed_result"


# ---------------------------------------------------------------------------
# Result shape contract
# ---------------------------------------------------------------------------


class TestResultShape:
    async def test_result_carries_tier_name(self):
        client = _FakeClient(PromptGuardResult(label=PromptGuardLabel.BENIGN, score=0.99))
        out = await check_injection(_inp(), client)
        assert out.tier == TierName.INJECTION_GUARD

    async def test_result_records_latency(self):
        client = _FakeClient(PromptGuardResult(label=PromptGuardLabel.BENIGN, score=0.99))
        out = await check_injection(_inp(), client)
        assert out.latency_ms >= 0
