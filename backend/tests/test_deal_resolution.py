"""Auto-detect upload should attach to an existing open Deal when
(detected customer_name + supplier) match — not always create new.

Sprint v3-C1 — covers ``_maybe_merge_into_existing_deal`` in
``app.pipeline``. Watt's mental model treats a (customer, supplier)
tuple as ONE open Deal; the upload handler creates a stub Deal per
upload, this helper collapses that stub once detection completes.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.models import Call, Customer, CustomerDeal
from app.pipeline import _maybe_merge_into_existing_deal


# Local alias of the shared `test_db` fixture from conftest so the test
# spec reads ``db: Session`` per the sprint plan verbatim.
@pytest.fixture
def db(test_db) -> Session:
    return test_db


@pytest.mark.asyncio
async def test_attach_to_existing_open_deal(db: Session):
    cust = Customer(id=uuid.uuid4(), legal_name="Acme Ltd", slug="acme")
    open_deal = CustomerDeal(
        id=uuid.uuid4(),
        customer_id=cust.id,
        customer_name="Acme Ltd",
        supplier="E.ON Next",
        status="in_progress",
    )
    # ``customer_name`` is NOT NULL in the schema, so the stub uses an
    # empty-marker string — what matters for the merge is that the stub
    # is identified by ``call.deal_id``, not by name.
    stub_deal = CustomerDeal(
        id=uuid.uuid4(),
        customer_id=None,
        customer_name="",
        supplier=None,
        status="in_progress",
    )
    call = Call(
        id=str(uuid.uuid4()),
        filename="t.mp3",
        file_path="/tmp/t.mp3",
        deal_id=stub_deal.id,
        agent_name="Sammy",
        customer_name="Acme Ltd",
        detected_supplier="E.ON Next",
    )
    db.add_all([cust, open_deal, stub_deal, call])
    db.commit()

    await _maybe_merge_into_existing_deal(call, db)
    db.commit()
    db.refresh(call)
    assert call.deal_id == open_deal.id
    # Stub deal should be deleted (had no other calls)
    assert db.query(CustomerDeal).filter_by(id=stub_deal.id).first() is None


def test_skip_merge_when_explicit_deal_id(db: Session):
    # If the stub Deal already has another call, don't delete it; just attach.
    pass
