"""Tier 2 of the outer safety gate — static rules engine.

Iterates rules from `config/static_rules.yaml` in declaration order and
returns on the first match. Rules read `(intent, user_query_raw,
user_role)` directly — no tool-surface predictor in front (ADR 005 D1).

Engine policy: **first match wins**. The YAML lists DENY rules before
FLAG rules so a query that matches both gets the stricter verdict.

Per-request hot path is regex `search` + lowercase `in`; runs in low ms
even with all rules evaluated. Compilation happens once at module load.

See docs/adr/005-outer-safety-layer.md for rationale and the locked
semantic in tests/test_outer_safety_static_rules.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from time import perf_counter
from typing import Any

import yaml

from safety.outer.schemas import (
    OuterSafetyInput,
    SafetyDecision,
    TierName,
    TierResult,
)

_RULES_PATH = Path(__file__).parent / "config" / "static_rules.yaml"


class _CompiledRule:
    """A pre-compiled rule. All matching state is pure-Python and re-entrant."""

    __slots__ = (
        "id",
        "decision",
        "reason_code",
        "reason_human",
        "required_intent",
        "query_pattern",
        "query_keywords",
    )

    def __init__(self, raw: dict[str, Any]) -> None:
        self.id: str = raw["id"]
        self.decision = SafetyDecision(raw["decision"])
        self.reason_code: str = raw["reason_code"]
        self.reason_human: str = (raw.get("reason_human") or "").strip()

        when = raw.get("when") or {}
        self.required_intent: str | None = when.get("intent")
        self.query_pattern: re.Pattern[str] | None = (
            re.compile(when["query_matches_pattern"], re.IGNORECASE)
            if "query_matches_pattern" in when
            else None
        )
        self.query_keywords: list[str] = [
            kw.lower() for kw in (when.get("query_contains_keywords") or [])
        ]

    def matches(self, intent: str, query: str) -> bool:
        if self.required_intent is not None and intent != self.required_intent:
            return False
        if self.query_pattern is not None and not self.query_pattern.search(query):
            return False
        if self.query_keywords:
            q = query.lower()
            if not any(kw in q for kw in self.query_keywords):
                return False
        return True


def _load_rules() -> list[_CompiledRule]:
    with open(_RULES_PATH) as f:
        data = yaml.safe_load(f) or {}
    return [_CompiledRule(r) for r in (data.get("rules") or [])]


# Module-load precompute. Hot reload not supported — redeploy to pick up
# rule changes (Phase 6 concern).
_RULES: list[_CompiledRule] = _load_rules()


def check_static_rules(safety_input: OuterSafetyInput) -> TierResult:
    """Tier 2 entry point.

    Iterates compiled rules in declaration order; returns on first match.
    If no rule matches → ALLOW.
    """
    t0 = perf_counter()
    intent = safety_input.intent
    query = safety_input.user_query_raw

    for rule in _RULES:
        if rule.matches(intent, query):
            return TierResult(
                tier=TierName.STATIC_RULES,
                decision=rule.decision,
                reason_code=rule.reason_code,
                reason_human=rule.reason_human,
                latency_ms=int((perf_counter() - t0) * 1000),
                metadata={"rule_id": rule.id, "intent": intent},
            )

    return TierResult(
        tier=TierName.STATIC_RULES,
        decision=SafetyDecision.ALLOW,
        reason_code="no_static_rule_matched",
        reason_human="",
        latency_ms=int((perf_counter() - t0) * 1000),
        metadata={"intent": intent, "rules_evaluated": len(_RULES)},
    )
