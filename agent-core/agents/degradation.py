"""Graceful degradation — retry logic and instructor escalation.

Handles failure detection, retry with simplified prompts, and
escalation to instructors on consecutive failures.
"""

import asyncio
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine


class FailureType(str, Enum):
    TIMEOUT = "timeout"
    REFUSAL = "refusal"
    HALLUCINATION = "hallucination"
    TOOL_ERROR = "tool_error"


@dataclass
class FailureResult:
    """Details of a detected failure."""

    failure_type: FailureType
    message: str
    original_response: str = ""


@dataclass
class EscalationTicket:
    """Escalation to instructor on consecutive failures."""

    user_query: str
    agent_type: str
    tools_attempted: list[str] = field(default_factory=list)
    errors: list[FailureResult] = field(default_factory=list)
    instructor_name: str = "your instructor"


# ---------------------------------------------------------------------------
# Failure Detection
# ---------------------------------------------------------------------------

TIMEOUT_SECONDS = 30

REFUSAL_PATTERNS = [
    r"i can(?:'t| not) (?:help|assist|do that)",
    r"i(?:'m| am) (?:not able|unable) to",
    r"(?:sorry|apologies),? (?:but )?i (?:can(?:'t| not)|don(?:'t| not))",
    r"as an ai,? i (?:can(?:'t| not)|don(?:'t| not))",
    r"i (?:must |need to )?(?:decline|refuse)",
]


def detect_refusal(response: str) -> bool:
    """Check if the response contains LLM refusal patterns."""
    lower = response.lower()
    return any(re.search(p, lower) for p in REFUSAL_PATTERNS)


def detect_hallucination(response: str, context_ids: set[str]) -> bool:
    """Check if the response references entities not in the retrieval context.

    Looks for course ID patterns (e.g., CS101, MATH200) in the response
    that aren't present in the provided context.
    """
    if not context_ids:
        return False

    # Find course-like IDs in the response
    found_ids = set(re.findall(r'\b[A-Z]{2,4}\d{3}\b', response.upper()))
    unknown_ids = found_ids - context_ids
    return len(unknown_ids) > 0


def detect_tool_error(tool_calls: list[dict]) -> FailureResult | None:
    """Check if any tool call resulted in an error."""
    for tc in tool_calls:
        if not tc.get("success", True):
            return FailureResult(
                failure_type=FailureType.TOOL_ERROR,
                message=f"Tool '{tc.get('tool_name', 'unknown')}' failed: {tc.get('error', 'unknown error')}",
            )
    return None


# ---------------------------------------------------------------------------
# Simplified Prompt (strip few-shot examples)
# ---------------------------------------------------------------------------

FEW_SHOT_MARKERS = [
    "## Few-Shot Examples",
    "### Example 1:",
    "### Example 2:",
    "### Example 3:",
]


def simplify_prompt(prompt: str) -> str:
    """Strip few-shot examples from a system prompt for retry.

    Removes everything from the first few-shot marker to the end of
    the examples section.
    """
    for marker in FEW_SHOT_MARKERS:
        idx = prompt.find(marker)
        if idx != -1:
            # Find the next ## section after examples, or take rest of string
            rest = prompt[idx:]
            # Look for next top-level heading after the examples
            next_section = re.search(r'\n## (?!Few-Shot|Example)', rest)
            if next_section:
                return prompt[:idx] + rest[next_section.start():]
            else:
                return prompt[:idx].rstrip()
    return prompt


# ---------------------------------------------------------------------------
# Retry + Escalation Logic
# ---------------------------------------------------------------------------

MAX_RETRIES = 1  # 1 retry = 2 total attempts before escalation


async def execute_with_degradation(
    agent_fn: Callable[..., Coroutine],
    agent_kwargs: dict[str, Any],
    agent_type: str,
    user_query: str,
    context_ids: set[str] | None = None,
) -> dict:
    """Execute an agent with retry and escalation on failure.

    Flow:
    1. First attempt with full prompt
    2. On failure: retry with simplified prompt
    3. On second failure: escalate to instructor

    Args:
        agent_fn: Async function to execute the agent.
        agent_kwargs: Kwargs to pass to agent_fn.
        agent_type: Name of the agent (for escalation context).
        user_query: Original user message.
        context_ids: Set of known entity IDs for hallucination detection.

    Returns:
        Dict with response and tool_calls (or escalation message).
    """
    errors: list[FailureResult] = []
    tools_attempted: list[str] = []

    for attempt in range(MAX_RETRIES + 1):
        try:
            result = await asyncio.wait_for(
                agent_fn(**agent_kwargs),
                timeout=TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            errors.append(FailureResult(
                failure_type=FailureType.TIMEOUT,
                message=f"Agent timed out after {TIMEOUT_SECONDS}s",
            ))
            continue

        response = result.get("response", "")
        tool_calls = result.get("tool_calls", [])
        tools_attempted.extend(tc.tool_name if hasattr(tc, 'tool_name') else tc.get("tool_name", "") for tc in tool_calls)

        # Check for failures
        failure = None

        if detect_refusal(response):
            failure = FailureResult(FailureType.REFUSAL, "LLM refused the request", response)
        elif context_ids and detect_hallucination(response, context_ids):
            failure = FailureResult(FailureType.HALLUCINATION, "Response references unknown entities", response)
        else:
            tool_err = detect_tool_error(
                [{"success": tc.success, "tool_name": tc.tool_name, "error": tc.error}
                 if hasattr(tc, 'success') else tc
                 for tc in tool_calls]
            )
            if tool_err:
                failure = tool_err

        if failure is None:
            return result  # Success

        errors.append(failure)

    # All attempts failed — escalate
    ticket = EscalationTicket(
        user_query=user_query,
        agent_type=agent_type,
        tools_attempted=list(set(tools_attempted)),
        errors=errors,
    )

    return {
        "response": f"I've forwarded your request to {ticket.instructor_name}. "
                     f"They'll be able to help you with this.",
        "tool_calls": [],
        "escalation": ticket,
    }
