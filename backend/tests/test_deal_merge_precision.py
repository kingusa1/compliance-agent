"""Regression tests — 100%-precision deal auto-merge contract.

Owner mandate 2026-05-26: *"I want the system to auto merge the calls
that is related one hundred percent, but not auto merge the wrong calls."*

The production incident screenshot showed 4 calls of mixed customers +
suppliers collapsed to one deal:
  - Jayashree Swaminathan / E.ON Next / Bradley     (call A)
  - Dinesh Gurung         / British Gas / Cade Tandy (call B)
  - Jayshree              / E.ON Next / Bradley Clayton (call C)
  - Jayashree Swaminathan / E.ON Next / Bradley     (call D)

The correct behaviour is:
  * A, D MAY merge — same supplier + identical normalised customer name.
  * C MAY merge with A/D under PHONETIC uplift only if floor (0.85)
    is cleared. Conservative — owner can decide via UI if needed.
  * B MUST NEVER auto-merge with A/C/D — different supplier (British Gas
    vs E.ON Next). Supplier-mismatch guard MUST block this.

Floors after 2026-05-26 precision tightening:
  * no phonetic / trailing signal:  >= 0.95 SequenceMatcher
  * phonetic strong:                >= 0.85 SequenceMatcher
  * trailing-2 exact-tokens match:  >= 0.75 SequenceMatcher
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


async def test_different_supplier_never_auto_merges(test_db):
    """Hard rule: even when customer names are similar, two deals with
    different suppliers MUST NOT auto-merge. This is the production
    incident's smoking gun.
    """
    from app.models import Call, CustomerDeal
    from app.pipeline import _maybe_merge_into_existing_deal

    # Existing deal: Dinesh Gurung on British Gas (call B equivalent)
    existing = CustomerDeal(
        customer_name="Dinesh Gurung",
        supplier="British Gas",
        status="open",
    )
    test_db.add(existing)
    test_db.flush()

    # Stub deal for the incoming call (call A equivalent — E.ON Next)
    stub = CustomerDeal(
        customer_name="(auto-detect pending …)",
        supplier=None,
        status="open",
    )
    test_db.add(stub)
    test_db.flush()

    # Call A: Jayashree Swaminathan / E.ON Next
    call = Call(
        filename="a.mp3",
        file_path="x/a.mp3",
        deal_id=stub.id,
        customer_name="Jayashree Swaminathan",
        detected_supplier="E.ON Next",
        status="processing",
    )
    test_db.add(call)
    test_db.flush()

    # Even passing the wrong customer name as override should NOT cross
    # the supplier guard (the matcher's per-candidate filter rejects any
    # candidate whose supplier doesn't match detected_supplier).
    await _maybe_merge_into_existing_deal(call, test_db, override_customer_name="Dinesh Gurung")

    # Call must still be on the stub, NOT folded into the BG deal.
    test_db.refresh(call)
    assert call.deal_id == stub.id, (
        "supplier-guard breach: call jumped to a deal with different supplier"
    )


async def test_same_supplier_same_customer_merges(test_db):
    """Symmetric expectation: when supplier AND customer match, the
    merger DOES collapse (the user wants merging when it's correct)."""
    from app.models import Call, CustomerDeal
    from app.pipeline import _maybe_merge_into_existing_deal

    existing = CustomerDeal(
        customer_name="Jayashree Swaminathan",
        supplier="E.ON Next",
        status="open",
    )
    test_db.add(existing)
    test_db.flush()

    stub = CustomerDeal(
        customer_name="(auto-detect pending …)",
        supplier=None,
        status="open",
    )
    test_db.add(stub)
    test_db.flush()

    call = Call(
        filename="d.mp3",
        file_path="x/d.mp3",
        deal_id=stub.id,
        customer_name="Jayashree Swaminathan",
        detected_supplier="E.ON Next",
        status="processing",
    )
    test_db.add(call)
    test_db.flush()

    await _maybe_merge_into_existing_deal(
        call, test_db, override_customer_name="Jayashree Swaminathan"
    )

    test_db.refresh(call)
    assert call.deal_id == existing.id, (
        "expected merge: same supplier + identical customer name should collapse"
    )


async def test_below_precision_floor_does_not_merge(test_db):
    """Customer-name similarity below the new 0.95 floor (no phonetic
    signal) must NOT auto-merge. Conservative — owner can manually link
    if needed."""
    from app.models import Call, CustomerDeal
    from app.pipeline import _maybe_merge_into_existing_deal

    # Two genuinely-different customers with the same supplier and only
    # weak name similarity (e.g. "Smith Trading" vs "Bob's Smithy").
    existing = CustomerDeal(
        customer_name="Bob's Smithy",
        supplier="E.ON Next",
        status="open",
    )
    test_db.add(existing)
    test_db.flush()

    stub = CustomerDeal(
        customer_name="(auto-detect pending …)",
        supplier=None,
        status="open",
    )
    test_db.add(stub)
    test_db.flush()

    call = Call(
        filename="x.mp3",
        file_path="x/x.mp3",
        deal_id=stub.id,
        customer_name="Smith Trading",
        detected_supplier="E.ON Next",
        status="processing",
    )
    test_db.add(call)
    test_db.flush()

    await _maybe_merge_into_existing_deal(
        call, test_db, override_customer_name="Smith Trading"
    )

    test_db.refresh(call)
    assert call.deal_id == stub.id, (
        "weak similarity (no phonetic, no trailing-2 match) must NOT merge "
        "under the new 0.95 floor"
    )
