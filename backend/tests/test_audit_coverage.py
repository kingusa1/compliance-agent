"""Every mutating route writes one audit row.

T8 series — extends `record_audit()` across the mutating routers so the
audit_log hash chain captures every state-changing API call. T8a
covers `app/routes.py`: POST /api/calls/upload + PATCH /api/calls/{id}/metadata.

Subsequent T8b–T8e tasks append more tests to this file (one per router).
"""
from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import SessionLocal, get_db
from app.main import app


@pytest.fixture(autouse=True)
def _clear_db_override():
    """Other test modules permanently override ``get_db`` to point at private
    in-memory SQLite engines. We need the real Postgres ``get_db`` so the
    handler's session and our ``SessionLocal`` queries see the same audit_log."""
    app.dependency_overrides.pop(get_db, None)
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def stub_upload_audio(monkeypatch):
    """Bypass Supabase Storage in tests — `SUPABASE_URL` is empty in .env so
    the real client raises. The handler still creates the Call row and writes
    the audit entry; we just need the storage call to no-op."""
    def _noop(local_path: str, remote_key: str, content_type: str = "audio/mpeg") -> str:
        return remote_key
    monkeypatch.setattr("app.routes.upload_audio", _noop)


@pytest.fixture
def stub_supabase_url(monkeypatch):
    """`app.auth._get_jwks_client()` raises when SUPABASE_URL is empty (it is
    in local .env). Set a stub URL so the JWKS guard passes; ``mock_jwks``
    then replaces the real client with one that returns our test public key."""
    from app.config import settings
    monkeypatch.setattr(settings, "supabase_url", "https://stub.test.local", raising=False)


def _audit_count(action: str) -> int:
    db = SessionLocal()
    try:
        return db.execute(
            text("SELECT count(*) FROM audit_log WHERE action = :a"),
            {"a": action},
        ).scalar() or 0
    finally:
        db.close()


def _mini_wav() -> bytes:
    # minimal valid wav header + empty data chunk (matches existing upload tests)
    return (
        b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x40\x1f\x00\x00\x80>\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )


def test_upload_writes_audit(client, stub_upload_audio):
    from app.models import Profile

    # audit_log.actor_id is FK → profiles.id, so seed a stub profile to
    # match the header value the test sends.
    db = SessionLocal()
    try:
        if not db.query(Profile).filter_by(id="test-user").first():
            db.add(Profile(
                id="test-user",
                email="test-user@test.local",
                name="Audit Test User",
                role="reviewer",
                active=True,
            ))
            db.commit()
    finally:
        db.close()

    before = _audit_count("call.upload")

    fname = f"audit-{uuid.uuid4().hex[:8]}.wav"
    files = {"file": (fname, io.BytesIO(_mini_wav()), "audio/wav")}
    r = client.post(
        "/api/calls/upload",
        files=files,
        headers={"x-user-id": "test-user"},
    )
    assert r.status_code in (200, 202), f"upload failed: {r.status_code} {r.text[:200]}"

    assert _audit_count("call.upload") == before + 1


def test_edit_metadata_writes_audit(client, stub_supabase_url, mock_jwks, auth):
    """PATCH /api/calls/{id}/metadata writes one audit row per call."""
    from app.models import Call, Customer, CustomerDeal, Profile

    # Seed a profile + call directly in the real Postgres test DB.
    db = SessionLocal()
    try:
        # idempotent profile seed
        if not db.query(Profile).filter_by(id="audit-reviewer").first():
            db.add(Profile(
                id="audit-reviewer",
                email="audit-reviewer@test.local",
                name="Audit Reviewer",
                role="reviewer",
                active=True,
            ))
            db.commit()

        cust = Customer(id=uuid.uuid4(), legal_name="AuditCo", slug=f"audit-{uuid.uuid4().hex[:6]}")
        db.add(cust)
        db.flush()
        deal = CustomerDeal(
            id=uuid.uuid4(),
            customer_id=cust.id,
            customer_name="AuditCo",
            supplier="E.ON Next",
            status="in_progress",
        )
        db.add(deal)
        db.flush()
        call = Call(
            id=str(uuid.uuid4()),
            filename=f"audit-{uuid.uuid4().hex[:6]}.mp3",
            file_path="/tmp/t.mp3",
            deal_id=deal.id,
            agent_name="Old Agent",
            customer_name="AuditCo",
            status="completed",
        )
        db.add(call)
        db.commit()
        call_id = call.id
    finally:
        db.close()

    before = _audit_count("call.edit_metadata")

    r = client.patch(
        f"/api/calls/{call_id}/metadata",
        json={"agent_name": "New Agent"},
        headers=auth("audit-reviewer"),
    )
    assert r.status_code == 200, f"edit_metadata failed: {r.status_code} {r.text[:200]}"

    assert _audit_count("call.edit_metadata") == before + 1


