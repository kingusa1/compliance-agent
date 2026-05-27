"""Post-extraction deal-merge — coalesce deals that share a meter id.

Problem the screenshot exposed (2026-05-24):
    Three calls for the same customer (same MPRN `5085812604`) all uploaded
    via audio-file upload — NOT via the L7 intake envelope, so the
    `app.intake.matcher.find_existing_deal` MPAN/MPRN hard-key path had
    nothing to grip on at intake time. The matcher created three separate
    `customer_deals` rows. The meter id was later extracted from each
    transcript by `app.agents.meter_extractor` and stamped onto each of
    those three deals — but by then the fragmentation was a fait accompli.
    The tracker shows three rows with identical MPAN/MPRN, customer
    "Unknown", same supplier, same date, same value — instead of one deal
    with three calls.

The fix: a fourth merge pass that runs at finalize, AFTER meter extraction
has written the canonical MPAN/MPRN onto the just-finalised call's deal.
We scan recent open deals for a matching canonical meter id; if one
exists, we re-point every call on the newer (post-fragmentation) deal to
the older one, lift any non-NULL fields from new → old where old is NULL,
delete the now-empty newer deal, and audit-log the merge.

Why "post-extraction" rather than "second-pass intake matcher":
    The intake matcher cannot see the MPAN until the transcript has been
    transcribed + meter-extracted. The transcript doesn't exist at intake.
    We need the merge step to be on the trailing edge of the pipeline, not
    the leading edge.

Why "merge into the OLDER deal":
    The older deal has more audit history; it's the one any reviewer who
    saw the data earlier has been editing. Preserving it keeps the audit
    chain stable. The newer (just-created) deal is the duplicate that
    shouldn't have existed.

Concurrency:
    Two finalises racing on the same MPAN both observing "no other deal
    yet" then both creating could happen. We mitigate by always picking
    the OLDEST candidate as survivor and using `SELECT ... FOR UPDATE` on
    the survivor row before re-pointing. Postgres serialises the
    re-pointers; the loser of the race finds the winner's already-merged
    state and no-ops.

Public API:
    `merge_deals_on_meter_match(call, db)`  — invoked once per finalise
    `consolidate_all_duplicate_deals(db)`   — one-shot batch fixer for
                                              pre-existing fragmentation
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.intake.matcher import _mpan_core, _mprn_norm
from app.models import Call, CustomerDeal

log = logging.getLogger("compliance.deal_meter_merge")

# How far back to look when searching for a duplicate deal. 365 days
# mirrors `intake.matcher.HARD_KEY_LOOKBACK_DAYS` — keep the two in lock-step
# so the same window applies at intake and post-extraction.
MERGE_LOOKBACK_DAYS = 365

# Fields we'll copy from the merged-away deal onto the survivor where the
# survivor's value is currently NULL/empty/placeholder. Stays narrow on
# purpose: never overwrite a real survivor value, and never touch FK
# columns that have their own merge story (rejection_id, customer_id,
# assigned_agent_id).
_COPY_FIELDS_IF_SURVIVOR_NULL = (
    "customer_name",
    "supplier",
    "deal_value_gbp",
    "mpan_or_mprn",
    "expected_live_date",
    "mpan_electricity",
    "mprn_gas",
    "commission_value",
    "commission_unit",
    "term_months",
    "docusign_reference",
    "external_watt_site_id",
    # `lifecycle_status` and `loa_status` are intentionally OMITTED —
    # both are derived state owned by `derive_lifecycle_status` /
    # `derive_loa_status` and hand-copying them across a merge could
    # desync from the post-merge call set. They get re-derived on the
    # next call lifecycle update.
    "loa_document_url",
)

# Strings that LOOK populated but mean "the upstream didn't really know" —
# treat the same as NULL when deciding whether to overwrite from a victim.
# `customer_name` is the canonical case: the column is `NOT NULL`, so when
# `detect_business_name` returns nothing, the writer stamps a placeholder.
# A merge where the survivor has a placeholder and the victim has a real
# name should prefer the real name. Includes both ASCII "?" and the
# full-width Unicode "？" (U+FF1F) — UK broker XLSX exports out of Asian-
# locale Excel installs sometimes carry the full-width form.
#
# CRITICAL — these MUST be a superset of `customers_routes._PLACEHOLDER_NAMES`
# (and must catch the dynamic-suffix variants like `(auto-detect pending
# {hash})`) so a survivor that inherits a stub name doesn't get a real
# victim name discarded. The customer page filters via _REAL_NAME_PREDICATE
# in customers_routes.py; if a merged deal's customer_name passes our
# check but FAILS the customer-page predicate, the deal vanishes from
# /customers — which is exactly the 2026-05-25 user-reported bug.
_PLACEHOLDER_VALUES = frozenset({
    # Generic null-meaning strings.
    "", "unknown", "n/a", "na", "none", "null", "-", "tbd",
    "?", "？", "missing", "not provided", "pending",
    # In-tree stub customer_names emitted by routes.py / intake paths.
    # See routes.py:407 ("(auto-detect pending {hash})") +
    # routes.py:577 ("(pending audio upload)") +
    # customers_routes._PLACEHOLDER_NAMES.
    "(pending audio upload)",
    "(no customer)",
    "untitled",
})
# Dynamic-prefix placeholders — when a customer_name STARTS with any of
# these, treat it as a placeholder regardless of the suffix. The upload
# route stamps the call_id slice on, e.g. `(auto-detect pending 4f3a905c)`,
# so equality-checking against a fixed set will always miss.
_PLACEHOLDER_PREFIXES = (
    "(auto-detect pending",
)


def _is_placeholder(val) -> bool:
    """True when `val` is None, empty, or a known placeholder string."""
    if val is None:
        return True
    if isinstance(val, str):
        s = val.strip().lower()
        if not s:
            return True
        if s in _PLACEHOLDER_VALUES:
            return True
        for prefix in _PLACEHOLDER_PREFIXES:
            if s.startswith(prefix):
                return True
    return False


@dataclass(frozen=True)
class MergeOutcome:
    """What happened on one merge attempt.

    `merged` is True only when at least one source deal was actually
    folded into the survivor. `survivor_id` is set whenever we picked one
    (even if no merge was needed because the candidate set was just the
    incoming deal). `source_ids` lists the deals consumed by the merge.
    `skipped_unsafe_ids` lists deals the meter matched but were not
    folded because `_is_safe_to_auto_merge` rejected them (e.g. supplier
    mismatch). The reviewer-facing log/audit lists those so a human can
    decide whether to merge them manually via the tracker side panel or
    via `/api/admin/undo-deal-merge` after the fact.
    """

    merged: bool
    survivor_id: Optional[uuid.UUID]
    source_ids: list[uuid.UUID]
    reason: str
    skipped_unsafe_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class SafetyVerdict:
    """Output of `_is_safe_to_auto_merge`. ``safe=False`` blocks the
    auto-fold; ``reason`` is the audit-payload string explaining why so
    a reviewer can later decide manually."""

    safe: bool
    reason: str


# 2026-05-25 — calibrated based on the broker pipeline lifecycle. Watt's
# typical deal completes lead-gen → verbal → LOA within ~30 days; we add
# margin for delayed audio uploads + retries. Beyond 90 days the same
# meter id is overwhelmingly a renewal / supplier switch / different
# contract cycle, NOT the same in-progress deal.
AUTO_MERGE_WINDOW_DAYS = 90

# Name fuzz floor below which two non-placeholder customer names are
# treated as DIFFERENT customers even when their MPAN/MPRN matches.
# Mirrors `intake.matcher.NAME_NEAR_CERTAIN` (87) so the intake-time
# matcher and the post-extraction merge use the same calibration.
_SAFE_NAME_FUZZ_FLOOR = 87


# Supplier-label aliases — collapse equivalent brand strings to one
# canonical form BEFORE comparison so the safety predicate doesn't
# false-negative on a real same-supplier pair like ("E.ON Next", "EON")
# or ("EDF", "EDF Energy"). False-positive is the dangerous direction
# (different suppliers normalising to the same string); these aliases
# are only collapsed when the source string already names a known UK
# supplier brand, never inferred from substring overlap.
_SUPPLIER_ALIASES: dict[str, str] = {
    # E.ON family — the prod incident on 2026-05-25 was a BG/E.ON pair,
    # so this family is the most important to get right.
    "eon": "e.on next",
    "e.on": "e.on next",
    "e.on next": "e.on next",
    "e.on energy": "e.on next",
    "e.on energy solutions": "e.on next",
    # British Gas family.
    "bg": "british gas",
    "british gas": "british gas",
    "british gas business": "british gas",
    "british gas lite": "british gas",
    # EDF family.
    "edf": "edf energy",
    "edf energy": "edf energy",
    "edf energy solutions": "edf energy",
    # SSE family (now OVO commercial but legacy contracts persist).
    "sse": "sse energy",
    "sse energy": "sse energy",
    "sse business energy": "sse energy",
    "sse business": "sse energy",
}


def _supplier_norm(raw) -> str:
    """Canonicalise a supplier label for safe equality comparison.

    Steps: strip + lower-case, drop common 'Unknown'-shaped sentinels,
    then collapse via `_SUPPLIER_ALIASES`. The alias map only canonicalises
    known UK brands — anything else passes through unchanged so a typo
    like `"E,ON"` still trips the safety guard (mismatch) rather than
    accidentally aliasing into the same bucket as a real brand.
    """
    if not raw:
        return ""
    s = str(raw).strip().lower()
    if s in ("unknown", "n/a", "none", "null", "-"):
        return ""
    return _SUPPLIER_ALIASES.get(s, s)


def _name_fuzz_ratio(a: str, b: str) -> int:
    """0–100 rapidfuzz token-set ratio over lower-cased + suffix-stripped
    names. Mirrors `intake.matcher._token_set_ratio` so the two surfaces
    rank candidates identically. Returns 0 when rapidfuzz isn't
    installed (degrades the safety check to "names didn't fuzzy match"
    which is the conservative outcome)."""
    if not a or not b:
        return 0
    try:
        from rapidfuzz import fuzz  # type: ignore[import-not-found]
        return int(fuzz.token_set_ratio(a, b))
    except Exception:
        # Jaccard fallback so the predicate still works in environments
        # without rapidfuzz (some CI configs).
        ta, tb = set(a.split()), set(b.split())
        if not ta or not tb:
            return 0
        return int(100 * len(ta & tb) / max(len(ta | tb), 1))


def _is_safe_to_auto_merge(
    survivor: CustomerDeal,
    victim: CustomerDeal,
) -> SafetyVerdict:
    """Decide whether an MPAN/MPRN-key match between two deals is safe
    to auto-fold WITHOUT a reviewer in the loop.

    UK MPAN cores (13 digits) and MPRNs (6–10 digits) are unique per
    PHYSICAL METER, not per supplier-contract. The same meter can be on
    E.ON one quarter and British Gas the next — that's a normal renewal
    / switch. So an unguarded auto-merge on meter id alone can fold two
    legitimately-separate commercial deals into one. Production reported
    exactly this on 2026-05-25 (BG call's deal folded into an E.ON deal),
    so we refuse to auto-merge whenever ANY of these signals fires:

    1. **Supplier mismatch** — both deals have a non-empty supplier set
       AND those supplier labels differ (case-insensitive). One side
       being empty/Unknown is OK; that side inherits.
    2. **Different customer** — both deals have a non-NULL `customer_id`
       AND those ids differ AND the customer-name fuzz ratio is below
       `_SAFE_NAME_FUZZ_FLOOR` (87, same as intake.matcher.NAME_NEAR_CERTAIN).
       Placeholder names ('Unknown' / '(pending audio upload)' / etc.) are
       always treated as "didn't fuzzy match" — i.e. unsafe when
       customer_id also differs.
    3. **Recency** — `created_at` timestamps more than
       `AUTO_MERGE_WINDOW_DAYS` apart. Beyond 90 days the same meter id
       is overwhelmingly a different contract cycle.

    A blocked auto-merge is NOT a permanent decline: the meter match is
    audit-logged as `deal.merge_skipped_unsafe` with the rejection
    reason so a reviewer can decide via the tracker side panel or the
    `/api/admin/undo-deal-merge` inverse.
    """
    # Guard 1 — supplier.
    s1 = _supplier_norm(survivor.supplier)
    s2 = _supplier_norm(victim.supplier)
    if s1 and s2 and s1 != s2:
        return SafetyVerdict(False, f"supplier mismatch: {s1!r} vs {s2!r}")

    # Guard 2 — customer identity.
    if (
        survivor.customer_id
        and victim.customer_id
        and survivor.customer_id != victim.customer_id
    ):
        from app.intake.matcher import _clean_name as _matcher_clean
        n1 = (survivor.customer_name or "").strip()
        n2 = (victim.customer_name or "").strip()
        if _is_placeholder(n1) or _is_placeholder(n2):
            return SafetyVerdict(
                False,
                f"different customer_id ({survivor.customer_id} vs "
                f"{victim.customer_id}) and one side has a placeholder name",
            )
        # Reuse intake.matcher._clean_name so we get the same legal-form
        # stripping ("Acme Plumbing Ltd" / "Acme Plumbing" / "Acme
        # Plumbing Limited" all collapse to "acme plumbing"). Without
        # this, two Customer rows for the same business with a typo'd
        # slug would always trip the guard.
        c1 = _matcher_clean(n1)
        c2 = _matcher_clean(n2)
        if c1 and c2 and c1 == c2:
            pass  # exact match after cleaning — safe
        else:
            ratio = _name_fuzz_ratio(c1 or n1.lower(), c2 or n2.lower())
            if ratio < _SAFE_NAME_FUZZ_FLOOR:
                return SafetyVerdict(
                    False,
                    f"different customer_id and name fuzz {ratio} < "
                    f"{_SAFE_NAME_FUZZ_FLOOR} ({n1!r} vs {n2!r})",
                )

    # Guard 3 — recency window.
    sc = _naive_dt_safe(survivor.created_at)
    vc = _naive_dt_safe(victim.created_at)
    delta_days = abs((sc - vc).days)
    if delta_days > AUTO_MERGE_WINDOW_DAYS:
        return SafetyVerdict(
            False,
            f"deals are {delta_days}d apart (> {AUTO_MERGE_WINDOW_DAYS}d window)",
        )

    return SafetyVerdict(
        True, f"supplier + customer + recency ({delta_days}d) all clear"
    )


def _naive_dt_safe(dt) -> datetime:
    """Forwarded shim so the safety predicate can call _naive_dt before
    that function is defined further down the module — Python doesn't
    forward-resolve at import time. Kept as a thin wrapper for clarity."""
    if dt is None:
        return datetime.min
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------------------
# Meter-id canonicalisation that tolerates the wrong-column case.
# ---------------------------------------------------------------------------


def _canon_mpan(raw: Optional[str]) -> str:
    """Return the 13-digit MPAN core, or '' if the input can't be one.

    Wraps `intake.matcher._mpan_core` for consistency — same canonical
    form as the intake hard-key path so both surfaces resolve identically.
    """
    return _mpan_core(raw)


def _canon_mprn(raw: Optional[str]) -> str:
    """Return the 6-10 digit MPRN, or '' if the input can't be one."""
    return _mprn_norm(raw)


def _meter_keys_for_deal(deal: CustomerDeal) -> tuple[str, str]:
    """Return (canonical_mpan, canonical_mprn) for `deal`, trying every
    column the meter id could plausibly live in.

    A reviewer typing an MPRN into the "MPAN/MPRN" side-panel field can
    land it in `mpan_electricity` even though semantically it's an MPRN.
    Likewise the legacy combined column `mpan_or_mprn` could hold either.
    We try every column with both canonicalisers so a 10-digit value in
    `mpan_electricity` still gets recognised as an MPRN.
    """
    candidates = (
        deal.mpan_electricity,
        deal.mprn_gas,
        deal.mpan_or_mprn,
    )
    mpan = ""
    mprn = ""
    for raw in candidates:
        if not raw:
            continue
        if not mpan:
            mpan = _canon_mpan(raw)
        if not mprn:
            mprn = _canon_mprn(raw)
    return mpan, mprn


# ---------------------------------------------------------------------------
# Candidate search.
# ---------------------------------------------------------------------------


def _find_meter_siblings(
    db: Session, exclude_deal_id: uuid.UUID, mpan: str, mprn: str
) -> list[CustomerDeal]:
    """Return open deals that share a canonical MPAN or MPRN with the
    incoming deal, excluding the incoming deal itself.

    Sorted oldest → newest so the caller can pick the survivor in one pass.
    """
    if not mpan and not mprn:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=MERGE_LOOKBACK_DAYS)
    # Use timezone-naive datetime here because `customer_deals.created_at` is
    # stored as `TIMESTAMP` (without timezone) per the migration; mixing
    # tz-aware and tz-naive on Postgres raises `can't compare offset-naive
    # and offset-aware`.
    cutoff_naive = cutoff.replace(tzinfo=None)
    q = (
        db.query(CustomerDeal)
        .filter(CustomerDeal.id != exclude_deal_id)
        .filter(CustomerDeal.created_at >= cutoff_naive)
        .filter(
            or_(
                CustomerDeal.mpan_electricity.isnot(None),
                CustomerDeal.mprn_gas.isnot(None),
                CustomerDeal.mpan_or_mprn.isnot(None),
            )
        )
        .order_by(CustomerDeal.created_at.asc())
    )
    siblings: list[CustomerDeal] = []
    for d in q.all():
        d_mpan, d_mprn = _meter_keys_for_deal(d)
        if mpan and d_mpan == mpan:
            siblings.append(d)
            continue
        if mprn and d_mprn == mprn:
            siblings.append(d)
    return siblings


