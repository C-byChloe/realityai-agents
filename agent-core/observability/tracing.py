"""LangSmith tracing integration for agent chains.

Provides callback handlers and trace configuration for observability.
All tracing is non-blocking — failures are logged but never affect requests.
"""

import os
import time
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

LANGSMITH_ENABLED = os.environ.get("LANGSMITH_TRACING", "false").lower() == "true"
LANGSMITH_PROJECT = os.environ.get("LANGSMITH_PROJECT", "realityai-agents")


@dataclass
class TraceSpan:
    """A trace span recording timing, token usage, and metadata."""
    name: str
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    token_usage: dict[str, int] = field(default_factory=dict)
    success: bool = True
    error: str | None = None

    @property
    def duration_ms(self) -> float:
        if self.end_time is None:
            return 0.0
        return (self.end_time - self.start_time) * 1000

    def finish(self, success: bool = True, error: str | None = None):
        self.end_time = time.time()
        self.success = success
        self.error = error


@dataclass
class TraceContext:
    """Collection of spans for a single request trace."""
    trace_id: str
    spans: list[TraceSpan] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_span(self, span: TraceSpan):
        self.spans.append(span)

    @property
    def total_duration_ms(self) -> float:
        if not self.spans:
            return 0.0
        start = min(s.start_time for s in self.spans)
        end = max(s.end_time or s.start_time for s in self.spans)
        return (end - start) * 1000

    @property
    def total_tokens(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for span in self.spans:
            for key, value in span.token_usage.items():
                totals[key] = totals.get(key, 0) + value
        return totals

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "total_tokens": self.total_tokens,
            "spans": [
                {
                    "name": s.name,
                    "duration_ms": round(s.duration_ms, 2),
                    "success": s.success,
                    "error": s.error,
                    "token_usage": s.token_usage,
                    "metadata": s.metadata,
                }
                for s in self.spans
            ],
        }


@contextmanager
def trace_span(context: TraceContext, name: str, **metadata):
    """Context manager for tracing a span within a request."""
    span = TraceSpan(name=name, metadata=metadata)
    try:
        yield span
        span.finish(success=True)
    except Exception as e:
        span.finish(success=False, error=str(e))
        raise
    finally:
        context.add_span(span)
        if LANGSMITH_ENABLED:
            _send_span_to_langsmith(span)


def get_langsmith_callback():
    """Get LangSmith callback handler if tracing is enabled.

    Returns None if LangSmith is not configured, ensuring non-blocking behavior.
    """
    if not LANGSMITH_ENABLED:
        return None

    try:
        from langsmith import Client
        client = Client()
        from langchain_core.tracers import LangChainTracer
        return LangChainTracer(
            project_name=LANGSMITH_PROJECT,
            client=client,
        )
    except Exception as e:
        logger.warning("LangSmith callback init failed (non-blocking): %s", e)
        return None


def get_callbacks() -> list:
    """Get all configured callback handlers for agent chains."""
    callbacks = []
    cb = get_langsmith_callback()
    if cb is not None:
        callbacks.append(cb)
    return callbacks


def _send_span_to_langsmith(span: TraceSpan) -> None:
    """Send a span to LangSmith. Non-blocking — failures are logged only."""
    try:
        from langsmith import Client
        client = Client()
        client.create_run(
            name=span.name,
            run_type="chain",
            inputs=span.metadata,
            outputs={"success": span.success, "error": span.error},
            extra={"token_usage": span.token_usage},
            project_name=LANGSMITH_PROJECT,
        )
    except Exception as e:
        logger.debug("LangSmith span send failed (non-blocking): %s", e)
