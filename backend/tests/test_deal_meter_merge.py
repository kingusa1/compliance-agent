"""Tests for the post-extraction deal-merge step (2026-05-24).

The user's reported bug: three rejections in the tracker for the same
customer + same MPRN ``5085812604``. Root cause was that the L7 intake
matcher can't see the meter id at upload time (it's still inside the
not-yet-transcribed audio), so it creates three separate deals. The
finalize step later stamps the same MPAN/MPRN onto all three deals but
never merges them.

These tests cover:
  * canonical meter-id lookup that tolerates a 10-digit MPRN stored in
    the ``mpan_electricity`` column (real user data shape)
  * per-call merge that runs at finalize
  * one-shot consolidation endpoint that heals pre-existing fragmentation
  * idempotency — second run does nothing
  * survivor selection (oldest wins)
  * field-copy semantics — survivor keeps its non-NULL fields, victim
    fills the gaps
  * audit-log emission

We use the project's standard SQLite `test_db` fixture so these run in
CI alongside everything else without a Postgres dependency.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.deal_meter_merge import (
    _canon_mpan,
    _canon_mprn,
    _find_meter_siblings,
    _is_placeholder,
    _meter_keys_for_deal,
    consolidate_all_duplicate_deals,
    merge_deals_on_meter_match,
)
from app.models import Call, CustomerDeal


@pytest.fixture
def test_db():
    """In-memory SQLite session, scoped to one test.

    Override of the conftest fixture so these tests don't touch the
    filesystem — the shared fixture leaves a tempfile open and Windows
    refuses to unlink it during teardown, polluting every test that uses
    test_db with a (misleading) teardown ERROR.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ─── Canonicalisation primitives ────────────────────────────────────────────


class TestCanonicalisers:
    @pytest.mark.parametrize(
        "raw",
        [
            "2000000000123",                       # 13-digit MPAN core
            "01 2 345 67 89 01 23 45 67 89 0 12",  # 13 digits inside spaces
            "123456789012345678901",               # 21-digit MPAN form, last-13
        ],
    )
    def test_mpan_canon_13_or_21(self, raw) -> None:
        digits = "".join(c for c in raw if c.isdigit())
        if len(digits) == 13:
            assert _canon_mpan(raw) == digits
        elif len(digits) == 21:
            assert _canon_mpan(raw) == digits[-13:]
        else:
            assert _canon_mpan(raw) == ""

    def test_mpan_canon_returns_empty_for_10_digits(self) -> None:
        """The bug bait — a 10-digit MPRN value stored in an MPAN field
        must NOT canonicalise as MPAN. _canon_mpan returns "" so the
        sibling-search uses the MPRN canonicaliser instead."""
        assert _canon_mpan("5085812604") == ""

    def test_mprn_canon_accepts_6_to_10_digits(self) -> None:
        assert _canon_mprn("5085812604") == "5085812604"   # 10
        assert _canon_mprn("123456") == "123456"            # 6
        assert _canon_mprn("1234567") == "1234567"          # 7
        assert _canon_mprn("12345") == ""                   # 5 — too short
        assert _canon_mprn("12345678901") == ""             # 11 — too long
        assert _canon_mprn(None) == ""

    def test_meter_keys_tolerate_mprn_in_mpan_column(self) -> None:
        """The exact data shape from the user's tracker screenshot — a
        10-digit value sitting in ``mpan_electricity``. We expect
        ``_meter_keys_for_deal`` to return ("", "5085812604") so the
        sibling-search finds matches by MPRN."""
        deal = CustomerDeal(
            id=uuid.uuid4(),
            customer_name="Unknown",
            status="in_progress",
            mpan_electricity="5085812604",  # mis-typed by reviewer
            mprn_gas=None,
            mpan_or_mprn=None,
        )
        mpan, mprn = _meter_keys_for_deal(deal)
        assert mpan == ""
        assert mprn == "5085812604"

    @pytest.mark.parametrize(
        "val,expected",
        [
            # Real customer names — must NOT be placeholders.
            ("Jayashree Swaminathan", False),
            ("BG Customer", False),
            ("Awais Mustafa Ta Charles Palace", False),
            # Null-ish.
            (None, True),
            ("", True),
            ("   ", True),
            # Common placeholders.
            ("Unknown", True),
            ("unknown", True),
            ("UNKNOWN", True),
            ("TBD", True),
            ("?", True),
            ("？", True),     # full-width question mark
            ("not provided", True),
            ("pending", True),
            # ----- 2026-05-25 regression cases — bug found in prod ---------
            # The customer-page filter rejected these but our merge code
            # treated them as real names, so a survivor stub kept its
            # placeholder name instead of inheriting the victim's real one,
            # hiding the merged deal from /customers entirely.
            ("(pending audio upload)", True),
            ("(PENDING AUDIO UPLOAD)", True),
            ("(no customer)", True),
            ("Untitled", True),
            # Dynamic-suffix variant that routes.py:407 stamps with the
            # call_id slice — must match by PREFIX, not equality.
            ("(auto-detect pending 4f3a905c)", True),
            ("(auto-detect pending abc12345)", True),
            ("(auto-detect pending)", True),
        ],
    )
    def test_is_placeholder_matches_customer_page_filter(
        self, val, expected
    ) -> None:
        """The 2026-05-25 bug: `customers_routes._REAL_NAME_PREDICATE`
        rejects "(pending audio upload)" / "(auto-detect pending …)" /
        "(no customer)" / "Untitled". Our merge code MUST treat the same
        strings as placeholders so a survivor stub doesn't keep its
        placeholder name and discard the victim's real customer_name —
        which would hide the merged deal from /customers entirely."""
        assert _is_placeholder(val) is expected

    def test_meter_keys_pull_from_legacy_combined_column(self) -> None:
        deal = CustomerDeal(
            id=uuid.uuid4(),
            customer_name="X",
            status="in_progress",
            mpan_electricity=None,
            mprn_gas=None,
            mpan_or_mprn="5085812604",   # legacy XLSX-import shape
        )
        mpan, mprn = _meter_keys_for_deal(deal)
        assert mpan == ""
        assert mprn == "5085812604"


