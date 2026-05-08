"""Sprint C2 (v3-watt-coverage W5) — verify a FAIL verdict back-links
the auto-created Rejection onto the parent Deal AND flips
``Deal.status`` to ``closed_lost``.

Setup mirrors test_ai_rejection_reason.py — in-memory SQLite + StaticPool,
autouse clean_db fixture overrides ``get_db``.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Call, CustomerDeal, Rejection
from app.rejections_routes import auto_create_rejection_for_verdict


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


@pytest.fixture(autouse=True)
def clean_db():
    app.dependency_overrides[get_db] = _override_get_db
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield
    # Pop the override so subsequent test files (e.g. test_deals_stub.py)
    # that read from the real SessionLocal don't transparently get routed
    # to this file's in-memory SQLite.
    app.dependency_overrides.pop(get_db, None)


def _seed(db, *, deal_status: str = "in_progress") -> tuple[Call, CustomerDeal]:
    deal = CustomerDeal(
        id=uuid.uuid4(),
        customer_name="Acme Ltd",
        supplier="E.ON Next",
        status=deal_status,
    )
    call = Call(
        id="c-c2-" + uuid.uuid4().hex[:8],
        filename="t.mp3",
        file_path="t/t.mp3",
        deal_id=deal.id,
        status="completed",
        detected_supplier="E.ON Next",
        agent_name="Sammie",
    )
    db.add_all([deal, call])
    db.commit()
    db.refresh(call)
    db.refresh(deal)
    return call, deal


def test_fail_verdict_flips_deal_status_and_sets_rejection_id():
    """W5/C2 happy path — FAIL verdict triggers auto-create which then
    back-links rejection.id onto deal.rejection_id and flips deal.status
    to ``closed_lost``."""
    db = TestSessionLocal()
    try:
        call, deal = _seed(db)
        rej = auto_create_rejection_for_verdict(
            db,
            call=call,
            actor_id="test-user",
            verdict_action="FAIL",
            reason="missing recording disclosure",
            rule_id="RECORDING_DISCLOSURE",
            checkpoint=None,
        )
        db.commit()
        db.refresh(deal)

        assert rej is not None
        assert deal.status == "closed_lost"
        assert deal.rejection_id == rej.id
    finally:
        db.close()


def test_pass_verdict_does_not_touch_deal_or_create_rejection():
    """Sanity guard — only FAIL/REVIEW verdicts auto-create. PASS leaves
    the deal alone."""
    db = TestSessionLocal()
    try:
        call, deal = _seed(db)
        rej = auto_create_rejection_for_verdict(
            db,
            call=call,
            actor_id="test-user",
            verdict_action="PASS",
            reason="all good",
            rule_id="OK",
            checkpoint=None,
        )
        db.commit()
        db.refresh(deal)

        assert rej is None
        assert deal.status == "in_progress"
        assert deal.rejection_id is None
        assert db.query(Rejection).count() == 0
    finally:
        db.close()


def test_already_terminal_deal_status_is_preserved():
    """If the deal is already ``closed_done`` (won), a stray FAIL on a
    later checkpoint should still create a rejection but must NOT clobber
    the won status. We only flip in_progress/open → closed_lost."""
    db = TestSessionLocal()
    try:
        call, deal = _seed(db, deal_status="closed_done")
        rej = auto_create_rejection_for_verdict(
            db,
            call=call,
            actor_id="test-user",
            verdict_action="FAIL",
            reason="late-arriving compliance issue",
            rule_id="VAT_EXCLUSION",
            checkpoint=None,
        )
        db.commit()
        db.refresh(deal)

        assert rej is not None
        # Status preserved (not flipped to closed_lost).
        assert deal.status == "closed_done"
        # But the back-link still recorded so /customers can surface it.
        assert deal.rejection_id == rej.id
    finally:
        db.close()
