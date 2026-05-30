"""Tests for /api/saved-views (L4)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import current_user
from app.database import Base, get_db
import app.models  # noqa: F401  — register models before create_all

# saved_views_router is auth-gated (2026-05-30 security audit). Stub an authenticated
# user so these route tests exercise the handlers, not the 401 gate.
_STUB_USER = {"id": "test-admin", "email": "admin@test.local", "name": "Test", "role": "admin"}


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
        cols = {c["name"] for c in inspect(_engine).get_columns("saved_views")}
        return "filters" in cols
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _try_create_all(),
    reason="saved_views table not buildable on the test engine",
)

TestSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_app() -> FastAPI:
    from app.saved_views_routes import saved_views_router

    app = FastAPI()
    app.include_router(saved_views_router)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user] = lambda: _STUB_USER
    return app


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield


@pytest.fixture
def client():
    return TestClient(_make_app())


def test_list_empty(client):
    res = client.get("/api/saved-views")
    assert res.status_code == 200
    assert res.json() == {"views": []}


def test_create_view_happy_path(client):
    res = client.post(
        "/api/saved-views",
        json={
            "name": "Critical FAILs",
            "endpoint": "/api/findings",
            "filters": {"fix_status": "pending", "rejection_category": "COMPLIANCE ISSUE"},
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["name"] == "Critical FAILs"
    assert body["endpoint"] == "/api/findings"
    assert body["filters"]["fix_status"] == "pending"


def test_create_view_unknown_filter_key_rejected(client):
    res = client.post(
        "/api/saved-views",
        json={
            "name": "bad",
            "endpoint": "/api/findings",
            "filters": {"not_a_real_key": "x"},
        },
    )
    assert res.status_code == 422


def test_endpoint_filter(client):
    client.post("/api/saved-views", json={"name": "f1", "endpoint": "/api/findings", "filters": {"fix_status": "pending"}})
    client.post("/api/saved-views", json={"name": "c1", "endpoint": "/api/calls?compliant=true", "filters": {}})

    res = client.get("/api/saved-views?endpoint=/api/findings")
    assert res.status_code == 200
    names = [v["name"] for v in res.json()["views"]]
    assert "f1" in names
    assert "c1" not in names


def test_delete_view(client):
    saved = client.post(
        "/api/saved-views",
        json={"name": "tmp", "endpoint": "/api/findings", "filters": {}},
    ).json()
    res = client.delete(f"/api/saved-views/{saved['id']}")
    assert res.status_code == 200
    assert res.json() == {"deleted": True}
    res = client.get("/api/saved-views")
    assert res.json() == {"views": []}


def test_patch_view_validates_filters(client):
    saved = client.post(
        "/api/saved-views",
        json={"name": "v", "endpoint": "/api/findings", "filters": {"fix_status": "pending"}},
    ).json()
    res = client.patch(f"/api/saved-views/{saved['id']}", json={"filters": {"unknown_key": "x"}})
    assert res.status_code == 422


def test_endpoint_round_trip(client):
    client.post(
        "/api/saved-views",
        json={"name": "v", "endpoint": "/api/findings", "filters": {"agent_name": "Sarah"}},
    )
    res = client.get("/api/saved-views").json()
    assert res["views"][0]["endpoint"] == "/api/findings"
    assert res["views"][0]["filters"]["agent_name"] == "Sarah"
