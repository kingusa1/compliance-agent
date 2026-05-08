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
