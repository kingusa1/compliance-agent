"""Agent escalation — TS §10 of the Watt system spec.

Counts how many CRITICAL rejections each sales agent has accumulated
in the last 7 days. Agents at or above the threshold (default 3) are
returned for management escalation. The actual notification (email /
Slack / dashboard banner) is delegated to ``feedback_email`` or any
follow-on plumbing the operator wires.

This module is pure compute — it touches the DB read-only and returns
a list. The Inngest scheduled function should call this and decide
what to do with the result. Keeping it pure makes it trivially testable
without spinning up the workflow engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class AgentEscalation:
    sales_agent: str
    critical_count: int
    rejection_codes: tuple[str, ...]
    period_start: datetime
    period_end: datetime


def find_agents_for_escalation(
    db: Session,
    *,
    threshold: int = 3,
    window_days: int = 7,
    now: datetime | None = None,
) -> list[AgentEscalation]:
    """Return the list of sales agents who have ≥ ``threshold``
    CRITICAL rejections in the trailing ``window_days``.

    The query is intentionally lightweight (one SELECT + GROUP BY) so it
    can run hourly without burning DB time. Empty list when no agents
    cross the threshold.
    """
    from app.models import Rejection  # local import — avoid circular at module load

    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(days=window_days)

    # Pull every CRITICAL rejection in the window grouped by sales_agent.
    # We do a second pass in Python to collect the rejection codes so we
    # don't need a Postgres-only string_agg (works against SQLite tests too).
    rows = (
        db.query(Rejection)
        .filter(Rejection.rejected_at >= window_start)
        .filter(Rejection.sales_agent.isnot(None))
        .all()
    )

    by_agent: dict[str, list[Rejection]] = {}
    for r in rows:
        # Severity isn't a column on Rejection (it lives on the source
        # checkpoint). Treat any rejection in COMPLIANCE_ISSUE category
        # plus anything explicitly marked critical as severity-critical.
        # The spec is "≥3 criticals/week"; the existing data model maps
        # closest to "rejection rows whose category is in our critical
        # set". Operators can refine if they need finer granularity.
        if not _is_critical(r):
            continue
        by_agent.setdefault(r.sales_agent, []).append(r)

    out: list[AgentEscalation] = []
    for agent, items in by_agent.items():
        if len(items) < threshold:
            continue
        out.append(AgentEscalation(
            sales_agent=agent,
            critical_count=len(items),
            rejection_codes=tuple(_reason_code(r) for r in items),
            period_start=window_start,
            period_end=now,
        ))
    # Stable order — most-criticals first so management sees worst offenders top.
    out.sort(key=lambda e: (-e.critical_count, e.sales_agent))
    return out


def _is_critical(rejection) -> bool:
    """Heuristic — ``COMPLIANCE_ISSUE`` plus anything tagged critical
    in fix_required / rejection_reason text. The cleaner long-term
    fix is to add a ``severity`` column to Rejection; for the scaffold
    we work with what exists."""
    cat = (getattr(rejection, "category", "") or "").upper()
    if cat == "COMPLIANCE_ISSUE":
        return True
    text = " ".join([
        str(getattr(rejection, "rejection_reason", "") or ""),
        str(getattr(rejection, "fix_required", "") or ""),
    ]).lower()
    return "critical" in text or "vulnerable" in text or "fraud" in text


def _reason_code(rejection) -> str:
    """Try to extract a reason code (R01..R27) from the row.

    The codebase stores reasons as free text today; we look for the
    code at the start of ``rejection_reason`` or in ``fix_required``.
    Returns "?" when no code present so the audit trail still works.
    """
    import re

    for attr in ("rejection_reason", "fix_required"):
        text = str(getattr(rejection, attr, "") or "")
        m = re.search(r"\bR\d{2}\b", text)
        if m:
            return m.group(0)
    return "?"
