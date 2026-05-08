"""Prompt Guard client — specialized injection-detection classifier.

Replaces the Claude-based Tier 3 intent_analyzer with a small fine-tuned
classifier specialized for prompt-injection / jailbreak detection.

Why this exists:
  - Tier 3's job in this codebase is "is this query an injection attempt?"
    Not "is this query semantically safe in some general sense?"
  - Claude Sonnet at 70B+ for a binary classification task is overkill:
    ~800ms latency, ~$0.003/call, and 5 distinct API failure modes that
    all fall back to FLAG_FOR_REVIEW.
  - Meta's Prompt Guard 2 (86M params, fine-tuned on labeled injection
    data) is the right tool: ~50ms local CPU inference, near-zero cost,
    higher F1 on the injection task specifically. Trade-off: it ONLY
    does injection classification, no general reasoning.

Two implementations:
  - LocalPromptGuardClient: loads the real model via HuggingFace
    transformers. Requires `pip install transformers torch` and HF auth
    for the gated model weights. Production deployments use this.
  - HeuristicPromptGuardClient: deterministic lexical-pattern fallback
    used in dev/CI when transformers isn't installed. NOT a substitute
    for the real model — it lacks semantic understanding and will miss
    novel patterns. The interface contract is identical so swapping in
    the real client is a single line.

The orchestrator picks via `get_default_prompt_guard()` which tries the
real model first and falls back to heuristic. This mirrors the gRPC
client's mock fallback in `grpc_client/client.py`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class PromptGuardLabel(str, Enum):
    BENIGN = "benign"
    INJECTION = "injection"


@dataclass
class PromptGuardResult:
    """Output of any PromptGuardClient. `score` is the model's
    confidence that the input is an INJECTION attempt (0..1).

    A BENIGN result has `score = 1 - p_injection` for symmetry.
    `detail` is human-readable; populated only by the heuristic fallback
    so traces can show which pattern fired.
    """

    label: PromptGuardLabel
    score: float
    detail: str = ""


class PromptGuardClient(Protocol):
    """Stable interface that injection_guard.check_injection consumes.

    Implementations may be sync (heuristic) or async (real model on a
    thread pool). The protocol method is async so callers don't need
    to know which is wired in.
    """

    async def classify(
        self,
        text: str,
        history: list[Any] | None = None,
    ) -> PromptGuardResult: ...


# ---------------------------------------------------------------------------
# Real model — production path.
# ---------------------------------------------------------------------------


class LocalPromptGuardClient:
    """Loads Meta Prompt Guard 2 (86M) via HuggingFace transformers.

    Production swap point. Requires:
      pip install transformers torch
    plus HF auth for the gated model weights:
      huggingface-cli login

    The model is loaded lazily on first `classify()` call (not at __init__)
    so the import-time cost stays out of test runs that monkeypatch the
    classifier. Inference runs sync inside `asyncio.to_thread` to avoid
    blocking the event loop.

    Failure modes:
      - transformers/torch not installed → ImportError at __init__
      - HF auth missing or model not gated for this account → OSError at
        first classify()
      - inference exception (corrupted weights, OOM, etc.) → caught at
        the injection_guard tier and routed to FLAG (D8 fail-open)

    Latency profile (M2 Pro, CPU): ~50ms p50, ~120ms p95.
    """

    MODEL_ID: str = "meta-llama/Prompt-Guard-2-86M"
    MAX_LENGTH: int = 512  # tokens; model context window cap

    def __init__(self) -> None:
        # Fail loudly at construction if the deps are missing — the
        # caller (`get_default_prompt_guard`) catches this and falls
        # back to the heuristic client.
        try:
            import torch  # noqa: F401
            from transformers import (  # noqa: F401
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )
        except ImportError as e:
            raise ImportError(
                "LocalPromptGuardClient requires `transformers` and `torch`. "
                "Install with `pip install transformers torch` or use "
                "HeuristicPromptGuardClient for dev/CI."
            ) from e

        self._tokenizer = None
        self._model = None
        self._torch = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )
        import torch

        logger.info("Loading Prompt Guard model: %s", self.MODEL_ID)
        self._tokenizer = AutoTokenizer.from_pretrained(self.MODEL_ID)
        self._model = AutoModelForSequenceClassification.from_pretrained(self.MODEL_ID)
        self._model.eval()
        self._torch = torch

    async def classify(
        self,
        text: str,
        history: list[Any] | None = None,
    ) -> PromptGuardResult:
        # History context is currently unused — Prompt Guard 2 is a
        # single-turn classifier. Multi-turn injection chains are caught
        # by separate logic in the heuristic client and (planned) in a
        # multi-turn judge tier. Production wiring of the real model
        # may concatenate history into `text` per Meta's guidance.
        return await asyncio.to_thread(self._classify_sync, text)

    def _classify_sync(self, text: str) -> PromptGuardResult:
        self._ensure_loaded()
        assert self._tokenizer is not None and self._model is not None and self._torch is not None

        inputs = self._tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=self.MAX_LENGTH,
        )
        with self._torch.no_grad():
            logits = self._model(**inputs).logits
        probs = self._torch.softmax(logits, dim=-1)[0].tolist()

        # Prompt Guard 2 86M label order: [BENIGN, INJECTION] per model card.
        # If a future model version permutes the order, this is the seam to
        # adjust — do NOT assume index by name.
        injection_score = float(probs[1])
        label = (
            PromptGuardLabel.INJECTION
            if injection_score >= 0.5
            else PromptGuardLabel.BENIGN
        )
        return PromptGuardResult(label=label, score=injection_score)


# ---------------------------------------------------------------------------
# Heuristic fallback — dev/CI / when transformers isn't installed.
# ---------------------------------------------------------------------------


class HeuristicPromptGuardClient:
    """Deterministic lexical fallback approximating Prompt Guard's verdict.

    Used when:
      - transformers isn't installed (dev environments)
      - tests want a fast, reproducible classifier
      - CI doesn't want to download 350MB of weights

    NOT a substitute for the real model in production. It catches the
    well-known direct-injection patterns by regex but lacks semantic
    understanding — paraphrased / encoded / multilingual-novel attacks
    that a fine-tuned model catches will pass through this client.

    Pattern scores roughly mirror the categories Prompt Guard 2 is
    trained on:
      1.0  delimiter spoofing (very-high-confidence injection)
      0.95 instruction-override + multi-language equivalents
      0.9  role-flip / persona injection
      0.85 goal-hijack
      0.7  encoding-smuggle ("decode this base64...")
      0.5  polite injection ("please ignore...")

    Multi-turn check: the same patterns are scanned in the last 3
    history turns at 0.8x discount, so split-injection chains get a
    weaker but non-zero signal.
    """

    _PATTERNS: list[tuple[float, str]] = [
        # Delimiter spoofing — ChatML / instruction tags / system markers.
        (
            1.0,
            r"</?(system|user|assistant|instruction)>"
            r"|<\|(im_start|im_end|system|user)\|>"
            r"|\[INST\]|\[/INST\]"
            r"|###\s*(System|Instruction|Assistant)\s*:",
        ),
        # Instruction-override — English.
        (
            0.95,
            r"\b(ignore|disregard|forget|override|bypass)\s+"
            r"(previous|above|all|prior|earlier|the system|"
            r"your instructions|safety|rules|the rules)\b",
        ),
        # Instruction-override — Chinese.
        (
            0.95,
            r"忽略(之前|以上|全部|先前)"
            r"|无视(以上|之前|规则)"
            r"|忘(记|掉)(之前|以上)"
            r"|重新设定",
        ),
        # Role-flip / persona injection — English.
        (
            0.9,
            r"\b(you are now|from now on you are|"
            r"pretend\s+(you|to be)|act as|roleplay as|act like)\b",
        ),
        # Role-flip — Chinese.
        (0.9, r"你现在是|从现在起你是|扮演|假装(你|是)"),
        # Goal-hijack.
        (
            0.85,
            r"\byour\s+(real|true|actual|secret)\s+"
            r"(goal|purpose|task|objective|instructions)\b",
        ),
        # Encoding-smuggle.
        (
            0.7,
            r"\b(decode|reverse|rot13|base64|hex)\b.{0,40}"
            r"\b(this|the following|below|above)\b"
            r"|\b(decode|execute)\s+the\s+following\b",
        ),
        # Polite injection.
        (0.5, r"\b(kindly|please)\s+(forget|ignore)\b"),
    ]

    HISTORY_DISCOUNT: float = 0.8
    HISTORY_TURNS: int = 3

    def __init__(self) -> None:
        self._compiled: list[tuple[float, re.Pattern[str]]] = [
            (score, re.compile(pattern, re.IGNORECASE | re.UNICODE))
            for score, pattern in self._PATTERNS
        ]

    async def classify(
        self,
        text: str,
        history: list[Any] | None = None,
    ) -> PromptGuardResult:
        max_score = 0.0
        matched: str = ""

        # Current query.
        for score, pat in self._compiled:
            if pat.search(text or ""):
                if score > max_score:
                    max_score, matched = score, pat.pattern[:60]

        # Recent history — discounted to reflect lower confidence that
        # an old turn is the active injection vector.
        recent = (history or [])[-self.HISTORY_TURNS:]
        for msg in recent:
            content = (
                msg
                if isinstance(msg, str)
                else getattr(msg, "content", "") or str(msg)
            )
            for score, pat in self._compiled:
                if pat.search(content):
                    discounted = score * self.HISTORY_DISCOUNT
                    if discounted > max_score:
                        max_score = discounted
                        matched = f"history:{pat.pattern[:50]}"

        if max_score >= 0.5:
            return PromptGuardResult(
                label=PromptGuardLabel.INJECTION,
                score=max_score,
                detail=matched,
            )
        return PromptGuardResult(
            label=PromptGuardLabel.BENIGN,
            score=1.0 - max_score,
            detail=matched,
        )


# ---------------------------------------------------------------------------
# Factory.
# ---------------------------------------------------------------------------


_DEFAULT_CLIENT: PromptGuardClient | None = None


def get_default_prompt_guard() -> PromptGuardClient:
    """Pick the best available client. Memoized so the model loads once.

    Resolution order:
      1. LocalPromptGuardClient if `transformers` + `torch` installed
         AND model weights accessible. Throws ImportError / OSError
         otherwise.
      2. HeuristicPromptGuardClient as fallback (always available).

    Tests should bypass this and inject a client directly via the
    composer's `prompt_guard=` kwarg — same pattern as the LLM injection
    used by the prior intent_analyzer.
    """
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is not None:
        return _DEFAULT_CLIENT

    try:
        _DEFAULT_CLIENT = LocalPromptGuardClient()
        logger.info("PromptGuardClient: using LocalPromptGuardClient (real model)")
    except ImportError:
        _DEFAULT_CLIENT = HeuristicPromptGuardClient()
        logger.info(
            "PromptGuardClient: using HeuristicPromptGuardClient (transformers "
            "not installed; install for production-grade detection)"
        )
    return _DEFAULT_CLIENT


def _reset_default_for_testing() -> None:
    """Test-only — clears the memoized client. Production never calls."""
    global _DEFAULT_CLIENT
    _DEFAULT_CLIENT = None
