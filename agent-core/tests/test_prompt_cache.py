"""Unit tests for prompt caching."""

import pytest

from caching.prompt_cache import (
    AGENT_PROMPTS,
    estimate_token_savings,
    get_cached_system_message,
)


class TestGetCachedSystemMessage:
    def test_action_agent_has_cache_control(self):
        msg = get_cached_system_message("action_agent")
        assert msg.additional_kwargs["cache_control"]["type"] == "ephemeral"
        assert "Action Agent" in msg.content

    def test_query_agent_has_cache_control(self):
        msg = get_cached_system_message("query_agent")
        assert msg.additional_kwargs["cache_control"]["type"] == "ephemeral"
        assert "Query Agent" in msg.content

    def test_planning_agent_has_cache_control(self):
        msg = get_cached_system_message("planning_agent")
        assert msg.additional_kwargs["cache_control"]["type"] == "ephemeral"
        assert "Planning Agent" in msg.content

    def test_unknown_agent_returns_empty(self):
        msg = get_cached_system_message("unknown_agent")
        assert msg.content == ""


class TestAgentPrompts:
    def test_all_agents_have_prompts(self):
        assert "action_agent" in AGENT_PROMPTS
        assert "query_agent" in AGENT_PROMPTS
        assert "planning_agent" in AGENT_PROMPTS

    def test_prompts_are_non_empty(self):
        for name, prompt in AGENT_PROMPTS.items():
            assert len(prompt) > 100, f"{name} prompt is too short"


class TestEstimateTokenSavings:
    def test_savings_with_multiple_requests(self):
        result = estimate_token_savings("action_agent", 100)
        assert result["savings_percent"] > 80  # should save 80%+
        assert result["cached_total_tokens"] < result["uncached_total_tokens"]
        assert result["num_requests"] == 100

    def test_single_request_no_savings(self):
        result = estimate_token_savings("action_agent", 1)
        # With 1 request, there's nothing to cache
        assert result["savings_percent"] == 0.0

    def test_unknown_agent_zero_tokens(self):
        result = estimate_token_savings("unknown", 100)
        assert result["prompt_tokens"] == 0
