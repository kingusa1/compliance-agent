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
    assert r["outcome_narrative"] == "Full coaching narrative."
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
        # 2026-05-23: the compliant tab now matches /compliant page semantics
        # (Call.compliant === true), not "any completed call with no
        # rejection". A passing call must have compliant=True for the tab to
        # surface it; failing-but-no-rejection calls belong on /non-compliant.
        compliant=True,
        # 2026-05-24: also requires `review_status=='reviewed'` so the same
        # call never appears in BOTH the Awaiting-review AND Compliant tabs
        # at the same time (owner-reported double-count).
        review_status="reviewed",
    )
    # Add a second call with the same shape but compliant=False to lock in
    # the new semantics — it must NOT appear on the compliant tab.
    call_fail = Call(
        id=str(uuid.uuid4()),
        filename="fail.mp3",
        file_path="/tmp/f.mp3",
        deal_id=deal.id,
        agent_name="Jack",
        status="completed",
        score="5/24",
        compliant=False,
    )
    test_db.add_all([cust, deal, call_pass, call_fail])
    test_db.commit()

    rows = build_tracker_rows(test_db, tab="compliant")
    assert len(rows) == 1, "compliant tab must surface ONLY Call.compliant=True"
    r = rows[0]
    assert r["customer_name"] == "Beta Ltd"
    assert r["score"] == "22/24"
    assert r["category"] is None
    assert r["rejection_id"] is None
    assert r["call_id"] == call_pass.id


def test_awaiting_review_deadline_state_filters_calls(test_db):
    """2026-05-18 audit: ``deadline_state`` was a silent no-op on the
    awaiting_review tab because the filter wiring lived only in
    ``_apply_rejection_advanced``. Awaiting-review rows derive a deadline
    from ``Call.completed_at + 2 days``; the matching predicate must now
    narrow the result set across all four states.
    """
    cust = Customer(id=uuid.uuid4(), legal_name="Gamma Ltd", slug="gamma")
    deal = CustomerDeal(
        id=uuid.uuid4(),
        customer_id=cust.id,
        customer_name="Gamma Ltd",
        supplier="E.ON Next",
        status="open",
    )
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    # Three calls with deadline = completed_at + 2 days landing in distinct
    # states relative to "today".
    overdue_call = Call(
        id=str(uuid.uuid4()),
        filename="overdue.mp3",
        file_path="/tmp/o.mp3",
        deal_id=deal.id,
        agent_name="A",
        status="completed",
        # deadline = today - 5d + 2d = today - 3d → overdue
        completed_at=today - timedelta(days=5),
        score="20/26",
    )
    due_soon_call = Call(
        id=str(uuid.uuid4()),
        filename="due_soon.mp3",
        file_path="/tmp/d.mp3",
        deal_id=deal.id,
        agent_name="B",
        status="completed",
        # deadline = today - 1d + 2d = today + 1d → due ≤3d
        completed_at=today - timedelta(days=1),
        score="20/26",
    )
    on_track_call = Call(
        id=str(uuid.uuid4()),
        filename="on_track.mp3",
        file_path="/tmp/t.mp3",
        deal_id=deal.id,
        agent_name="C",
        status="completed",
        # deadline = today + 5d + 2d = today + 7d → still on track
        completed_at=today + timedelta(days=5),
        score="20/26",
    )
    test_db.add_all([cust, deal, overdue_call, due_soon_call, on_track_call])
    test_db.commit()

    # Sanity: no filter → all three rows.
    assert len(build_tracker_rows(test_db, tab="awaiting_review")) == 3

    # Overdue → only the call whose deadline is before today.
    overdue_rows = build_tracker_rows(
        test_db, tab="awaiting_review", deadline_state="overdue"
    )
    assert len(overdue_rows) == 1
    assert overdue_rows[0]["call_id"] == overdue_call.id

    # Due ≤3d → the call whose deadline is today..today+3.
    due_3d_rows = build_tracker_rows(
        test_db, tab="awaiting_review", deadline_state="due_3d"
    )
    due_3d_ids = {r["call_id"] for r in due_3d_rows}
    assert due_soon_call.id in due_3d_ids
    assert overdue_call.id not in due_3d_ids
    assert on_track_call.id not in due_3d_ids

    # On track → only the call whose deadline is past today+3 (or null).
    on_track_rows = build_tracker_rows(
        test_db, tab="awaiting_review", deadline_state="on_track"
    )
    on_track_ids = {r["call_id"] for r in on_track_rows}
    assert on_track_call.id in on_track_ids
    assert overdue_call.id not in on_track_ids


