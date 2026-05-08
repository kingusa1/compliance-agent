"""Composite verdict = weighted average of all calls in the deal.

Sprint Task B — verifies the pure function in app.deals_composite
matches the per-Deal mental model (a single bad verbal call drives the
worst_action even if other calls scored fine).
"""
import uuid

from app.deals_composite import compute_composite_verdict
from app.models import Call, CustomerDeal


def test_composite_weighted_average(test_db):
    deal = CustomerDeal(
        id=uuid.uuid4(),
        customer_name="Composite Tester",
        supplier="E.ON Next",
        status="in_progress",
    )
    test_db.add(deal)
    test_db.commit()
    # Three calls with different scores + weights:
    #   lead_gen 75% × weight 20  = 1500
    #   qualification 87% × 30   ≈ no — call_type mapped to lead_gen here
    # Use the spec's verbatim setup: two lead_gen + one closer.
    test_db.add_all([
        Call(
            id=str(uuid.uuid4()),
            filename="intro.mp3",
            file_path="/tmp/intro.mp3",
            deal_id=deal.id,
            status="completed",
            score="75/100",
            call_type="lead_gen",
        ),
        Call(
            id=str(uuid.uuid4()),
            filename="qual.mp3",
            file_path="/tmp/qual.mp3",
            deal_id=deal.id,
            status="completed",
            score="87/100",
            call_type="lead_gen",
        ),
        Call(
            id=str(uuid.uuid4()),
            filename="verbal.mp3",
            file_path="/tmp/verbal.mp3",
            deal_id=deal.id,
            status="completed",
            score="88/100",
            call_type="closer",
        ),
    ])
    test_db.commit()

    res = compute_composite_verdict(deal.id, test_db)
    assert res["calls_scored"] == 3
    # weighted-avg = (75*20 + 87*20 + 88*50) / 90 = 7640/90 ≈ 84.9%
    assert 80 <= res["composite_pct"] <= 90
    assert res["threshold_met"] is True  # ≥80% threshold
    # 75/100 trips REVIEW (<80) but no call <70 so not FAIL
    assert res["worst_action"] == "REVIEW"
    assert all(c["score"] is not None for c in res["per_call"])


def test_composite_fail_when_any_call_below_70(test_db):
    """One call at 50/100 → composite may still ≥80, but worst_action=FAIL."""
    deal = CustomerDeal(
        id=uuid.uuid4(),
        customer_name="Composite Fail Tester",
        supplier="E.ON Next",
        status="in_progress",
    )
    test_db.add(deal)
    test_db.commit()
    test_db.add_all([
        Call(
            id=str(uuid.uuid4()),
            filename="intro.mp3",
            file_path="/tmp/intro.mp3",
            deal_id=deal.id,
            status="completed",
            score="95/100",
            call_type="lead_gen",
        ),
        Call(
            id=str(uuid.uuid4()),
            filename="verbal.mp3",
            file_path="/tmp/verbal.mp3",
            deal_id=deal.id,
            status="completed",
            score="50/100",
            call_type="closer",
        ),
    ])
    test_db.commit()

    res = compute_composite_verdict(deal.id, test_db)
    assert res["calls_scored"] == 2
    assert res["worst_action"] == "FAIL"  # one call <70 trips FAIL
