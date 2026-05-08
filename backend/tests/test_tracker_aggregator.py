"""Tracker aggregator returns rows shaped to mirror Watt's XLSX cols A-Q.

Each Rejection becomes a row; passing calls (no rejection) become rows on
the Compliant tab. Same shape both ways — empty cols use ``None``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, UTC

from app.tracker_aggregator import build_tracker_rows
from app.models import Call, Customer, CustomerDeal, Rejection


def test_active_tab_returns_rejection_row(test_db):
    cust = Customer(id=uuid.uuid4(), legal_name="Acme Ltd", slug="acme")
    deal = CustomerDeal(
        id=uuid.uuid4(),
        customer_id=cust.id,
        customer_name="Acme Ltd",
        supplier="E.ON Next",
        expected_live_date=datetime(2026, 4, 30),
        deal_value_gbp=42000,
        mpan_or_mprn="1234567890",
        status="closed_lost",
    )
    call = Call(
        id=str(uuid.uuid4()),
        filename="t.mp3",
        file_path="/tmp/t.mp3",
        deal_id=deal.id,
        agent_name="Sammy",
        status="completed",
        score="20/24",
    )
    rej = Rejection(
        id=uuid.uuid4(),
        call_id=call.id,
        customer_slug="acme",
        supplier="E.ON Next",
        sales_agent="Sammy",
        category="VERBAL_SALES_ERROR",
        rejection_reason="Agent missed disclosure",
        outcome_narrative="Full coaching narrative.",
        fix_required="AMENDMENT_CALL",
        status="NOT_STARTED",
        rejected_at=datetime.now(UTC),
        deadline=datetime.now(UTC) + timedelta(days=2),
    )
    test_db.add_all([cust, deal, call, rej])
    test_db.commit()
    deal.rejection_id = rej.id
    test_db.commit()

    rows = build_tracker_rows(test_db, tab="active")
    assert len(rows) == 1
    r = rows[0]
    # Watt XLSX cols A-Q, in order:
    assert r["customer_name"] == "Acme Ltd"
    assert r["mpan_mprn"] == "1234567890"
    assert r["expected_live_date"] is not None
    assert r["deal_value_gbp"] == 42000
    assert r["supplier"] == "E.ON Next"
    assert r["rejected_at"] is not None
    assert r["sales_agent"] == "Sammy"
    assert r["rejection_reason"] == "Agent missed disclosure"
    assert r["category"] == "VERBAL_SALES_ERROR"
    assert r["fix_required"] == "AMENDMENT_CALL"
    assert r["fix_assignee_id"] is None
    assert r["status"] == "NOT_STARTED"
    assert r["last_action_date"] is None
    assert r["deadline"] is not None
    assert r["outcome"] is None
    assert r["notes"] == "Full coaching narrative."
    assert r["call_id"] == call.id
    assert r["rejection_id"] == str(rej.id)
    assert r["deal_id"] == str(deal.id)


def test_compliant_tab_returns_passing_calls(test_db):
    cust = Customer(id=uuid.uuid4(), legal_name="Beta Ltd", slug="beta")
    deal = CustomerDeal(
        id=uuid.uuid4(),
        customer_id=cust.id,
        customer_name="Beta Ltd",
        supplier="British Gas Lite",
        status="closed_done",
    )
    call_pass = Call(
        id=str(uuid.uuid4()),
        filename="pass.mp3",
        file_path="/tmp/p.mp3",
        deal_id=deal.id,
        agent_name="Jack",
        status="completed",
        score="22/24",
    )
    test_db.add_all([cust, deal, call_pass])
    test_db.commit()

    rows = build_tracker_rows(test_db, tab="compliant")
    assert len(rows) == 1
    r = rows[0]
    assert r["customer_name"] == "Beta Ltd"
    assert r["score"] == "22/24"
    assert r["category"] is None
    assert r["rejection_id"] is None
    assert r["call_id"] == call_pass.id
