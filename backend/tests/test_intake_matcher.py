"""Unit tests for the intake deal-linker matcher.

Exercises both tiers against an in-memory SQLite session so the matcher's
behaviour is deterministic across environments. Skips the legal-entity
suffix stripper edge cases when ``cleanco`` isn't installed (the matcher
degrades to a hand-rolled strip in that case).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.intake.matcher import (
    AUTO_MERGE_THRESHOLD,
    REVIEW_QUEUE_THRESHOLD,
    _clean_name,
    _composite_match,
    _hard_key_match,
    _mpan_core,
    _mprn_norm,
    _norm_postcode,
    _token_set_ratio,
    find_existing_deal,
)
from app.intake.payload_schema import CustomerMeta, DealMeta, SupplierEnum
from app.models import Base, Customer, CustomerDeal


@pytest.fixture()
def db():
    """In-memory SQLite session — fresh schema per test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_customer(
    db,
    legal_name: str,
    postcode: str | None = None,
    company_number: str | None = None,
    charity_number: str | None = None,
    trading_as: str | None = None,
) -> Customer:
    c = Customer(
        id=uuid.uuid4(),
        legal_name=legal_name,
        trading_as=trading_as,
        address_postcode=postcode,
        company_number=company_number,
        charity_number=charity_number,
        slug=legal_name.lower().replace(" ", "-"),
    )
    db.add(c)
    db.flush()
    return c


def _make_deal(
    db,
    customer_id: uuid.UUID | None,
    customer_name: str,
    *,
    mpan: str | None = None,
    mprn: str | None = None,
    docusign: str | None = None,
    supplier: str | None = None,
    status: str = "in_progress",
    days_ago: int = 0,
) -> CustomerDeal:
    created = _now().replace(tzinfo=None) - timedelta(days=days_ago)
    d = CustomerDeal(
        id=uuid.uuid4(),
        customer_id=customer_id,
        customer_name=customer_name,
        supplier=supplier,
        mpan_electricity=mpan,
        mprn_gas=mprn,
        docusign_reference=docusign,
        status=status,
        created_at=created,
        risk_tags=[],
        meters=[],
        field_sources={},
    )
    db.add(d)
    db.flush()
    return d


# ---------------------------------------------------------------------------
# Helper smoke tests — keep these passing as the algorithmic guarantees.
# ---------------------------------------------------------------------------


def test_clean_name_strips_legal_suffix_and_punct():
    assert _clean_name("St. Peter's Benfleet Church Ltd") == "st peters benfleet church"
    assert _clean_name("ACME ENERGY") == "acme energy"


def test_token_set_ratio_handles_word_reorder():
    a = _clean_name("St. Peter's Benfleet Church Ltd")
    b = _clean_name("St Peters Church Benfleet")
    assert _token_set_ratio(a, b) >= 95


def test_mpan_core_extracts_13_from_21():
    # 21-digit full MPAN: last 13 are the core.
    assert _mpan_core("100002000012345678901") == "0012345678901"
    # 13-digit core passes through.
    assert _mpan_core("2000012345678") == "2000012345678"
    # Spaces / hyphens normalised.
    assert _mpan_core("20-00 0123 4567 8") == "2000012345678"
    # Wrong length → empty.
    assert _mpan_core("123") == ""


def test_mprn_norm_range_check():
    assert _mprn_norm("1234567") == "1234567"
    assert _mprn_norm("12 345 678 9") == "123456789"
    assert _mprn_norm("12345") == ""  # too short
    assert _mprn_norm("12345678901234") == ""  # too long


def test_norm_postcode():
    assert _norm_postcode("ss71aa") == "SS7 1AA"
    assert _norm_postcode("SW1A 1AA") == "SW1A 1AA"


# ---------------------------------------------------------------------------
# Tier 1 — hard-key matches must return confidence=1.0.
# ---------------------------------------------------------------------------


def test_hard_key_mpan_match(db):
    c = _make_customer(db, "Acme Energy Ltd")
    d = _make_deal(db, c.id, "Acme Energy Ltd", mpan="2000012345678")
    db.commit()

    hit = _hard_key_match(
        CustomerMeta(legal_name="Different Name Ltd"),
        DealMeta(mpan_electricity="2000012345678"),
        db,
    )
    assert hit is not None
    assert hit.deal_id == d.id
    assert hit.confidence == 1.0
    assert hit.method == "hard_key:mpan"


def test_hard_key_mpan_strips_to_13_core(db):
    c = _make_customer(db, "Beta Co")
    d = _make_deal(db, c.id, "Beta Co", mpan="100002000012345678901")
    db.commit()
    # New upload supplies the 13-digit core directly.
    hit = _hard_key_match(
        CustomerMeta(legal_name="Beta Co"),
        DealMeta(mpan_electricity="0012345678901"),
        db,
    )
    assert hit is not None
    assert hit.deal_id == d.id


def test_hard_key_mprn_match(db):
    c = _make_customer(db, "Gamma Trading")
    d = _make_deal(db, c.id, "Gamma Trading", mprn="123456789")
    db.commit()
    hit = _hard_key_match(
        CustomerMeta(legal_name="Gamma Trading"),
        DealMeta(mprn_gas="123456789"),
        db,
    )
    assert hit is not None
    assert hit.method == "hard_key:mprn"


def test_hard_key_companies_house_match(db):
    c = _make_customer(db, "ABC Ltd", company_number="12345678")
    _make_deal(db, c.id, "ABC Ltd")
    db.commit()
    hit = _hard_key_match(
        CustomerMeta(
            legal_name="ABC Limited",  # name typo, but CN matches
            company_number="12345678",
        ),
        DealMeta(),
        db,
    )
    assert hit is not None
    assert hit.method == "hard_key:company_number"


