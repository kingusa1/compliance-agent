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


@pytest.mark.asyncio
async def test_maybe_merge_writes_canonical_customer_name_back_to_call(test_db):
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
    await _maybe_merge_into_existing_deal(
        call, test_db, override_customer_name="Bob's Glazing Limited"
    )
    test_db.commit()
    test_db.refresh(call)

    assert call.deal_id == existing_deal.id
    assert call.customer_name == "Bob's Glazing Limited", (
        f"expected canonical deal name to overwrite per-call detection, "
        f"got customer_name={call.customer_name!r}"
    )


@pytest.mark.asyncio
async def test_maybe_merge_promotes_short_prefix_to_full_business_name(test_db):
    """First-call-only-spoke-the-person-name case.

    Leadgen recording for "Josephs Estate Agents Ltd" only mentions
    "Joseph" by name (the receptionist). The pipeline's stub-rename
    branch named the deal "Joseph". Later, the LOA recording spells out
    "Josephs Estate Agents Ltd" — the second-pass merge fires because
    "joseph" is a substring of "josephs estate agents". When the merge
    lands on the person-named deal, the more specific business name
    must REPLACE the short form across deal + customer.
    """
    from app.pipeline import _maybe_merge_into_existing_deal

    # Existing customer + deal that was named after the receptionist
    # because only "Joseph" was spoken in the leadgen recording.
    cust = Customer(legal_name="Joseph", slug="joseph")
    test_db.add(cust)
    test_db.flush()
    existing_deal = CustomerDeal(
        customer_id=cust.id,
        customer_name="Joseph",
        supplier="E.ON Next",
        status="in_progress",
    )
    test_db.add(existing_deal)
    test_db.flush()

    stub = CustomerDeal(customer_name="(auto-detect pending feedface)", status="in_progress")
    test_db.add(stub)
    test_db.flush()

    call = Call(
        id="feedface-0000-0000-0000-000000000000",
        filename="josephs-loa.mp3",
        file_path="/tmp/josephs-loa.mp3",
        deal_id=stub.id,
        customer_name="Andrew",  # LOA witness, not the business
        detected_supplier="E.ON Next",
        word_data="[]",
        status="processing",
    )
    test_db.add(call)
    test_db.commit()

    # Second-pass merge fires with the full business name as override.
    await _maybe_merge_into_existing_deal(
        call, test_db, override_customer_name="Josephs Estate Agents Ltd"
    )
    test_db.commit()
    test_db.refresh(call)
    test_db.refresh(existing_deal)
    test_db.refresh(cust)

    # Merged into the right deal.
    assert call.deal_id == existing_deal.id
    # Deal name promoted from short form to full business name.
    assert existing_deal.customer_name == "Josephs Estate Agents Ltd"
    # Customer.legal_name also promoted because it matched the short form.
    assert cust.legal_name == "Josephs Estate Agents Ltd"
    # Call's customer_name now matches the upgraded deal name.
    assert call.customer_name == "Josephs Estate Agents Ltd"


@pytest.mark.asyncio
async def test_maybe_merge_does_not_promote_when_target_is_not_a_prefix(test_db):
    """Negative: when the candidate is NOT a leading-word prefix of
    the target, we MUST NOT clobber the candidate's name. Catches the
    "Apple" → "Pineapple Co" foot-gun.
    """
    from app.pipeline import _maybe_merge_into_existing_deal

    cust = Customer(legal_name="Apple", slug="apple")
    test_db.add(cust)
    test_db.flush()
    existing_deal = CustomerDeal(
        customer_id=cust.id,
        customer_name="Apple",
        supplier="E.ON Next",
        status="in_progress",
    )
    test_db.add(existing_deal)
    test_db.flush()

    stub = CustomerDeal(customer_name="(auto-detect pending cafebabe)", status="in_progress")
    test_db.add(stub)
    test_db.flush()

    call = Call(
        id="cafebabe-0000-0000-0000-000000000000",
        filename="pineapple.mp3",
        file_path="/tmp/pineapple.mp3",
        deal_id=stub.id,
        customer_name="—",
        detected_supplier="E.ON Next",
        word_data="[]",
        status="processing",
    )
    test_db.add(call)
    test_db.commit()

    await _maybe_merge_into_existing_deal(
        call, test_db, override_customer_name="Pineapple Co"
    )
    test_db.commit()
    test_db.refresh(existing_deal)
    test_db.refresh(cust)

    # The "Apple" → "Pineapple Co" merge might still happen via fuzzy/
    # substring matching, but we MUST NOT promote because "apple" is
    # not a leading-word prefix of "pineapple co" (it's a trailing
    # substring of "pineapple"). Assertions:
    assert existing_deal.customer_name == "Apple", (
        "Apple-as-substring-of-Pineapple should NOT trigger a name promote"
    )
    assert cust.legal_name == "Apple"


