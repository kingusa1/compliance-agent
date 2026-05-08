"""verdict_state AI/HUMAN provenance gate.

Covers: factory writes AI_PENDING, /confirm flips to HUMAN_CONFIRMED,
/override flips to HUMAN_OVERRIDDEN + writes audit-log rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, Rejection, RejectionAuditLog
from app.rejection_factory import build_rejection_for_call


# ── In-memory SQLite harness — StaticPool keeps the same connection across
# the test client + helper sessions (matches test_compliance_override.py).
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _bootstrap_schema():
    Base.metadata.create_all(_engine)
    yield
    Base.metadata.drop_all(_engine)


@pytest.fixture
def client():
    def _override():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override

    from app.auth import current_user
    app.dependency_overrides[current_user] = lambda: {"id": "test-reviewer", "email": "rev@example.com", "role": "admin"}

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed_rejection(state: str = "AI_PENDING") -> Rejection:
    s = TestSession()
    call = Call(id=str(uuid.uuid4()), filename="x.mp3", file_path="/tmp/x.mp3")
    s.add(call)
    s.commit()
    r = Rejection(
        id=uuid.uuid4(),
        call_id=call.id,
        category="ADMIN_ERROR",
        rejection_reason="seeded",
        status="NOT_STARTED",
        verdict_state=state,
    )
    s.add(r)
    s.commit()
    rid = r.id
    s.close()
    s2 = TestSession()
    out = s2.query(Rejection).filter(Rejection.id == rid).first()
    s2.close()
    return out


# ── Factory writes AI_PENDING ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_factory_writes_ai_pending_state():
    failing = [{"name": "X", "status": "fail", "evidence": "", "notes": ""}]
    with patch("app.rejection_factory._classify_category", new_callable=AsyncMock) as cls, \
         patch("app.rejection_factory._summarise_reason", new_callable=AsyncMock) as rsn, \
         patch("app.rejection_factory._propose_fix", new_callable=AsyncMock) as fix, \
         patch("app.rejection_factory._propose_narrative", new_callable=AsyncMock) as nar:
        cls.return_value = "ADMIN_ERROR"
        rsn.return_value = "ok"
        fix.return_value = None
        nar.return_value = ""
        out = await build_rejection_for_call(
            call_id="c1", customer_slug=None, supplier=None,
            sales_agent=None, failing_checkpoints=failing,
        )
    assert out["verdict_state"] == "AI_PENDING"


# ── /confirm flips to HUMAN_CONFIRMED ─────────────────────────────────────
def test_confirm_endpoint_flips_state(client):
    r = _seed_rejection()
    resp = client.post(f"/api/rejections/{r.id}/confirm")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict_state"] == "HUMAN_CONFIRMED"
    assert body["confirmed_by"] == "test-reviewer"
    assert body["confirmed_at"] is not None

    # Audit row written.
    s = TestSession()
    rows = s.query(RejectionAuditLog).filter(RejectionAuditLog.rejection_id == r.id).all()
    s.close()
    assert any(a.action == "verdict_confirmed" and a.to_status == "HUMAN_CONFIRMED" for a in rows)


# ── /override flips to HUMAN_OVERRIDDEN + applies field changes ──────────
def test_override_endpoint_flips_state_and_applies_changes(client):
    r = _seed_rejection()
    resp = client.post(
        f"/api/rejections/{r.id}/override",
        json={"category": "PRICING_ERROR", "rejection_reason": "reviewer rewrote this"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict_state"] == "HUMAN_OVERRIDDEN"
    assert body["category"] == "PRICING_ERROR"
    assert body["rejection_reason"] == "reviewer rewrote this"
    assert body["confirmed_by"] == "test-reviewer"

    # Audit row.
    s = TestSession()
    rows = s.query(RejectionAuditLog).filter(RejectionAuditLog.rejection_id == r.id).all()
    s.close()
    assert any(a.action == "verdict_overridden" and a.to_status == "HUMAN_OVERRIDDEN" for a in rows)


# ── Override accepts fix_narrative free-text ─────────────────────────────
def test_override_accepts_fix_narrative(client):
    r = _seed_rejection()
    resp = client.post(
        f"/api/rejections/{r.id}/override",
        json={"fix_narrative": "amendment + confirmation call · resend docusign"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict_state"] == "HUMAN_OVERRIDDEN"
    assert "amendment" in body["fix_narrative"]


# ── Override rejects unknown category enum ───────────────────────────────
def test_override_rejects_invalid_category(client):
    r = _seed_rejection()
    resp = client.post(
        f"/api/rejections/{r.id}/override",
        json={"category": "TOTALLY_BOGUS"},
    )
    assert resp.status_code == 400


# ── 404 on unknown rejection id ──────────────────────────────────────────
def test_confirm_404_for_unknown_id(client):
    resp = client.post(f"/api/rejections/{uuid.uuid4()}/confirm")
    assert resp.status_code == 404
