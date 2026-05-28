import pytest
from fastapi.testclient import TestClient
from app.auth import current_user
from app.database import SessionLocal
from app.main import app
from app.models import Profile
from app.reviewers import current_reviewer, require_lead

# 2026-05-24 — POST /api/deals now requires `require_lead`; GETs require
# `current_reviewer`. Stub both + seed the test-lead Profile so the
# record_audit chain extension doesn't FK-violate on actor_id.
_STUB_LEAD = {
    "id": "test-lead",
    "email": "lead@compliance-agent.local",
    "name": "Test Lead",
    "role": "lead",
}


@pytest.fixture(autouse=True)
def _stub_auth():
    app.dependency_overrides[current_user] = lambda: _STUB_LEAD
    app.dependency_overrides[current_reviewer] = lambda: _STUB_LEAD
    app.dependency_overrides[require_lead] = lambda: _STUB_LEAD
    db = SessionLocal()
    try:
        if not db.query(Profile).filter_by(id="test-lead").first():
            db.add(Profile(
                id="test-lead",
                email="lead@compliance-agent.local",
                name="Test Lead",
                role="lead",
                active=True,
            ))
            db.commit()
    finally:
        db.close()
    yield
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_reviewer, None)
    app.dependency_overrides.pop(require_lead, None)


client = TestClient(app)


def test_create_and_get_deal():
    r = client.post("/api/deals", json={"customer_name": "Acme Ltd", "supplier": "E.ON"})
    assert r.status_code == 201, r.text
    body = r.json()
    deal_id = body["id"]
    assert body["customer_name"] == "Acme Ltd"
    assert body["status"] == "in_progress"
    assert body["supplier"] == "E.ON"

    r2 = client.get(f"/api/deals/{deal_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == deal_id


def test_list_deals_filters_by_status():
    client.post("/api/deals", json={"customer_name": "Foo", "status": "closed"})
    r = client.get("/api/deals?status=closed")
    assert r.status_code == 200
    rows = r.json()["deals"]
    assert all(d["status"] == "closed" for d in rows)


def test_get_deal_calls_returns_empty_for_new_deal():
    r = client.post("/api/deals", json={"customer_name": "Bar"})
    deal_id = r.json()["id"]
    r2 = client.get(f"/api/deals/{deal_id}/calls")
    assert r2.status_code == 200
    assert r2.json() == {"calls": []}


def test_get_deal_404_when_not_found():
    import uuid
    r = client.get(f"/api/deals/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Wave-46 — meter-display coalesce. The deal-detail page read only the
# legacy `mpan_or_mprn` column, so a reviewer-typed MPAN (which lands in
# the L7 `mpan_electricity` column) showed as "—". These lock the
# serialiser's fallback order across all three storage generations.
# ---------------------------------------------------------------------------


class _FakeDeal:
    """Minimal stand-in for CustomerDeal — the serialiser helpers only
    touch meter columns, so we don't need a DB row to test them."""

    def __init__(self, *, mpan_or_mprn=None, mpan_electricity=None,
                 mprn_gas=None, meters=None):
        self.mpan_or_mprn = mpan_or_mprn
        self.mpan_electricity = mpan_electricity
        self.mprn_gas = mprn_gas
        self.meters = meters


def test_wave46_meter_display_prefers_l7_split_columns():
    from app.deals_routes import _meter_display

    d = _FakeDeal(mpan_electricity="8888777766665")
    assert _meter_display(d) == "8888777766665"


def test_wave46_meter_display_dual_fuel_joins_both():
    from app.deals_routes import _meter_display

    d = _FakeDeal(mpan_electricity="1012371240692", mprn_gas="9876543210")
    assert _meter_display(d) == "1012371240692 / 9876543210"


def test_wave46_meter_display_falls_back_to_legacy_column():
    from app.deals_routes import _meter_display

    d = _FakeDeal(mpan_or_mprn="LEGACY123")
    assert _meter_display(d) == "LEGACY123"


def test_wave46_meter_display_falls_back_to_meters_array():
    from app.deals_routes import _meter_display

    d = _FakeDeal(meters=[{"mpan": "ARR111"}])
    assert _meter_display(d) == "ARR111"


def test_wave46_meter_display_none_when_all_empty():
    from app.deals_routes import _meter_display

    assert _meter_display(_FakeDeal()) is None


def test_wave46_meters_display_synthesises_from_split_columns():
    from app.deals_routes import _meters_display

    d = _FakeDeal(mpan_electricity="555", mprn_gas="666")
    assert _meters_display(d) == [{"mpan": "555", "mprn": "666"}]


def test_wave46_meters_display_prefers_existing_json_array():
    from app.deals_routes import _meters_display

    d = _FakeDeal(mpan_electricity="555", meters=[{"mpan": "PRIOR"}])
    assert _meters_display(d) == [{"mpan": "PRIOR"}]