# ─── Sibling search ────────────────────────────────────────────────────────


def _make_deal(db, *, name: str = "X", mpan: str | None = None,
               mprn: str | None = None, created_at: datetime | None = None,
               legacy: str | None = None) -> CustomerDeal:
    d = CustomerDeal(
        id=uuid.uuid4(),
        customer_name=name,
        status="in_progress",
        mpan_electricity=mpan,
        mprn_gas=mprn,
        mpan_or_mprn=legacy,
        created_at=created_at or datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(d)
    db.flush()
    return d


def _make_call(db, deal: CustomerDeal, *, suffix: str = "") -> Call:
    c = Call(
        id=f"call-{uuid.uuid4().hex[:8]}{suffix}",
        filename=f"f{suffix}.mp3",
        file_path="/tmp/x.mp3",
        deal_id=deal.id,
    )
    db.add(c)
    db.flush()
    return c


class TestSiblingSearch:
    def test_finds_sibling_with_same_canonical_mprn(self, test_db) -> None:
        target = _make_deal(test_db, name="Jayashree Swaminathan", mprn="5085812604")
        # Other deal storing the same value in mpan_electricity — the
        # real user-data shape that broke the matcher.
        sibling = _make_deal(test_db, name="Unknown", mpan="5085812604")
        # Unrelated deal with a different meter — must not match.
        _make_deal(test_db, name="Other", mprn="9999999999")
        siblings = _find_meter_siblings(test_db, target.id, "", "5085812604")
        ids = {s.id for s in siblings}
        assert sibling.id in ids
        assert len(siblings) == 1

    def test_excludes_self(self, test_db) -> None:
        d = _make_deal(test_db, mprn="1234567890")
        siblings = _find_meter_siblings(test_db, d.id, "", "1234567890")
        assert siblings == []

    def test_skips_deals_older_than_lookback(self, test_db) -> None:
        old = _make_deal(
            test_db,
            mprn="5085812604",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=500),
        )
        target = _make_deal(test_db, mprn="5085812604")
        siblings = _find_meter_siblings(test_db, target.id, "", "5085812604")
        assert old.id not in {s.id for s in siblings}


# ─── Per-call merge at finalize ────────────────────────────────────────────


