"""Inner safety layer — four-tier sequential gate + audit sidecar.

Runs AFTER the LLM has chosen a tool and produced args, BEFORE the
actual write executes. Internal structure:

  Layer 1 — Tool authorization re-check (role × actual tool name)
  Layer 2 — Parameter presence
  Layer 3 — Parameter format
  Layer 4 — Live state (cache-bypass Postgres-direct read)

Plus an audit sidecar that ALWAYS runs (even on DENY) so attempted
actions leave a trace.

See `docs/adr/006-inner-safety-layer.md` for design rationale.

Phase 0 ships only the typed contracts (`schemas.py`). Layer
implementations + audit + node + plan-path wiring land in Phases 1-5.
"""
