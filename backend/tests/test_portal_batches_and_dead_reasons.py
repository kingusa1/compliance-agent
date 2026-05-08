"""W4.5 + W4.6 (v3-watt-coverage): portal-batches admin + dead-reasons UI tests.

Covers:
  - GET  /api/rejections/dead-reasons          (vocab + glosses)
  - PATCH /api/rejections/{id}                  with dead_reason validation
  - GET  /api/portal-batches?supplier=          (supplier-grouped FIXED)
  - POST /api/portal-batches/submit             (batch submit + audit + validation)

Setup mirrors test_rejections.py: in-memory SQLite + StaticPool, autouse
clean_db override, ES256 keypair via mock_jwks.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Profile, Rejection, RejectionAuditLog
from app.rejections_routes import DEAD_REASONS


_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_engine)
TestSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _disable_dev_all_admin(monkeypatch):
    """Wave 4 DEV_ALL_ADMIN flag rewrites every user's role to 'admin'.
    test_submit_batch_admin_only asserts that 'sarah' (reviewer) gets a
    403 from POST /api/portal-batches/submit — needs stored role to win."""
    monkeypatch.setattr("app.config.settings.dev_all_admin", False)
    yield


@pytest.fixture
def seed_profiles_local():
    db = TestSessionLocal()
    try:
        db.add_all([
            Profile(id="sarah", email="sarah@test.local", name="Sarah Ali",   role="reviewer", active=True),
            Profile(id="zoe",   email="zoe@test.local",   name="Zoe Admin",   role="admin",    active=True),
        ])
        db.commit()
    finally:
        db.close()


def _create(payload: dict, headers: dict) -> dict:
    r = client.post("/api/rejections", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _patch(rid: str, body: dict, headers: dict) -> dict:
    r = client.patch(f"/api/rejections/{rid}", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ─── W4.6 — dead-reasons vocab endpoint ────────────────────────────────


def test_dead_reasons_endpoint_returns_5_keys_with_glosses(
    mock_jwks, seed_profiles_local, auth
):
    r = client.get("/api/rejections/dead-reasons", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()
    keys = [d["key"] for d in body["dead_reasons"]]
    assert set(keys) == set(DEAD_REASONS.keys())
    # Every entry has a non-empty gloss + a humanised label.
    for entry in body["dead_reasons"]:
        assert entry["gloss"], entry
        assert entry["label"], entry


def test_dead_reasons_endpoint_requires_auth(mock_jwks):
    # No Authorization header → 401/403 from the dependency.
    r = client.get("/api/rejections/dead-reasons")
    assert r.status_code in (401, 403)


# ─── W4.6 — patch validation ───────────────────────────────────────────


def test_patch_with_valid_dead_reason_persists(mock_jwks, seed_profiles_local, auth):
    body = _create(
        {"category": "ADMIN_ERROR", "rejection_reason": "x"}, auth("zoe")
    )
    rid = body["id"]
    out = _patch(
        rid,
        {"status": "DEAD", "dead_reason": "in_contract"},
        auth("zoe"),
    )
    assert out["status"] == "DEAD"
    assert out["dead_reason"] == "in_contract"


def test_patch_with_invalid_dead_reason_rejected(mock_jwks, seed_profiles_local, auth):
    body = _create(
        {"category": "ADMIN_ERROR", "rejection_reason": "x"}, auth("zoe")
    )
    rid = body["id"]
    r = client.patch(
        f"/api/rejections/{rid}",
        json={"status": "DEAD", "dead_reason": "made_up_reason"},
        headers=auth("zoe"),
    )
    assert r.status_code == 400
    # Error mentions the field name so the UI can highlight it.
    assert "dead_reason" in r.text


def test_list_filters_by_dead_reason(mock_jwks, seed_profiles_local, auth):
    # Two DEAD rows, different reasons; one ACTIVE row to make sure we
    # aren't accidentally widening the tab filter.
    a = _create({"category": "ADMIN_ERROR", "rejection_reason": "a"}, auth("zoe"))
    b = _create({"category": "ADMIN_ERROR", "rejection_reason": "b"}, auth("zoe"))
    _create({"category": "ADMIN_ERROR", "rejection_reason": "still active"}, auth("zoe"))

    _patch(a["id"], {"status": "DEAD", "dead_reason": "in_contract"}, auth("zoe"))
    _patch(b["id"], {"status": "DEAD", "dead_reason": "customer_debt"}, auth("zoe"))

    r = client.get(
        "/api/rejections?tab=dead&dead_reason=in_contract",
        headers=auth("zoe"),
    )
    assert r.status_code == 200, r.text
    rows = r.json()["rejections"]
    assert len(rows) == 1
    assert rows[0]["id"] == a["id"]
    assert rows[0]["dead_reason"] == "in_contract"


# ─── W4.5 — portal-batches list ────────────────────────────────────────


def _seed_supplier_fixed_rows() -> dict[str, list[str]]:
    """Make 3 FIXED rows for E.ON + 1 for BGL + 1 NOT_STARTED for E.ON.
    Returns {supplier: [rejection_id...]} of the FIXED ones only."""
    eon_ids: list[str] = []
    bgl_ids: list[str] = []
    db = TestSessionLocal()
    try:
        for i in range(3):
            r = Rejection(
                id=__import__("uuid").uuid4(),
                customer_slug=f"acme-{i}",
                supplier="E.ON Next Energy",
                category="ADMIN_ERROR",
                rejection_reason=f"reason-{i}",
                status="FIXED",
                rejected_at=__import__("datetime").datetime.utcnow(),
            )
            db.add(r)
            db.flush()
            eon_ids.append(str(r.id))
        r = Rejection(
            id=__import__("uuid").uuid4(),
            customer_slug="bgl-co",
            supplier="BGL",
            category="PRICING_ISSUE",
            rejection_reason="price drift",
            status="FIXED",
            rejected_at=__import__("datetime").datetime.utcnow(),
        )
        db.add(r)
        db.flush()
        bgl_ids.append(str(r.id))
        # Not-started E.ON row that should NOT appear in batches.
        ns = Rejection(
            id=__import__("uuid").uuid4(),
            customer_slug="acme-active",
            supplier="E.ON Next Energy",
            category="ADMIN_ERROR",
            rejection_reason="not yet fixed",
            status="NOT_STARTED",
            rejected_at=__import__("datetime").datetime.utcnow(),
        )
        db.add(ns)
        db.commit()
    finally:
        db.close()
    return {"E.ON Next Energy": eon_ids, "BGL": bgl_ids}


def test_portal_batches_groups_by_supplier(
    mock_jwks, seed_profiles_local, auth
):
    seeded = _seed_supplier_fixed_rows()
    r = client.get("/api/portal-batches", headers=auth("zoe"))
    assert r.status_code == 200, r.text
    batches = r.json()["batches"]
    by_sup = {b["supplier"]: b for b in batches}
    assert set(by_sup.keys()) == {"E.ON Next Energy", "BGL"}
    assert by_sup["E.ON Next Energy"]["count"] == 3
    assert by_sup["BGL"]["count"] == 1
    # NOT_STARTED rows excluded.
    eon_ids = {r["id"] for r in by_sup["E.ON Next Energy"]["rejections"]}
    assert eon_ids == set(seeded["E.ON Next Energy"])


def test_portal_batches_filters_by_supplier_param(
    mock_jwks, seed_profiles_local, auth
):
    _seed_supplier_fixed_rows()
    r = client.get("/api/portal-batches?supplier=BGL", headers=auth("zoe"))
    assert r.status_code == 200
    batches = r.json()["batches"]
    assert [b["supplier"] for b in batches] == ["BGL"]


# ─── W4.5 — portal-batches submit ──────────────────────────────────────


def test_submit_batch_flips_status_and_writes_audit(
    mock_jwks, seed_profiles_local, auth
):
    seeded = _seed_supplier_fixed_rows()
    eon_ids = seeded["E.ON Next Energy"]
    r = client.post(
        "/api/portal-batches/submit",
        json={"supplier": "E.ON Next Energy", "rejection_ids": eon_ids},
        headers=auth("zoe"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["submitted"] == len(eon_ids)
    assert body["supplier"] == "E.ON Next Energy"

    # All 3 are now SUBMITTED_TO_PORTAL.
    db = TestSessionLocal()
    try:
        rows = db.query(Rejection).filter(Rejection.supplier == "E.ON Next Energy",
                                           Rejection.status == "SUBMITTED_TO_PORTAL").all()
        assert len(rows) == 3

        # And each got an audit row with action=portal_submitted.
        audit_rows = (
            db.query(RejectionAuditLog)
            .filter(RejectionAuditLog.action == "portal_submitted")
            .all()
        )
        assert len(audit_rows) == 3
        assert all(a.to_status == "SUBMITTED_TO_PORTAL" for a in audit_rows)
    finally:
        db.close()


def test_submit_batch_admin_only(mock_jwks, seed_profiles_local, auth):
    seeded = _seed_supplier_fixed_rows()
    r = client.post(
        "/api/portal-batches/submit",
        json={"supplier": "E.ON Next Energy", "rejection_ids": seeded["E.ON Next Energy"]},
        headers=auth("sarah"),  # reviewer, not admin
    )
    assert r.status_code == 403


def test_submit_batch_rejects_supplier_mismatch(
    mock_jwks, seed_profiles_local, auth
):
    seeded = _seed_supplier_fixed_rows()
    # Mix one BGL id into the E.ON batch — server must refuse.
    mixed = list(seeded["E.ON Next Energy"]) + list(seeded["BGL"])
    r = client.post(
        "/api/portal-batches/submit",
        json={"supplier": "E.ON Next Energy", "rejection_ids": mixed},
        headers=auth("zoe"),
    )
    assert r.status_code == 400
    assert "supplier" in r.text.lower()


def test_submit_batch_rejects_non_fixed_status(
    mock_jwks, seed_profiles_local, auth
):
    # NOT_STARTED row → can't be submitted to portal.
    body = _create(
        {"category": "ADMIN_ERROR", "rejection_reason": "x", "supplier": "E.ON Next Energy"},
        auth("zoe"),
    )
    r = client.post(
        "/api/portal-batches/submit",
        json={"supplier": "E.ON Next Energy", "rejection_ids": [body["id"]]},
        headers=auth("zoe"),
    )
    assert r.status_code == 400
    assert "submittable" in r.text.lower() or "fixed" in r.text.lower()
