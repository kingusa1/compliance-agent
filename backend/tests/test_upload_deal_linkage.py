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
    DB session see the same rows."""
    app.dependency_overrides.pop(get_db, None)
    yield


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


def _mini_wav() -> bytes:
    # minimal valid wav header + empty data chunk
    return (
        b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x40\x1f\x00\x00\x80>\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )


def _upload(**form_fields):
    files = {"file": (f"sample-{uuid.uuid4().hex[:8]}.wav", io.BytesIO(_mini_wav()), "audio/wav")}
    return client.post("/api/calls/upload", files=files, data=form_fields)


def test_upload_auto_creates_deal_from_customer_name():
    name = f"AutoDealCo-{uuid.uuid4().hex[:6]}"
    r = _upload(customer_name=name, call_type="closer")
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
        assert call.call_type == "closer"
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

    r = _upload(deal_id=did, call_type="amendment")
    assert r.status_code in (200, 201), r.text
    body = r.json()
    call_id = body.get("id") or body.get("call_id")
    db = SessionLocal()
    try:
        call = db.query(Call).filter(Call.id == call_id).first()
        assert call is not None
        assert str(call.deal_id) == did
        assert call.call_type == "amendment"
    finally:
        db.close()


def test_upload_without_any_deal_hint_still_works():
    r = _upload()  # no customer_name, no deal_id
    assert r.status_code in (200, 201), r.text
    # Legacy path — deal_id is null; not an error.
