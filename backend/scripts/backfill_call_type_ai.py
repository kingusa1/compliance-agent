"""Backfill `Call.call_type` by re-classifying every transcript via AI.

The previous filename-hint pre-pass landed an incorrect call_type on
many historical recordings (especially upload paths where filenames were
generic like `audio.mp3` / `full call.mp3`). After 2026-05-11 the system
classifies call_type from the transcript via
`app.analysis.detect_call_type`; this script re-runs the classifier
across every call that already has a transcript and writes the result.

Re-runs the deal-lifecycle resolver after each batch so the
`CustomerDeal.lifecycle_status` column stays in sync.

Usage (local against prod DB via PSQL_URL):

    cd backend
    export PSQL_URL="postgres://…"
    ./venv/Scripts/python.exe -m scripts.backfill_call_type_ai          # dry run
    ./venv/Scripts/python.exe -m scripts.backfill_call_type_ai --apply  # write

Idempotent — re-running won't drift values further; reviewer-set
``call_type`` values (committed verdicts) are still respected because the
script skips any call whose `Call.review_status == 'reviewed'` and the
existing call_type is one of the canonical codes (i.e. the reviewer has
ratified it).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import List

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Ensure we can import the app package whether the script is invoked as
# `python -m scripts.backfill_call_type_ai` or directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.analysis import detect_call_type  # noqa: E402
from app.deal_lifecycle import derive_lifecycle_status  # noqa: E402
from app.models import Call, CustomerDeal  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("backfill_call_type")


_CANONICAL = {"lead_gen", "passover", "closer", "standalone_loa", "c_call", "amendment"}


def _resolve_engine_url() -> str:
    url = os.environ.get("PSQL_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("PSQL_URL or DATABASE_URL must be set")
    return url


async def _classify_one(call: Call) -> str | None:
    if not call.transcript:
        return None
    return await detect_call_type(call.transcript)


async def run(*, apply: bool, only_full: bool) -> None:
    engine = create_engine(_resolve_engine_url(), pool_pre_ping=True)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    db: Session = SessionFactory()

    try:
        q = db.query(Call).filter(Call.transcript.isnot(None))
        if only_full:
            q = q.filter((Call.call_type.is_(None)) | (Call.call_type == "full"))
        calls: List[Call] = q.order_by(Call.created_at.desc()).all()
        log.info(
            "scanning %d call(s) (only_full=%s, apply=%s)",
            len(calls),
            only_full,
            apply,
        )

        updates = 0
        skipped_reviewed = 0
        unchanged = 0
        unresolved = 0
        for call in calls:
            existing = (call.call_type or "").strip().lower()
            if call.review_status == "reviewed" and existing in _CANONICAL:
                skipped_reviewed += 1
                continue
            new_type = await _classify_one(call)
            if new_type is None:
                unresolved += 1
                log.info("  unresolved call=%s existing=%r", str(call.id)[:8], existing)
                continue
            if new_type == existing:
                unchanged += 1
                continue
            log.info(
                "  call=%s %s -> %s",
                str(call.id)[:8],
                existing or "<unset>",
                new_type,
            )
            if apply:
                call.call_type = new_type
            updates += 1

        # Re-derive lifecycle on every affected deal so the customer page
        # reflects the new classification immediately. Cheap — runs in
        # Python against the calls list we already have loaded.
        if apply:
            deal_ids = {c.deal_id for c in calls if c.deal_id}
            for deal_id in deal_ids:
                deal = db.query(CustomerDeal).filter_by(id=deal_id).first()
                if not deal:
                    continue
                deal_calls = [c for c in calls if c.deal_id == deal_id]
                new_status = derive_lifecycle_status(deal, deal_calls)
                if new_status and new_status != deal.lifecycle_status:
                    log.info(
                        "  deal=%s lifecycle %s -> %s",
                        str(deal.id)[:8],
                        deal.lifecycle_status,
                        new_status,
                    )
                    deal.lifecycle_status = new_status
            db.commit()
            log.info("committed %d call updates", updates)
        else:
            log.info("dry-run — pass --apply to persist")

        log.info(
            "summary: updates=%d unchanged=%d unresolved=%d skipped_reviewed=%d",
            updates,
            unchanged,
            unresolved,
            skipped_reviewed,
        )
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="persist changes (default: dry run)",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="re-classify every call (default: only call_type IS NULL or 'full')",
    )
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply, only_full=not args.all))


if __name__ == "__main__":
    main()
