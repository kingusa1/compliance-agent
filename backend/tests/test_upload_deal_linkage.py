"""Verify POST /api/calls/upload wires deal_id/call_type and auto-creates deals."""
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal, get_db
from app.models import CustomerDeal, Call

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_db_override():
    """Other test files permanently mutate ``app.dependency_overrides[get_db]``
    to point at their private in-memory SQLite engines. This file needs the
    real Postgres ``get_db`` so its ``SessionLocal`` queries and the endpoint's
    DB session see the same rows.

    2026-05-24 wiring audit C2 added ``Depends(current_reviewer)`` to
    ``POST /api/calls/upload``. The conftest autouse stub doesn't reach
    this test on CI (likely a fixture-resolution timing issue with the
    file's own autouse + module-level TestClient). Install the override
    explicitly here AND seed a test-admin profile so the audit log FK
    doesn't violate.
    """
    app.dependency_overrides.pop(get_db, None)

    from app.auth import current_user, require_lead
    from app.reviewers import current_reviewer
    from app.models import Profile

    _stub_admin = {
        "id": "test-admin",
        "email": "test-admin@compliance-agent.local",
        "name": "Test Admin",
        "role": "admin",
    }
    app.dependency_overrides[current_user] = lambda: _stub_admin
    app.dependency_overrides[current_reviewer] = lambda: _stub_admin
    app.dependency_overrides[require_lead] = lambda: _stub_admin

    db = SessionLocal()
    try:
        if not db.query(Profile).filter_by(id="test-admin").first():
            db.add(Profile(
                id="test-admin",
                email="test-admin@compliance-agent.local",
                name="Test Admin",
                role="admin",
                active=True,
            ))
            db.commit()
    finally:
        db.close()

    yield
    # Explicit teardown so the override doesn't leak into test files
    # that follow alphabetically (test_verdict, test_workflows, etc.).
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_reviewer, None)
    app.dependency_overrides.pop(require_lead, None)


@pytest.fixture(autouse=True)
def _stub_storage(monkeypatch):
    """Bypass Supabase Storage in CI: the test env points SUPABASE_URL at
    https://stub.invalid so the real upload_audio path raises ConnectError.
    Replace it (and signed_url) with no-op stubs so the upload route only
    exercises DB linkage, which is what these tests are about."""
    import app.routes as routes_mod
    monkeypatch.setattr(routes_mod, "upload_audio", lambda *a, **kw: None)
    monkeypatch.setattr(routes_mod, "signed_url", lambda *a, **kw: "https://stub.invalid/audio")
    yield


def _mini_wav(nonce: bytes = b"") -> bytes:
    """Minimal valid WAV with an optional nonce appended to the data chunk
    so two uploads from the same test produce different SHA-256 hashes —
    avoids the upload route's content-hash dedup short-circuit returning
    the previous call instead of creating a new deal."""
    return (
        b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x40\x1f\x00\x00\x80>\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        + nonce
    )


def _upload(**form_fields):
    nonce = uuid.uuid4().bytes  # unique 16 bytes per upload
    fname = f"sample-{uuid.uuid4().hex[:8]}.wav"
    files = {"file": (fname, io.BytesIO(_mini_wav(nonce)), "audio/wav")}
    return client.post("/api/calls/upload", files=files, data=form_fields)


def test_upload_auto_creates_deal_from_customer_name():
    name = f"AutoDealCo-{uuid.uuid4().hex[:6]}"
    r = _upload(customer_name=name, call_type="verbal")
    assert r.status_code in (200, 201), r.text
    body = r.json()

    # Deal auto-created with that customer_name
    db = SessionLocal()
    try:
        deal = db.query(CustomerDeal).filter(CustomerDeal.customer_name == name).first()
        assert deal is not None
        # The created call is linked to this deal
        call_id = body.get("id") or body.get("call_id")
        assert call_id
        call = db.query(Call).filter(Call.id == call_id).first()
        assert call is not None
        assert call.deal_id == deal.id
        assert call.call_type == "verbal"
    finally:
        db.close()


def test_upload_reuses_existing_deal_for_same_customer_name():
    name = f"ReuseCo-{uuid.uuid4().hex[:6]}"
    _upload(customer_name=name)
    r2 = _upload(customer_name=name, call_type="lead_gen")
    assert r2.status_code in (200, 201), r2.text

    db = SessionLocal()
    try:
        deals = db.query(CustomerDeal).filter(CustomerDeal.customer_name == name).all()
        assert len(deals) == 1, f"expected 1 deal for {name}, got {len(deals)}"
    finally:
        db.close()


def test_upload_uses_explicit_deal_id_when_given():
    db = SessionLocal()
    try:
        deal = CustomerDeal(customer_name=f"ExplicitCo-{uuid.uuid4().hex[:6]}")
        db.add(deal)
        db.commit()
        db.refresh(deal)
        did = str(deal.id)
    finally:
        db.close()

    r = _upload(deal_id=did, call_type="loa")
    assert r.status_code in (200, 201), r.text
    body = r.json()
    call_id = body.get("id") or body.get("call_id")
    db = SessionLocal()
    try:
        call = db.query(Call).filter(Call.id == call_id).first()
        assert call is not None
        assert str(call.deal_id) == did
        assert call.call_type == "loa"
    finally:
        db.close()


def test_upload_without_any_deal_hint_still_works():
    r = _upload()  # no customer_name, no deal_id
    assert r.status_code in (200, 201), r.text
    # Legacy path — deal_id is null; not an error.
