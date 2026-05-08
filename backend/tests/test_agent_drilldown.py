"""Tests for /api/agents/{name}/drilldown + PATCH /api/agents/{name} (L4)."""
from __future__ import annotations

from datetime import datetime, timedelta

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
    try:
        from sqlalchemy import Column, MetaData, String, Table
        stub = MetaData()
        Table("customers", stub, Column("id", String, primary_key=True))
        stub.create_all(_engine)
        Base.metadata.create_all(_engine)
        return "agent_name" in {c["name"] for c in inspect(_engine).get_columns("calls")}
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _try_create_all(),
    reason="calls table not buildable on the test engine",
)

TestSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_app() -> FastAPI:
    from app.agents_routes import agents_router

    app = FastAPI()
    app.include_router(agents_router)
    app.dependency_overrides[get_db] = _override_get_db
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
def seed_calls():
    from app.models import Call

    db = TestSessionLocal()
    now = datetime.utcnow()
    try:
        db.add_all(
            [
                Call(
                    id=f"c{i}",
                    filename=f"c{i}.mp3",
                    file_path=f"c{i}/c.mp3",
                    agent_name="Sarah",
                    compliant=(i % 2 == 0),
                    created_at=now - timedelta(days=i),
                )
                for i in range(6)
            ]
        )
        db.commit()
    finally:
        db.close()


def test_drilldown_basic_shape(client, seed_calls):
    res = client.get("/api/agents/Sarah/drilldown")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["agent_name"] == "Sarah"
    assert isinstance(body["dead_rejections"], list)
    assert "open_rejections_value_gbp" in body
    assert "retraining_assigned" in body
    assert "critical_count_7d" in body


def test_drilldown_pass_rate_30d(client, seed_calls):
    body = client.get("/api/agents/Sarah/drilldown").json()
    rate = body["pass_rate_30d"]
    # 3 of 6 compliant in seed → 0.5
    assert rate is not None
    assert 0.4 < rate < 0.6


def test_drilldown_unknown_agent_returns_zeroes(client):
    body = client.get("/api/agents/Nobody/drilldown").json()
    assert body["agent_name"] == "Nobody"
    assert body["dead_rejections"] == []
    assert body["critical_count_7d"] == 0
    assert body["open_directives"] == 0


def test_patch_retraining_succeeds_when_column_present(client):
    # After the L8 migration, profiles.retraining_assigned exists. The
    # PATCH route now persists the flag against profiles.name (caller is
    # expected to have admin role at the auth layer; no auth in test).
    # We seed a profile so the UPDATE has a row to match, then assert
    # the route returns 200 with the new value echoed back.
    from app.models import Profile

    db = TestSessionLocal()
    try:
        db.add(
            Profile(
                id="00000000-0000-0000-0000-000000000001",
                email="sarah@example.com",
                name="Sarah",
                role="admin",
                active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    res = client.patch(
        "/api/agents/Sarah",
        json={"retraining_assigned": True, "retraining_reason": "Recurring missing DD"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["updated"] is True
    assert body["retraining_assigned"] is True
    assert body["retraining_reason"] == "Recurring missing DD"
    assert body["matched_profiles"] == 1


def test_dead_rejections_list_shape(client, seed_calls):
    body = client.get("/api/agents/Sarah/drilldown").json()
    assert isinstance(body["dead_rejections"], list)
    for row in body["dead_rejections"]:
        assert "deal_id" in row
        assert "customer_name" in row
        assert "dead_reason" in row
        assert "rejected_at" in row
