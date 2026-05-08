"""Tamper-evident audit log helper.

`record_audit()` appends one row to `audit_log` and chains it to the
previous row by hashing (prev_hash + canonical(payload)). A future
verifier can walk the chain and detect tampering: any retroactive edit
to a row's payload invalidates every subsequent this_hash.

The migration that introduces this table is 497bd38e5551.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash_link(prev_hash: str | None, action: str, entity_type: str,
               entity_id: str | None, payload_canonical: str) -> str:
    h = hashlib.sha256()
    h.update((prev_hash or "").encode())
    h.update(b"|")
    h.update(action.encode())
    h.update(b"|")
    h.update(entity_type.encode())
    h.update(b"|")
    h.update((entity_id or "").encode())
    h.update(b"|")
    h.update(payload_canonical.encode())
    return h.hexdigest()


def record_audit(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
    organization_id: str | None = None,
    actor_id: str | None = None,
) -> None:
    """Append one tamper-evident row to audit_log.

    Caller is responsible for `db.commit()` — we deliberately keep this
    inside the caller's transaction so audit + business write are atomic.
    """
    payload = payload or {}
    canonical = _canonical(payload)

    prev_hash_row = db.execute(
        text(
            "SELECT this_hash FROM audit_log "
            "ORDER BY occurred_at DESC, id DESC LIMIT 1"
        )
    ).fetchone()
    prev_hash = prev_hash_row[0] if prev_hash_row else None
    this_hash = _hash_link(prev_hash, action, entity_type, entity_id, canonical)

    # SQLite (used by tests) doesn't understand `CAST(... AS jsonb)`. The
    # AuditLog ORM model declares ``payload`` via ``JSONBCompat`` which
    # already stores TEXT on SQLite, so we only need the cast on Postgres.
    # SQLite also has no ``gen_random_uuid()`` server_default, so we
    # generate the id Python-side on that path.
    bind = db.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    is_pg = dialect_name == "postgresql"
    payload_expr = "CAST(:payload AS jsonb)" if is_pg else ":payload"

    params: dict[str, Any] = {
        "org": organization_id,
        "actor": actor_id,
        "action": action,
        "etype": entity_type,
        "eid": entity_id,
        "payload": canonical,
        "prev": prev_hash,
        "this": this_hash,
    }
    if is_pg:
        sql = (
            "INSERT INTO audit_log "
            "(organization_id, actor_id, action, entity_type, entity_id, "
            " payload, prev_hash, this_hash) "
            "VALUES (:org, :actor, :action, :etype, :eid, "
            f"        {payload_expr}, :prev, :this)"
        )
    else:
        params["aid"] = str(uuid.uuid4())
        sql = (
            "INSERT INTO audit_log "
            "(id, organization_id, actor_id, action, entity_type, entity_id, "
            " payload, prev_hash, this_hash) "
            "VALUES (:aid, :org, :actor, :action, :etype, :eid, "
            f"        {payload_expr}, :prev, :this)"
        )

    db.execute(text(sql), params)
