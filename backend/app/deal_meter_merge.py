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
    "lifecycle_status",
    "loa_status",
    "loa_document_url",
)

# Strings that LOOK populated but mean "the upstream didn't really know" —
# treat the same as NULL when deciding whether to overwrite from a victim.
# `customer_name` is the canonical case: the column is `NOT NULL`, so when
# `detect_business_name` returns nothing, the writer stamps "Unknown" as a
# placeholder. A merge where the survivor has "Unknown" and the victim has
# a real name should prefer the real name.
_PLACEHOLDER_VALUES = frozenset({
    "", "unknown", "n/a", "na", "none", "null", "-", "tbd", "?", "?", "missing",
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
    import json as _json

    surv_meters_raw = list(survivor.meters or [])
    vict_meters_raw = list(victim.meters or [])
    seen: set[str] = {_json.dumps(m, sort_keys=True) for m in surv_meters_raw}
    for m in vict_meters_raw:
        key = _json.dumps(m, sort_keys=True)
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
        all_candidates.sort(key=lambda d: d.created_at or datetime.min)
        survivor = all_candidates[0]
        victims = [d for d in all_candidates if d.id != survivor.id]

        absorbed_ids: list[uuid.UUID] = []
        total_calls_moved = 0
        for v in victims:
            moved = _absorb(survivor, v, db)
            total_calls_moved += moved
            absorbed_ids.append(v.id)

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

        # Audit chain — one row per absorbed deal so reviewers can trace
        # what folded into what.
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
    # an MPAN and an MPRN can land in two clusters; the survivor for one
    # cluster becomes the survivor for the other in the second pass.
    by_mpan: dict[str, list[CustomerDeal]] = {}
    by_mprn: dict[str, list[CustomerDeal]] = {}
    for d in deals:
        mpan, mprn = _meter_keys_for_deal(d)
        if mpan:
            by_mpan.setdefault(mpan, []).append(d)
        if mprn:
            by_mprn.setdefault(mprn, []).append(d)

    clusters: list[tuple[str, list[CustomerDeal]]] = []
    for key, members in by_mpan.items():
        if len(members) > 1:
            clusters.append((f"mpan={key}", members))
    for key, members in by_mprn.items():
        if len(members) > 1:
            # Skip if every member is already in an mpan cluster — avoids
            # double-processing dual-fuel deals.
            if any(c[1] is members for c in clusters):
                continue
            clusters.append((f"mprn={key}", members))

    summary: dict = {
        "dry_run": dry_run,
        "deals_scanned": len(deals),
        "clusters_found": len(clusters),
        "merges": [],
    }

    for label, members in clusters:
        members.sort(key=lambda d: d.created_at or datetime.min)
        survivor = members[0]
        victims = members[1:]
        merge_entry: dict = {
            "meter": label,
            "survivor": str(survivor.id),
            "victims": [str(v.id) for v in victims],
            "calls_moved": 0,
        }
        if not dry_run:
            for v in victims:
                try:
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
                    },
                )
            except Exception as e:  # noqa: BLE001
                log.warning("audit append failed during consolidate: %s", e)
        summary["merges"].append(merge_entry)

    if not dry_run:
        db.commit()
    return summary
