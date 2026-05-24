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

from sqlalchemy import or_
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
# `detect_business_name` returns nothing, the writer stamps "Unknown" as a
# placeholder. A merge where the survivor has "Unknown" and the victim has
# a real name should prefer the real name. Includes both ASCII "?" and
# the full-width Unicode "？" (U+FF1F) — UK broker XLSX exports out of
# Asian-locale Excel installs sometimes carry the full-width form.
_PLACEHOLDER_VALUES = frozenset({
    "", "unknown", "n/a", "na", "none", "null", "-", "tbd",
    "?", "？", "missing", "not provided", "pending",
})


def _is_placeholder(val) -> bool:
    """True when `val` is None, empty, or a known placeholder string."""
    if val is None:
        return True
    if isinstance(val, str):
        return val.strip().lower() in _PLACEHOLDER_VALUES
    return False


@dataclass(frozen=True)
class MergeOutcome:
    """What happened on one merge attempt.

    `merged` is True only when at least one source deal was actually
    folded into the survivor. `survivor_id` is set whenever we picked one
    (even if no merge was needed because the candidate set was just the
    incoming deal). `source_ids` lists the deals consumed by the merge.
    """

    merged: bool
    survivor_id: Optional[uuid.UUID]
    source_ids: list[uuid.UUID]
    reason: str


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

    Postgres semantics: the second worker blocks here until the first
    worker commits or rolls back. After acquiring the lock the second
    worker re-validates — if the survivor row was deleted in the
    meantime, returns None and the caller short-circuits.
    """
    is_pg = db.bind.dialect.name == "postgresql" if db.bind else False
    q = db.query(CustomerDeal).filter(CustomerDeal.id == deal_id)
    if is_pg:
        q = q.with_for_update()
    return q.first()


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
        rejection_ids_moved: list[str] = []
        cross_customer_warnings: list[str] = []
        total_calls_moved = 0

        for vid in victim_ids_planned:
            v = db.query(CustomerDeal).filter_by(id=vid).first()
            if v is None:
                # A concurrent merge already absorbed this victim — skip
                # silently. The loser of the race ends up doing less work.
                continue
            # HIGH-2 — Cross-customer orphan warning. We still merge (meter
            # IDs are globally unique by physical meter), but log it so an
            # operator can decide whether to also merge the customer rows
            # via /api/admin/sweep-orphans.
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
        )
    except Exception as e:  # noqa: BLE001 — finalize must NEVER fail because of merge
        log.warning("merge_deals_on_meter_match failed (ignored): %s", e)
        return MergeOutcome(False, None, [], f"error: {type(e).__name__}")


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
            for v in victims:
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
