"""C2 (tracker XLSX parity): PATCH /api/tracker/rows/{id} inline-edit endpoint.

Setup mirrors test_ai_category_suggestion.py — in-memory SQLite + StaticPool,
autouse clean_db fixture overrides ``get_db`` so the FastAPI route hits the
same session as the test queries.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Rejection, ReviewerEdit
from app import reviewers as _rev


_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _fake_reviewer():
    return {"id": "rev-test", "email": "rev@test", "role": "admin"}


@pytest.fixture(autouse=True)
def clean_db():
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[_rev.current_reviewer] = _fake_reviewer
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield
    app.dependency_overrides = {}


@pytest.fixture
def evangelical_rejection():
    db = TestSessionLocal()
    rej = Rejection(
        customer_slug="evangelical-church",
        category="COMPLIANCE_ISSUE",
        rejection_reason="...",
        status="NOT_STARTED",
    )
    db.add(rej)
    db.commit()
    db.refresh(rej)
    rej_id = rej.id
    db.close()
    # Return a re-fetched instance so the caller's session can use it.
    return rej_id


def test_patch_tracker_row_flips_source_to_human(evangelical_rejection):
    client = TestClient(app)
    rej_id = str(evangelical_rejection)
    r = client.patch(f"/api/tracker/rows/{rej_id}", json={"category": "PRICING_ISSUE"})
    assert r.status_code == 200, r.text

    db = TestSessionLocal()
    try:
        rej = db.query(Rejection).filter_by(id=evangelical_rejection).first()
        assert rej.category == "PRICING_ISSUE"
        assert rej.field_sources.get("category") == "human"

        audit = db.query(ReviewerEdit).filter_by(rejection_id=str(rej.id)).first()
        assert audit is not None
        assert audit.field == "category"
        assert audit.old_value == "COMPLIANCE_ISSUE"
        assert audit.new_value == "PRICING_ISSUE"
    finally:
        db.close()


def test_patch_tracker_row_rejects_unknown_field(evangelical_rejection):
    client = TestClient(app)
    r = client.patch(f"/api/tracker/rows/{evangelical_rejection}", json={"hax": "y"})
    assert r.status_code == 400


def test_patch_tracker_row_404_on_unknown_id():
    client = TestClient(app)
    r = client.patch(f"/api/tracker/rows/{uuid.uuid4()}", json={"category": "PRICING_ISSUE"})
    assert r.status_code == 404
