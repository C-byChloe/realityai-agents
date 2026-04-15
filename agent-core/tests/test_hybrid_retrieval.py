"""Unit tests for the hybrid retrieval pipeline and RRF fusion."""

import pytest

from retrieval.hybrid import (
    Document,
    hybrid_retrieve,
    keyword_search,
    reciprocal_rank_fusion,
    vector_search,
)


# ---------------------------------------------------------------------------
# RRF Score Computation Tests
# ---------------------------------------------------------------------------


class TestReciprocalRankFusion:
    def test_single_list_ranking(self):
        """Documents from a single list are ranked by their position."""
        docs = [
            Document("A", "First"),
            Document("B", "Second"),
            Document("C", "Third"),
        ]
        results = reciprocal_rank_fusion(docs, k=60, top_n=3)

        assert len(results) == 3
        assert results[0].doc_id == "A"
        assert results[1].doc_id == "B"
        assert results[2].doc_id == "C"

    def test_two_lists_boost_shared_documents(self):
        """Documents appearing in both lists get boosted scores."""
        list1 = [Document("A", "A"), Document("B", "B"), Document("C", "C")]
        list2 = [Document("B", "B"), Document("A", "A"), Document("D", "D")]

        results = reciprocal_rank_fusion(list1, list2, k=60, top_n=4)

        # A and B appear in both lists, so they should rank higher than C and D
        top_ids = [r.doc_id for r in results[:2]]
        assert "A" in top_ids
        assert "B" in top_ids

    def test_rrf_score_computation(self):
        """Verify RRF score formula: 1/(k+rank) summed across lists."""
        k = 60
        list1 = [Document("A", "A")]  # rank 1 in list1
        list2 = [Document("A", "A")]  # rank 1 in list2

        results = reciprocal_rank_fusion(list1, list2, k=k, top_n=1)

        expected_score = 1 / (k + 1) + 1 / (k + 1)  # = 2/61
        assert abs(results[0].score - expected_score) < 1e-10

    def test_document_in_only_one_source(self):
        """Document in only one list gets score from that list only."""
        k = 60
        list1 = [Document("A", "A")]
        list2 = [Document("B", "B")]

        results = reciprocal_rank_fusion(list1, list2, k=k, top_n=2)

        # Both should have same score (rank 1 in one list each)
        assert abs(results[0].score - results[1].score) < 1e-10
        expected = 1 / (k + 1)
        assert abs(results[0].score - expected) < 1e-10

    def test_top_n_limits_results(self):
        """Only top_n results are returned."""
        docs = [Document(f"D{i}", f"Doc {i}") for i in range(10)]
        results = reciprocal_rank_fusion(docs, top_n=3)
        assert len(results) == 3

    def test_empty_lists(self):
        """Empty input returns empty results."""
        results = reciprocal_rank_fusion([], [], top_n=5)
        assert results == []


# ---------------------------------------------------------------------------
# Vector Search Tests
# ---------------------------------------------------------------------------


class TestVectorSearch:
    def test_returns_results(self):
        results = vector_search("CS101 schedule")
        assert len(results) > 0

    def test_metadata_filtering_by_course(self):
        results = vector_search("schedule", course_id="CS101")
        assert all(d.course_id == "CS101" for d in results)

    def test_metadata_filtering_by_semester(self):
        results = vector_search("syllabus", semester="Fall2025")
        assert all(d.semester == "Fall2025" for d in results)

    def test_top_k_limits(self):
        results = vector_search("computer science", top_k=3)
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# Keyword Search Tests
# ---------------------------------------------------------------------------


class TestKeywordSearch:
    def test_finds_matching_documents(self):
        results = keyword_search("CS101")
        assert len(results) > 0
        assert any("CS101" in d.content for d in results)

    def test_course_id_filter(self):
        results = keyword_search("syllabus", course_id="CS201")
        assert all(d.course_id == "CS201" for d in results)

    def test_no_results_for_nonexistent(self):
        results = keyword_search("quantum_physics_XYZ999")
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Hybrid Pipeline Tests
# ---------------------------------------------------------------------------


class TestHybridRetrieve:
    def test_returns_fused_results(self):
        results = hybrid_retrieve("CS101 schedule")
        assert len(results) > 0
        assert len(results) <= 5  # default top_n

    def test_results_have_scores(self):
        results = hybrid_retrieve("CS101")
        for doc in results:
            assert doc.score > 0

    def test_course_filter(self):
        results = hybrid_retrieve("schedule", course_id="CS101")
        assert all(d.course_id == "CS101" for d in results)

    def test_custom_top_n(self):
        results = hybrid_retrieve("computer science", top_n=2)
        assert len(results) <= 2

    def test_results_ordered_by_score(self):
        results = hybrid_retrieve("CS101 syllabus schedule")
        scores = [d.score for d in results]
        assert scores == sorted(scores, reverse=True)