# ---------------------------------------------------------------------------
# Re-pointing primitive.
# ---------------------------------------------------------------------------


def _absorb(survivor: CustomerDeal, victim: CustomerDeal, db: Session) -> int:
    """Re-point every Call on `victim` to `survivor`, copy any missing
    fields, then delete the empty victim row. Returns the number of calls
    re-pointed.

    Caller owns the transaction — we never commit. We never touch
    `victim.id` until every dependent row has moved off it, so a rollback
    leaves the database in a consistent pre-merge state.
    """
    # Phase 1 — re-point Calls. Batched UPDATE is faster than ORM loop on
    # deals with 5+ calls and is safe because Call.deal_id is the only FK
    # from calls → customer_deals.
    moved = (
        db.query(Call)
        .filter(Call.deal_id == victim.id)
        .update({"deal_id": survivor.id}, synchronize_session=False)
    )

    # Phase 2 — copy missing fields. The survivor keeps any field it
    # already has a real value for. "Real" means: not NULL, not the empty
    # string, not a known placeholder like "Unknown" / "TBD" / "?". The
    # placeholder check matters because `customer_deals.customer_name` is
    # NOT NULL on Postgres, so a deal whose business-name detection
    # failed carries the literal "Unknown" — which IS truthy in plain
    # Python but should still lose to a victim's real customer name.
    copied: list[str] = []
    for field in _COPY_FIELDS_IF_SURVIVOR_NULL:
        if not _is_placeholder(getattr(survivor, field, None)):
            continue
        new_val = getattr(victim, field, None)
        if _is_placeholder(new_val) or new_val == []:
            continue
        setattr(survivor, field, new_val)
        copied.append(field)

    # Phase 3 — merge `field_sources` jsonb. Existing entries on the survivor
    # are preserved; victim entries fill the gaps.
    surv_fs = dict(survivor.field_sources or {})
    vict_fs = dict(victim.field_sources or {})
    for k, v in vict_fs.items():
        surv_fs.setdefault(k, v)
    survivor.field_sources = surv_fs

    # Phase 4 — merge `meters` jsonb (Watt's multi-meter shape). Union by
    # serialised JSON form to dedup dual-fuel entries that already appear
    # on both sides.
    surv_meters_raw = list(survivor.meters or [])
    vict_meters_raw = list(victim.meters or [])
    seen: set[str] = {json.dumps(m, sort_keys=True) for m in surv_meters_raw}
    for m in vict_meters_raw:
        key = json.dumps(m, sort_keys=True)
        if key not in seen:
            surv_meters_raw.append(m)
            seen.add(key)
    survivor.meters = surv_meters_raw

    # Phase 5 — null any FK on `customer_deals` that points back at the victim
    # so the delete in phase 6 doesn't trigger SET NULL noise elsewhere.
    # `rejection_id` is the only such column.
    if victim.rejection_id is not None and survivor.rejection_id is None:
        survivor.rejection_id = victim.rejection_id
    victim.rejection_id = None

    db.flush()

    # Phase 6 — delete the now-empty victim. We do NOT use ON DELETE CASCADE
    # because nothing should still point at it (calls re-pointed, rejection
    # back-ref nulled). If something does — e.g. a row we don't know about —
    # the FK will error and the caller's transaction rolls back cleanly.
    db.delete(victim)
    db.flush()

    return moved