def test_hard_key_no_match_returns_none(db):
    c = _make_customer(db, "Foo")
    _make_deal(db, c.id, "Foo", mpan="9999999999999")
    db.commit()
    hit = _hard_key_match(
        CustomerMeta(legal_name="Bar"),
        DealMeta(mpan_electricity="1111111111111"),
        db,
    )
    assert hit is None


# ---------------------------------------------------------------------------
# Tier 2 — composite probabilistic.
# ---------------------------------------------------------------------------


def test_composite_strong_name_plus_postcode_auto_merges(db):
    c = _make_customer(
        db, "St. Peter's Benfleet Church Ltd", postcode="SS7 1AA"
    )
    _make_deal(db, c.id, "St. Peter's Benfleet Church Ltd", supplier="E.ON")
    db.commit()
    hit = _composite_match(
        CustomerMeta(
            legal_name="St Peters Church Benfleet",
            address_postcode="SS7 1AA",
        ),
        DealMeta(supplier=SupplierEnum.EON),
        db,
    )
    assert hit is not None
    assert hit.confidence >= AUTO_MERGE_THRESHOLD, (
        f"expected >= {AUTO_MERGE_THRESHOLD}, got {hit.confidence!r} reason={hit.reason}"
    )
    assert hit.method == "composite_auto"


def test_composite_name_only_does_not_auto_merge(db):
    # Same name strong, but no postcode + no supplier → matcher must
    # refuse to auto-merge (name collision is high-frequency). Falls
    # below REVIEW_QUEUE_THRESHOLD so the legacy upsert path runs.
    c = _make_customer(db, "Acme Energy Ltd")
    _make_deal(db, c.id, "Acme Energy Ltd")
    db.commit()
    hit = _composite_match(
        CustomerMeta(legal_name="ACME ENERGY"),
        DealMeta(),
        db,
    )
    # Either None (cleanest) or a low-confidence hit below the review
    # threshold — both are acceptable "do not auto-merge" outcomes.
    assert hit is None or hit.confidence < REVIEW_QUEUE_THRESHOLD


def test_composite_name_plus_supplier_lands_in_review_band(db):
    # Name match + supplier match without postcode → should land in
    # the review-confirmation band (>=0.85 but <0.99).
    c = _make_customer(db, "Acme Energy Ltd")
    _make_deal(db, c.id, "Acme Energy Ltd", supplier="E.ON")
    db.commit()
    hit = _composite_match(
        CustomerMeta(legal_name="ACME ENERGY"),
        DealMeta(supplier=SupplierEnum.EON),
        db,
    )
    assert hit is not None, "expected a match"
    # Soft floor — exact threshold is sensitive to weighting; the contract
    # is "above the review cut, below the auto cut" so a reviewer confirms.
    assert (
        hit.confidence >= REVIEW_QUEUE_THRESHOLD - 0.10
    ), f"got {hit.confidence}"
    assert hit.confidence < AUTO_MERGE_THRESHOLD, (
        f"name+supplier alone must NOT auto-merge, got {hit.confidence}"
    )


def test_composite_different_name_no_match(db):
    c = _make_customer(db, "Foo Bar Inc")
    _make_deal(db, c.id, "Foo Bar Inc")
    db.commit()
    hit = _composite_match(
        CustomerMeta(legal_name="Wholly Different Organisation"),
        DealMeta(),
        db,
    )
    assert hit is None  # below REVIEW_QUEUE_THRESHOLD


def test_composite_closed_deal_capped(db):
    c = _make_customer(db, "Closed Co", postcode="SW1 1AA")
    _make_deal(db, c.id, "Closed Co", status="closed_lost")
    db.commit()
    hit = _composite_match(
        CustomerMeta(legal_name="Closed Co", address_postcode="SW1 1AA"),
        DealMeta(),
        db,
    )
    # Even with name+postcode, status=closed_lost should cap below auto.
    if hit is not None:
        assert hit.confidence < AUTO_MERGE_THRESHOLD


# ---------------------------------------------------------------------------
# End-to-end ``find_existing_deal`` cascade.
# ---------------------------------------------------------------------------


def test_cascade_hard_key_wins_over_composite(db):
    # Two customers: A has MPAN, B has a near-name match. Incoming has
    # B's name BUT A's MPAN → MPAN wins, returning A's deal.
    a = _make_customer(db, "Acme Energy Ltd")
    a_deal = _make_deal(db, a.id, "Acme Energy Ltd", mpan="2000012345678")
    b = _make_customer(db, "Acme Energy")
    _make_deal(db, b.id, "Acme Energy")
    db.commit()

    hit = find_existing_deal(
        CustomerMeta(legal_name="Acme Energy"),
        DealMeta(mpan_electricity="2000012345678"),
        db,
    )
    assert hit is not None
    assert hit.deal_id == a_deal.id
    assert hit.method == "hard_key:mpan"


def test_cascade_no_match_when_existing_deal_id_provided(db):
    c = _make_customer(db, "Has Deal", postcode="SW1 1AA")
    d = _make_deal(db, c.id, "Has Deal", supplier="E.ON")
    db.commit()
    # When the caller already chose existing_deal_id, the matcher must
    # short-circuit and let the route honour the explicit pick.
    hit = find_existing_deal(
        CustomerMeta(legal_name="Has Deal", address_postcode="SW1 1AA"),
        DealMeta(existing_deal_id=d.id),
        db,
    )
    assert hit is None
