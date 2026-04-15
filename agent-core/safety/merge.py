"""Safety merge — combines static and dynamic safety layers.

Uses OR policy: either layer flagging triggers HiTL review.
Both layers run in parallel via asyncio.gather.
"""

import asyncio
from dataclasses import dataclass

from safety.intent_analyzer import IntentAnalysisResult, analyze_intent
from safety.risk_classifier import RiskResult, classify_risk
from state import SafetyResult


async def run_safety_check(
    user_message: str,
    tool_name: str | None,
    llm,
) -> SafetyResult:
    """Run both safety layers in parallel and merge with OR policy.

    Args:
        user_message: The user's original message.
        tool_name: The tool being called (None if no tool call yet).
        llm: LLM instance for the dynamic intent analyzer.

    Returns:
        SafetyResult with merged flagged status.
    """
    # Run both layers in parallel
    static_result, dynamic_result = await asyncio.gather(
        _run_static(tool_name),
        analyze_intent(user_message, llm),
    )

    # OR merge: either layer flagging → flagged
    flagged = (static_result.risk_level == "high") or dynamic_result.flagged

    # Build combined reason
    reasons = []
    if static_result.risk_level == "high" and static_result.reason:
        reasons.append(f"Static: {static_result.reason}")
    if dynamic_result.flagged and dynamic_result.reason:
        reasons.append(f"Dynamic: {dynamic_result.reason}")

    return SafetyResult(
        flagged=flagged,
        reason=" | ".join(reasons) if reasons else None,
        static_risk=static_result.risk_level,
        dynamic_flagged=dynamic_result.flagged,
    )


async def _run_static(tool_name: str | None) -> RiskResult:
    """Wrap sync classify_risk as async for parallel execution."""
    if tool_name is None:
        return RiskResult(risk_level="low", reason=None)
    return classify_risk(tool_name)