# ---------------------------------------------------------------------------
# Public entrypoints.
# ---------------------------------------------------------------------------


def _naive_dt(dt) -> datetime:
    """Normalise a possibly-tz-aware datetime to naive UTC for sort comparison.

    `customer_deals.created_at` is declared as `DateTime` (no tz), but
    historical inserts via paths that used `datetime.now(timezone.utc)`
    can produce tz-aware rows. Python 3.11+ raises `TypeError` when you
    sort mixed naive/aware datetimes — that would silently demote the
    whole merge to a no-op via the outer try/except.
    """
    if dt is None:
        return datetime.min
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _lock_survivor(db: Session, deal_id: uuid.UUID) -> Optional[CustomerDeal]:
    """Re-fetch the survivor with SELECT FOR UPDATE so concurrent finalises
    serialise on it. Falls back to a plain SELECT on SQLite (tests) which
    has no row-level locking.

    Postgres semantics (post wave-15, 2026-05-27): we set
    ``SET LOCAL lock_timeout = '2s'`` before the FOR UPDATE so a contended
    lock fails fast inside the 15s statement_timeout budget instead of
    queueing the entire pipeline. The transaction-scoped setting reverts
    automatically at COMMIT/ROLLBACK, so it never leaks across requests.

    Returns ``None`` on lock contention (typed as `_LockBusy`-like
    semantically — but we keep the existing `None` contract because the
    upstream caller already treats `None` as "survivor disappeared under
    lock"; either way the merge short-circuits and a future re-finalize
    picks it up.

    Reference: pganalyze L72 — "Canceling statement due to lock timeout"
    + Postgres lock_timeout docs. Burn 2s on a contended lock, never 15s.
    """
    # 2026-05-27 wave-15 (database-reviewer CRITICAL) — `db.bind` is
    # deprecated in SQLAlchemy 2.0 and emits a warning + raises on full
    # 2.0-strict mode. `db.get_bind()` is the forward-compatible form
    # that resolves to the same Engine via Session.get_bind().
    bind = db.get_bind()
    is_pg = bind.dialect.name == "postgresql" if bind is not None else False
    if is_pg:
        # SET LOCAL applies to the current transaction only. SQLAlchemy
        # opens the implicit txn on this first `.execute()` so the
        # subsequent FOR UPDATE shares the same transaction scope.
        db.execute(text("SET LOCAL lock_timeout = '2s'"))
    q = db.query(CustomerDeal).filter(CustomerDeal.id == deal_id)
    if is_pg:
        q = q.with_for_update()
    try:
        return q.first()
    except Exception as e:  # noqa: BLE001 — structured detection below
        # 2026-05-27 wave-15 (python+database-reviewer HIGH) — detect
        # lock_timeout by Postgres SQLSTATE (55P03 = lock_not_available)
        # OR psycopg2's typed exception class. Substring on the error
        # message is the last-resort fallback for the SQLAlchemy-wrapped
        # variant where the inner pgcode isn't always reachable. This
        # avoids both false-positives (random RuntimeError happens to
        # mention "lock timeout") and false-negatives (locale changes).
        if _is_lock_timeout(e):
            return None
        raise


