"""Tests for `_step_detect_metadata` stub-merge logic (Task A3).

When an upload arrives via the auto-detect path the API creates a stub
``CustomerDeal`` named ``(auto-detect pending <call_id_prefix>)`` and a
matching ``Call`` row pointing at it. After transcription, the pipeline's
detect step should:

  1. Run ``detect_business_name`` on the transcript.
  2. ``fuzzy_match_customer`` against ``Customer.legal_name``.
  3. If a match scores ≥ threshold and the matched customer has an open
     deal, re-attach the call to that deal and delete the orphaned stub
     (only if no other call still points at it).

This test wires the same ``db_session`` alias the A2 test uses so the
plan's spec-style fixture name still works.
"""

import pytest
from unittest.mock import AsyncMock, patch

from sqlalchemy.orm import Session

from app.models import Call, Customer, CustomerDeal


@pytest.fixture
def db_session(test_db) -> Session:
    return test_db


@pytest.mark.asyncio
async def test_step_detect_metadata_merges_stub_into_existing_customer(db_session):
    cust = Customer(legal_name="Evangelical Church", slug="evangelical-church")
    db_session.add(cust)
    db_session.flush()
    existing_deal = CustomerDeal(
        customer_id=cust.id,
        customer_name="Evangelical Church",
        supplier="E.ON Next",
        status="in_progress",
    )
    db_session.add(existing_deal)
    db_session.flush()

    stub = CustomerDeal(customer_name="(auto-detect pending abc12345)", status="in_progress")
    db_session.add(stub)
    db_session.flush()

    call = Call(
        id="abc12345-0000-0000-0000-000000000000",
        filename="church.mp3",
        file_path="/tmp/church.mp3",
        deal_id=stub.id,
        word_data="[]",
        status="processing",
    )
    db_session.add(call)
    db_session.commit()

    transcript_data = {"transcript": "Hi am I speaking with Evangelical Church"}

    with patch("app.pipeline.detect_supplier", new_callable=AsyncMock) as ds, \
         patch("app.pipeline.detect_names", new_callable=AsyncMock) as dn, \
         patch("app.pipeline.detect_business_name", new_callable=AsyncMock) as dbn:
        ds.return_value = "E.ON Next"
        dn.return_value = ("Afaq", "Christopher")
        dbn.return_value = "Evangelical Church"
        from app.pipeline import _step_detect_metadata
        await _step_detect_metadata(str(call.id), transcript_data, db_session, None)

    db_session.refresh(call)
    assert call.deal_id == existing_deal.id, "stub should have been replaced by existing deal"
    # Stub should be deleted since no other calls reference it
    assert db_session.query(CustomerDeal).filter_by(id=stub.id).first() is None


def test_maybe_merge_writes_canonical_customer_name_back_to_call(test_db):
    """Post-merge writeback: align call.customer_name with the deal's
    canonical business name so the call detail / tracker / recent-calls
    UIs no longer surface stray person fragments ("Singh", "Bob") that
    the per-call LLM detector picks up from witnesses or signatories.

    Scenario mirrors the 2026-05-17 Playwright run where three "Bob's
    Glazing Limited" uploads merged correctly into one deal but each
    Call row still showed a different wrong customer_name on /calls/{id}.
    """
    from app.pipeline import _maybe_merge_into_existing_deal

    cust = Customer(legal_name="Bob's Glazing Limited", slug="bobs-glazing-limited")
    test_db.add(cust)
    test_db.flush()
    existing_deal = CustomerDeal(
        customer_id=cust.id,
        customer_name="Bob's Glazing Limited",
        supplier="E.ON Next",
        status="in_progress",
    )
    test_db.add(existing_deal)
    test_db.flush()

    stub = CustomerDeal(customer_name="(auto-detect pending deadbeef)", status="in_progress")
    test_db.add(stub)
    test_db.flush()

    call = Call(
        id="deadbeef-0000-0000-0000-000000000000",
        filename="bobs-glazing-loa.mp3",
        file_path="/tmp/bobs-glazing-loa.mp3",
        deal_id=stub.id,
        # Per-call LLM detector latched onto a signatory's first name.
        customer_name="Singh",
        detected_supplier="E.ON Next",
        word_data="[]",
        status="processing",
    )
    test_db.add(call)
    test_db.commit()

    # Override path mirrors the second-pass merge that fires after
    # detect_business_name in the real pipeline.
    _maybe_merge_into_existing_deal(
        call, test_db, override_customer_name="Bob's Glazing Limited"
    )
    test_db.commit()
    test_db.refresh(call)

    assert call.deal_id == existing_deal.id
    assert call.customer_name == "Bob's Glazing Limited", (
        f"expected canonical deal name to overwrite per-call detection, "
        f"got customer_name={call.customer_name!r}"
    )


def test_maybe_merge_does_not_overwrite_with_stub_placeholder(test_db):
    """Defensive check: if the matched deal is itself an auto-detect-pending
    stub (shouldn't happen because the candidate query filters on status,
    but belt-and-braces), we MUST NOT clobber the call's real customer name
    with the placeholder ``(auto-detect pending xxxx)`` string.
    """
    from app.pipeline import _maybe_merge_into_existing_deal

    # Two stubs — both look pending. Should be a no-op since names won't match.
    other_stub = CustomerDeal(
        customer_name="(auto-detect pending aaaaaaaa)",
        supplier="E.ON Next",
        status="in_progress",
    )
    test_db.add(other_stub)
    test_db.flush()

    stub = CustomerDeal(customer_name="(auto-detect pending bbbbbbbb)", status="in_progress")
    test_db.add(stub)
    test_db.flush()

    call = Call(
        id="bbbbbbbb-0000-0000-0000-000000000000",
        filename="x.mp3",
        file_path="/tmp/x.mp3",
        deal_id=stub.id,
        customer_name="Real Customer Name",
        detected_supplier="E.ON Next",
        word_data="[]",
        status="processing",
    )
    test_db.add(call)
    test_db.commit()

    _maybe_merge_into_existing_deal(
        call, test_db, override_customer_name="Real Customer Name"
    )
    test_db.commit()
    test_db.refresh(call)

    # No fuzzy match → no merge → no clobber.
    assert call.customer_name == "Real Customer Name"