def test_awaiting_review_includes_needs_manual_review_status(test_db):
    """2026-05-25 regression: the Awaiting Review tab filter used to
    match ONLY `Call.status == 'completed'`, but `pipeline.py` also
    emits the terminal state `needs_manual_review` when the AI couldn't
    grade the call cleanly (transcript-divergence, analyzer errors >50%,
    zero segments classified). Those calls are EXACTLY what a reviewer
    needs to see, but they were silently dropping out of every tracker
    tab.

    Reproduction (live-prod 2026-05-25): user uploaded 4 calls, 3 came
    back as `needs_manual_review`, 1 as `completed`. Tracker Awaiting
    Review tab showed 1 row instead of 4. /api/calls returned all 4.

    The fix in `tracker_aggregator.build_tracker_rows` updates the
    filter to `Call.status.in_(('completed', 'needs_manual_review'))`.
    Both states are terminal-but-not-reviewed, which is the population
    the tab is documented to surface.
    """
    cust = Customer(id=uuid.uuid4(), legal_name="Acme Ltd", slug="acme-nmr")
    deal = CustomerDeal(
        id=uuid.uuid4(),
        customer_id=cust.id,
        customer_name="Acme Ltd",
        supplier="E.ON Next",
        status="in_progress",
    )
    completed_call = Call(
        id=str(uuid.uuid4()),
        filename="c1.mp3",
        file_path="/tmp/c1.mp3",
        deal_id=deal.id,
        agent_name="Reviewer A",
        status="completed",
        score="20/24",
        customer_name="Acme Ltd",
        completed_at=datetime.now(UTC),
        review_status="unclaimed",
    )
    nmr_call_1 = Call(
        id=str(uuid.uuid4()),
        filename="c2.mp3",
        file_path="/tmp/c2.mp3",
        deal_id=deal.id,
        agent_name="Reviewer B",
        status="needs_manual_review",
        score="6/22",
        customer_name="Acme Ltd",
        completed_at=datetime.now(UTC),
        review_status="unclaimed",
    )
    nmr_call_2 = Call(
        id=str(uuid.uuid4()),
        filename="c3.mp3",
        file_path="/tmp/c3.mp3",
        deal_id=deal.id,
        agent_name="Reviewer C",
        status="needs_manual_review",
        score="19/25",
        customer_name="Acme Ltd",
        completed_at=datetime.now(UTC),
        review_status="unclaimed",
    )
    in_review_nmr_call = Call(
        id=str(uuid.uuid4()),
        filename="c4.mp3",
        file_path="/tmp/c4.mp3",
        deal_id=deal.id,
        agent_name="Reviewer D",
        status="needs_manual_review",
        score="78/124",
        customer_name="Acme Ltd",
        completed_at=datetime.now(UTC),
        review_status="in_review",  # opened but not yet signed off
    )
    # A call that's truly reviewed must NOT appear — the guard is on
    # `review_status != 'reviewed'` and applies equally to both
    # `completed` and `needs_manual_review`.
    reviewed_call = Call(
        id=str(uuid.uuid4()),
        filename="c5.mp3",
        file_path="/tmp/c5.mp3",
        deal_id=deal.id,
        agent_name="Reviewer E",
        status="needs_manual_review",
        score="10/10",
        customer_name="Acme Ltd",
        completed_at=datetime.now(UTC),
        review_status="reviewed",
    )
    # A still-processing call must NOT appear.
    processing_call = Call(
        id=str(uuid.uuid4()),
        filename="c6.mp3",
        file_path="/tmp/c6.mp3",
        deal_id=deal.id,
        agent_name="Reviewer F",
        status="processing",
        customer_name="Acme Ltd",
        review_status="unclaimed",
    )
    test_db.add_all([
        cust, deal, completed_call, nmr_call_1, nmr_call_2,
        in_review_nmr_call, reviewed_call, processing_call,
    ])
    test_db.commit()

    rows = build_tracker_rows(test_db, tab="awaiting_review")
    surfaced_ids = {r["call_id"] for r in rows}

    # All 4 terminal-but-unreviewed calls show — completed AND nmr.
    assert completed_call.id in surfaced_ids
    assert nmr_call_1.id in surfaced_ids
    assert nmr_call_2.id in surfaced_ids
    assert in_review_nmr_call.id in surfaced_ids
    # The fully-reviewed and still-processing calls stay hidden.
    assert reviewed_call.id not in surfaced_ids
    assert processing_call.id not in surfaced_ids
    assert len(rows) == 4