def _is_lock_timeout(exc: BaseException) -> bool:
    """True iff `exc` is a Postgres lock_timeout cancellation.

    Three checks in order of fidelity:
      1. The underlying psycopg2/psycopg cause's `pgcode == '55P03'`
         (LOCK_NOT_AVAILABLE — emitted when `lock_timeout` fires).
      2. `isinstance` against `psycopg2.errors.LockNotAvailable` if the
         driver is psycopg2 (the project's current driver).
      3. Substring match `"lock timeout"` on the error message as a
         resilient last-resort. Bounded false-positive risk because
         the surrounding code only calls this from FOR UPDATE call
         sites where lock_timeout is the dominant cancellation mode.

    Returns False for `statement_timeout` cancellations (different
    error message + SQLSTATE 57014), which must propagate to the
    outer pipeline so the surrounding retry layer can react.
    """
    # Layer 1 — SQLSTATE
    orig = getattr(exc, "orig", None)
    pgcode = getattr(orig, "pgcode", None) or getattr(exc, "pgcode", None)
    if pgcode == "55P03":
        return True
    # Layer 2 — psycopg2 typed exception
    try:
        from psycopg2 import errors as _pg_errors  # type: ignore
        if orig is not None and isinstance(orig, _pg_errors.LockNotAvailable):
            return True
        if isinstance(exc, _pg_errors.LockNotAvailable):
            return True
    except Exception:  # noqa: BLE001 — psycopg2 not installed (SQLite-only env)
        pass
    # Layer 3 — message substring (last-resort)
    return "lock timeout" in str(exc).lower()


