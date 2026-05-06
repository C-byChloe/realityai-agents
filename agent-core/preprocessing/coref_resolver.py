"""Coref resolver — Layer 1 of query rewrite.

Coref resolution runs on the `execute` branch only — it sits between
safety_check and execution. The `awaiting_approval` branch routes
directly from hitl_approval to response_generation and does not pass
through this node, so `user_query_normalized` will be unset in flows
that go through approval.

If a future revision adds an edge from hitl_approval back into
execution (e.g., to actually perform an approved action), that edge
must either run coref_resolver first, or its downstream consumers
must fallback to `state["messages"][-1].content`.

This is a forward-compat guard, not a v1 concern — current graph has
no such edge.

Architectural rationale:
  - Separating coref from planning lets us evaluate coref accuracy
    independently and skip the LLM call for first-turn / unambiguous
    queries via the deterministic gate.
  - Placing coref AFTER safety_check (not before) ensures the safety
    pipeline sees the raw user message — multi-turn prompt-injection
    patterns cannot dilute or reframe the malicious signal in a rewrite.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain_anthropic import ChatAnthropic

from preprocessing.coref_gate import needs_coref
from preprocessing.schemas import RewrittenQuery

logger = logging.getLogger(__name__)

CONFIDENCE_FALLBACK_THRESHOLD = 0.5
MAX_HISTORY_TURNS = 6  # bound prompt size

COREF_SYSTEM_PROMPT = """\
You resolve coreferences and ellipsis in academic-advising follow-up queries.

Given:
- A conversation history (prior user turns + assistant responses)
- A new user query that may contain pronouns ("it", "that", "those"),
  definite references ("the course"), or elliptical phrases
  ("再查一下", "what about Spring?")

Produce:
- A self-contained rewritten query with all referents made explicit
- A map of resolved entities
- A confidence score

Rules:
1. Preserve user intent exactly. Do NOT add information user did not imply.
2. If the query is already self-contained, return it unchanged with
   rewrite_reason="no_rewrite" and confidence ≥ 0.9.
3. If a referent is genuinely ambiguous (multiple plausible antecedents),
   pick the most recent and lower confidence (0.4–0.6).
4. Do NOT resolve into hallucinated entities — if no referent exists in
   history, leave the pronoun as-is and lower confidence (< 0.5).

Output must conform to the RewrittenQuery schema.
"""


def _get_llm():
    """Build a ChatAnthropic client. Mirrors orchestrator._get_llm."""
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        temperature=0,
    )


def _format_history_for_prompt(history: list, current_query: str) -> str:
    """Format conversation history for the coref LLM prompt.

    Keeps only the most recent MAX_HISTORY_TURNS messages so prompt
    size stays bounded.
    """
    recent = history[-MAX_HISTORY_TURNS:] if len(history) > MAX_HISTORY_TURNS else history

    formatted: list[str] = []
    for msg in recent:
        # Accept either dict-style {"role", "content"} or LangChain BaseMessage.
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content", "")
        else:
            role = getattr(msg, "type", None) or getattr(msg, "role", "user")
            content = getattr(msg, "content", "")
        formatted.append(f"[{role}] {content}")
    formatted.append(f"[user_new] {current_query}")
    return "\n".join(formatted)


def make_coref_resolver_node(llm=None):
    """Build the coref_resolver_node with a bound LLM.

    Mirrors the `_make_route_node(llm)` factory used by the query/action
    subgraphs, so tests can pass a mock LLM and the orchestrator passes
    a real ChatAnthropic instance.

    Pass `llm=None` to defer creation until first call (production path).
    """

    async def coref_resolver_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages", [])
        if not messages:
            return {}

        last = messages[-1]
        user_query = last.content if hasattr(last, "content") else str(last)
        history = messages[:-1]

        # Gate first — empty history or no referential expressions → skip LLM.
        if not needs_coref(user_query, history):
            rewritten = RewrittenQuery(
                original_query=user_query,
                rewritten_query=user_query,
                rewrite_reason="no_rewrite",
                confidence=1.0,
            )
            return {
                "rewritten_query": rewritten,
                "user_query_normalized": user_query,
            }

        # Gate fired — call the LLM with structured output.
        active_llm = llm if llm is not None else _get_llm()
        structured = active_llm.with_structured_output(RewrittenQuery)
        prompt = _format_history_for_prompt(list(history), user_query)

        try:
            rewritten: RewrittenQuery = await structured.ainvoke(
                [
                    {"role": "system", "content": COREF_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]
            )
        except Exception as e:
            logger.warning("Coref resolution failed, falling back to original: %s", e)
            rewritten = RewrittenQuery(
                original_query=user_query,
                rewritten_query=user_query,
                rewrite_reason="no_rewrite",
                confidence=0.0,  # signal failure
            )

        # Confidence-based fallback: low confidence → use original verbatim.
        effective_query = (
            rewritten.rewritten_query
            if rewritten.confidence >= CONFIDENCE_FALLBACK_THRESHOLD
            else rewritten.original_query
        )

        return {
            "rewritten_query": rewritten,
            "user_query_normalized": effective_query,
        }

    return coref_resolver_node


# Production node binding — uses the default _get_llm() at first call.
coref_resolver_node = make_coref_resolver_node(llm=None)
