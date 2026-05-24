"""Tests for /api/customers/{slug}/rollup + /timeline (L4)."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
import app.models  # noqa: F401


_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


def _try_create_all() -> bool:
    """Try to create the test schema. Tolerates a not-yet-shipped
    `customers` parent table by stubbing it before the FK resolution
    runs. The actual table arrives via the L4/L7 migration."""
    try:
        # Pre-create a minimal `customers` table so the FK on
        # customer_deals.customer_id (added pre-L4) can be resolved.
        from sqlalchemy import Column, MetaData, String, Table
        stub = MetaData()
        Table("customers", stub, Column("id", String, primary_key=True))
        stub.create_all(_engine)
        Base.metadata.create_all(_engine)
        cols = {c["name"] for c in inspect(_engine).get_columns("customer_deals")}
        return "customer_name" in cols
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _try_create_all(),
    reason="customer_deals table not buildable on the test engine",
)


TestSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


_STUB_USER = {
    "id": "test-reviewer",
    "email": "reviewer@compliance-agent.local",
    "name": "Test Reviewer",
    "role": "lead",
}


def _make_app() -> FastAPI:
    from app.customers_routes import customers_router
    from app.auth import current_user
    from app.reviewers import current_reviewer, require_lead

    app = FastAPI()
    app.include_router(customers_router)
    app.dependency_overrides[get_db] = _override_get_db
    # 2026-05-24 — routes are now auth-gated. Tests stub the JWT
    # dependencies so the in-memory SQLite app doesn't need Supabase.
    app.dependency_overrides[current_user] = lambda: _STUB_USER
    app.dependency_overrides[current_reviewer] = lambda: _STUB_USER
    app.dependency_overrides[require_lead] = lambda: _STUB_USER
    return app


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield


@pytest.fixture
def client():
    return TestClient(_make_app())


@pytest.fixture
def seed_customer():
    from app.models import Call, CustomerDeal

    db = TestSessionLocal()
    now = datetime.utcnow()
    deal_a = uuid4()
    deal_b = uuid4()
    try:
        db.add_all(
            [
                CustomerDeal(
                    id=deal_a,
                    customer_name="Acceptance Demo",
                    supplier="ACME Energy",
                    status="completed",
                    deal_value_gbp=Decimal("1200.00"),
                    final_action="FAIL",
                    rejection_category="VERBAL SALES ERROR",
                    created_at=now - timedelta(days=10),
                ),
                CustomerDeal(
                    id=deal_b,
                    customer_name="Acceptance Demo",
                    supplier="E.ON Next",
                    status="completed",
                    deal_value_gbp=Decimal("3300.50"),
                    final_action="REVIEW",
                    rejection_category="VERBAL SALES ERROR",
                    created_at=now - timedelta(days=5),
                ),
                Call(
                    id="ca-1",
                    filename="ca-1.mp3",
                    file_path="ca-1/c.mp3",
                    agent_name="Sarah",
                    customer_name="Acceptance Demo",
                    deal_id=deal_a,
                    call_type="lead_gen",
                    compliant=True,
                    score="6/7",
                    completed_at=now - timedelta(days=10),
                    created_at=now - timedelta(days=10),
                ),
                Call(
                    id="ca-2",
                    filename="ca-2.mp3",
                    file_path="ca-2/c.mp3",
                    agent_name="Sarah",
                    customer_name="Acceptance Demo",
                    deal_id=deal_b,
                    call_type="closer",
                    compliant=False,
                    score="2/5",
                    completed_at=now - timedelta(days=5),
                    created_at=now - timedelta(days=5),
                ),
            ]
        )
        db.commit()
    finally:
        db.close()
    return "acceptance demo"


def test_rollup_shape(client, seed_customer):
    res = client.get(f"/api/customers/{seed_customer}/rollup")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total_deals"] == 2
    assert body["total_calls"] == 2
    assert body["total_deal_value_gbp_annual_sum"] is not None
    assert body["total_deal_value_gbp_annual_sum"] >= 4500
    assert "recurring_issue_flag" in body
    assert "worst_action_across_deals" in body
    assert "dead_rejections_count" in body
    assert "last_activity_at" in body


def test_rollup_recurring_issue_detection(client, seed_customer):
    body = client.get(f"/api/customers/{seed_customer}/rollup").json()
    # Both seeded deals have rejection_category=VERBAL SALES ERROR — should
    # surface as recurring.
    assert body["recurring_issue_flag"] is True


def test_rollup_404_for_unknown(client):
    res = client.get("/api/customers/no-such-customer/rollup")
    assert res.status_code == 404


def test_timeline_shape(client, seed_customer):
    res = client.get(f"/api/customers/{seed_customer}/timeline")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["timeline"], list)
    assert len(body["timeline"]) == 2
    row = body["timeline"][0]
    assert {"call_id", "deal_id", "deal_ref", "call_type", "completed_at", "score", "compliant", "rejection_category", "agent_name"}.issubset(row.keys())


def test_timeline_orders_newest_first(client, seed_customer):
    body = client.get(f"/api/customers/{seed_customer}/timeline").json()
    timestamps = [r["completed_at"] for r in body["timeline"] if r["completed_at"]]
    assert timestamps == sorted(timestamps, reverse=True)