def merge_deals_on_meter_match(call: Call, db: Session) -> MergeOutcome:
    """Coalesce `call`'s current deal with any other deal that shares its
    canonical MPAN/MPRN. Returns `MergeOutcome(merged=False, …)` when
    there's nothing to do (which is the common case — most deals are
    unique).

    Designed to be called once at the tail of `_step_finalize`, AFTER the
    meter-extractor has written MPAN/MPRN onto the current deal. Safe to
    call on a call without a deal_id (no-ops). Safe to call when no
    meter id has been extracted yet (no-ops). Never raises — internal
    errors are logged and converted to MergeOutcome(merged=False).

    Concurrency: under Supavisor transaction-mode pooling two finalises
    on the same MPAN can race. We mitigate by SELECT FOR UPDATE on the
    survivor row before re-pointing — the loser blocks until the winner
    commits, then re-reads and short-circuits when the victims are
    already gone.
    """
    try:
        if not call.deal_id:
            return MergeOutcome(False, None, [], "call has no deal_id")
        current = db.query(CustomerDeal).filter_by(id=call.deal_id).first()
        if current is None:
            return MergeOutcome(False, None, [], "current deal not found")
        mpan, mprn = _meter_keys_for_deal(current)
        if not mpan and not mprn:
            return MergeOutcome(False, current.id, [], "no meter id on deal yet")
        siblings = _find_meter_siblings(db, current.id, mpan, mprn)
        if not siblings:
            return MergeOutcome(False, current.id, [], "no sibling with same meter")

        # Pick the OLDEST deal among (current, *siblings) as the survivor.
        all_candidates = [current] + siblings
        all_candidates.sort(key=lambda d: _naive_dt(d.created_at))
        survivor_target = all_candidates[0]
        victim_ids_planned = [d.id for d in all_candidates if d.id != survivor_target.id]

        # Lock survivor for the duration of the merge — under Postgres this
        # blocks any other concurrent finalise from re-electing the same
        # survivor. SQLite (tests) silently no-ops the FOR UPDATE.
        survivor = _lock_survivor(db, survivor_target.id)
        if survivor is None:
            # A concurrent merge already absorbed our survivor — short-circuit.
            return MergeOutcome(False, None, [], "survivor disappeared under lock")

        absorbed_ids: list[uuid.UUID] = []
        skipped_unsafe_ids: list[uuid.UUID] = []
        rejection_ids_moved: list[str] = []
        cross_customer_warnings: list[str] = []
        total_calls_moved = 0

        for vid in victim_ids_planned:
            v = db.query(CustomerDeal).filter_by(id=vid).first()
            if v is None:
                # A concurrent merge already absorbed this victim — skip
                # silently. The loser of the race ends up doing less work.
                continue

            # 2026-05-25 — supplier / customer / recency safety guard.
            # The hard-key MPAN/MPRN match alone is NOT enough — meters
            # switch suppliers between contracts. When the guard fires we
            # log an audit row with the rejection reason so a reviewer
            # can still decide manually via /api/admin/undo-deal-merge or
            # the tracker side-panel, but we DO NOT touch the data.
            verdict = _is_safe_to_auto_merge(survivor, v)
            if not verdict.safe:
                skipped_unsafe_ids.append(vid)
                log.warning(
                    "deal_meter_merge SKIPPED unsafe pair survivor=%s victim=%s reason=%s",
                    str(survivor.id), str(vid), verdict.reason,
                )
                try:
                    record_audit(
                        db,
                        action="deal.merge_skipped_unsafe",
                        entity_type="customer_deal",
                        entity_id=str(survivor.id),
                        payload={
                            "candidate_deal_id": str(vid),
                            "meter_mpan": mpan or None,
                            "meter_mprn": mprn or None,
                            "reason": verdict.reason,
                            "trigger": "post_extraction_meter_match",
                            "trigger_call_id": str(call.id),
                        },
                        organization_id=str(call.organization_id) if call.organization_id else None,
                    )
                except Exception as e:  # noqa: BLE001 — audit must never break the path
                    log.warning("audit append failed on merge_skipped_unsafe: %s", e)
                continue

            # HIGH-2 — Cross-customer orphan warning. With the safety
            # guard above, this only fires when same customer_id was
            # missing on one side, so it's a notification not a block.
            if v.customer_id and survivor.customer_id and v.customer_id != survivor.customer_id:
                cross_customer_warnings.append(
                    f"victim {vid} customer_id {v.customer_id} != survivor customer_id {survivor.customer_id}"
                )
            # Track the rejection_id transfer for audit traceability.
            if v.rejection_id is not None and survivor.rejection_id is None:
                rejection_ids_moved.append(str(v.rejection_id))
            moved = _absorb(survivor, v, db)
            total_calls_moved += moved
            absorbed_ids.append(vid)

        if not absorbed_ids:
            if skipped_unsafe_ids:
                return MergeOutcome(
                    merged=False,
                    survivor_id=survivor.id,
                    source_ids=[],
                    reason=f"all {len(skipped_unsafe_ids)} meter-matched candidates were unsafe to auto-merge",
                    skipped_unsafe_ids=tuple(skipped_unsafe_ids),
                )
            return MergeOutcome(False, survivor.id, [], "all victims gone (concurrent merge)")

        meter_label = f"mpan={mpan}" if mpan else ""
        if mprn:
            meter_label = f"{meter_label}{' ' if meter_label else ''}mprn={mprn}".strip()

        log.info(
            "deal_meter_merge survivor=%s absorbed=%d calls_moved=%d %s",
            str(survivor.id),
            len(absorbed_ids),
            total_calls_moved,
            meter_label,
        )
        for w in cross_customer_warnings:
            log.warning("deal_meter_merge cross_customer_orphan_risk: %s", w)

        # Audit chain — one row per absorbed deal so reviewers can trace
        # what folded into what. Includes rejection-id transfer + cross-
        # customer warnings so an operator can reconstruct the merge.
        for vid in absorbed_ids:
            try:
                record_audit(
                    db,
                    action="deal.merged_into",
                    entity_type="customer_deal",
                    entity_id=str(survivor.id),
                    payload={
                        "absorbed_deal_id": str(vid),
                        "meter_mpan": mpan or None,
                        "meter_mprn": mprn or None,
                        "trigger": "post_extraction_meter_match",
                        "trigger_call_id": str(call.id),
                        "transferred_rejection_ids": rejection_ids_moved or None,
                        "cross_customer_warnings": cross_customer_warnings or None,
                    },
                    organization_id=str(call.organization_id) if call.organization_id else None,
                )
            except Exception as e:  # noqa: BLE001 — audit must never break the merge
                log.warning("audit_log append failed (merge still applied): %s", e)

        return MergeOutcome(
            merged=True,
            survivor_id=survivor.id,
            source_ids=absorbed_ids,
            reason=f"matched on {meter_label}",
            skipped_unsafe_ids=tuple(skipped_unsafe_ids),
        )
    except Exception as e:  # noqa: BLE001 — finalize must NEVER fail because of merge
        log.warning("merge_deals_on_meter_match failed (ignored): %s", e)
        return MergeOutcome(False, None, [], f"error: {type(e).__name__}")