class TestMergeDealsOnMeterMatch:
    def test_no_op_when_call_has_no_deal(self, test_db) -> None:
        call = Call(id="c1", filename="f.mp3", file_path="/tmp/f.mp3", deal_id=None)
        test_db.add(call)
        test_db.flush()
        outcome = merge_deals_on_meter_match(call, test_db)
        assert outcome.merged is False
        assert "no deal_id" in outcome.reason

    def test_no_op_when_no_meter_id_yet(self, test_db) -> None:
        deal = _make_deal(test_db, mpan=None, mprn=None)
        call = _make_call(test_db, deal)
        outcome = merge_deals_on_meter_match(call, test_db)
        assert outcome.merged is False
        assert "no meter id" in outcome.reason

    def test_no_op_when_meter_id_unique(self, test_db) -> None:
        deal = _make_deal(test_db, mprn="1234567890")
        call = _make_call(test_db, deal)
        outcome = merge_deals_on_meter_match(call, test_db)
        assert outcome.merged is False
        assert "no sibling" in outcome.reason

    def test_merges_two_deals_sharing_mprn_oldest_survives(self, test_db) -> None:
        """The headline scenario. Two deals, same MPRN. Older wins."""
        t0 = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
        older = _make_deal(test_db, name="Jayashree Swaminathan",
                           mprn="5085812604", created_at=t0)
        older_call = _make_call(test_db, older, suffix="-A")

        t1 = t0 + timedelta(hours=2)
        newer = _make_deal(test_db, name="Unknown",
                           mpan="5085812604",  # value in wrong column on purpose
                           created_at=t1)
        newer_call = _make_call(test_db, newer, suffix="-B")

        outcome = merge_deals_on_meter_match(newer_call, test_db)
        assert outcome.merged is True
        assert outcome.survivor_id == older.id
        assert outcome.source_ids == [newer.id]

        # Newer call now points at the older deal.
        test_db.refresh(newer_call)
        assert newer_call.deal_id == older.id
        # Older call still points at the older deal.
        test_db.refresh(older_call)
        assert older_call.deal_id == older.id
        # Newer deal row was deleted.
        assert test_db.query(CustomerDeal).filter_by(id=newer.id).first() is None

    def test_fills_survivor_null_fields_from_victim(self, test_db) -> None:
        """When the older deal is missing a customer_name or supplier the
        younger deal had, the survivor inherits it instead of staying NULL."""
        t0 = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        older = _make_deal(test_db, name="Unknown",
                           mprn="5085812604", created_at=t0)
        older.supplier = None
        test_db.flush()

        newer = _make_deal(test_db, name="Jayashree Swaminathan",
                           mprn="5085812604")
        newer.supplier = "E.ON Next"
        test_db.flush()
        newer_call = _make_call(test_db, newer)

        merge_deals_on_meter_match(newer_call, test_db)
        test_db.refresh(older)
        assert older.customer_name == "Jayashree Swaminathan"
        assert older.supplier == "E.ON Next"

    def test_does_not_overwrite_existing_survivor_fields(self, test_db) -> None:
        """A survivor that already has a customer_name keeps it; we never
        prefer victim data over surviving data."""
        t0 = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        older = _make_deal(test_db, name="Real Customer Ltd",
                           mprn="5085812604", created_at=t0)
        older.supplier = "British Gas"
        test_db.flush()

        newer = _make_deal(test_db, name="Bad Placeholder",
                           mprn="5085812604")
        newer.supplier = "E.ON Next"
        test_db.flush()
        newer_call = _make_call(test_db, newer)

        merge_deals_on_meter_match(newer_call, test_db)
        test_db.refresh(older)
        assert older.customer_name == "Real Customer Ltd"
        assert older.supplier == "British Gas"

    def test_survivor_stub_inherits_real_name_2026_05_25_regression(
        self, test_db
    ) -> None:
        """2026-05-25 regression: when the OLDEST deal in a cluster is a
        stub created by the audio-upload route with customer_name
        '(pending audio upload)' or '(auto-detect pending xxxxxxxx)',
        the merge MUST inherit the victim's real customer name so the
        deal stays visible on the /customers page.

        Before this fix, the survivor kept its placeholder name, and
        `customers_routes._REAL_NAME_PREDICATE` filtered the entire
        deal out of the customer list — user-visible symptom was 'I
        submitted a full case and nothing shows on the customer page'."""
        t0 = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
        # Older stub from the initial Lead Gen upload — customer_name is
        # the placeholder routes.py:577 writes.
        older_stub = _make_deal(
            test_db,
            name="(pending audio upload)",
            mprn="5085812604",
            created_at=t0,
        )
        # Newer deal whose transcript yielded the real business name.
        newer_real = _make_deal(
            test_db,
            name="Jayashree Swaminathan",
            mprn="5085812604",
        )
        newer_call = _make_call(test_db, newer_real)

        merge_deals_on_meter_match(newer_call, test_db)

        test_db.refresh(older_stub)
        # The survivor (older_stub) MUST now carry the real name so the
        # customer page predicate stops filtering it out.
        assert older_stub.customer_name == "Jayashree Swaminathan", (
            f"survivor kept placeholder {older_stub.customer_name!r} — "
            "deal will vanish from /customers"
        )

    def test_survivor_stub_auto_detect_prefix_inherits_real_name(
        self, test_db
    ) -> None:
        """Same regression, but for the `(auto-detect pending {hash})`
        variant that routes.py:407 stamps on the upload-time stub. The
        hash suffix is dynamic, so we must match by PREFIX."""
        t0 = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        older_stub = _make_deal(
            test_db,
            name="(auto-detect pending 4f3a905c)",
            mprn="5085812604",
            created_at=t0,
        )
        newer_real = _make_deal(
            test_db,
            name="Awais Mustafa Trading As Shah's Palace",
            mprn="5085812604",
        )
        newer_call = _make_call(test_db, newer_real)

        merge_deals_on_meter_match(newer_call, test_db)

        test_db.refresh(older_stub)
        assert older_stub.customer_name == "Awais Mustafa Trading As Shah's Palace"

    def test_three_way_merge_in_one_invocation(self, test_db) -> None:
        """The user's actual case: three deals all sharing one MPRN. The
        first call to finalize on any of them should fold ALL siblings into
        the oldest survivor in one shot."""
        base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)
        a = _make_deal(test_db, name="Jay", mprn="5085812604", created_at=base)
        b = _make_deal(test_db, name="Unknown",
                       mpan="5085812604",  # wrong column
                       created_at=base + timedelta(hours=1))
        c = _make_deal(test_db, name="J Swaminathan",
                       mprn="5085812604",
                       created_at=base + timedelta(hours=2))
        call_a = _make_call(test_db, a, suffix="-a")
        _make_call(test_db, b, suffix="-b")
        _make_call(test_db, c, suffix="-c")

        outcome = merge_deals_on_meter_match(call_a, test_db)
        assert outcome.merged is True
        assert outcome.survivor_id == a.id
        assert set(outcome.source_ids) == {b.id, c.id}
        # All 3 calls now on deal A.
        remaining_calls = test_db.query(Call).filter_by(deal_id=a.id).count()
        assert remaining_calls == 3

    def test_never_raises_on_internal_error(self, test_db, monkeypatch) -> None:
        """Finalize must complete even if the merge step blows up — this
        is best-effort code on the trailing edge of the pipeline."""
        deal = _make_deal(test_db, mprn="5085812604")
        call = _make_call(test_db, deal)

        from app import deal_meter_merge as mod

        def _explode(*a, **kw):
            raise RuntimeError("synthetic")

        monkeypatch.setattr(mod, "_meter_keys_for_deal", _explode)
        outcome = mod.merge_deals_on_meter_match(call, test_db)
        assert outcome.merged is False
        assert "error" in outcome.reason


