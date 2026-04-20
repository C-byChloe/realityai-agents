"""Evaluation harness for measuring context precision.

Context precision = relevant docs in top-5 / total docs in top-5

Compares vector-only and hybrid retrieval against 100 annotated
query-document pairs.
"""

import json
from pathlib import Path

from retrieval.hybrid import Document, hybrid_retrieve, vector_search


def load_ground_truth(path: str | None = None) -> list[dict]:
    """Load the annotated ground truth dataset."""
    if path is None:
        path = str(Path(__file__).parent / "ground_truth.json")
    with open(path) as f:
        return json.load(f)


def evaluate_retrieval(
    retrieve_fn,
    ground_truth: list[dict],
    top_n: int = 5,
) -> dict:
    """Evaluate a retrieval function against ground truth.

    Args:
        retrieve_fn: Function(query, course_id) → list[Document]
        ground_truth: List of annotated query-document pairs.
        top_n: Number of results to consider.

    Returns:
        Dict with precision metrics and per-query details.
    """
    total_precision = 0.0
    results = []

    for entry in ground_truth:
        query = entry["query"]
        expected = set(entry["relevant_docs"])
        course_id = entry.get("course_id")

        retrieved = retrieve_fn(query, course_id)
        retrieved_ids = {doc.doc_id for doc in retrieved[:top_n]}

        hits = len(expected & retrieved_ids)
        precision = hits / min(len(expected), top_n) if expected else 0.0
        total_precision += precision

        results.append({
            "id": entry["id"],
            "query": query,
            "expected": list(expected),
            "retrieved": list(retrieved_ids),
            "hits": hits,
            "precision": precision,
        })

    avg_precision = total_precision / len(ground_truth) if ground_truth else 0.0

    return {
        "total_queries": len(ground_truth),
        "average_precision": round(avg_precision, 4),
        "details": results,
    }


def run_baseline_vs_hybrid(ground_truth: list[dict] | None = None) -> dict:
    """Run both vector-only and hybrid evaluations and compare.

    Returns results for both methods and the improvement delta.
    """
    if ground_truth is None:
        ground_truth = load_ground_truth()

    def vector_only(query, course_id):
        return vector_search(query, top_k=10, course_id=course_id)

    def hybrid(query, course_id):
        return hybrid_retrieve(query, course_id=course_id, top_n=5)

    baseline = evaluate_retrieval(vector_only, ground_truth)
    hybrid_result = evaluate_retrieval(hybrid, ground_truth)

    return {
        "vector_only": {
            "precision": baseline["average_precision"],
            "total_queries": baseline["total_queries"],
        },
        "hybrid": {
            "precision": hybrid_result["average_precision"],
            "total_queries": hybrid_result["total_queries"],
        },
        "improvement": round(
            hybrid_result["average_precision"] - baseline["average_precision"], 4
        ),
    }