def backfill_placeholder_customer_names(
    db: Session,
    *,
    dry_run: bool = False,
) -> dict:
    """Promote a real customer name onto deals whose `customer_name` is
    still a placeholder.

    Why this exists alongside `consolidate_all_duplicate_deals`:
        The meter-merge consolidator only acts when ≥2 deals share a
        canonical MPAN/MPRN. It does nothing for the single-deal case
        where the deal is already correctly coalesced but its
        `customer_name` is still e.g. `"(pending audio upload)"` because
        the audio-upload route stamped that stub at intake and no later
        write promoted the real name. The `/customers` page filters via
        `_REAL_NAME_PREDICATE` which excludes those placeholders, so the
        deal is invisible to the reviewer even though everything else
        about it is correct.

    What it does:
        For each deal where `_is_placeholder(customer_name)` is True,
        look at the deal's calls and pick the first non-placeholder
        `Call.customer_name`. If found, promote it onto the deal (and
        onto the linked Customer row's `legal_name` if THAT is also a
        placeholder). The pipeline already writes `Call.customer_name`
        via `detect_metadata` and `detect_business_name` — the data is
        usually there; it just never bubbled up onto the deal.

    Idempotent: a second run after the first finds zero deals to heal.

    Transaction ownership: flushes only — caller commits. Same contract
    as `consolidate_all_duplicate_deals`.

    Returns a summary the caller (admin endpoint or lifespan startup)
    can log + audit. When `dry_run=True`, lists what WOULD have been
    promoted without mutating.
    """
    # Pull the candidate deals in one query. We can't push `_is_placeholder`
    # into SQL because of the dynamic-prefix `(auto-detect pending ...)`
    # check, so filter in Python.
    cutoff_naive = (
        datetime.now(timezone.utc) - timedelta(days=MERGE_LOOKBACK_DAYS)
    ).replace(tzinfo=None)
    deals = (
        db.query(CustomerDeal)
        .filter(CustomerDeal.created_at >= cutoff_naive)
        .order_by(CustomerDeal.created_at.asc())
        .all()
    )

    candidates = [d for d in deals if _is_placeholder(d.customer_name)]
    summary: dict = {
        "dry_run": dry_run,
        "deals_scanned": len(deals),
        "deals_with_placeholder": len(candidates),
        "promoted": 0,
        "skipped_no_real_name_on_calls": 0,
        "details": [],
    }
    if not candidates:
        return summary

    # Pull all calls for the candidate deals in one query (bounded N+1).
    # Order by Call.created_at so the "first real name" we pick later is the
    # earliest one — deterministic across runs and reproducible in tests.
    deal_ids = [d.id for d in candidates]
    call_rows = (
        db.query(Call.deal_id, Call.customer_name)
        .filter(Call.deal_id.in_(deal_ids))
        .filter(Call.customer_name.isnot(None))
        .order_by(Call.created_at.asc().nullslast())
        .all()
    )
    calls_by_deal: dict = {}
    for deal_id, cname in call_rows:
        calls_by_deal.setdefault(deal_id, []).append(cname)

    # Best-effort Customer lookup so we can also lift the Customer.legal_name
    # when the deal carries a customer_id pointing at a placeholder Customer.
    from app.models import Customer
    customer_ids = {d.customer_id for d in candidates if d.customer_id}
    customer_map: dict = {}
    if customer_ids:
        for c in db.query(Customer).filter(Customer.id.in_(customer_ids)).all():
            customer_map[c.id] = c

    for d in candidates:
        names = calls_by_deal.get(d.id, [])
        # Pick the first non-placeholder name. Order is insertion order
        # which matches Call.created_at because the parent query is bounded
        # only by deal_id, but practical-enough: any real name is better
        # than the stub.
        real_name = next((n for n in names if not _is_placeholder(n)), None)
        # Fallback: the linked Customer.legal_name if the calls didn't help.
        if real_name is None and d.customer_id:
            cust = customer_map.get(d.customer_id)
            if cust and not _is_placeholder(cust.legal_name):
                real_name = cust.legal_name
        if real_name is None:
            summary["skipped_no_real_name_on_calls"] += 1
            continue

        old_name = d.customer_name
        entry = {
            "deal_id": str(d.id),
            "old_name": old_name,
            "new_name": real_name,
        }
        summary["details"].append(entry)
        if not dry_run:
            d.customer_name = real_name
            # Also lift onto the parent Customer row if THAT is a stub.
            if d.customer_id:
                cust = customer_map.get(d.customer_id)
                if cust and _is_placeholder(cust.legal_name):
                    cust.legal_name = real_name
            try:
                record_audit(
                    db,
                    action="deal.customer_name_promoted",
                    entity_type="customer_deal",
                    entity_id=str(d.id),
                    payload={
                        "old_name": old_name,
                        "new_name": real_name,
                        "trigger": "backfill_placeholder_customer_names",
                    },
                )
            except Exception as e:  # noqa: BLE001
                log.warning("audit append failed during name promotion: %s", e)
            summary["promoted"] += 1

    if not dry_run:
        db.flush()
    return summary