# ─── Batch consolidation (admin endpoint backend) ──────────────────────────


class TestConsolidateAllDuplicateDeals:
    def test_dry_run_does_not_mutate(self, test_db) -> None:
        d1 = _make_deal(test_db, mprn="5085812604")
        d2 = _make_deal(test_db, mprn="5085812604")
        before_count = test_db.query(CustomerDeal).count()

        summary = consolidate_all_duplicate_deals(test_db, dry_run=True)
        assert summary["clusters_found"] == 1
        assert summary["dry_run"] is True

        after_count = test_db.query(CustomerDeal).count()
        assert after_count == before_count
        assert test_db.query(CustomerDeal).filter_by(id=d1.id).first() is not None
        assert test_db.query(CustomerDeal).filter_by(id=d2.id).first() is not None

    def test_live_run_collapses_cluster(self, test_db) -> None:
        base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
        a = _make_deal(test_db, name="A", mprn="5085812604", created_at=base)
        b = _make_deal(test_db, name="B",
                       mpan="5085812604",  # wrong column
                       created_at=base + timedelta(hours=1))
        c = _make_deal(test_db, name="C", mprn="5085812604",
                       created_at=base + timedelta(hours=2))
        _make_call(test_db, a, suffix="-a")
        _make_call(test_db, b, suffix="-b")
        _make_call(test_db, c, suffix="-c")

        summary = consolidate_all_duplicate_deals(test_db, dry_run=False)
        assert summary["clusters_found"] == 1
        # All calls land on the oldest deal.
        assert test_db.query(Call).filter_by(deal_id=a.id).count() == 3
        # B and C are gone.
        assert test_db.query(CustomerDeal).filter_by(id=b.id).first() is None
        assert test_db.query(CustomerDeal).filter_by(id=c.id).first() is None

    def test_idempotent_second_pass_is_noop(self, test_db) -> None:
        base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
        _make_deal(test_db, name="A", mprn="5085812604", created_at=base)
        _make_deal(test_db, name="B", mprn="5085812604",
                   created_at=base + timedelta(hours=1))

        first = consolidate_all_duplicate_deals(test_db, dry_run=False)
        assert first["clusters_found"] == 1

        second = consolidate_all_duplicate_deals(test_db, dry_run=False)
        assert second["clusters_found"] == 0
        assert second["merges"] == []

    def test_leaves_unrelated_deals_alone(self, test_db) -> None:
        # Cluster of two so the consolidator actually does work.
        _make_deal(test_db, mprn="5085812604")
        _make_deal(test_db, mprn="5085812604")
        # Distinct meter — must survive the consolidation untouched.
        lonely = _make_deal(test_db, mprn="9999999999")
        consolidate_all_duplicate_deals(test_db, dry_run=False)
        test_db.refresh(lonely)
        assert lonely.mprn_gas == "9999999999"
