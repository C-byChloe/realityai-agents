"""Audit sidecar for the inner safety gate (ADR 006 D4).

Audit is NOT one of the four sequential layers — it's a sidecar concern
that ALWAYS runs, even when the safety chain short-circuited at Layer 1.
Catches scan patterns where an attacker probes for permissions; if audit
only ran on ALLOW, denied attempts would leave no trace.

Two-phase population:
  1. `build_audit_record(input, ...)` — called after the layer chain
     finishes (allow OR deny). Captures: who, what tool, what arg-keys,
     verdict, attribution. Args VALUES are intentionally not captured —
     `args_keys` is the schema's only field for argument data, so PII
     stays in the LangSmith trace (which has its own access controls)
     rather than the audit log.
  2. `update_audit_for_execution(record, ...)` — called after the gRPC
     call (only on ALLOW path). Adds execution_succeeded / execution_error.

Persistence:
  `persist_audit_record(record)` — current stub emits a structured log
  line + LangSmith span attribute. Idempotent: persisting the same
  audit_id twice is a no-op (defensive against retry paths).

  Production swap: replace the stub body with a durable append-only
  sink (e.g., Postgres `audit_log` table, S3 manifest, vendor logging).
  The function signature stays stable — callers (the inner node + the
  plan-driven action path) don't change.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from safety.inner.schemas import (
    AuditRecord,
    InnerLayerName,
    InnerSafetyDecision,
    InnerSafetyInput,
)

logger = logging.getLogger(__name__)

# Module-level set of persisted audit_ids. Production replaces this with
# a real durable sink (DB unique-key constraint, idempotency table, etc.).
# Kept here so `persist_audit_record` is genuinely idempotent in the stub.
_PERSISTED_IDS: set[str] = set()


# ---------------------------------------------------------------------------
# Phase 1: build (pre-execution)
# ---------------------------------------------------------------------------


def build_audit_record(
    safety_input: InnerSafetyInput,
    *,
    decision: InnerSafetyDecision,
    short_circuited_at: InnerLayerName | None,
    final_reason_code: str,
) -> AuditRecord:
    """Construct the pre-execution audit record from the safety verdict.

    `execution_attempted` defaults to False here; it gets flipped to True
    in `update_audit_for_execution` only on the ALLOW path. Records
    args_keys (sorted, deterministic) — never the values themselves.
    """
    args_keys = sorted((safety_input.tool_args or {}).keys())
    return AuditRecord(
        audit_id=_new_audit_id(),
        timestamp_iso=datetime.now(timezone.utc).isoformat(),
        tool_name=safety_input.tool_name,
        args_keys=args_keys,
        user_id=safety_input.session.user_id,
        session_id=safety_input.session.session_id,
        user_role=safety_input.session.user_role,
        tenant_id=safety_input.session.tenant_id,
        decision=decision,
        short_circuited_at=short_circuited_at,
        final_reason_code=final_reason_code,
        execution_attempted=False,
        execution_succeeded=None,
        execution_error=None,
    )


def _new_audit_id() -> str:
    """12-character uuid hex slice — opaque, sortable enough for trace
    correlation, short enough for log greppability."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Phase 2: update (post-execution)
# ---------------------------------------------------------------------------


def update_audit_for_execution(
    record: AuditRecord,
    *,
    succeeded: bool,
    error: str | None = None,
) -> AuditRecord:
    """Return a NEW AuditRecord with the execution outcome attached.

    Pydantic models are immutable-by-convention; we return a copy
    rather than mutating in place. The original record is preserved for
    callers that want to compare pre/post snapshots.

    `execution_attempted` is unconditionally set to True here — the only
    way to reach this function is to have actually attempted the call.
    """
    return record.model_copy(
        update={
            "execution_attempted": True,
            "execution_succeeded": succeeded,
            "execution_error": error,
        }
    )


# ---------------------------------------------------------------------------
# Phase 3: persist (stub; production swap point)
# ---------------------------------------------------------------------------


def persist_audit_record(record: AuditRecord) -> None:
    """Emit the audit record to the durable sink.

    Current implementation: structured log + module-level dedup. This
    is sufficient for development and for piping into a log aggregator
    in production — but a real audit log requires durable, append-only
    storage (e.g., a Postgres `audit_log` table with a unique constraint
    on audit_id, or an S3 manifest). Phase 4 documents the seam; the
    production swap doesn't change any caller.

    Idempotency: persisting the same audit_id twice is a no-op. Useful
    for retry paths and defensive against double-fire from the action
    subgraph + plan-driven path both invoking inner safety on the same
    request (shouldn't happen, but cheap to guard).
    """
    if record.audit_id in _PERSISTED_IDS:
        logger.debug(
            "audit %s already persisted; skipping duplicate write",
            record.audit_id,
        )
        return

    _PERSISTED_IDS.add(record.audit_id)
    logger.info(
        "audit",
        extra={
            "audit_id": record.audit_id,
            "tool": record.tool_name,
            "user_id": record.user_id,
            "user_role": record.user_role,
            "decision": record.decision.value,
            "short_circuited_at": (
                record.short_circuited_at.value
                if record.short_circuited_at
                else None
            ),
            "execution_attempted": record.execution_attempted,
            "execution_succeeded": record.execution_succeeded,
            "args_keys": record.args_keys,
        },
    )


# Test hook — exposed so tests can verify persistence state without
# reaching into module internals. Not part of the public API.
def _persisted_audit_ids() -> set[str]:
    return set(_PERSISTED_IDS)


def _reset_persisted_audit_ids_for_testing() -> None:
    """Test-only reset of the dedup set. Production never calls this."""
    _PERSISTED_IDS.clear()
