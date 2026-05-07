"""Prompt library — single source of truth for all production system prompts.

Each prompt is a markdown file with YAML frontmatter carrying versioning,
performance benchmarks, audit status, and changelog. Load via `load_prompt(id)`
at runtime; never inline prompt bodies in Python code.

Why this layer exists:
  - **Reviewability**: prompt edits land as diffs on a single .md file, not
    buried inside a Python module. PR reviewers see the prompt change in
    isolation.
  - **Audit surface**: `tools/prompt_audit.py` reads this directory to run
    overkill / leakage checks. The audit's frame of reference is the
    library, not greps across the codebase.
  - **Version metadata co-located with content**: frontmatter carries
    owner, last_review, the eval set this prompt is benchmarked against,
    and a changelog with eval deltas. So "why did we make this change?"
    has a permanent answer next to the prompt itself.

The library is read-only at runtime. Edits require redeploy — `lru_cache`
guarantees the first read per prompt is the only filesystem I/O.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PROMPTS_DIR = Path(__file__).parent

# These index files live alongside prompts but are not themselves prompts.
_NON_PROMPT_FILES: frozenset[str] = frozenset({"REGISTRY", "CHANGELOG", "README"})


def load_prompt(prompt_id: str) -> str:
    """Return the body of `<prompt_id>.md`, with frontmatter stripped.

    Cached: repeated calls hit the LRU. Edits to prompt files require
    a process restart.
    """
    return _load_cached(prompt_id)["body"]


def load_prompt_meta(prompt_id: str) -> dict[str, Any]:
    """Return the parsed YAML frontmatter (without the body)."""
    return _load_cached(prompt_id)["meta"]


def list_prompts() -> list[str]:
    """Return all available prompt IDs (sorted)."""
    return sorted(
        p.stem for p in _PROMPTS_DIR.glob("*.md")
        if p.stem not in _NON_PROMPT_FILES
    )


@lru_cache(maxsize=None)
def _load_cached(prompt_id: str) -> dict:
    return _parse(_PROMPTS_DIR / f"{prompt_id}.md")


def _parse(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path.stem} ({path})")
    text = path.read_text(encoding="utf-8")

    # Frontmatter is delimited by `---\n` at line 1 and a closing `---\n`.
    # No frontmatter → whole file is body (legacy migration path).
    if not text.startswith("---\n"):
        return {"meta": {}, "body": text}

    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError(
            f"prompt {path.stem!r} has malformed frontmatter "
            "(missing closing `---` delimiter)"
        )

    fm_text = text[4:end]
    body = text[end + 5:].lstrip("\n")
    meta = yaml.safe_load(fm_text) or {}
    return {"meta": meta, "body": body}
