"""Output filter — defense-in-depth scrubbing of internal information from
LLM-generated responses before they reach the user.

This is the 4th and final layer of the safety stack:

  | Layer                | Direction       | When                          |
  |----------------------|-----------------|-------------------------------|
  | outer safety         | input → agent   | request entry, pre-tool-pick  |
  | inner safety         | agent → tool    | tool selected, pre-execution  |
  | content isolation    | retrieval → LLM | RAG output, pre-prompt-build  |
  | output filter (this) | LLM → user      | response, pre-user-delivery   |

Threat model:
  - LLM is convinced (via injection or hallucination) to leak its system
    prompt, internal markers, or repo-specific identifiers.
  - LLM accidentally echoes a debug field that a tool result happened
    to include (reason codes, threshold names, audit IDs).
  - A tool result legitimately contains a secret pattern (API key in a
    test fixture, JWT in an error message); the filter catches it
    before it reaches the user.

All three are post-hoc — by the time output_filter runs, "the bad thing
already happened upstream." The filter exists to make sure the bad
thing isn't visible. Findings are logged for auditing so upstream
incidents can be diagnosed; the user just gets `[REDACTED]` in place
of whatever leaked.

Audit safety: findings store sha256[:12] hash of the matched text, NEVER
the matched text itself. Otherwise the audit log becomes the leak channel.
"""

from __future__ import annotations

import hashlib
import logging
import re
from enum import Enum
from typing import Iterable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Sentinel substituted in place of a redacted match. Distinct enough
# that humans + log-aggregators can spot it; short enough that even a
# response of all-redactions stays readable.
REDACTION_TOKEN: str = "[REDACTED]"


class OutputFinding(str, Enum):
    """Categories of internal leakage the filter detects.

    Five categories are deliberate (system_prompt_fragment is deferred
    to Phase 8.3 — needs N-gram / shingle-based fingerprinting which
    is more involved than regex).
    """

    MARKER_LEAK = "marker_leak"
    REASON_CODE = "reason_code"
    THRESHOLD_NAME = "threshold_name"
    SECRET_PATTERN = "secret_pattern"
    INTERNAL_ARCHITECTURE_TERM = "internal_architecture_term"


class OutputFilterFindingDetail(BaseModel):
    """One leak instance. Stores a hash of the matched text — NEVER
    the text — so audit logs don't become a re-leakage channel."""

    category: OutputFinding
    match_hash: str = Field(
        description="sha256 of the matched substring, truncated to 12 hex chars."
    )


class OutputFilterResult(BaseModel):
    """Aggregate verdict written to `AgentState.output_filter_result`.

    `redacted_text` is the user-safe response; `findings` is the
    audit-safe trace of what was redacted (without the leaked content).
    `redactions_count` is a quick metric for dashboards / alerting.
    """

    findings: list[OutputFilterFindingDetail] = Field(default_factory=list)
    redacted_text: str
    redactions_count: int
    original_length: int
    redacted_length: int


# ---------------------------------------------------------------------------
# Pattern registries — kept module-load-precompiled for sub-ms scanning.
# ---------------------------------------------------------------------------


# 1. MARKER_LEAK — Phase 8.1 spotlighting markers leaking back out.
#    If this fires, an attacker has learned the boundary format AND we
#    failed to keep markers out of user-facing text. Both are bad.
_MARKER_PATTERN = re.compile(
    r"\[(?:BEGIN|END)-DATA:[0-9a-fA-F]+\]",
)


# 2. REASON_CODE — internal verdict identifiers from outer + inner safety.
#    Users should see `reason_human` text, never these machine-readable
#    codes. Hardcoded list rather than dynamic introspection so adding a
#    new reason_code is an explicit registry update + filter test, not a
#    silent leak.
_REASON_CODE_TERMS: tuple[str, ...] = (
    # Outer Tier 1 (RBAC)
    "role_grants_action", "role_lacks_action_grant",
    "unknown_role", "unknown_intent_for_role",
    # Outer Tier 2 (static rules) — rule IDs
    "prompt_injection_lexical", "sensitive_info_request",
    "bulk_modify_pattern_detected", "intent_action_lexical_mismatch",
    "bulk_query_other_users", "no_static_rule_matched",
    # Outer Tier 3 (Prompt Guard injection_guard)
    "prompt_guard_injection_high_confidence",
    "prompt_guard_injection_uncertain", "prompt_guard_benign",
    "prompt_guard_timeout", "prompt_guard_exception",
    "prompt_guard_malformed_result",
    # Inner Layer 1 (tool authorization)
    "role_grants_tool", "role_lacks_tool_grant", "unknown_tool_for_role",
    # Inner Layer 2 / 3 (parameter validation)
    "all_required_args_present", "missing_required_args",
    "unknown_tool_for_presence_check",
    "format_checks_passed", "invalid_arg_format",
    # Inner Layer 4 (live state)
    "enrollment_capacity_ok", "course_full",
    "enrollment_record_exists", "not_enrolled",
    "grade_window_closed", "student_not_enrolled",
    "grade_update_preconditions_met",
    "course_ownership_confirmed", "not_course_instructor",
    "live_state_timeout", "live_state_unavailable",
    "course_not_found", "unknown_tool_for_live_state",
    "student_can_only_self_modify_enrollment",
    # Aggregate verdicts
    "all_layers_passed", "all_tiers_passed",
)
_REASON_CODE_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in _REASON_CODE_TERMS) + r")\b"
)


