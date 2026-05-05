"""Tests for the evaluation harness and ground truth dataset."""

import json
from pathlib import Path

import pytest

from evaluation.precision_harness import (
    evaluate_retrieval,
    load_ground_truth,
    run_baseline_vs_hybrid,
)
from retrieval.hybrid import Document, hybrid_retrieve, vector_search


class TestGroundTruth:
    def test_loads_100_pairs(self):
        gt = load_ground_truth()
        assert len(gt) == 100

    def test_entries_have_required_fields(self):
        gt = load_ground_truth()
        for entry in gt:
            assert "id" in entry
            assert "query" in entry
            assert "relevant_docs" in entry
            assert len(entry["relevant_docs"]) > 0

    def test_unique_ids(self):
        gt = load_ground_truth()
        ids = [e["id"] for e in gt]
        assert len(ids) == len(set(ids))


class TestEvaluateRetrieval:
    def test_perfect_retrieval(self):
        """A retrieval function that always returns the right doc gets precision 1.0."""
        gt = [{"id": 1, "query": "test", "relevant_docs": ["DOC001"], "course_id": "CS101"}]

        def perfect_fn(query, course_id):
            return [Document("DOC001", "content")]

        result = evaluate_retrieval(perfect_fn, gt)
        assert result["average_precision"] == 1.0

    def test_empty_retrieval(self):
        """A retrieval function that returns nothing gets precision 0.0."""
        gt = [{"id": 1, "query": "test", "relevant_docs": ["DOC001"], "course_id": "CS101"}]

        def empty_fn(query, course_id):
            return []

        result = evaluate_retrieval(empty_fn, gt)
        assert result["average_precision"] == 0.0

    def test_result_structure(self):
        gt = load_ground_truth()[:5]

        def simple_fn(query, course_id):
            return vector_search(query, course_id=course_id, top_k=5)

        result = evaluate_retrieval(simple_fn, gt)
        assert "total_queries" in result
        assert "average_precision" in result
        assert "details" in result
        assert len(result["details"]) == 5


class TestBaselineVsHybrid:
    def test_runs_comparison(self):
        gt = load_ground_truth()[:20]  # Use subset for speed
        result = run_baseline_vs_hybrid(gt)

        assert "vector_only" in result
        assert "hybrid" in result
        assert "improvement" in result
        assert result["vector_only"]["total_queries"] == 20
        assert result["hybrid"]["total_queries"] == 20

    def test_precision_in_valid_range(self):
        gt = load_ground_truth()[:20]
        result = run_baseline_vs_hybrid(gt)

        assert 0.0 <= result["vector_only"]["precision_at_5"] <= 1.0
        assert 0.0 <= result["hybrid"]["precision_at_5"] <= 1.0
        assert 0.0 <= result["vector_only"]["recall_at_5"] <= 1.0
        assert 0.0 <= result["hybrid"]["recall_at_5"] <= 1.0

    def test_full_dataset_evaluation(self):
        """Run full evaluation on all 100 pairs and document results."""
        result = run_baseline_vs_hybrid()

        assert result["vector_only"]["total_queries"] == 100
        assert result["hybrid"]["total_queries"] == 100

        print(f"\n--- Retrieval Precision/Recall Results ---")
        print(f"Vector-only: P@5={result['vector_only']['precision_at_5']:.1%}, "
              f"R@5={result['vector_only']['recall_at_5']:.1%}")
        print(f"Hybrid (RRF): P@5={result['hybrid']['precision_at_5']:.1%}, "
              f"R@5={result['hybrid']['recall_at_5']:.1%}")
        print(f"Improvement: P@5{result['improvement']['precision_at_5']:+.1%}, "
              f"R@5{result['improvement']['recall_at_5']:+.1%}")