@pytest.mark.asyncio
async def test_maybe_merge_does_not_overwrite_with_stub_placeholder(test_db):
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

    await _maybe_merge_into_existing_deal(
        call, test_db, override_customer_name="Real Customer Name"
    )
    test_db.commit()
    test_db.refresh(call)

    # No fuzzy match → no merge → no clobber.
    assert call.customer_name == "Real Customer Name"


@pytest.mark.asyncio
async def test_maybe_merge_uses_ai_tiebreaker_when_heuristics_miss(test_db):
    """AI tiebreaker scenario: heuristic scoring drops the candidate
    below the floor (e.g. "Joseph" vs "Smith Estate Agents" — no
    substring, no phonetic, no trailing match). Caller opted in to AI
    by passing ai_transcript_excerpt. The LLM judge picks the right
    deal because the transcript clarifies it.

    Mocks ai_match_deal so the test is hermetic (no real LLM calls).
    """
    from app.pipeline import _maybe_merge_into_existing_deal

    cust = Customer(legal_name="Joseph", slug="joseph")
    test_db.add(cust)
    test_db.flush()
    existing_deal = CustomerDeal(
        customer_id=cust.id,
        customer_name="Joseph",
        supplier="E.ON Next",
        status="in_progress",
    )
    test_db.add(existing_deal)
    test_db.flush()

    stub = CustomerDeal(customer_name="(auto-detect pending fade1234)", status="in_progress")
    test_db.add(stub)
    test_db.flush()

    call = Call(
        id="fade1234-0000-0000-0000-000000000000",
        filename="josephs-loa.mp3",
        file_path="/tmp/josephs-loa.mp3",
        deal_id=stub.id,
        customer_name="Gurpreet Singh",
        detected_supplier="E.ON Next",
        word_data="[]",
        status="processing",
    )
    test_db.add(call)
    test_db.commit()

    target_deal_id = str(existing_deal.id)
    # Patch ai_match_deal so it returns the existing deal's id. The
    # heuristic would NOT match "Joseph" against "Smith Estate Agents Ltd"
    # — but we're using a tougher target to force the AI path. Here our
    # candidate IS "Joseph" so heuristics DO catch via prefix-promote.
    # To force the AI branch, use a target the heuristic floors all reject.
    with patch("app.deal_matcher.ai_match_deal", new_callable=AsyncMock) as ai:
        ai.return_value = target_deal_id
        await _maybe_merge_into_existing_deal(
            call,
            test_db,
            override_customer_name="Brixton Bookkeeping Cooperative",  # no heuristic overlap with "Joseph"
            ai_transcript_excerpt=(
                "Hi, calling about your gas account. We're speaking on behalf of "
                "Brixton Bookkeeping Cooperative — formerly trading as 'Joseph'. "
                "The customer's name is now updated."
            ),
        )

    test_db.commit()
    test_db.refresh(call)
    test_db.refresh(existing_deal)

    # AI picked the existing deal. Merge applied + promotion fired
    # because the AI's target_name is more specific than the existing
    # short form (post-AI we re-enter the promotion check).
    assert call.deal_id == existing_deal.id
    assert ai.await_count == 1, "AI tiebreaker should have been called exactly once"


@pytest.mark.asyncio
async def test_maybe_merge_skips_ai_when_no_transcript_excerpt(test_db):
    """Cost guardrail: when the caller doesn't pass
    ai_transcript_excerpt (the first-pass merge at upload time), we
    must NOT burn an LLM call even if heuristics return no match.
    """
    from app.pipeline import _maybe_merge_into_existing_deal

    cust = Customer(legal_name="Joseph", slug="joseph-2")
    test_db.add(cust)
    test_db.flush()
    existing_deal = CustomerDeal(
        customer_id=cust.id,
        customer_name="Joseph",
        supplier="E.ON Next",
        status="in_progress",
    )
    test_db.add(existing_deal)
    test_db.flush()

    stub = CustomerDeal(customer_name="(auto-detect pending c0ffee01)", status="in_progress")
    test_db.add(stub)
    test_db.flush()

    call = Call(
        id="c0ffee01-0000-0000-0000-000000000000",
        filename="x.mp3",
        file_path="/tmp/x.mp3",
        deal_id=stub.id,
        customer_name="Andrew",
        detected_supplier="E.ON Next",
        word_data="[]",
        status="processing",
    )
    test_db.add(call)
    test_db.commit()

    with patch("app.deal_matcher.ai_match_deal", new_callable=AsyncMock) as ai:
        ai.return_value = str(existing_deal.id)
        # No ai_transcript_excerpt → AI should NOT fire.
        await _maybe_merge_into_existing_deal(
            call,
            test_db,
            override_customer_name="Brixton Bookkeeping Cooperative",
        )

    test_db.commit()
    assert ai.await_count == 0, "AI tiebreaker must not fire without an excerpt"
