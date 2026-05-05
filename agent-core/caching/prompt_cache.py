"""Prompt caching — static system prompt prefix caching via Anthropic cache_control.

Extracts static system prompt prefixes per agent and applies cache_control
breakpoints so Anthropic's API caches the static portion across requests.
"""

from langchain_core.messages import SystemMessage

from agents.action_agent import ACTION_AGENT_SYSTEM_PROMPT
from agents.planning_agent import PLANNING_AGENT_SYSTEM_PROMPT
from agents.query_agent import QUERY_AGENT_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# Static prompt prefixes per agent
# ---------------------------------------------------------------------------

AGENT_PROMPTS = {
    "action_agent": ACTION_AGENT_SYSTEM_PROMPT,
    "query_agent": QUERY_AGENT_SYSTEM_PROMPT,
    "planning_agent": PLANNING_AGENT_SYSTEM_PROMPT,
}


def get_cached_system_message(agent_name: str) -> SystemMessage:
    """Build a SystemMessage with Anthropic cache_control for the static prefix.

    The cache_control breakpoint tells Anthropic's API to cache everything
    up to this point. Subsequent requests with the same prefix hit the cache,
    reducing token costs by ~90% for the cached portion.

    Args:
        agent_name: One of "action_agent", "query_agent", "planning_agent".

    Returns:
        SystemMessage with cache_control metadata for Anthropic prompt caching.
    """
    prompt = AGENT_PROMPTS.get(agent_name, "")
    return SystemMessage(
        content=prompt,
        additional_kwargs={
            "cache_control": {"type": "ephemeral"},
        },
    )


def estimate_token_savings(agent_name: str, num_requests: int) -> dict:
    """Estimate token savings from prompt caching.

    Assumes ~4 chars per token and that cached tokens cost ~10% of uncached.

    Args:
        agent_name: The agent whose prompt to estimate.
        num_requests: Number of requests to estimate savings over.

    Returns:
        Dict with estimated tokens, savings percentage, etc.
    """
    prompt = AGENT_PROMPTS.get(agent_name, "")
    estimated_tokens = len(prompt) // 4  # rough chars-to-tokens

    uncached_total = estimated_tokens * num_requests
    # First request is uncached; subsequent hit cache at ~10% cost
    cached_total = estimated_tokens + estimated_tokens * (num_requests - 1) * 0.1

    return {
        "agent": agent_name,
        "prompt_tokens": estimated_tokens,
        "num_requests": num_requests,
        "uncached_total_tokens": uncached_total,
        "cached_total_tokens": int(cached_total),
        "savings_percent": round((1 - cached_total / uncached_total) * 100, 1) if uncached_total > 0 else 0,
    }
