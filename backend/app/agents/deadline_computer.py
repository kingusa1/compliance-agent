"""DeadlineComputerAgent — assign a sensible deadline to a Rejection.

No LLM. Pure compute. Picks the earlier of:

  - rejected_at + N business-days (N depends on severity)
  - expected_live_date − 1 business-day  (so the fix lands BEFORE go-live)

Severity → window:

  CRITICAL   1 business day
  HIGH       3 business days
  MEDIUM     5 business days
  LOW       10 business days

Severity comes from the RejectionAdvisorAgent's verdict; if absent,
defaults to MEDIUM.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from app.logger import log


_WINDOWS_BUSINESS_DAYS = {
    "CRITICAL": 1,
    "HIGH": 3,
    "MEDIUM": 5,
    "LOW": 10,
}


def _add_business_days(start: datetime, days: int) -> datetime:
    """Return start + N business days (skip Sat/Sun)."""
    cur = start
    added = 0
    while added < days:
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5:  # Mon=0 .. Fri=4
            added += 1
    return cur


def compute_deadline(
    *,
    rejected_at: datetime,
    severity: str | None,
    expected_live_date: date | None = None,
) -> datetime:
    """Pure function — easy to unit-test, no DB."""
    sev = (severity or "MEDIUM").upper()
    window = _WINDOWS_BUSINESS_DAYS.get(sev, 5)
    by_severity = _add_business_days(rejected_at, window)

    if expected_live_date is not None:
        # Land ONE business day before go-live so the supplier can
        # process the fix.
        live_dt = datetime.combine(expected_live_date, datetime.min.time())
        by_live = _add_business_days(live_dt, -1) if False else live_dt - timedelta(days=1)
        # Skip back to the previous business day if we landed on Sat/Sun.
        while by_live.weekday() >= 5:
            by_live = by_live - timedelta(days=1)
        return min(by_severity, by_live)

    return by_severity


def DeadlineComputerAgent(
    *,
    rejected_at: datetime,
    severity: str | None,
    expected_live_date: date | None = None,
) -> Optional[datetime]:
    """Public entry — same as `compute_deadline` but logs + returns None
    when inputs are too thin (callers can then leave Rejection.deadline as-is).
    """
    if not rejected_at:
        return None
    out = compute_deadline(
        rejected_at=rejected_at,
        severity=severity,
        expected_live_date=expected_live_date,
    )
    log.info(
        f"⏰ DEADLINE_COMPUTER severity={severity!r} "
        f"rejected_at={rejected_at.date()} "
        f"live={expected_live_date} → deadline={out.date()}"
    )
    return out
