"""Prompt audit — overkill and leakage checks for the prompt library.

Run:
    python -m tools.prompt_audit                     # audit all prompts
    python -m tools.prompt_audit coref_resolver      # audit one
    python -m tools.prompt_audit --json              # machine-readable output
    python -m tools.prompt_audit --strict            # exit 1 on any warning

What this catches:

  Overkill — prompts that pay token cost or behavioral cost without
  pulling weight. Categories:
    - token_count       : raw cl100k_base tokens (≤ 800 warn, ≤ 1500 fail)
    - rules_count       : numbered/bulleted directives the LLM has to
                          satisfy. Beyond ~10, instruction following
                          empirically degrades.
    - examples_count    : few-shot examples. Useful in moderation; too
                          many pulls the LLM toward "pattern-match the
                          listed examples" rather than "understand the
                          rule."
    - shouting_ratio    : MUST / NEVER / ALWAYS / DO NOT density. LLMs
                          desensitize to caps; >5% of words is a smell.
    - redundancy        : sentences repeating the same instruction
                          (heuristic — token-set overlap).

  Leakage — content that is unsafe to log or unsafe to expose. Surfaces
  this prompt could leak through: LangSmith traces, CI artifacts, error
  responses, LLM provider logs, model fine-tuning corpus.
    - hardcoded_thresholds : numbers that look like internal policy
                             knobs (e.g. `confidence < 0.3`, `top_k = 5`).
                             Documenting target ranges in prose is OK
                             (often necessary); embedded magic numbers
                             give attackers a bypass playbook.
    - pii_patterns         : email / phone / SSN regex.
    - internal_identifiers : specific real-world IDs (course codes
                             matching repo's mock data, real student
                             IDs from canvas mocks). These shouldn't
                             be in prompts that are expected to ship.
    - shell_or_sql         : SQL keywords, shell metachars — would
                             indicate prompt-injection vector or
                             accidental data-layer leakage.

This audit is rule-based and offline. There's a separate optional
LLM-as-judge pass (`--with-llm-judge`) that asks Claude to spot
exploit playbooks the regexes miss; not run by default to keep the
audit free + reproducible.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# tiktoken is the cl100k_base tokenizer (OpenAI). Within ~10% of Claude
# tokens for English/code; absolute counts not exact, but the relative
# comparisons + threshold checks here are robust to that error.
import tiktoken

from prompts import _PROMPTS_DIR, list_prompts, load_prompt, load_prompt_meta

# ---------------------------------------------------------------------------
# Thresholds — tunable. Keep modest defaults; flag-don't-fail on warnings.
# ---------------------------------------------------------------------------

TOKEN_WARN = 800
TOKEN_FAIL = 1500

RULES_WARN = 10
RULES_FAIL = 20

EXAMPLES_WARN = 5
EXAMPLES_FAIL = 10

SHOUTING_RATIO_WARN = 0.05  # 5% of words in caps-emphasis
SHOUTING_RATIO_FAIL = 0.10

REDUNDANCY_WARN = 0.30  # 30% of sentence pairs overlap > 0.6 token-set


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_RULE_LINE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+\S", re.MULTILINE)
_EXAMPLE_HEADER = re.compile(
    r"^(#+\s*)?(example\s*\d*|few[- ]shot|sample\s*input)",
    re.IGNORECASE | re.MULTILINE,
)
_SHOUT_TOKEN = re.compile(r"\b(MUST|NEVER|ALWAYS|REQUIRED|DO NOT|FORBIDDEN|CRITICAL)\b")

# Leakage patterns
_HARDCODED_THRESHOLD = re.compile(
    r"""(?ix)
    (?:
      (?:confidence|threshold|top[_-]?k|max[_-]?\w+|min[_-]?\w+)
      \s*[<>=]+\s*[0-9.]+
      |
      [0-9]+\s*%
    )
    """,
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b")
_SSN = re.compile(r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b")
# Repo-specific real identifiers — anything that matches the mock data
# in agent-core/agents/*. Adjust these patterns when adding new mocks.
_INTERNAL_IDS = re.compile(
    r"""(?x)
    \b(?:
      u[12]\b                                    # mock user_ids
      | COMS\d{4}                                # specific Columbia codes
      | CS3\d{3}                                 # specific course codes
      | dr\.\s*(?:lee|park|chen|singh|davis)     # mock instructors
    )
    """,
    re.IGNORECASE,
)
_SQL_OR_SHELL = re.compile(
    r"""(?ix)
    \b(?:
      SELECT\s+.+\sFROM
      | DROP\s+TABLE
      | rm\s+-rf
      | curl\s+http
    )\b
    """,
)


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------


Severity = str  # "ok" | "warn" | "fail"


@dataclass
class Finding:
    check: str
    severity: Severity
    detail: str
    value: Any = None


@dataclass
class PromptAudit:
    prompt_id: str
    version: str
    overall_severity: Severity = "ok"
    findings: list[Finding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def _bump(audit: PromptAudit, finding: Finding) -> None:
    audit.findings.append(finding)
    if finding.severity == "fail":
        audit.overall_severity = "fail"
    elif finding.severity == "warn" and audit.overall_severity != "fail":
        audit.overall_severity = "warn"


# ---------------------------------------------------------------------------
# Tokenizer (cached)
# ---------------------------------------------------------------------------


def _enc():
    if not hasattr(_enc, "_cached"):
        _enc._cached = tiktoken.get_encoding("cl100k_base")
    return _enc._cached


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_token_count(body: str, audit: PromptAudit) -> None:
    n = len(_enc().encode(body))
    audit.metrics["token_count"] = n
    if n >= TOKEN_FAIL:
        _bump(audit, Finding(
            "token_count", "fail",
            f"prompt is {n} tokens — exceeds fail threshold {TOKEN_FAIL}; "
            "consider trimming verbose framing.",
            n,
        ))
    elif n >= TOKEN_WARN:
        _bump(audit, Finding(
            "token_count", "warn",
            f"prompt is {n} tokens — above warn threshold {TOKEN_WARN}.",
            n,
        ))
    else:
        _bump(audit, Finding(
            "token_count", "ok",
            f"prompt is {n} tokens — within budget.",
            n,
        ))


def check_rules_count(body: str, audit: PromptAudit) -> None:
    rules = _RULE_LINE.findall(body)
    n = len(rules)
    audit.metrics["rules_count"] = n
    if n >= RULES_FAIL:
        _bump(audit, Finding(
            "rules_count", "fail",
            f"{n} bulleted/numbered directives — LLMs lose instruction "
            "following beyond ~20.",
            n,
        ))
    elif n >= RULES_WARN:
        _bump(audit, Finding(
            "rules_count", "warn",
            f"{n} directives — consider consolidating overlapping rules.",
            n,
        ))
    else:
        _bump(audit, Finding("rules_count", "ok", f"{n} directives.", n))


def check_examples_count(body: str, audit: PromptAudit) -> None:
    n = len(_EXAMPLE_HEADER.findall(body))
    audit.metrics["examples_count"] = n
    if n >= EXAMPLES_FAIL:
        _bump(audit, Finding(
            "examples_count", "fail",
            f"{n} explicit examples — LLM may pattern-match listed cases "
            "rather than generalize the rule.",
            n,
        ))
    elif n >= EXAMPLES_WARN:
        _bump(audit, Finding(
            "examples_count", "warn",
            f"{n} explicit examples — verify each one earns its tokens.",
            n,
        ))
    else:
        _bump(audit, Finding("examples_count", "ok", f"{n} examples.", n))


def check_shouting_ratio(body: str, audit: PromptAudit) -> None:
    words = re.findall(r"\b[\w']+\b", body)
    if not words:
        audit.metrics["shouting_ratio"] = 0.0
        _bump(audit, Finding("shouting_ratio", "ok", "no words.", 0.0))
        return
    shouts = len(_SHOUT_TOKEN.findall(body))
    ratio = shouts / len(words)
    audit.metrics["shouting_ratio"] = round(ratio, 4)
    audit.metrics["shouting_count"] = shouts
    if ratio >= SHOUTING_RATIO_FAIL:
        _bump(audit, Finding(
            "shouting_ratio", "fail",
            f"{shouts} caps-emphasis tokens out of {len(words)} words "
            f"({ratio:.1%}) — LLM desensitizes to MUST/NEVER spam.",
            ratio,
        ))
    elif ratio >= SHOUTING_RATIO_WARN:
        _bump(audit, Finding(
            "shouting_ratio", "warn",
            f"{shouts} caps-emphasis tokens out of {len(words)} words "
            f"({ratio:.1%}) — borderline.",
            ratio,
        ))
    else:
        _bump(audit, Finding(
            "shouting_ratio", "ok",
            f"{shouts}/{len(words)} caps tokens ({ratio:.1%}).",
            ratio,
        ))


def check_redundancy(body: str, audit: PromptAudit) -> None:
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if len(s.split()) >= 4]
    if len(sents) < 4:
        audit.metrics["redundancy"] = 0.0
        _bump(audit, Finding("redundancy", "ok", "too few sentences to score.", 0.0))
        return

    def _toks(s: str) -> set[str]:
        return set(re.findall(r"\b[a-zA-Z]{4,}\b", s.lower()))

    pairs_total = 0
    pairs_overlap = 0
    overlapping_examples: list[tuple[str, str]] = []
    for i in range(len(sents)):
        for j in range(i + 1, len(sents)):
            a, b = _toks(sents[i]), _toks(sents[j])
            if not a or not b:
                continue
            pairs_total += 1
            jaccard = len(a & b) / len(a | b)
            if jaccard >= 0.6:
                pairs_overlap += 1
                if len(overlapping_examples) < 2:
                    overlapping_examples.append((sents[i][:60], sents[j][:60]))

    ratio = pairs_overlap / pairs_total if pairs_total else 0.0
    audit.metrics["redundancy"] = round(ratio, 4)
    if ratio >= REDUNDANCY_WARN:
        _bump(audit, Finding(
            "redundancy", "warn",
            f"{pairs_overlap}/{pairs_total} sentence pairs share ≥60% "
            f"content tokens ({ratio:.1%}) — likely duplicate guidance. "
            f"e.g. {overlapping_examples[:1]}",
            ratio,
        ))
    else:
        _bump(audit, Finding(
            "redundancy", "ok",
            f"{pairs_overlap}/{pairs_total} sentence pairs overlap ({ratio:.1%}).",
            ratio,
        ))


def check_hardcoded_thresholds(body: str, audit: PromptAudit) -> None:
    hits = _HARDCODED_THRESHOLD.findall(body)
    audit.metrics["hardcoded_thresholds"] = hits
    if hits:
        _bump(audit, Finding(
            "hardcoded_thresholds", "warn",
            f"found {len(hits)} threshold-shaped patterns: {hits[:5]}. "
            "If these are behavioral targets (LLM is asked to self-report "
            "in this band), document in frontmatter `leakage_check.notes`. "
            "If they're internal policy knobs, move to config and "
            "parameterize the prompt.",
            hits,
        ))
    else:
        _bump(audit, Finding(
            "hardcoded_thresholds", "ok",
            "no threshold-shaped magic numbers in prompt body.",
        ))


def check_pii(body: str, audit: PromptAudit) -> None:
    emails = _EMAIL.findall(body)
    phones = _PHONE.findall(body)
    ssns = _SSN.findall(body)
    total = len(emails) + len(phones) + len(ssns)
    audit.metrics["pii_emails"] = len(emails)
    audit.metrics["pii_phones"] = len(phones)
    audit.metrics["pii_ssns"] = len(ssns)
    if total:
        _bump(audit, Finding(
            "pii", "fail",
            f"PII patterns found — emails:{len(emails)} phones:{len(phones)} "
            f"ssns:{len(ssns)}. Real PII must never appear in production "
            "prompts (LangSmith, CI logs, error responses all surface them).",
            {"emails": emails, "phones": phones, "ssns": ssns},
        ))
    else:
        _bump(audit, Finding("pii", "ok", "no PII patterns."))


def check_internal_identifiers(body: str, audit: PromptAudit) -> None:
    hits = _INTERNAL_IDS.findall(body)
    audit.metrics["internal_identifiers"] = hits
    if hits:
        _bump(audit, Finding(
            "internal_identifiers", "warn",
            f"prompt mentions repo-specific identifiers: {hits[:5]}. "
            "Example IDs in prompts are fine if they're synthetic; "
            "real-world IDs (matching mock data or production rows) "
            "should be parameterized.",
            hits,
        ))
    else:
        _bump(audit, Finding(
            "internal_identifiers", "ok",
            "no repo-specific identifiers in body.",
        ))


def check_sql_or_shell(body: str, audit: PromptAudit) -> None:
    hits = _SQL_OR_SHELL.findall(body)
    audit.metrics["sql_or_shell"] = hits
    if hits:
        _bump(audit, Finding(
            "sql_or_shell", "warn",
            f"prompt body contains SQL/shell-shaped fragments: {hits[:3]}. "
            "Verify these are illustrative; if they're executed, the "
            "prompt is a code-injection surface.",
            hits,
        ))
    else:
        _bump(audit, Finding("sql_or_shell", "ok", "no SQL/shell fragments."))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def audit_prompt(prompt_id: str) -> PromptAudit:
    body = load_prompt(prompt_id)
    meta = load_prompt_meta(prompt_id)
    audit = PromptAudit(
        prompt_id=prompt_id,
        version=str(meta.get("version", "?")),
    )
    # Overkill checks
    check_token_count(body, audit)
    check_rules_count(body, audit)
    check_examples_count(body, audit)
    check_shouting_ratio(body, audit)
    check_redundancy(body, audit)
    # Leakage checks
    check_hardcoded_thresholds(body, audit)
    check_pii(body, audit)
    check_internal_identifiers(body, audit)
    check_sql_or_shell(body, audit)
    return audit


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


_RESET = "\033[0m"
_RED = "\033[31m"
_YEL = "\033[33m"
_GRN = "\033[32m"
_DIM = "\033[2m"
_BOLD = "\033[1m"


def _color(severity: str) -> str:
    return {"ok": _GRN, "warn": _YEL, "fail": _RED}.get(severity, "")


def print_report_human(audits: list[PromptAudit]) -> None:
    for a in audits:
        c = _color(a.overall_severity)
        print(
            f"\n{_BOLD}{a.prompt_id}{_RESET} v{a.version}  "
            f"{c}[{a.overall_severity.upper()}]{_RESET}"
        )
        for f in a.findings:
            mark = {"ok": "✓", "warn": "!", "fail": "✗"}[f.severity]
            color = _color(f.severity)
            print(f"  {color}{mark}{_RESET} {f.check:<24s} {f.detail}")

    # Summary
    n_ok = sum(1 for a in audits if a.overall_severity == "ok")
    n_warn = sum(1 for a in audits if a.overall_severity == "warn")
    n_fail = sum(1 for a in audits if a.overall_severity == "fail")
    print(f"\n{_BOLD}Summary{_RESET}: {len(audits)} prompts — "
          f"{_GRN}{n_ok} ok{_RESET} / "
          f"{_YEL}{n_warn} warn{_RESET} / "
          f"{_RED}{n_fail} fail{_RESET}")


def print_report_json(audits: list[PromptAudit]) -> None:
    payload = [
        {
            **asdict(a),
            "findings": [asdict(f) for f in a.findings],
        }
        for a in audits
    ]
    print(json.dumps(payload, indent=2, default=str))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prompt_audit", description=__doc__)
    parser.add_argument(
        "prompts", nargs="*",
        help="Prompt IDs to audit (default: all).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of human-readable colored output.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit 1 on any warning (default: exit 1 only on fail).",
    )
    parser.add_argument(
        "--write-report", type=Path, default=None,
        help="Also write the JSON report to this path.",
    )
    args = parser.parse_args(argv)

    targets = args.prompts or list_prompts()
    if not targets:
        print("No prompts found in prompts/.", file=sys.stderr)
        return 2

    audits = [audit_prompt(p) for p in targets]

    if args.json:
        print_report_json(audits)
    else:
        print_report_human(audits)

    if args.write_report is not None:
        args.write_report.write_text(
            json.dumps(
                [{**asdict(a), "findings": [asdict(f) for f in a.findings]} for a in audits],
                indent=2, default=str,
            ),
            encoding="utf-8",
        )

    has_fail = any(a.overall_severity == "fail" for a in audits)
    has_warn = any(a.overall_severity == "warn" for a in audits)
    if has_fail:
        return 1
    if args.strict and has_warn:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
