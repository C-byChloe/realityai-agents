"""Gap analysis: what does the student still need to take?

Pure set operations over a typed RequirementNode tree. No LLM. The set
diff is the canonical example from Talking Point 3 of work that should
not be delegated to a probabilistic model.
"""

from __future__ import annotations

from schemas.query_outputs import (
    DegreeProgram,
    RequirementNode,
    StudentTranscript,
    UnsatisfiedRequirement,
)


def compute_unsatisfied(
    program: DegreeProgram,
    transcript: StudentTranscript,
) -> list[UnsatisfiedRequirement]:
    """Return the leaf requirements that are not yet satisfied."""
    completed = transcript.completed
    out: list[UnsatisfiedRequirement] = []
    _walk(program.root, completed, out, parent_satisfied=False)
    return out


def _walk(
    node: RequirementNode,
    completed: set[str],
    out: list[UnsatisfiedRequirement],
    parent_satisfied: bool,
) -> bool:
    """Walk the requirement tree. Return True iff this subtree is satisfied."""
    if node.kind == "leaf":
        already = [c for c in node.pool if c in completed]
        if len(already) >= node.need:
            return True
        if not parent_satisfied:
            out.append(
                UnsatisfiedRequirement(
                    requirement_id=node.requirement_id,
                    name=node.name or node.requirement_id,
                    pool=node.pool,
                    need=node.need - len(already),
                    already_satisfied_by=already,
                )
            )
        return False

    child_results = [_walk(c, completed, out, parent_satisfied) for c in node.children]

    if node.kind == "and":
        return all(child_results)
    if node.kind == "or":
        return any(child_results)
    raise ValueError(f"unknown requirement kind: {node.kind}")
