from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hovita.db.models import AuditLog
from hovita.logging.logger import get_trace_id, redact_secrets


def _compute_entry_hash(
    seq: int,
    character_id: str,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details_json: dict[str, Any],
    prev_hash: str,
) -> str:
    canonical = json.dumps(
        {
            "seq": seq,
            "character_id": character_id,
            "actor": actor,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details_json,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_audit_log(
    session: Session,
    *,
    character_id: str,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str = "",
    details: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> AuditLog:
    """Write an immutable, hash-chained audit entry with automatic secret redaction."""
    safe_details_str = redact_secrets(json.dumps(details or {}, default=str, ensure_ascii=False))
    safe_details: dict[str, Any] = json.loads(safe_details_str)

    last_row = session.execute(
        select(AuditLog).order_by(AuditLog.seq.desc()).limit(1)
    ).scalar_one_or_none()
    seq = 1 if last_row is None else last_row.seq + 1
    prev_hash = "GENESIS" if last_row is None else last_row.entry_hash
    entry_hash = _compute_entry_hash(
        seq, character_id, actor, action, resource_type, resource_id, safe_details, prev_hash
    )
    row = AuditLog(
        character_id=character_id,
        seq=seq,
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details_json=safe_details,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        trace_id=trace_id or get_trace_id(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def verify_audit_chain(session: Session) -> tuple[bool, int]:
    """Verify hash-chain integrity across all audit entries."""
    rows = list(session.execute(select(AuditLog).order_by(AuditLog.seq.asc())).scalars())
    expected_prev = "GENESIS"
    for row in rows:
        if row.prev_hash != expected_prev:
            return False, row.seq
        expected_hash = _compute_entry_hash(
            row.seq,
            row.character_id,
            row.actor,
            row.action,
            row.resource_type,
            row.resource_id,
            row.details_json,
            row.prev_hash,
        )
        if row.entry_hash != expected_hash:
            return False, row.seq
        expected_prev = row.entry_hash
    count = session.execute(select(func.count(AuditLog.id))).scalar_one()
    return True, int(count)
