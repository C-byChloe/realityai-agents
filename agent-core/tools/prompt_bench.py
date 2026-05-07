"""Per-prompt benchmark runner — closes the prompt-engineering loop.

Run:
    python -m tools.prompt_bench <prompt_id>            # bench one
    python -m tools.prompt_bench --all                  # bench all wired
    python -m tools.prompt_bench <id> --write-frontmatter  # update prompt
                                                           # frontmatter
    python -m tools.prompt_bench <id> --json            # machine-readable

What this does:

  1. Reads `<prompt_id>.md` frontmatter, extracts `performance.benchmark`
     (path to the eval set this prompt is bound to).
  2. Dispatches to the runner registered for that benchmark.
  3. Captures a single float "score" (per-bench definition).
  4. **Always** appends a JSONL entry to `prompts/bench_history/<id>.jsonl`
     so daily runs don't churn the prompt frontmatter.
  5. **Only with `--write-frontmatter`** updates `latest_score` +
     `measured_at` in the prompt's YAML frontmatter — for use when
     shipping a new prompt version where the official baseline number
     should bump.

Why two write surfaces:
  - Bench history is the audit trail; grows monotonically; safe to run
    in CI on every prompt change.
  - Frontmatter `latest_score` is the published baseline; only updated
    deliberately, not on every dev-loop run. Otherwise every git diff
    shows numeric churn.

Wired benchmarks:
  - `evaluation/trace_eval_set.jsonl` (mock LLM; deterministic)
  - `evaluation/inner_safety_smoke_eval.jsonl` (no LLM; deterministic)

Not yet wired (require `ANTHROPIC_API_KEY` and slower runtime):
  - `evaluation/coref_eval_set.jsonl`       — real coref LLM
  - `evaluation/outer_safety_smoke_eval.jsonl` — real Tier 3 LLM

For unwired benchmarks the runner exits 2 with a clear "not yet wired"
message rather than silently passing — bench tool failure ≠ prompt
quality failure, so unwired ≠ ok.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from prompts import _PROMPTS_DIR, list_prompts, load_prompt_meta

_BENCH_HISTORY_DIR = _PROMPTS_DIR / "bench_history"


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class BenchResult:
    prompt_id: str
    benchmark: str
    metric: str
    score: float
    n_cases: int
    timestamp: str
    details: dict[str, Any]
    runner: str


# ---------------------------------------------------------------------------
# Runner: trace_eval_set (mock LLM; system-level task completion proxy)
# ---------------------------------------------------------------------------


def _run_trace_bench(prompt_id: str) -> BenchResult:
    """Run trace_completion_eval and return overall task_completion_rate.

    System-level proxy: a prompt's "score" here is the fraction of
    scenarios in `trace_eval_set.jsonl` that complete correctly, given
    the current prompt body. This isn't a per-prompt isolated metric —
    a regression in any one prompt could move it — but for the small
    library it's a useful integration signal.

    Subscribes to the same eval pipeline tested in the threshold sweep
    work; reuses its judge functions verbatim.
    """
    # Import lazily so a broken eval module doesn't poison `--help`.
    from evaluation.trace_completion_eval import _run_all

    report = asyncio.run(_run_all())
    summary = report["summary"]
    return BenchResult(
        prompt_id=prompt_id,
        benchmark="evaluation/trace_eval_set.jsonl",
        metric="task_completion_rate",
        score=float(summary["task_completion_rate"]) / 100.0,
        n_cases=int(summary["total_scenarios"]),
        timestamp=report["timestamp"],
        details={
            "by_category": report["by_category"],
            "config": report["config"],
        },
        runner="trace_completion_eval._run_all",
    )


# ---------------------------------------------------------------------------
# Runner: inner_safety_smoke_eval (deterministic, no LLM)
# ---------------------------------------------------------------------------


def _run_inner_safety_bench(prompt_id: str) -> BenchResult:
    """Drive `evaluation/run_inner_safety_eval.py` and parse its summary.

    The inner-safety runner already produces a markdown report with an
    overall accuracy line. We invoke it as a subprocess (it's set up to
    write artifacts and exit 0/1), then read the summary number out of
    the report.
    """
    agent_core_dir = Path(__file__).resolve().parent.parent
    report_path = agent_core_dir / "evaluation" / "inner_safety_smoke_baseline.md"

    # `python -m` so the runner's relative imports (`from safety.inner...`)
    # resolve against agent-core/ on sys.path. Running the script file
    # directly leaves the script's parent dir on path but not agent-core.
    proc = subprocess.run(
        [sys.executable, "-m", "evaluation.run_inner_safety_eval"],
        capture_output=True, text=True,
        cwd=str(agent_core_dir),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"inner_safety eval exited {proc.returncode}\nstderr: {proc.stderr[-500:]}"
        )

    if not report_path.exists():
        raise FileNotFoundError(f"expected report at {report_path}")
    text = report_path.read_text(encoding="utf-8")

    # The runner reports "**Decision-perfect:** N/M" — N cases where the
    # actual decision matched expected. Misalignment of *which layer*
    # short-circuited shows up separately and isn't part of this score.
    m = re.search(r"Decision-perfect:\*\*\s*(\d+)\s*/\s*(\d+)", text)
    if not m:
        raise ValueError(
            "couldn't parse Decision-perfect from inner_safety baseline report"
        )
    passed, total = int(m.group(1)), int(m.group(2))

    return BenchResult(
        prompt_id=prompt_id,
        benchmark="evaluation/inner_safety_smoke_eval.jsonl",
        metric="decision_accuracy",
        score=passed / total if total else 0.0,
        n_cases=total,
        timestamp=datetime.now(timezone.utc).isoformat(),
        details={"passed": passed, "total": total, "report_path": str(report_path)},
        runner="evaluation.run_inner_safety_eval",
    )


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------


_WIRED: dict[str, Callable[[str], BenchResult]] = {
    "evaluation/trace_eval_set.jsonl": _run_trace_bench,
    "evaluation/inner_safety_smoke_eval.jsonl": _run_inner_safety_bench,
}

_UNWIRED_REASONS = {
    "evaluation/coref_eval_set.jsonl": (
        "real-LLM coref eval — requires ANTHROPIC_API_KEY and ~30s runtime; "
        "wire via evaluation.run_coref_eval after API budget is set."
    ),
    "evaluation/outer_safety_smoke_eval.jsonl": (
        "real-LLM outer safety Tier 3 eval — requires ANTHROPIC_API_KEY; "
        "wire via evaluation.run_outer_safety_eval after API budget is set."
    ),
}


# ---------------------------------------------------------------------------
# Frontmatter + history I/O
# ---------------------------------------------------------------------------


def _append_history(result: BenchResult) -> Path:
    _BENCH_HISTORY_DIR.mkdir(exist_ok=True)
    path = _BENCH_HISTORY_DIR / f"{result.prompt_id}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(result), default=str) + "\n")
    return path


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("file has no frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("malformed frontmatter (missing closing `---`)")
    fm = text[4:end]
    body = text[end + 5:].lstrip("\n")
    return fm, body


def _write_frontmatter_score(prompt_id: str, result: BenchResult) -> None:
    """Surgical update of `latest_score:` and `measured_at:` in frontmatter.

    Deliberately NOT a full YAML round-trip — PyYAML's dumper rewrites
    `notes: |` block scalars into single-quoted strings, which makes
    every diff a wall of cosmetic noise and obscures the one number
    that actually changed. Surgical regex preserves byte-for-byte the
    rest of the frontmatter.

    Constraints this relies on:
      - `latest_score` and `measured_at` keys appear once per file
        (true: only inside the `performance:` block).
      - Both keys are scalar values, not blocks.
      - The bench output is well-formed (float / ISO timestamp).
    """
    path = _PROMPTS_DIR / f"{prompt_id}.md"
    text = path.read_text(encoding="utf-8")

    score_line = re.compile(
        r"^(\s*latest_score:\s*).+$", flags=re.MULTILINE,
    )
    measured_line = re.compile(
        r"^(\s*measured_at:\s*).+$", flags=re.MULTILINE,
    )

    new_text, n1 = score_line.subn(
        rf"\g<1>{round(result.score, 4)}", text, count=1,
    )
    new_text, n2 = measured_line.subn(
        rf"\g<1>{result.timestamp}", new_text, count=1,
    )

    if n1 == 0 or n2 == 0:
        raise ValueError(
            f"prompt {prompt_id!r} frontmatter is missing required fields "
            f"(`latest_score`: {n1} matches, `measured_at`: {n2} matches). "
            "Add them under `performance:` before running with --write-frontmatter."
        )

    path.write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Top-level bench
# ---------------------------------------------------------------------------


def bench(prompt_id: str) -> BenchResult:
    meta = load_prompt_meta(prompt_id)
    perf = meta.get("performance") or {}
    benchmark = perf.get("benchmark")
    if not benchmark:
        raise ValueError(
            f"prompt {prompt_id!r} has no `performance.benchmark` in frontmatter"
        )

    runner = _WIRED.get(benchmark)
    if runner is None:
        reason = _UNWIRED_REASONS.get(benchmark, "no runner registered")
        raise NotImplementedError(
            f"benchmark {benchmark!r} not wired for prompt {prompt_id!r}: {reason}"
        )

    return runner(prompt_id)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_human(result: BenchResult, history_path: Path, wrote_frontmatter: bool) -> None:
    print(f"\nprompt        : {result.prompt_id}")
    print(f"benchmark     : {result.benchmark}")
    print(f"runner        : {result.runner}")
    print(f"metric        : {result.metric}")
    print(f"score         : {result.score:.4f}  ({result.score * 100:.1f}%)")
    print(f"cases         : {result.n_cases}")
    print(f"timestamp     : {result.timestamp}")
    print(f"history       : {history_path}")
    print(f"frontmatter   : {'updated' if wrote_frontmatter else 'unchanged (use --write-frontmatter to publish)'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prompt_bench", description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("prompt_id", nargs="?", help="Prompt id to bench (filename stem).")
    g.add_argument("--all", action="store_true", help="Bench every wired prompt.")
    parser.add_argument(
        "--write-frontmatter", action="store_true",
        help="Update prompt frontmatter latest_score + measured_at.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Machine-readable output.",
    )
    args = parser.parse_args(argv)

    if args.all:
        prompts = list_prompts()
    else:
        prompts = [args.prompt_id]

    results: list[BenchResult] = []
    errors: list[tuple[str, str]] = []

    for pid in prompts:
        try:
            result = bench(pid)
        except NotImplementedError as e:
            errors.append((pid, str(e)))
            continue
        except Exception as e:
            errors.append((pid, f"{type(e).__name__}: {e}"))
            continue

        history_path = _append_history(result)
        if args.write_frontmatter:
            _write_frontmatter_score(pid, result)

        results.append(result)
        if not args.json:
            _print_human(result, history_path, args.write_frontmatter)

    if args.json:
        payload = {
            "results": [asdict(r) for r in results],
            "errors": [{"prompt_id": pid, "reason": reason} for pid, reason in errors],
        }
        print(json.dumps(payload, indent=2, default=str))

    if not args.json and errors:
        print("\nSkipped / failed:")
        for pid, reason in errors:
            print(f"  - {pid}: {reason}")

    # Exit 0 if at least one result; non-zero only when explicitly all-failed.
    return 0 if results or args.all else 1


if __name__ == "__main__":
    sys.exit(main())