def test_hitl_claim_release_writes_audit(client, stub_supabase_url, mock_jwks, auth):
    """POST /api/calls/{id}/claim and /api/review-sessions/{id}/release each
    write one audit row. T8b — proves the HITL claim/release pair extends the
    tamper-evident chain, not just the upload/edit pair from T8a."""
    from app.models import Call, Profile

    db = SessionLocal()
    try:
        # Reviewer profile — current_reviewer dependency requires the JWT's
        # `sub` to resolve to an active row in profiles.
        if not db.query(Profile).filter_by(id="hitl-reviewer").first():
            db.add(Profile(
                id="hitl-reviewer",
                email="hitl-reviewer@test.local",
                name="HITL Reviewer",
                role="reviewer",
                active=True,
            ))
            db.commit()

        # Minimum-viable Call — no deal/customer needed for claim/release.
        call = Call(
            id=str(uuid.uuid4()),
            filename=f"hitl-{uuid.uuid4().hex[:6]}.mp3",
            file_path="/tmp/t.mp3",
            status="completed",
        )
        db.add(call)
        db.commit()
        call_id = call.id
    finally:
        db.close()

    before_claim = _audit_count("hitl.claim")
    before_release = _audit_count("hitl.release")

    r = client.post(f"/api/calls/{call_id}/claim", headers=auth("hitl-reviewer"))
    assert r.status_code == 200, f"claim failed: {r.status_code} {r.text[:200]}"
    session_id = r.json()["review_session_id"]

    r = client.post(
        f"/api/review-sessions/{session_id}/release",
        headers=auth("hitl-reviewer"),
    )
    assert r.status_code == 200, f"release failed: {r.status_code} {r.text[:200]}"

    assert _audit_count("hitl.claim") == before_claim + 1
    assert _audit_count("hitl.release") == before_release + 1


# --- T8d: script_routes.py — create/update/delete ----------------------------
#
# Scripts are admin-managed config (supplier checklists). Mutations are rare
# but high-impact — every reviewer's verdict depends on the active checkpoint
# set. We audit create/update/delete so retroactive checkpoint edits leave a
# tamper-evident trail. The /upload route is pure parsing (no DB write) and
# is not audited here — auditing happens when the parsed result is committed
# via POST /api/scripts.

def _seed_audit_profile(profile_id: str = "test-user") -> None:
    """Idempotent profile seed for actor_id FK."""
    from app.models import Profile

    db = SessionLocal()
    try:
        if not db.query(Profile).filter_by(id=profile_id).first():
            db.add(Profile(
                id=profile_id,
                email=f"{profile_id}@test.local",
                name=f"Audit {profile_id}",
                role="reviewer",
                active=True,
            ))
            db.commit()
    finally:
        db.close()


def _script_create_payload(name_suffix: str = "") -> dict:
    """Minimal valid ScriptCreate body — one required checkpoint."""
    return {
        "supplier_name": f"AuditCo{name_suffix}",
        "script_name": f"Audit Script {name_suffix}".strip(),
        "version": "1",
        "mode": "meaning_for_meaning",
        "checkpoints": [
            {
                "section": 1,
                "name": "audit_test",
                "required": "must say hello",
                "key_phrases": ["hello"],
                "customer_response_required": False,
                "strictness": "mandatory",
            }
        ],
    }


def test_script_create_writes_audit(client):
    """POST /api/scripts writes one script.create audit row."""
    _seed_audit_profile("test-user")

    before = _audit_count("script.create")

    r = client.post(
        "/api/scripts",
        json=_script_create_payload(uuid.uuid4().hex[:6]),
        headers={"x-user-id": "test-user"},
    )
    assert r.status_code in (200, 201), f"create failed: {r.status_code} {r.text[:200]}"
    assert _audit_count("script.create") == before + 1


