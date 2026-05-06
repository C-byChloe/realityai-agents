"""Schemas produced by the preprocessing layer.

Layer 1 of the query rewrite design — runs once per turn, BEFORE the
planning agent. The output `RewrittenQuery` carries both the resolved
form and the original verbatim user input so traces can show them
side-by-side and downstream code can fall back on low confidence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RewrittenQuery(BaseModel):
    """Output of the coref resolver node.

    Layer 1 of the query rewrite design. Produced once per turn, BEFORE
    planning agent runs. Read by planning agent via the explicit fallback
    chain in `run_planning_agent`.
    """

    original_query: str = Field(
        description="Verbatim user input. Preserved for trace and fallback."
    )
    rewritten_query: str = Field(
        description=(
            "Self-contained query with all coreferences resolved. "
            "What planning_agent actually consumes."
        )
    )
    resolved_entities: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of pronoun/ellipsis -> resolved entity. "
            "E.g., {'it': 'COMS4705', 'next semester': 'Spring 2026'}"
        ),
    )
    rewrite_reason: Literal[
        "coreference",
        "ellipsis",
        "context_inject",
        "no_rewrite",
    ] = Field(
        description=(
            "Why rewrite was applied. 'no_rewrite' means original == rewritten "
            "(used when gate decides skip but we still emit a record for trace consistency)."
        )
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "LLM self-reported confidence in the rewrite. "
            "Downstream may fallback to original if < threshold."
        ),
    )
