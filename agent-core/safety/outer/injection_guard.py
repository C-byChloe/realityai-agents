"""Tier 3 of the outer safety gate — prompt-injection detection.

Replaces the legacy `intent_analyzer.py` (Claude Sonnet, ~800ms, 5
distinct API failure modes) with a specialized injection classifier
(Meta Prompt Guard 2 86M or its heuristic dev fallback).

Why this swap:
  - Tier 3 in this codebase exists to catch injection attempts that
    Tier 2's lexical regex misses. That's a binary classification task,
    not "general safety reasoning."
  - A 70B+ general-purpose LLM doing binary classification is overkill;
    a 86M specialized classifier does it ~16x faster, ~30x cheaper, with
    better F1 on labeled injection corpora.
  - When HiTL means "the user themselves," low-confidence Tier 3 verdicts
    just route to user step-up auth anyway — paying for Claude reasoning
    on top of that doesn't change the outcome on uncertain cases.

Verdict mapping:
  Prompt Guard score | Decision         | Why
  -------------------|------------------|--------------------------------
  >= 0.9 INJECTION   | DENY             | high-confidence attack — block
  0.5–0.9 INJECTION  | FLAG_FOR_REVIEW  | suspicious — let user confirm
  < 0.5 (BENIGN)     | ALLOW            | clean

Failure modes (ADR 005 D8 — fail-open to FLAG, never DENY):
  - Classifier raises (model corrupt / OOM / network) → FLAG_FOR_REVIEW
  - Classifier exceeds 1s timeout → FLAG_FOR_REVIEW
  - Classifier returns malformed result (negative score, non-finite) →
    FLAG_FOR_REVIEW

The 5 failure modes the legacy LLM analyzer had (TimeoutError, generic
Exception, JSONDecodeError, schema violation, low confidence) collapse
to 2 here because there's no JSON parsing and no schema negotiation —
the classifier returns a typed Pydantic-compatible result directly.
"""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any

from safety.outer.prompt_guard import (
    PromptGuardClient,
    PromptGuardLabel,
    PromptGuardResult,
)
from safety.outer.schemas import (
    OuterSafetyInput,
    SafetyDecision,
    TierName,
    TierResult,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S: float = 1.0  # 86M model on CPU ≈ 50ms p50, generous ceiling
DENY_THRESHOLD: float = 0.9
FLAG_THRESHOLD: float = 0.5


async def check_injection(
    safety_input: OuterSafetyInput,
    prompt_guard: PromptGuardClient,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> TierResult:
    """Tier 3 entry point — classify the request as injection or benign.

    Calls the injected `prompt_guard` client and maps its result to a
    TierResult. Any failure (timeout / exception / malformed score) falls
    back to FLAG_FOR_REVIEW per D8.
    """
    t0 = perf_counter()

    try:
        result = await asyncio.wait_for(
            prompt_guard.classify(
                text=safety_input.user_query_raw,
                history=safety_input.conversation_history,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        return _fallback_flag(
            reason_code="prompt_guard_timeout",
            reason_human=(
                f"Injection classifier timed out after {timeout_s}s; "
                f"routing to review."
            ),
            t0=t0,
            metadata={"failure_mode": "timeout"},
        )
    except Exception as e:
        logger.warning("Prompt guard classification failed: %s", e)
        return _fallback_flag(
            reason_code="prompt_guard_exception",
            reason_human=(
                "Injection classifier call failed; routing to review."
            ),
            t0=t0,
            metadata={"failure_mode": "exception", "error": str(e)},
        )

    # Defensive: any non-finite or out-of-range score is treated as a
    # malformed result and routed to review. The real model can't emit
    # these, but a future client could.
    score = result.score
    if not _score_well_formed(score):
        return _fallback_flag(
            reason_code="prompt_guard_malformed_result",
            reason_human="Injection classifier returned out-of-range score.",
            t0=t0,
            metadata={"failure_mode": "malformed_score", "raw_score": str(score)},
        )

    return _result_from_score(result, t0)


def _result_from_score(result: PromptGuardResult, t0: float) -> TierResult:
    """Apply the deny/flag/allow thresholds to a PromptGuard score."""
    score = result.score
    is_injection = result.label is PromptGuardLabel.INJECTION

    if is_injection and score >= DENY_THRESHOLD:
        return TierResult(
            tier=TierName.INJECTION_GUARD,
            decision=SafetyDecision.DENY,
            reason_code="prompt_guard_injection_high_confidence",
            reason_human=(
                "Request matches known prompt-injection patterns "
                "with high confidence."
            ),
            latency_ms=int((perf_counter() - t0) * 1000),
            metadata={
                "score": score,
                "label": result.label.value,
                "detail": result.detail,
            },
        )

    if is_injection and score >= FLAG_THRESHOLD:
        return TierResult(
            tier=TierName.INJECTION_GUARD,
            decision=SafetyDecision.FLAG_FOR_REVIEW,
            reason_code="prompt_guard_injection_uncertain",
            reason_human=(
                "Request shows possible injection signal; routing to review."
            ),
            latency_ms=int((perf_counter() - t0) * 1000),
            metadata={
                "score": score,
                "label": result.label.value,
                "detail": result.detail,
            },
        )

    return TierResult(
        tier=TierName.INJECTION_GUARD,
        decision=SafetyDecision.ALLOW,
        reason_code="prompt_guard_benign",
        reason_human="",
        latency_ms=int((perf_counter() - t0) * 1000),
        metadata={"score": score, "label": result.label.value},
    )


def _fallback_flag(
    *,
    reason_code: str,
    reason_human: str,
    t0: float,
    metadata: dict[str, Any],
) -> TierResult:
    """Build a FLAG_FOR_REVIEW TierResult — universal fallback per D8."""
    return TierResult(
        tier=TierName.INJECTION_GUARD,
        decision=SafetyDecision.FLAG_FOR_REVIEW,
        reason_code=reason_code,
        reason_human=reason_human,
        latency_ms=int((perf_counter() - t0) * 1000),
        metadata=metadata,
    )


def _score_well_formed(score: float) -> bool:
    """Score must be a finite number in [0, 1]. Anything else → fallback."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return False
    if s != s:  # NaN
        return False
    return 0.0 <= s <= 1.0