def test_script_update_writes_audit(client):
    """PUT /api/scripts/{id} writes one script.update audit row."""
    _seed_audit_profile("test-user")

    # Seed a script first via the create route so we have a valid id.
    create_resp = client.post(
        "/api/scripts",
        json=_script_create_payload(uuid.uuid4().hex[:6]),
        headers={"x-user-id": "test-user"},
    )
    assert create_resp.status_code in (200, 201), create_resp.text[:200]
    sid = create_resp.json()["id"]

    before = _audit_count("script.update")

    # Update with a different script_name to trigger a real change.
    update_body = _script_create_payload(uuid.uuid4().hex[:6])
    r = client.put(
        f"/api/scripts/{sid}",
        json=update_body,
        headers={"x-user-id": "test-user"},
    )
    assert r.status_code in (200, 204), f"update failed: {r.status_code} {r.text[:200]}"
    assert _audit_count("script.update") == before + 1


def test_script_delete_writes_audit(client):
    """DELETE /api/scripts/{id} writes one script.delete audit row."""
    _seed_audit_profile("test-user")

    create_resp = client.post(
        "/api/scripts",
        json=_script_create_payload(uuid.uuid4().hex[:6]),
        headers={"x-user-id": "test-user"},
    )
    assert create_resp.status_code in (200, 201), create_resp.text[:200]
    sid = create_resp.json()["id"]

    before = _audit_count("script.delete")

    r = client.delete(
        f"/api/scripts/{sid}",
        headers={"x-user-id": "test-user"},
    )
    assert r.status_code in (200, 204), f"delete failed: {r.status_code} {r.text[:200]}"
    assert _audit_count("script.delete") == before + 1


# --- T8e: deals_routes.py — POST /api/deals + POST /api/deals/stub ----------
#
# Deal creation is the entry point for the Pillar 3 lifecycle. We audit
# both the explicit create (deals_routes.create_deal) and the stub create
# used by the same-deal upload handshake (routes.post_deal_stub) so the
# tamper-evident chain captures the full set of deal-row inserts. Update /
# resolve / verdict mutations are pipeline-driven (no public route) and
# fall outside this task's scope.


def test_deal_create_writes_audit(client):
    """POST /api/deals writes one deal.create audit row."""
    _seed_audit_profile("test-user")

    before = _audit_count("deal.create")

    r = client.post(
        "/api/deals",
        json={
            "customer_name": f"AuditCo-{uuid.uuid4().hex[:6]}",
            "supplier": "E.ON Next",
            "status": "in_progress",
        },
        headers={"x-user-id": "test-user"},
    )
    assert r.status_code in (200, 201), f"create failed: {r.status_code} {r.text[:200]}"
    assert _audit_count("deal.create") == before + 1


def test_deal_stub_writes_audit(client):
    """POST /api/deals/stub writes one deal.create audit row.

    Stub creates are used by the same-deal upload handshake — they
    insert a placeholder CustomerDeal that the pipeline back-fills as
    audio uploads land. They go through the audit chain identically.
    """
    _seed_audit_profile("test-user")

    before = _audit_count("deal.create")

    r = client.post(
        "/api/deals/stub",
        headers={"x-user-id": "test-user"},
    )
    assert r.status_code in (200, 201), f"stub create failed: {r.status_code} {r.text[:200]}"
    assert _audit_count("deal.create") == before + 1


def test_audit_log_orm_model_exists():
    """AuditLog ORM model is registered in Base.metadata so SQLite tests
    can create_all() the table without depending on Postgres-only migration.

    Regression guard: if someone removes the ORM class, this test fails.
    """
    from app.database import Base

    assert "audit_log" in Base.metadata.tables
    cols = {c.name for c in Base.metadata.tables["audit_log"].columns}
    expected = {
        "id", "occurred_at", "organization_id", "actor_id",
        "action", "entity_type", "entity_id",
        "payload", "prev_hash", "this_hash",
    }
    assert expected.issubset(cols), f"missing columns: {expected - cols}"
