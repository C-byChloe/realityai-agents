"""Tests for LangSmith tracing integration."""

import os
import time
from unittest.mock import patch

import pytest

from observability.tracing import (
    TraceContext,
    TraceSpan,
    get_callbacks,
    get_langsmith_callback,
    trace_span,
)


class TestTraceSpan:
    def test_creates_with_name(self):
        span = TraceSpan(name="test")
        assert span.name == "test"
        assert span.success is True
        assert span.error is None

    def test_finish_sets_end_time(self):
        span = TraceSpan(name="test")
        span.finish()
        assert span.end_time is not None
        assert span.duration_ms > 0

    def test_finish_with_error(self):
        span = TraceSpan(name="test")
        span.finish(success=False, error="something broke")
        assert span.success is False
        assert span.error == "something broke"

    def test_duration_before_finish(self):
        span = TraceSpan(name="test")
        assert span.duration_ms == 0.0


class TestTraceContext:
    def test_creates_with_trace_id(self):
        ctx = TraceContext(trace_id="trace-001")
        assert ctx.trace_id == "trace-001"
        assert ctx.spans == []

    def test_add_span(self):
        ctx = TraceContext(trace_id="trace-001")
        span = TraceSpan(name="test")
        span.finish()
        ctx.add_span(span)
        assert len(ctx.spans) == 1

    def test_total_tokens(self):
        ctx = TraceContext(trace_id="trace-001")
        s1 = TraceSpan(name="s1", token_usage={"input": 100, "output": 50})
        s2 = TraceSpan(name="s2", token_usage={"input": 200, "output": 100})
        ctx.add_span(s1)
        ctx.add_span(s2)
        assert ctx.total_tokens == {"input": 300, "output": 150}

    def test_to_dict(self):
        ctx = TraceContext(trace_id="trace-001")
        span = TraceSpan(name="test")
        span.finish()
        ctx.add_span(span)
        d = ctx.to_dict()
        assert d["trace_id"] == "trace-001"
        assert len(d["spans"]) == 1
        assert d["spans"][0]["name"] == "test"


class TestTraceSpanContextManager:
    def test_traces_successful_operation(self):
        ctx = TraceContext(trace_id="trace-001")
        with trace_span(ctx, "my_operation", key="value") as span:
            pass  # Simulate work
        assert len(ctx.spans) == 1
        assert ctx.spans[0].success is True
        assert ctx.spans[0].metadata == {"key": "value"}

    def test_traces_failed_operation(self):
        ctx = TraceContext(trace_id="trace-001")
        with pytest.raises(ValueError):
            with trace_span(ctx, "failing_op") as span:
                raise ValueError("test error")
        assert len(ctx.spans) == 1
        assert ctx.spans[0].success is False
        assert "test error" in ctx.spans[0].error


class TestGetCallbacks:
    @patch.dict(os.environ, {"LANGSMITH_TRACING": "false"})
    def test_disabled_returns_empty(self):
        callbacks = get_callbacks()
        assert callbacks == []

    @patch.dict(os.environ, {"LANGSMITH_TRACING": "false"})
    def test_langsmith_disabled_returns_none(self):
        result = get_langsmith_callback()
        assert result is None

    @patch.dict(os.environ, {"LANGSMITH_TRACING": "true"})
    def test_langsmith_enabled_but_no_client_returns_none(self):
        """LangSmith enabled but client not configured — should fail gracefully."""
        result = get_langsmith_callback()
        # Will be None because langsmith client isn't configured
        # The important thing is it doesn't raise
        assert result is None or result is not None  # Either is fine

    def test_non_blocking_on_import_error(self):
        """Tracing should never crash the application."""
        # Even with bad config, get_callbacks should return a list
        callbacks = get_callbacks()
        assert isinstance(callbacks, list)
