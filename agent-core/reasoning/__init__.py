"""Symbolic reasoning layer.

LLM does what LLM is good at (constraint extraction, explanation). This
module does what algorithms are good at (set diff, interval overlap,
backtracking CSP). See Talking Point 3.
"""

from reasoning.gap_analysis import compute_unsatisfied
from reasoning.solver import ConstraintSolver

__all__ = ["ConstraintSolver", "compute_unsatisfied"]
