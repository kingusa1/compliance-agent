"""Verify POST /api/deals/stub creates a CustomerDeal in 'pending_audio' state.

A6 (Tracker XLSX Parity, Phase A): same-deal upload mode helper. UI calls
this once before firing N parallel /api/calls/upload requests with the
returned deal_id, so all N audio files attach to one deal record. Pipeline
detect_metadata is race-safe (only-fill-if-blank) so the first call's
detection backfills the shared stub deal.
"""
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import CustomerDeal

client = TestClient(app)


def test_post_deals_stub_returns_uuid():
    r = client.post("/api/deals/stub")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "deal_id" in data

    # Returned id must be a parseable UUID string.
    deal_uuid = uuid.UUID(data["deal_id"])

    db = SessionLocal()
    try:
        deal = db.query(CustomerDeal).filter_by(id=deal_uuid).first()
        assert deal is not None, f"deal {deal_uuid} not persisted"
        assert deal.status == "pending_audio"
        # Stub marker the pipeline + UI use to recognise an unfilled deal.
        assert deal.customer_name == "(pending audio upload)"
    finally:
        db.close()
