"""W2 (v3-watt-coverage): /api/rejections endpoint suite + auto-create on FAIL verdict.

Setup mirrors test_compliance_override.py: dedicated in-memory SQLite +
StaticPool, autouse clean_db fixture overrides ``get_db``. Every test
authenticates with the shared test ES256 keypair via ``mock_jwks``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, CallCheckpoint, Profile, Rejection, RejectionAuditLog
from app.rejections_routes import infer_category


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
    Admin-gate tests (POST /api/rejections requires admin/lead, DELETE
    requires admin) need stored Profile.role to be honored so 'sarah'
    (reviewer) gets the 403 the test expects."""
    monkeypatch.setattr("app.config.settings.dev_all_admin", False)
    yield


@pytest.fixture
def seed_profiles_local():
    db = TestSessionLocal()
    try:
        db.add_all([
            Profile(id="sarah", email="sarah@test.local", name="Sarah Ali",   role="reviewer", active=True),
            Profile(id="omar",  email="omar@test.local",  name="Omar Hassan", role="lead",     active=True),
            Profile(id="zoe",   email="zoe@test.local",   name="Zoe Admin",   role="admin",    active=True),
        ])
        db.commit()
    finally:
        db.close()


def _create(payload: dict, headers: dict) -> dict:
    r = client.post("/api/rejections", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ─── Category inference (pure function) ─────────────────────────────────


def test_infer_category_keyword_branches():
    assert infer_category("BGL pulled prices and rate ceiling exceeded") == "PRICING_ISSUE"
    assert infer_category("agent missed cooling-off disclosure") == "VERBAL_SALES_ERROR"
    assert (
        infer_category("compliance / TPI commission disclosure missing", "WATT_BROKER")
        == "COMPLIANCE_ISSUE"
    )
    assert infer_category("wrong name on the LOA — typo on signup") == "ADMIN_ERROR"
    assert infer_category("BACS rejected three times — bank account closed") == "PROCESS_FAILURE"
    assert infer_category("VAT clause missing — Green Deal section") == "COMPLIANCE_ERROR"
    assert infer_category("DocuSign envelope expired") in {"DOCUSIGN_ERROR", "PROCESS_FAILURE"}
    # Default — nothing matches.
    assert infer_category("just a vague note") == "ADMIN_ERROR"
    # No reason at all.
    assert infer_category(None) == "ADMIN_ERROR"


# ─── CRUD ───────────────────────────────────────────────────────────────


def test_admin_creates_with_default_status_and_2day_deadline(
    mock_jwks, seed_profiles_local, auth
):
    payload = {
        "category": "ADMIN_ERROR",
        "rejection_reason": "wrong name on the account",
        "supplier": "E.ON Next Energy",
        "sales_agent": "Sammie",
    }
    body = _create(payload, auth("zoe"))
    assert body["status"] == "NOT_STARTED"
    assert body["category"] == "ADMIN_ERROR"
    rejected = datetime.fromisoformat(body["rejected_at"])
    deadline = datetime.fromisoformat(body["deadline"])
    diff = deadline - rejected
    assert timedelta(days=2) - timedelta(seconds=2) <= diff <= timedelta(days=2) + timedelta(seconds=2)

    # An audit row was written.
    db = TestSessionLocal()
    try:
        rows = db.query(RejectionAuditLog).all()
        assert len(rows) == 1
        assert rows[0].action == "created"
        assert rows[0].to_status == "NOT_STARTED"
    finally:
        db.close()


def test_non_admin_cannot_create(mock_jwks, seed_profiles_local, auth):
    r = client.post(
        "/api/rejections",
        json={"category": "ADMIN_ERROR", "rejection_reason": "x"},
        headers=auth("sarah"),
    )
    assert r.status_code == 403


def test_invalid_category_rejected(mock_jwks, seed_profiles_local, auth):
    r = client.post(
        "/api/rejections",
        json={"category": "BOGUS", "rejection_reason": "x"},
        headers=auth("zoe"),
    )
    assert r.status_code == 400


def test_list_with_tab_filtering(mock_jwks, seed_profiles_local, auth):
    # Create one in each canonical bucket.
    for status_, cat in [
        ("NOT_STARTED", "ADMIN_ERROR"),
        ("FIXED", "PRICING_ISSUE"),
        ("DEAD", "VERBAL_SALES_ERROR"),
    ]:
        body = _create(
            {"category": cat, "rejection_reason": f"seed-{cat}"},
            auth("zoe"),
        )
        if status_ != "NOT_STARTED":
            r = client.patch(
                f"/api/rejections/{body['id']}",
                json={"status": status_},
                headers=auth("zoe"),
            )
            assert r.status_code == 200, r.text

    def _list(tab):
        r = client.get(f"/api/rejections?tab={tab}", headers=auth("sarah"))
        assert r.status_code == 200
        return r.json()

    a = _list("active")
    f = _list("fixed")
    d = _list("dead")
    arch = _list("archive")

    assert {x["category"] for x in a["rejections"]} == {"ADMIN_ERROR"}
    assert {x["category"] for x in f["rejections"]} == {"PRICING_ISSUE"}
    assert {x["category"] for x in d["rejections"]} == {"VERBAL_SALES_ERROR"}
    assert len(arch["rejections"]) == 3
    # Counts correct regardless of which tab we hit.
    assert a["counts"] == {"active": 1, "fixed": 1, "dead": 1, "archive": 3}


def test_grouped_endpoint_collapses_rejections_per_call(
    mock_jwks, seed_profiles_local, auth
):
    """2026-05-23 redesign — /api/rejections/grouped collapses many
    rejection rows from the same call into a single group. Verifies:
      - one group per distinct call_id
      - rejection_count + status_mix + category_mix correctly summed
      - groups sorted by rejection_count DESC (worst call first)
      - counts use distinct call_id (not row count)
    """
    # Seed: 3 rejections on call-A, 1 on call-B, 1 with no call_id (orphan).
    base = {"rejection_reason": "seed"}
    call_a = "test-call-a"
    call_b = "test-call-b"
    for cat in ("ADMIN_ERROR", "ADMIN_ERROR", "PRICING_ISSUE"):
        _create(
            {**base, "category": cat, "call_id": call_a},
            auth("zoe"),
        )
    _create(
        {**base, "category": "VERBAL_SALES_ERROR", "call_id": call_b},
        auth("zoe"),
    )
    # Orphan: no call_id at all — must NOT appear in grouped view.
    _create({**base, "category": "ADMIN_ERROR"}, auth("zoe"))

    r = client.get("/api/rejections/grouped?tab=active", headers=auth("sarah"))
    assert r.status_code == 200, r.text
    body = r.json()

    groups = body["groups"]
    assert len(groups) == 2, "orphan rejection must be excluded from grouped view"

    # Worst call (3 rejections) first.
    a = groups[0]
    assert a["call_id"] == call_a
    assert a["rejection_count"] == 3
    assert a["status_mix"] == {"NOT_STARTED": 3}
    assert a["category_mix"] == {"ADMIN_ERROR": 2, "PRICING_ISSUE": 1}
    assert len(a["rejections"]) == 3

    b = groups[1]
    assert b["call_id"] == call_b
    assert b["rejection_count"] == 1

    # `counts` reports distinct call_ids, not row count — there are 2
    # calls in active state even though there are 4 active rejection rows.
    assert body["counts"]["active"] == 2
    # Total still tracks the underlying rejections, useful for hero counters.
    assert body["total_rejections"] == 4


def test_grouped_endpoint_respects_source_filter(
    mock_jwks, seed_profiles_local, auth
):
    """source=reviewer must include only rejections with a confirmed_by;
    the auto-created flat-list endpoint already does this — grouped
    must do the same so the same Source chip works on both views."""
    _create(
        {
            "category": "ADMIN_ERROR",
            "rejection_reason": "human-confirmed",
            "call_id": "call-with-reviewer",
        },
        auth("zoe"),  # zoe is admin → confirmed_by gets set by the factory
    )
    # The default factory creates with confirmed_by populated by the
    # admin actor — both endpoints should agree on it.
    r1 = client.get(
        "/api/rejections?tab=active&source=reviewer", headers=auth("sarah")
    )
    r2 = client.get(
        "/api/rejections/grouped?tab=active&source=reviewer",
        headers=auth("sarah"),
    )
    assert r1.status_code == 200 and r2.status_code == 200
    # Same population, different shape.
    flat_call_ids = {x["call_id"] for x in r1.json()["rejections"]}
    grouped_call_ids = {g["call_id"] for g in r2.json()["groups"]}
    assert flat_call_ids == grouped_call_ids


def test_patch_with_status_writes_audit_log(mock_jwks, seed_profiles_local, auth):
    body = _create(
        {"category": "ADMIN_ERROR", "rejection_reason": "x"}, auth("zoe")
    )
    rid = body["id"]
    r = client.patch(
        f"/api/rejections/{rid}",
        json={"status": "IN_PROGRESS"},
        headers=auth("sarah"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "IN_PROGRESS"

    log_resp = client.get(f"/api/rejections/{rid}/audit-log", headers=auth("sarah"))
    assert log_resp.status_code == 200
    rows = log_resp.json()["audit_log"]
    # At least: created + updated.
    assert any(a["action"] == "created" for a in rows)
    upd = [a for a in rows if a["action"] == "updated"]
    assert upd and upd[0]["from_status"] == "NOT_STARTED"
    assert upd[0]["to_status"] == "IN_PROGRESS"


def test_bulk_transition_flips_all_active_rejections(
    mock_jwks, seed_profiles_local, auth
):
    """2026-05-24 — POST /api/rejections/bulk-transition flips many ids
    to one status in a single trip. Verifies:
      - every active rejection is updated
      - resolved_at is set when to_status is terminal
      - per-row audit log is written (action='bulk_transitioned')
      - response surfaces updated / skipped / not_found correctly
    """
    ids: list[str] = []
    for i in range(3):
        body = _create(
            {
                "category": "ADMIN_ERROR",
                "rejection_reason": f"bulk-seed-{i}",
                "call_id": "test-call-bulk",
            },
            auth("zoe"),
        )
        ids.append(body["id"])

    r = client.post(
        "/api/rejections/bulk-transition",
        json={
            "rejection_ids": ids,
            "to_status": "FIXED_AND_APPROVED",
            "notes": "cleared in bulk",
        },
        headers=auth("sarah"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == 3
    assert body["skipped_already_in_state"] == 0
    assert body["not_found"] == 0
    assert set(body["ids_updated"]) == set(ids)

    # Every row now terminal + resolved_at populated; audit row recorded.
    db = TestSessionLocal()
    try:
        rows = db.query(Rejection).all()
        assert {r.status for r in rows} == {"FIXED_AND_APPROVED"}
        assert all(r.resolved_at is not None for r in rows)
        logs = (
            db.query(RejectionAuditLog)
            .filter(RejectionAuditLog.action == "bulk_transitioned")
            .all()
        )
        assert len(logs) == 3
        assert {log.to_status for log in logs} == {"FIXED_AND_APPROVED"}
        assert {log.notes for log in logs} == {"cleared in bulk"}
    finally:
        db.close()


def test_bulk_transition_is_idempotent(mock_jwks, seed_profiles_local, auth):
    """Resending the same payload must report skipped_already_in_state
    instead of double-writing audit rows. Safe to retry on network errors.
    """
    body = _create(
        {"category": "ADMIN_ERROR", "rejection_reason": "idem", "call_id": "c-idem"},
        auth("zoe"),
    )
    rid = body["id"]

    first = client.post(
        "/api/rejections/bulk-transition",
        json={"rejection_ids": [rid], "to_status": "FIXED"},
        headers=auth("sarah"),
    )
    assert first.status_code == 200
    assert first.json()["updated"] == 1

    second = client.post(
        "/api/rejections/bulk-transition",
        json={"rejection_ids": [rid], "to_status": "FIXED"},
        headers=auth("sarah"),
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["updated"] == 0
    assert second_body["skipped_already_in_state"] == 1
    assert second_body["ids_skipped"] == [rid]

    db = TestSessionLocal()
    try:
        logs = (
            db.query(RejectionAuditLog)
            .filter(RejectionAuditLog.action == "bulk_transitioned")
            .all()
        )
        assert len(logs) == 1, "second call must NOT write a second audit row"
    finally:
        db.close()


def test_bulk_transition_reports_not_found_separately(
    mock_jwks, seed_profiles_local, auth
):
    """Mix of real + missing ids: real ones move, missing ones surface
    in ids_not_found. The endpoint must not 404 the whole request."""
    body = _create(
        {"category": "ADMIN_ERROR", "rejection_reason": "mix"}, auth("zoe")
    )
    real_id = body["id"]
    ghost = "00000000-0000-0000-0000-000000000000"

    r = client.post(
        "/api/rejections/bulk-transition",
        json={"rejection_ids": [real_id, ghost], "to_status": "FIXED"},
        headers=auth("sarah"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 1
    assert body["not_found"] == 1
    assert body["ids_not_found"] == [ghost]


def test_bulk_transition_rejects_invalid_status(
    mock_jwks, seed_profiles_local, auth
):
    body = _create(
        {"category": "ADMIN_ERROR", "rejection_reason": "x"}, auth("zoe")
    )
    r = client.post(
        "/api/rejections/bulk-transition",
        json={"rejection_ids": [body["id"]], "to_status": "BOGUS_STATUS"},
        headers=auth("sarah"),
    )
    assert r.status_code == 400


def test_bulk_transition_rejects_empty_list(
    mock_jwks, seed_profiles_local, auth
):
    """min_length=1 on the Pydantic field — empty list must 422 before
    the route body even runs."""
    r = client.post(
        "/api/rejections/bulk-transition",
        json={"rejection_ids": [], "to_status": "FIXED"},
        headers=auth("sarah"),
    )
    assert r.status_code == 422


def test_transition_endpoint_sets_resolved_at_on_terminal(
    mock_jwks, seed_profiles_local, auth
):
    body = _create(
        {"category": "PRICING_ISSUE", "rejection_reason": "x"}, auth("zoe")
    )
    rid = body["id"]
    r = client.post(
        f"/api/rejections/{rid}/transition",
        json={"to_status": "FIXED_AND_APPROVED", "notes": "approved by EON"},
        headers=auth("sarah"),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "FIXED_AND_APPROVED"
    assert out["resolved_at"] is not None


def test_delete_admin_only(mock_jwks, seed_profiles_local, auth):
    body = _create(
        {"category": "ADMIN_ERROR", "rejection_reason": "x"}, auth("zoe")
    )
    rid = body["id"]

    # Non-admin can't delete.
    r = client.delete(f"/api/rejections/{rid}", headers=auth("sarah"))
    assert r.status_code == 403

    # Admin can.
    r = client.delete(f"/api/rejections/{rid}", headers=auth("zoe"))
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    # And it's gone.
    r = client.get(f"/api/rejections/{rid}", headers=auth("sarah"))
    assert r.status_code == 404


# ─── Auto-create on FAIL verdict ────────────────────────────────────────


def _seed_call_with_one_cp() -> str:
    db = TestSessionLocal()
    try:
        cps = [
            {
                "id": "cp_1",
                "name": "Confirm consent",
                "status": "pass",
                "verdict": "pass",
                "confidence": 0.9,
                "rule_id": "MISSING_PRICE",
            }
        ]
        c = Call(
            id="c-auto",
            filename="x.mp3",
            file_path="c-auto/x.mp3",
            duration_seconds=10.0,
            transcript="...",
            detected_supplier="E.ON Next Energy",
            agent_name="Sammie",
            checkpoint_results=json.dumps(cps),
        )
        db.add(c)
        # Sprint A1+ — auto_create_rejection_for_verdict iterates failed
        # CallCheckpoint ORM rows (not the JSON blob), so seed at least
        # one passed=False row keyed to the same call.
        db.add(CallCheckpoint(
            id="aaaaaaaa-1111-1111-1111-000000000001",
            call_id="c-auto",
            rule_text="MISSING_PRICE",
            passed=False,
            excerpt=None,
        ))
        db.commit()
    finally:
        db.close()
    return "c-auto"


def test_auto_create_on_fail_verdict(mock_jwks, seed_profiles_local, auth):
    cid = _seed_call_with_one_cp()
    r = client.post(
        f"/api/calls/{cid}/verdict",
        headers=auth("sarah"),
        json={
            "checkpoint_id": "cp_1",
            "verdict": "FAIL",
            "reasoning": "BGL pulled prices and unit rate exceeded ceiling — needs re-quote",
        },
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["saved"] is True
    assert payload.get("auto_rejection_id"), payload

    # Resulting rejection: hooked to call + supplier + sales-agent + inferred category.
    db = TestSessionLocal()
    try:
        rej = db.query(Rejection).one()
        assert str(rej.id) == payload["auto_rejection_id"]
        assert rej.call_id == cid
        assert rej.category == "PRICING_ISSUE"
        assert rej.supplier == "E.ON Next Energy"
        assert rej.sales_agent == "Sammie"
        assert rej.status == "NOT_STARTED"
        assert rej.deadline is not None
    finally:
        db.close()


def test_no_auto_create_on_pass_verdict(mock_jwks, seed_profiles_local, auth):
    cid = _seed_call_with_one_cp()
    r = client.post(
        f"/api/calls/{cid}/verdict",
        headers=auth("sarah"),
        json={
            "checkpoint_id": "cp_1",
            "verdict": "PASS",
            "reasoning": "all good",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json().get("auto_rejection_id") is None
    db = TestSessionLocal()
    try:
        assert db.query(Rejection).count() == 0
    finally:
        db.close()