# 3. THRESHOLD_NAME — internal numeric / configuration constants.
_THRESHOLD_PATTERNS = (
    re.compile(
        r"\b(?:DENY_THRESHOLD|FLAG_THRESHOLD|CONFIDENCE_FALLBACK_THRESHOLD"
        r"|MAX_HISTORY_TURNS|DEFAULT_TIMEOUT_S|NONCE_BYTES|HISTORY_DISCOUNT"
        r"|HISTORY_TURNS|MAX_LENGTH)\b"
    ),
    # "confidence < 0.7" / "confidence ≥ 0.5" / similar threshold disclosures
    re.compile(r"\bconfidence\s*[<>≤≥=]+\s*0?\.\d+\b", re.IGNORECASE),
)


# 4. SECRET_PATTERN — defense in depth; secrets shouldn't be in prompts
#    or tool results, but if they are, this is the last line of defense.
_SECRET_PATTERNS = (
    # OpenAI / Anthropic API keys
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"),
    # Slack tokens (xoxb / xoxa / xoxp / xoxs / xoxr)
    re.compile(r"\bxox[abposr]-[0-9A-Za-z-]{10,}\b"),
    # GitHub classic + fine-grained personal access tokens
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    # AWS access key
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    # Bearer header value (long-ish base64-ish payload)
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{30,}\b"),
    # JWT (3 base64url segments dot-separated)
    re.compile(
        r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"
    ),
)


# 5. INTERNAL_ARCHITECTURE_TERM — repo-specific design jargon. Users should
#    get business-language explanations, not implementation references.
_ARCH_PATTERNS = (
    # "ADR 005 D7", "ADR 006", "ADR-005-D7"
    re.compile(r"\bADR[-\s]?\d{3}(?:[-\s]?D\d+)?\b", re.IGNORECASE),
    # "D5 invariant", "D7 invariant"
    re.compile(r"\bD\d+\s+invariant\b", re.IGNORECASE),
    # "Phase 4 cutover" / "Phase 8.1"
    re.compile(r"\bPhase\s+\d+(?:\.\d+)?\b"),
    # Internal jargon nouns
    re.compile(r"\bspotlighting\b", re.IGNORECASE),
    # Code-path hints — module names users shouldn't see
    re.compile(r"\bouter_safety_(?:check|node|result)\b"),
    re.compile(r"\binner_safety_(?:check|node|result)\b"),
    re.compile(r"\bsafety/(?:outer|inner)/[A-Za-z_/]*\.py\b"),
    # Tier names
    re.compile(r"\bTier\s*[1-3]\s+(?:RBAC|static|injection)\b", re.IGNORECASE),
    # Layer names (inner safety)
    re.compile(r"\bLayer\s*[1-4]\s+(?:tool|parameter|live)\b", re.IGNORECASE),
)


_REGISTRY: tuple[tuple[OutputFinding, tuple[re.Pattern[str], ...]], ...] = (
    (OutputFinding.MARKER_LEAK, (_MARKER_PATTERN,)),
    (OutputFinding.REASON_CODE, (_REASON_CODE_PATTERN,)),
    (OutputFinding.THRESHOLD_NAME, _THRESHOLD_PATTERNS),
    (OutputFinding.SECRET_PATTERN, _SECRET_PATTERNS),
    (OutputFinding.INTERNAL_ARCHITECTURE_TERM, _ARCH_PATTERNS),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def filter_response(text: str) -> OutputFilterResult:
    """Scan `text` against all pattern registries; redact any matches.

    Pure function: no I/O, no globals mutated, no LLM. Sub-millisecond
    even on long responses. Idempotent — running the filter twice on
    its own output returns the second result unchanged.

    Each match becomes one finding (with hash, never raw text). Matches
    are replaced with `[REDACTED]` in the returned `redacted_text`.

    Pattern-set ordering: secrets first (highest sensitivity), then
    architectural / reason / threshold leaks, finally markers. The
    order doesn't change correctness — patterns don't overlap by
    construction — but matches the priority of what we'd want to see
    redacted first if the regex engine ever became cost-sensitive.
    """
    if not text:
        return OutputFilterResult(
            findings=[],
            redacted_text="",
            redactions_count=0,
            original_length=0,
            redacted_length=0,
        )

    findings: list[OutputFilterFindingDetail] = []
    redacted = text

    for category, patterns in _REGISTRY:
        for pattern in patterns:
            redacted = pattern.sub(
                _make_redactor(category, findings),
                redacted,
            )

    return OutputFilterResult(
        findings=findings,
        redacted_text=redacted,
        redactions_count=len(findings),
        original_length=len(text),
        redacted_length=len(redacted),
    )


def _make_redactor(
    category: OutputFinding,
    findings: list[OutputFilterFindingDetail],
):
    """Closure factory that captures `category` correctly for the regex
    `sub` callback. Plain inline `lambda m: ...` would late-bind
    `category` to whatever the loop variable points to at call time;
    the factory pattern locks the bind at definition time.
    """

    def _record_and_redact(match: re.Match[str]) -> str:
        matched_text = match.group(0)
        match_hash = hashlib.sha256(
            matched_text.encode("utf-8")
        ).hexdigest()[:12]
        findings.append(
            OutputFilterFindingDetail(
                category=category,
                match_hash=match_hash,
            )
        )
        return REDACTION_TOKEN

    return _record_and_redact


# ---------------------------------------------------------------------------
# Test / introspection helpers — used by the audit log + tests, not
# part of the per-request hot path.
# ---------------------------------------------------------------------------


def known_reason_codes() -> Iterable[str]:
    """The hardcoded reason-code allowlist. Test reference for ensuring
    no production reason_code escapes the filter."""
    return _REASON_CODE_TERMS
