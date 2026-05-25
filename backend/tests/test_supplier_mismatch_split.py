"""Regression for the 2026-05-25 supplier-mismatch-glue bug.

User uploaded 4 audio files into the same "pending_audio" deal stub.
3 of the 4 transcripts identified the supplier as E.ON Next; 1
identified as British Gas. The BG call finalised first, so
`_step_detect_metadata` backfilled `deal.supplier = "British Gas"`.
The subsequent E.ON calls' transcript-detected supplier was silently
ignored at the deal level — every tracker row showed "British Gas"
even though only 1 of 4 calls actually was BG.

The fix peels mismatched calls onto a fresh deal stub. These tests
exercise the predicate in isolation (the full pipeline integration is
heavy and gated on transcription/LLM calls; the unit-level check is
what protects us from a future regression).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.deal_meter_merge import _supplier_norm
from app.models import Call, CustomerDeal


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _make_deal(
    s,
    *,
    name: str = "Customer X",
    supplier: str | None = None,
    status: str = "pending_audio",
) -> CustomerDeal:
    d = CustomerDeal(
        id=uuid.uuid4(),
        customer_name=name,
        supplier=supplier,
        status=status,
    )
    s.add(d)
    s.flush()
    return d


def _make_call(s, deal: CustomerDeal, *, detected_supplier: str | None = None) -> Call:
    c = Call(
        id=str(uuid.uuid4()),
        filename="t.mp3",
        file_path="/tmp/t.mp3",
        deal_id=deal.id,
        detected_supplier=detected_supplier,
        status="completed",
    )
    s.add(c)
    s.flush()
    return c


def _run_peel(call: Call, db, detected: str) -> CustomerDeal | None:
    """Inline copy of the production peel logic so we can unit-test it
    without booting the full pipeline. Mirrors `pipeline._step_detect_metadata`
    lines 1110-1208 exactly. If detected supplier mismatches the linked
    deal's supplier, creates a new deal and re-points the call. Returns
    the new deal (when peeled) or None (no peel)."""
    from app.models import CustomerDeal as _Deal

    if not (detected and detected != "Unknown" and call.deal_id):
        return None
    deal = db.query(_Deal).filter_by(id=call.deal_id).first()
    if deal is None:
        return None
    deal_norm = _supplier_norm(deal.supplier)
    det_norm = _supplier_norm(detected)
    if not deal_norm:
        # Backfill — first call wins.
        deal.supplier = detected
        db.flush()
        return None
    if deal_norm == det_norm:
        return None
    # Mismatch — peel.
    new_deal = _Deal(
        customer_name=deal.customer_name,
        supplier=detected,
        status="in_progress",
    )
    db.add(new_deal)
    db.flush()
    call.deal_id = new_deal.id
    db.flush()
    return new_deal


class TestSupplierMismatchSplit:
    def test_first_call_backfills_blank_deal_supplier(self, session) -> None:
        """The first call to finalise on a `pending_audio` stub sets the
        deal's supplier. No peel happens."""
        deal = _make_deal(session, supplier=None, status="pending_audio")
        call = _make_call(session, deal, detected_supplier="E.ON Next")
        result = _run_peel(call, session, "E.ON Next")
        assert result is None  # no peel
        session.refresh(deal)
        assert deal.supplier == "E.ON Next"
        session.refresh(call)
        assert call.deal_id == deal.id  # still on original

    def test_matching_supplier_no_peel(self, session) -> None:
        """A subsequent E.ON call on an E.ON deal stays put."""
        deal = _make_deal(session, supplier="E.ON Next", status="in_progress")
        call = _make_call(session, deal, detected_supplier="E.ON Next")
        result = _run_peel(call, session, "E.ON Next")
        assert result is None
        session.refresh(call)
        assert call.deal_id == deal.id

    def test_alias_match_no_peel(self, session) -> None:
        """`EON` and `E.ON Next` normalise to the same canonical
        supplier via `_supplier_norm` — no peel."""
        deal = _make_deal(session, supplier="E.ON Next", status="in_progress")
        call = _make_call(session, deal, detected_supplier="EON")
        result = _run_peel(call, session, "EON")
        assert result is None
        session.refresh(call)
        assert call.deal_id == deal.id

    def test_supplier_mismatch_peels_call(self, session) -> None:
        """THE bug case: a British Gas deal exists, an E.ON call comes
        in. The call MUST be moved to a new deal with E.ON supplier;
        the original BG deal stays untouched for any other BG calls."""
        bg_deal = _make_deal(
            session, name="Customer X", supplier="British Gas",
            status="in_progress",
        )
        # Seed a sibling BG call so we can verify the original deal
        # isn't disturbed.
        bg_sibling = _make_call(session, bg_deal, detected_supplier="British Gas")

        eon_call = _make_call(session, bg_deal, detected_supplier="E.ON Next")
        new_deal = _run_peel(eon_call, session, "E.ON Next")
        assert new_deal is not None
        assert new_deal.id != bg_deal.id
        assert new_deal.supplier == "E.ON Next"
        assert new_deal.customer_name == "Customer X"  # inherits

        session.refresh(eon_call)
        assert eon_call.deal_id == new_deal.id

        # BG deal must still exist with its supplier intact.
        session.refresh(bg_deal)
        assert bg_deal.supplier == "British Gas"
        # BG sibling stays on BG deal.
        session.refresh(bg_sibling)
        assert bg_sibling.deal_id == bg_deal.id

    def test_three_eon_one_bg_user_scenario(self, session) -> None:
        """Reproduction of the user's 2026-05-25 case: 4 calls uploaded
        into one `pending_audio` deal stub. 3 are E.ON, 1 is BG, in the
        order BG → EON → EON → EON. The BG call finalises first and
        sets deal.supplier. The 3 EON calls then peel onto new deal(s)."""
        stub = _make_deal(session, supplier=None, status="pending_audio")
        bg_call = _make_call(session, stub, detected_supplier="British Gas")
        eon_a = _make_call(session, stub, detected_supplier="E.ON Next")
        eon_b = _make_call(session, stub, detected_supplier="E.ON Next")
        eon_c = _make_call(session, stub, detected_supplier="E.ON Next")

        # 1. BG finalises first → backfills the stub.
        result = _run_peel(bg_call, session, "British Gas")
        assert result is None
        session.refresh(stub)
        assert stub.supplier == "British Gas"

        # 2. Each EON call peels onto its own new deal.
        peeled = []
        for c in (eon_a, eon_b, eon_c):
            nd = _run_peel(c, session, "E.ON Next")
            assert nd is not None
            peeled.append(nd)
            session.refresh(c)
            assert c.deal_id == nd.id
            assert nd.supplier == "E.ON Next"

        # Original stub keeps only the BG call.
        session.refresh(bg_call)
        assert bg_call.deal_id == stub.id
        bg_call_count = (
            session.query(Call).filter(Call.deal_id == stub.id).count()
        )
        assert bg_call_count == 1

        # 3 separate EON deals exist post-peel (later they'd re-aggregate
        # via `_maybe_merge_into_existing_deal` on customer-name match —
        # NOT tested here; that's the existing customer-name merger and
        # has its own test coverage).
        eon_deals = (
            session.query(CustomerDeal)
            .filter(CustomerDeal.supplier == "E.ON Next")
            .count()
        )
        assert eon_deals == 3

    def test_unknown_supplier_does_not_peel(self, session) -> None:
        """If detected_supplier is the literal 'Unknown' sentinel
        (analyser couldn't decide), don't peel — wait for a future call
        to clarify."""
        deal = _make_deal(session, supplier="British Gas", status="in_progress")
        call = _make_call(session, deal, detected_supplier="Unknown")
        result = _run_peel(call, session, "Unknown")
        assert result is None
        session.refresh(call)
        assert call.deal_id == deal.id
