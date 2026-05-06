"""Outer safety layer — three-tier sequential gate.

Runs after intent classification and BEFORE the LLM picks a tool.
Internal structure: RBAC → static rules → LLM intent analyzer,
short-circuiting on the first non-ALLOW decision.

See docs/adr/005-outer-safety-layer.md for design rationale.

Phase 0 ships only the typed contracts (`schemas.py`). Tier
implementations land in Phases 1-4.
"""
