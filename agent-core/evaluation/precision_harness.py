"""Evaluation harness for retrieval precision and recall.

Metrics:
  Precision@k = |relevant ∩ retrieved_top_k| / min(k, |retrieved_top_k|)
  Recall@k    = |relevant ∩ retrieved_top_k| / |relevant|

Both Precision@5 and Recall@5 are computed against `ground_truth.json`
(self-curated annotations against the mock document universe). Compares
vector-only vs. hybrid (vector + keyword + RRF fusion).

See `evaluation/README.md` for methodology and honest limitations.
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

    Returns aggregate Precision@k and Recall@k plus per-query details.
    """
    total_precision = 0.0
    total_recall = 0.0
    results = []

    for entry in ground_truth:
        query = entry["query"]
        expected = set(entry["relevant_docs"])
        course_id = entry.get("course_id")

        retrieved = retrieve_fn(query, course_id)
        retrieved_ids = {doc.doc_id for doc in retrieved[:top_n]}

        hits = len(expected & retrieved_ids)
        # Precision normalizes by the *capacity* of the result slot, capped
        # by the number of relevant docs (so a query with 1 relevant doc
        # can score 1.0 even though we returned 5).
        precision = hits / min(len(expected), top_n) if expected else 0.0
        # Recall is hits / total relevant.
        recall = hits / len(expected) if expected else 0.0

        total_precision += precision
        total_recall += recall

        results.append({
            "id": entry["id"],
            "query": query,
            "expected": list(expected),
            "retrieved": list(retrieved_ids),
            "hits": hits,
            "precision": precision,
            "recall": recall,
        })

    n = len(ground_truth) or 1
    return {
        "total_queries": len(ground_truth),
        "top_n": top_n,
        "average_precision": round(total_precision / n, 4),
        "average_recall": round(total_recall / n, 4),
        "details": results,
    }


def run_baseline_vs_hybrid(ground_truth: list[dict] | None = None) -> dict:
    """Run both vector-only and hybrid evaluations and compare."""
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
            "precision_at_5": baseline["average_precision"],
            "recall_at_5": baseline["average_recall"],
            "total_queries": baseline["total_queries"],
        },
        "hybrid": {
            "precision_at_5": hybrid_result["average_precision"],
            "recall_at_5": hybrid_result["average_recall"],
            "total_queries": hybrid_result["total_queries"],
        },
        "improvement": {
            "precision_at_5": round(
                hybrid_result["average_precision"] - baseline["average_precision"], 4
            ),
            "recall_at_5": round(
                hybrid_result["average_recall"] - baseline["average_recall"], 4
            ),
        },
    }
