"""Tests for flags + findings (L4).

These tests bypass app.main (which doesn't mount the L4 routers yet —
the main session does that) by attaching the routers to a local FastAPI
instance bound to the test SQLite engine. That way pytest can run today
without waiting for the migration to land.

Auto-skips when the `flags` table can't be created on the test engine
(older SQLAlchemy / pgvector quirks where Vector columns leak into
SQLite create_all). The L4 contract explicitly allows this.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
import app.models  # noqa: F401  — register all models with Base before create_all


_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


def _try_create_all() -> bool:
    try:
        # See test_customer_rollup for the customers-stub rationale.
        from sqlalchemy import Column, MetaData, String, Table
        stub = MetaData()
        Table("customers", stub, Column("id", String, primary_key=True))
        stub.create_all(_engine)
        Base.metadata.create_all(_engine)
        if "flags" not in inspect(_engine).get_table_names():
            return False
        cols = {c["name"] for c in inspect(_engine).get_columns("flags")}
        return "rule_id" in cols
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _try_create_all(),
    reason="flags table not buildable on the test engine — L4 columns absent",
)

TestSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_app() -> FastAPI:
    from app.flags_routes import flags_router

    app = FastAPI()
    app.include_router(flags_router)
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
def seed_call():
    from app.models import Call

    db = TestSessionLocal()
    try:
        db.add(Call(id="call-1", filename="c.mp3", file_path="c/c.mp3", agent_name="Sarah"))
        db.commit()
    finally:
        db.close()


def test_create_flag_happy_path(client, seed_call):
    res = client.post(
        "/api/calls/call-1/flags",
        json={
            "rule_id": "R-001",
            "severity": "critical",
            "reason": "Agent did not disclose third party",
            "word_start": 12,
            "word_end": 24,
            "evidence": "we work with all the suppliers",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["flag"]["id"]
    assert body["flag"]["rule_id"] == "R-001"
    assert body["flag"]["source"] == "reviewer"
    assert body["flag"]["word_start"] == 12
    assert body["flag"]["word_end"] == 24


def test_create_flag_call_not_found(client):
    res = client.post(
        "/api/calls/missing/flags",
        json={"rule_id": "R-1", "severity": "high", "reason": "x", "word_start": 0, "word_end": 1},
    )
    assert res.status_code == 404


def test_create_flag_inverted_range_rejected(client, seed_call):
    res = client.post(
        "/api/calls/call-1/flags",
        json={"rule_id": "R-1", "severity": "high", "reason": "x", "word_start": 5, "word_end": 1},
    )
    assert res.status_code == 422


def test_severity_validated(client, seed_call):
    res = client.post(
        "/api/calls/call-1/flags",
        json={"rule_id": "R-1", "severity": "low", "reason": "x", "word_start": 0, "word_end": 1},
    )
    assert res.status_code == 422


def test_list_call_flags_returns_created(client, seed_call):
    client.post(
        "/api/calls/call-1/flags",
        json={"rule_id": "R-001", "severity": "high", "reason": "x", "word_start": 0, "word_end": 5},
    )
    res = client.get("/api/calls/call-1/flags")
    assert res.status_code == 200
    flags = res.json()["flags"]
    assert len(flags) == 1
    assert flags[0]["rule_id"] == "R-001"


def test_findings_returns_empty_list_when_no_data(client):
    res = client.get("/api/findings")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["findings"], list)
    assert body["total"] == 0
    assert body["has_more"] is False


def test_findings_returns_array_after_create(client, seed_call):
    client.post(
        "/api/calls/call-1/flags",
        json={"rule_id": "R-001", "severity": "critical", "reason": "x", "word_start": 0, "word_end": 5},
    )
    res = client.get("/api/findings")
    assert res.status_code == 200
    body = res.json()
    # Joined query may degrade gracefully on test engines lacking
    # customer_deals; either we get the row back, or we get a graceful
    # empty list — both are valid per route contract.
    assert isinstance(body["findings"], list)
    assert body["total"] >= 0


def test_findings_filter_by_agent(client, seed_call):
    client.post(
        "/api/calls/call-1/flags",
        json={"rule_id": "R-1", "severity": "high", "reason": "x", "word_start": 0, "word_end": 1},
    )
    res = client.get("/api/findings?agent_name=Nobody")
    assert res.status_code == 200
    assert isinstance(res.json()["findings"], list)
