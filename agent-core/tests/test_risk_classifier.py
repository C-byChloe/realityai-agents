"""Unit tests for the static risk classifier."""

import time

import pytest

from safety.risk_classifier import RISK_MAP, RiskResult, classify_risk


class TestClassifyRisk:
    """Tests for risk classification of known and unknown tools."""

    def test_high_risk_grade_update(self):
        result = classify_risk("grade_update")
        assert result.risk_level == "high"
        assert "grade_update" in result.reason
        assert "high-risk" in result.reason

    def test_high_risk_enrollment_modify(self):
        result = classify_risk("enrollment_modify")
        assert result.risk_level == "high"

    def test_high_risk_assignment_create(self):
        result = classify_risk("assignment_create")
        assert result.risk_level == "high"

    def test_low_risk_course_lookup(self):
        result = classify_risk("course_lookup")
        assert result.risk_level == "low"
        assert result.reason is None

    def test_low_risk_schedule_query(self):
        result = classify_risk("schedule_query")
        assert result.risk_level == "low"

    def test_low_risk_syllabus_retrieve(self):
        result = classify_risk("syllabus_retrieve")
        assert result.risk_level == "low"

    def test_unknown_tool_defaults_to_high(self):
        result = classify_risk("delete_everything")
        assert result.risk_level == "high"
        assert "Unknown tool" in result.reason
        assert "defaults to high-risk" in result.reason

    def test_another_unknown_tool(self):
        result = classify_risk("never_seen_before")
        assert result.risk_level == "high"


class TestPerformance:
    """Verify classification completes in under 1ms."""

    def test_execution_under_1ms(self):
        # Warm up
        classify_risk("grade_update")

        # Measure 1000 iterations
        start = time.perf_counter_ns()
        for _ in range(1000):
            classify_risk("grade_update")
        elapsed_ns = time.perf_counter_ns() - start

        avg_ns = elapsed_ns / 1000
        avg_ms = avg_ns / 1_000_000
        assert avg_ms < 1.0, f"Average classification took {avg_ms:.4f}ms, expected <1ms"

    def test_unknown_tool_also_fast(self):
        start = time.perf_counter_ns()
        for _ in range(1000):
            classify_risk("unknown_tool")
        elapsed_ns = time.perf_counter_ns() - start

        avg_ms = (elapsed_ns / 1000) / 1_000_000
        assert avg_ms < 1.0


class TestRiskMap:
    """Tests for the RISK_MAP dictionary."""

    def test_all_action_tools_are_high(self):
        for tool in ["grade_update", "enrollment_modify", "assignment_create"]:
            assert RISK_MAP[tool] == "high"

    def test_all_query_tools_are_low(self):
        for tool in ["course_lookup", "schedule_query", "syllabus_retrieve"]:
            assert RISK_MAP[tool] == "low"

    def test_map_has_6_entries(self):
        assert len(RISK_MAP) == 6
