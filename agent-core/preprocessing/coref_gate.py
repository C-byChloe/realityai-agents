"""Deterministic gate: should the coref resolver run for this turn?

This is intentionally rule-based. Running an LLM to decide whether to
run an LLM is wasteful for a per-turn check, and adds latency on the
common-case (first-turn / unambiguous) path.

False positives are cheap: we pay one extra LLM call and the resolver
will emit `no_rewrite` for queries that are already self-contained.
False negatives leak unresolved pronouns into planning, so the gate
errs on the side of running.
"""

from __future__ import annotations

import re
from typing import Iterable

# Heuristics. Tune as eval data accumulates.
PRONOUN_PATTERNS: list[str] = [
    r"\bit\b",
    r"\bits\b",
    r"\bthat\b",
    r"\bthose\b",
    r"\bthese\b",
    r"\bthis\b",
    r"\bthey\b",
    r"\bthem\b",
    r"\btheir\b",
    # Chinese
    r"它",
    r"那个",
    r"那门",
    r"这个",
    r"这门",
    r"那些",
    r"这些",
]

ELLIPSIS_PATTERNS: list[str] = [
    # Short queries that look like elliptical follow-ups.
    r"^(再查|再看|那|然后|接着)",
    r"^(check|look|find|show)\s+(again|more|those|that)",
    r"^(what about|how about)\s+",
]

DEFINITE_REFERENCE_PATTERNS: list[str] = [
    r"\bthe (course|class|requirement|professor|instructor)\b",
    r"那门课",
    r"那个老师",
    r"那个 ?requirement",
]


def needs_coref(user_query: str, session_history: Iterable) -> bool:
    """Return True if coref resolver should run.

    First-turn queries (empty session_history) skip. Queries with no
    detectable referential expressions skip. Everything else runs.
    """
    history = list(session_history) if session_history is not None else []
    if not history:
        return False

    query_lower = user_query.lower()

    for pattern in PRONOUN_PATTERNS + DEFINITE_REFERENCE_PATTERNS + ELLIPSIS_PATTERNS:
        if re.search(pattern, query_lower):
            return True

    # Very short query in mid-conversation — likely elliptical.
    if len(history) >= 2 and len(user_query.split()) <= 3:
        return True

    return False