def consolidate_all_duplicate_deals(
    db: Session,
    *,
    dry_run: bool = False,
) -> dict:
    """Backfill: scan every open deal and merge any cluster that shares a
    canonical MPAN/MPRN.

    Use this once to clean up fragmentation that pre-dates the per-call
    merge step (e.g. the user's three Jayashree Swaminathan deals at
    `5085812604`). Idempotent — running it twice is a no-op.

    Returns a structured summary the admin route can echo to the caller.
    When `dry_run` is True, walks the cluster graph without re-pointing or
    deleting anything; the summary lists what WOULD have merged.

    Transaction ownership: this function flushes but does NOT commit. The
    caller (admin route) commits AFTER appending its own audit row so the
    merges and the audit live in a single transaction. Atomicity matters
    here because a partial-failure window between merges-committed and
    audit-uncommitted would leave a forensic gap.
    """
    cutoff_naive = (
        datetime.now(timezone.utc) - timedelta(days=MERGE_LOOKBACK_DAYS)
    ).replace(tzinfo=None)
    deals = (
        db.query(CustomerDeal)
        .filter(CustomerDeal.created_at >= cutoff_naive)
        .filter(
            or_(
                CustomerDeal.mpan_electricity.isnot(None),
                CustomerDeal.mprn_gas.isnot(None),
                CustomerDeal.mpan_or_mprn.isnot(None),
            )
        )
        .order_by(CustomerDeal.created_at.asc())
        .all()
    )

    # Build clusters keyed on canonical meter id. A deal that carries both
    # an MPAN and an MPRN can land in two clusters; we collapse those so
    # the same set of deals doesn't get processed twice.
    by_mpan: dict[str, list[CustomerDeal]] = {}
    by_mprn: dict[str, list[CustomerDeal]] = {}
    for d in deals:
        mpan, mprn = _meter_keys_for_deal(d)
        if mpan:
            by_mpan.setdefault(mpan, []).append(d)
        if mprn:
            by_mprn.setdefault(mprn, []).append(d)

    # Dedup by frozenset of deal IDs — handles the dual-fuel case where
    # the same cluster of deals appears under both an mpan key and an
    # mprn key. Identity comparison (`is`) on list objects would FAIL
    # because by_mpan[k] and by_mprn[k] are distinct list instances even
    # when they contain the same deals.
    clusters: list[tuple[str, list[CustomerDeal]]] = []
    processed_id_sets: set[frozenset] = set()
    for key, members in by_mpan.items():
        if len(members) > 1:
            id_set = frozenset(m.id for m in members)
            if id_set in processed_id_sets:
                continue
            processed_id_sets.add(id_set)
            clusters.append((f"mpan={key}", members))
    for key, members in by_mprn.items():
        if len(members) > 1:
            id_set = frozenset(m.id for m in members)
            if id_set in processed_id_sets:
                continue
            processed_id_sets.add(id_set)
            clusters.append((f"mprn={key}", members))

    summary: dict = {
        "dry_run": dry_run,
        "deals_scanned": len(deals),
        "clusters_found": len(clusters),
        "merges": [],
    }

    for label, members in clusters:
        members.sort(key=lambda d: _naive_dt(d.created_at))
        survivor = members[0]
        victims = members[1:]
        merge_entry: dict = {
            "meter": label,
            "survivor": str(survivor.id),
            "victims": [str(v.id) for v in victims],
            "calls_moved": 0,
        }
        if not dry_run:
            transferred_rejections: list[str] = []
            cross_customer_warnings: list[str] = []
            skipped_unsafe: list[dict] = []
            for v in victims:
                # 2026-05-25 safety guard — refuse cross-supplier /
                # cross-customer / out-of-window merges. The batch path
                # uses the same predicate as the per-call path so the
                # two surfaces never disagree.
                verdict = _is_safe_to_auto_merge(survivor, v)
                if not verdict.safe:
                    skipped_unsafe.append(
                        {"victim": str(v.id), "reason": verdict.reason}
                    )
                    log.warning(
                        "consolidate SKIPPED unsafe victim=%s reason=%s",
                        v.id, verdict.reason,
                    )
                    try:
                        record_audit(
                            db,
                            action="deal.merge_skipped_unsafe",
                            entity_type="customer_deal",
                            entity_id=str(survivor.id),
                            payload={
                                "candidate_deal_id": str(v.id),
                                "meter": label,
                                "reason": verdict.reason,
                                "trigger": "consolidate_all_duplicate_deals",
                            },
                        )
                    except Exception as e:  # noqa: BLE001
                        log.warning("audit append failed on skipped: %s", e)
                    continue
                try:
                    if v.customer_id and survivor.customer_id and v.customer_id != survivor.customer_id:
                        cross_customer_warnings.append(
                            f"victim {v.id} customer_id {v.customer_id} != survivor {survivor.customer_id}"
                        )
                    if v.rejection_id is not None and survivor.rejection_id is None:
                        transferred_rejections.append(str(v.rejection_id))
                    moved = _absorb(survivor, v, db)
                    merge_entry["calls_moved"] += moved
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "consolidate: failed to absorb %s into %s: %s",
                        v.id, survivor.id, e,
                    )
                    merge_entry.setdefault("errors", []).append(
                        {"victim": str(v.id), "err": str(e)}
                    )
            if skipped_unsafe:
                merge_entry["skipped_unsafe"] = skipped_unsafe
            try:
                record_audit(
                    db,
                    action="deal.merged_into",
                    entity_type="customer_deal",
                    entity_id=str(survivor.id),
                    payload={
                        "absorbed_deal_ids": merge_entry["victims"],
                        "meter": label,
                        "trigger": "consolidate_all_duplicate_deals",
                        "transferred_rejection_ids": transferred_rejections or None,
                        "cross_customer_warnings": cross_customer_warnings or None,
                    },
                )
            except Exception as e:  # noqa: BLE001
                log.warning("audit append failed during consolidate: %s", e)
            for w in cross_customer_warnings:
                log.warning("consolidate cross_customer_orphan_risk: %s", w)
        summary["merges"].append(merge_entry)

    # Flush but DO NOT commit — caller owns the transaction boundary so its
    # own audit row lives in the same transaction as the merges.
    if not dry_run:
        db.flush()
    return summary
