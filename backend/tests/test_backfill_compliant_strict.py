"""POST /api/admin/backfill-compliant-strict endpoint contract.

2026-05-24 — the pipeline was tightened so only the strict ``pass`` bucket
flips ``Call.compliant=True``. Existing DB rows that were graded under the
prior (``pass`` OR ``coaching``) rule still carry the lax value. This file
locks in the contract of the one-shot remediation endpoint:

- coaching-worst call: compliant True→False, compliance_status "compliant"→"pending"
- blocked-worst call:  compliant True→False, compliance_status "compliant"→"non_compliant"
- review-worst call:   already compliant=False from pipeline, untouched
- pure-pass call:      compliant=True stays True
- second invocation:   flips zero rows (idempotent)
- auth gate:           returns 401 without require_lead override

The CI ``coverage`` workflow runs the full pytest suite, so this file's
failures gate merge to main.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import current_user, require_lead
from app.database import Base, get_db
from app.main import app
from app.models import Call, CallSegment, Profile
from app.reviewers import current_reviewer


# Private in-memory engine per test module — keeps this file's rows
# isolated from any other test module's get_db override. StaticPool +
# `:memory:` shares a single connection across the engine so every
# session sees the same tables (default in-memory SQLite gives each
# connection its own empty database).
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

_STUB_LEAD = {
    "id": "test-lead",
    "email": "lead@compliance-agent.local",
    "name": "Test Lead",
    "role": "lead",
}


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _setup_app():
    Base.metadata.create_all(bind=_engine)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user] = lambda: _STUB_LEAD
    app.dependency_overrides[current_reviewer] = lambda: _STUB_LEAD
    app.dependency_overrides[require_lead] = lambda: _STUB_LEAD

    # Seed the lead profile so the audit FK is satisfied if/when one fires.
    db = TestingSessionLocal()
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

    # Teardown — pop everything so other test modules see a clean slate.
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_reviewer, None)
    app.dependency_overrides.pop(require_lead, None)
    Base.metadata.drop_all(bind=_engine)


def _client() -> TestClient:
    return TestClient(app)


def _new_call(db, *, compliant: bool, status: str) -> str:
    cid = f"call-{uuid.uuid4().hex[:8]}"
    db.add(Call(
        id=cid,
        filename=f"{cid}.wav",
        file_path=f"/tmp/{cid}.wav",  # nullable=False on Call
        compliant=compliant,
        compliance_status=status,
    ))
    return cid


def _new_segment(db, *, call_id: str, bucket: str, idx: int = 0) -> None:
    db.add(CallSegment(
        call_id=call_id,
        idx=idx,
        stage="lead_gen",
        bucket=bucket,
    ))


def test_coaching_call_flips_to_pending():
    db = TestingSessionLocal()
    cid = _new_call(db, compliant=True, status="compliant")
    _new_segment(db, call_id=cid, bucket="coaching")
    db.commit()

    r = _client().post("/api/admin/backfill-compliant-strict")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["flipped"] == 1
    assert body["to_pending"] == 1
    assert body["to_non_compliant"] == 0

    db.expire_all()
    call = db.query(Call).filter_by(id=cid).one()
    assert call.compliant is False
    assert call.compliance_status == "pending"
    db.close()


def test_blocked_call_flips_to_non_compliant():
    db = TestingSessionLocal()
    cid = _new_call(db, compliant=True, status="compliant")
    _new_segment(db, call_id=cid, bucket="blocked")
    db.commit()

    r = _client().post("/api/admin/backfill-compliant-strict")
    assert r.status_code == 200
    body = r.json()
    assert body["flipped"] == 1
    assert body["to_non_compliant"] == 1
    assert body["to_pending"] == 0

    db.expire_all()
    call = db.query(Call).filter_by(id=cid).one()
    assert call.compliant is False
    assert call.compliance_status == "non_compliant"
    db.close()


def test_pure_pass_call_is_untouched():
    db = TestingSessionLocal()
    cid = _new_call(db, compliant=True, status="compliant")
    _new_segment(db, call_id=cid, bucket="pass")
    db.commit()

    r = _client().post("/api/admin/backfill-compliant-strict")
    assert r.status_code == 200
    assert r.json()["flipped"] == 0

    db.expire_all()
    call = db.query(Call).filter_by(id=cid).one()
    assert call.compliant is True
    assert call.compliance_status == "compliant"
    db.close()


def test_review_worst_call_with_lax_compliant_flips():
    """Defensive: if any historical row somehow has compliant=True AND a
    review-worst segment (shouldn't happen post-pipeline-fix, but guard
    against drift), it must flip to pending."""
    db = TestingSessionLocal()
    cid = _new_call(db, compliant=True, status="compliant")
    _new_segment(db, call_id=cid, bucket="review")
    db.commit()

    r = _client().post("/api/admin/backfill-compliant-strict")
    assert r.json()["flipped"] == 1
    assert r.json()["to_pending"] == 1

    db.expire_all()
    call = db.query(Call).filter_by(id=cid).one()
    assert call.compliant is False
    assert call.compliance_status == "pending"
    db.close()


def test_worst_bucket_wins_when_call_has_mixed_segments():
    """A call with [pass, coaching, blocked] segments must be treated as
    blocked-worst → compliance_status='non_compliant'."""
    db = TestingSessionLocal()
    cid = _new_call(db, compliant=True, status="compliant")
    _new_segment(db, call_id=cid, bucket="pass", idx=0)
    _new_segment(db, call_id=cid, bucket="coaching", idx=1)
    _new_segment(db, call_id=cid, bucket="blocked", idx=2)
    db.commit()

    r = _client().post("/api/admin/backfill-compliant-strict")
    assert r.json()["to_non_compliant"] == 1

    db.expire_all()
    call = db.query(Call).filter_by(id=cid).one()
    assert call.compliance_status == "non_compliant"
    db.close()


def test_endpoint_is_idempotent():
    db = TestingSessionLocal()
    cid = _new_call(db, compliant=True, status="compliant")
    _new_segment(db, call_id=cid, bucket="coaching")
    db.commit()

    first = _client().post("/api/admin/backfill-compliant-strict").json()
    second = _client().post("/api/admin/backfill-compliant-strict").json()

    assert first["flipped"] == 1
    assert second["flipped"] == 0
    db.close()


def test_endpoint_requires_lead_auth():
    """Pop the require_lead override and confirm 401."""
    app.dependency_overrides.pop(require_lead, None)
    app.dependency_overrides.pop(current_user, None)

    r = TestClient(app).post("/api/admin/backfill-compliant-strict")
    assert r.status_code in (401, 403), f"expected auth gate, got {r.status_code}"
