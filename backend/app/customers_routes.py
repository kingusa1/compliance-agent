"""Read-only Customer derived view.

Customer is not a stored entity — the page materializes from
customer_deals + calls, grouped by LOWER(TRIM(customer_name)). The SQL
in `_LIST_SQL` follows §4 of docs/research/2026-04-26-customer-page-data-model.excalidraw.

Two metrics from the diagram (critical_flag_count, open_directives)
require the `flags` and `fix_directives` tables which are not part of
v2 yet. They are returned as 0 so the response shape stays stable for
the frontend; backfill the values when those tables ship.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.intake.payload_schema import CustomerMeta as _CustomerMeta
from app.intake.upsert import upsert_customer as _upsert_customer


customers_router = APIRouter(prefix="/api/customers", tags=["customers"])


# ── response models ──────────────────────────────────────────────────────

class CustomerSummary(BaseModel):
    slug: str
    display_name: str
    deal_count: int
    call_count: int
    agents: list[str]
    suppliers: list[str]
    worst_action: str | None
    last_seen: datetime | None
    open_directives: int
    critical_flag_count: int
    has_duplicate_hint: bool
    # W1 (v3-watt-coverage): Watt portal deep-link integer. Picked from
    # any deal that has one (typical single-site customers will share
    # a site_id across all their deals).
    external_watt_site_id: int | None = None


class CustomerListResponse(BaseModel):
    customers: list[CustomerSummary]
    total: int
    has_more: bool


class DealCallSlot(BaseModel):
    id: str
    call_type: str | None
    status: str | None
    score: str | None  # fraction like "5/7" — calls.score is varchar, not numeric
    created_at: datetime | None


class CustomerDealCard(BaseModel):
    id: str
    deal_ref: str
    supplier: str | None
    deal_value_gbp: float | None
    agent_name: str | None
    status: str | None
    final_action: str | None
    open_directives: int
    last_call_at: datetime | None
    calls: list[DealCallSlot]


class CustomerDetailResponse(BaseModel):
    customer: CustomerSummary
    deals: list[CustomerDealCard]


# ── SQL ─────────────────────────────────────────────────────────────────

_LIST_SQL = """
SELECT
    LOWER(TRIM(d.customer_name))                                            AS slug,
    MAX(d.customer_name)                                                    AS display_name,
    COUNT(DISTINCT d.id)                                                    AS deal_count,
    COUNT(DISTINCT c.id)                                                    AS call_count,
    COALESCE(
        ARRAY_AGG(DISTINCT c.agent_name) FILTER (WHERE c.agent_name IS NOT NULL),
        ARRAY[]::text[]
    )                                                                       AS agents,
    COALESCE(
        ARRAY_AGG(DISTINCT d.supplier)   FILTER (WHERE d.supplier IS NOT NULL),
        ARRAY[]::text[]
    )                                                                       AS suppliers,
    MAX(c.created_at)                                                       AS last_seen,
    MAX(d.final_action)                                                     AS worst_action,
    -- W1 (v3-watt-coverage): pick any non-null site_id across the customer's
    -- deals. MAX is fine for single-site customers (1 distinct value);
    -- multi-site customers get whichever site_id sorts highest — the deal-
    -- level chip on /deals/[id] is the canonical link for those.
    MAX(d.external_watt_site_id)                                            AS external_watt_site_id
FROM customer_deals  d
LEFT JOIN calls      c ON c.deal_id = d.id
WHERE d.customer_name IS NOT NULL
GROUP BY LOWER(TRIM(d.customer_name))
"""


# ── helpers ─────────────────────────────────────────────────────────────

def _deal_ref(deal_id: str, created_at: datetime | str | None) -> str:
    """DEAL-2026-1a2b style identifier derived purely from id+year.

    `created_at` may arrive as an ISO string when the caller used a raw SQL
    SELECT against SQLite (which returns strings, not datetimes); coerce
    defensively rather than crashing the rollup/timeline endpoint.
    """
    year: int
    if isinstance(created_at, datetime):
        year = created_at.year
    elif isinstance(created_at, str) and len(created_at) >= 4 and created_at[:4].isdigit():
        year = int(created_at[:4])
    else:
        year = datetime.utcnow().year
    short = (deal_id or "").replace("-", "")[:4] or "0000"
    return f"DEAL-{year}-{short}"


def _has_duplicate_hint(rows: list[dict[str, Any]]) -> dict[str, bool]:
    """Cheap heuristic: flag a slug if any other slug differs by ≤2 chars
    on a same-length compare. Avoids full Levenshtein. n²; n stays small.
    """
    slugs = [r["slug"] for r in rows]
    flags: dict[str, bool] = {s: False for s in slugs}
    for i, a in enumerate(slugs):
        for b in slugs[i + 1:]:
            if abs(len(a) - len(b)) > 2:
                continue
            shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
            diffs = len(longer) - len(shorter)
            for ca, cb in zip(shorter, longer):
                if ca != cb:
                    diffs += 1
                if diffs > 2:
                    break
            if diffs <= 2:
                flags[a] = True
                flags[b] = True
    return flags


def _row_to_summary(row: Any, dup_flag: bool) -> CustomerSummary:
    return CustomerSummary(
        slug=row.slug,
        display_name=row.display_name,
        deal_count=row.deal_count,
        call_count=row.call_count,
        agents=list(row.agents or []),
        suppliers=list(row.suppliers or []),
        worst_action=row.worst_action,
        last_seen=row.last_seen,
        # `flags` + `fix_directives` tables not in v2 schema yet.
        open_directives=0,
        critical_flag_count=0,
        has_duplicate_hint=dup_flag,
        external_watt_site_id=getattr(row, "external_watt_site_id", None),
    )


# ── B-3: create-customer payload + response ────────────────────────────


class CustomerCreatePayload(BaseModel):
    """Body for POST /api/customers — mirrors L7 ``CustomerMeta`` but
    ``legal_name`` is required (we cannot create a row without it)."""

    legal_name: str = Field(min_length=1)
    trading_as: Optional[str] = None
    dob: Optional[date] = None
    company_number: Optional[str] = None
    charity_number: Optional[str] = None
    address_postcode: Optional[str] = None
    business_type: Optional[
        Literal["sole_trader", "limited", "partnership", "charity"]
    ] = None
    vulnerable_customer_flag: bool = False


class CustomerCreateRow(BaseModel):
    id: str
    slug: str
    legal_name: str
    trading_as: Optional[str] = None


class CustomerCreateResponse(BaseModel):
    customer: CustomerCreateRow
    slug: str


# ── routes ──────────────────────────────────────────────────────────────

@customers_router.post(
    "",
    response_model=CustomerCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_customer(
    payload: CustomerCreatePayload,
    db: Session = Depends(get_db),
) -> CustomerCreateResponse:
    """Create-or-return a Customer row keyed by slug(legal_name + trading_as).

    Idempotent on slug: re-POSTing the same legal_name returns the existing
    row with the original id (HTTP 201 either way — clients always receive
    a usable {customer, slug} payload).
    """
    meta = _CustomerMeta(
        legal_name=payload.legal_name,
        trading_as=payload.trading_as,
        dob=payload.dob,
        company_number=payload.company_number,
        charity_number=payload.charity_number,
        address_postcode=payload.address_postcode,
        business_type=payload.business_type,
        vulnerable_customer_flag=payload.vulnerable_customer_flag,
    )
    try:
        row = _upsert_customer(meta, db)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return CustomerCreateResponse(
        customer=CustomerCreateRow(
            id=str(row.id),
            slug=row.slug,
            legal_name=row.legal_name,
            trading_as=row.trading_as,
        ),
        slug=row.slug,
    )


@customers_router.get("", response_model=CustomerListResponse)
def list_customers(
    q: str | None = Query(None),
    supplier: str | None = Query(None),
    action: str | None = Query(None, regex="^(PASS|REVIEW|REJECT|TRIAGE)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> CustomerListResponse:
    having: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if q:
        having.append("MAX(d.customer_name) ILIKE :q_pattern")
        params["q_pattern"] = f"%{q}%"
    if supplier:
        having.append("BOOL_OR(d.supplier = :supplier)")
        params["supplier"] = supplier
    if action:
        having.append("MAX(d.final_action) = :action")
        params["action"] = action

    having_sql = ("HAVING " + " AND ".join(having)) if having else ""
    sql = f"""
        WITH agg AS (
            {_LIST_SQL}
            {having_sql}
        )
        SELECT *, COUNT(*) OVER() AS total_count
        FROM agg
        ORDER BY last_seen DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """
    rows = db.execute(text(sql), params).fetchall()
    total = rows[0].total_count if rows else 0

    dup_flags = _has_duplicate_hint([{"slug": r.slug} for r in rows])
    customers = [_row_to_summary(r, dup_flags.get(r.slug, False)) for r in rows]
    return CustomerListResponse(
        customers=customers,
        total=int(total),
        has_more=(offset + len(customers)) < int(total),
    )


@customers_router.get("/{slug}", response_model=CustomerDetailResponse)
def get_customer(slug: str, db: Session = Depends(get_db)) -> CustomerDetailResponse:
    summary_sql = f"""
        {_LIST_SQL}
        HAVING LOWER(TRIM(MAX(d.customer_name))) = :slug
    """
    summary_row = db.execute(text(summary_sql), {"slug": slug}).fetchone()
    if not summary_row:
        raise HTTPException(404, "customer not found")

    deals_rows = db.execute(text("""
        SELECT
            d.id, d.supplier, d.deal_value_gbp, d.status, d.final_action,
            d.created_at,
            (SELECT MAX(c2.agent_name) FROM calls c2 WHERE c2.deal_id = d.id) AS agent_name,
            (SELECT MAX(c2.created_at) FROM calls c2 WHERE c2.deal_id = d.id) AS last_call_at
        FROM customer_deals d
        WHERE LOWER(TRIM(d.customer_name)) = :slug
        ORDER BY d.created_at DESC
    """), {"slug": slug}).fetchall()

    deal_ids = [r.id for r in deals_rows]
    calls_by_deal: dict[Any, list[DealCallSlot]] = {did: [] for did in deal_ids}
    if deal_ids:
        call_rows = db.execute(text("""
            SELECT id, deal_id, call_type, status, score, created_at
            FROM calls WHERE deal_id = ANY(:deal_ids)
            ORDER BY created_at ASC
        """), {"deal_ids": deal_ids}).fetchall()
        for c in call_rows:
            calls_by_deal[c.deal_id].append(DealCallSlot(
                id=str(c.id), call_type=c.call_type, status=c.status,
                score=str(c.score) if c.score is not None else None,
                created_at=c.created_at,
            ))

    deals = [
        CustomerDealCard(
            id=str(d.id),
            deal_ref=_deal_ref(str(d.id), d.created_at),
            supplier=d.supplier,
            deal_value_gbp=float(d.deal_value_gbp) if d.deal_value_gbp is not None else None,
            agent_name=d.agent_name,
            status=d.status,
            final_action=d.final_action,
            open_directives=0,  # fix_directives table not in v2 yet.
            last_call_at=d.last_call_at,
            calls=calls_by_deal.get(d.id, []),
        )
        for d in deals_rows
    ]
    return CustomerDetailResponse(
        customer=_row_to_summary(summary_row, dup_flag=False),
        deals=deals,
    )


# ── L4: rollup + timeline ──────────────────────────────────────────────


def _isoformat_loose(v: datetime | str | None) -> str | None:
    """SQLite raw-SELECTs return datetimes as strings; pass them through."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


@customers_router.get("/{slug}/rollup")
def customer_rollup(slug: str, db: Session = Depends(get_db)) -> dict:
    """Pure DB aggregation — no LLM. Per L4 design_decisions:

      total_deals               = COUNT(d)
      total_calls               = COUNT(c)
      total_open_directives     = COUNT(fd) FILTER status IN (pending,in_progress)
      total_deal_value_gbp_..   = SUM(d.deal_value_gbp)
      recurring_issue_flag      = max(count(*) FILTER WHERE rejection_category=X) > 1
      worst_action_across_deals = MAX(d.final_action) ranked PASS<REVIEW<COACHING<FAIL<BLOCK
      dead_rejections_count     = COUNT(fd) FILTER status='dead'
    """
    base = db.execute(
        text(
            """
            SELECT
                COUNT(DISTINCT d.id)                              AS total_deals,
                COUNT(DISTINCT c.id)                              AS total_calls,
                COALESCE(SUM(d.deal_value_gbp), 0)                AS total_deal_value_gbp_annual_sum,
                MAX(d.final_action)                               AS worst_action,
                MAX(c.created_at)                                 AS last_activity_at
            FROM customer_deals d
            LEFT JOIN calls c ON c.deal_id = d.id
            WHERE LOWER(TRIM(d.customer_name)) = :slug
            """
        ),
        {"slug": slug},
    ).fetchone()
    if not base or (base.total_deals or 0) == 0:
        raise HTTPException(404, "customer not found")

    # Recurring issue: a rejection_category appearing on >1 deal for this customer.
    recurring_reasons: list[str] = []
    recurring = False
    try:
        rec_rows = db.execute(
            text(
                """
                SELECT rejection_category AS cat, COUNT(*) AS n
                FROM customer_deals
                WHERE LOWER(TRIM(customer_name)) = :slug
                  AND rejection_category IS NOT NULL
                GROUP BY rejection_category
                HAVING COUNT(*) > 1
                ORDER BY n DESC
                """
            ),
            {"slug": slug},
        ).fetchall()
        for r in rec_rows:
            recurring_reasons.append(f"{r.cat} appears in {int(r.n)} deals")
            recurring = True
    except Exception:
        pass

    # Open directives + dead rejections counts. Tolerate fix_directives
    # absence (older v2 DBs).
    open_directives = 0
    dead_count = 0
    try:
        rows = db.execute(
            text(
                """
                SELECT fd.status AS s, COUNT(*) AS n
                FROM fix_directives fd
                JOIN calls c ON c.id = fd.call_id
                JOIN customer_deals d ON d.id = c.deal_id
                WHERE LOWER(TRIM(d.customer_name)) = :slug
                GROUP BY fd.status
                """
            ),
            {"slug": slug},
        ).fetchall()
        for r in rows:
            if r.s in ("pending", "in_progress"):
                open_directives += int(r.n)
            elif r.s == "dead":
                dead_count += int(r.n)
    except Exception:
        pass

    # Risk-tag aggregate from flags joined to this customer's calls.
    risk_aggregate: dict[str, int] = {"ombudsman": 0, "mis_selling": 0, "complaint": 0, "cancellation": 0}
    try:
        rt_rows = db.execute(
            text(
                """
                SELECT f.risk_tag AS tag, COUNT(*) AS n
                FROM flags f
                JOIN calls c ON c.id = f.call_id
                JOIN customer_deals d ON d.id = c.deal_id
                WHERE LOWER(TRIM(d.customer_name)) = :slug
                  AND f.risk_tag IS NOT NULL
                GROUP BY f.risk_tag
                """
            ),
            {"slug": slug},
        ).fetchall()
        for r in rt_rows:
            key = (r.tag or "").replace("-", "_")
            if key in risk_aggregate:
                risk_aggregate[key] = int(r.n)
    except Exception:
        pass

    return {
        "total_deals": int(base.total_deals or 0),
        "total_calls": int(base.total_calls or 0),
        "total_open_directives": int(open_directives),
        "total_deal_value_gbp_annual_sum": float(base.total_deal_value_gbp_annual_sum)
        if base.total_deal_value_gbp_annual_sum is not None
        else None,
        "recurring_issue_flag": recurring,
        "recurring_issue_reasons": recurring_reasons or None,
        "worst_action_across_deals": base.worst_action,
        "dead_rejections_count": int(dead_count),
        "last_activity_at": _isoformat_loose(base.last_activity_at),
        "risk_tag_aggregate": risk_aggregate,
    }


@customers_router.get("/{slug}/timeline")
def customer_timeline(slug: str, db: Session = Depends(get_db)) -> dict:
    """Chronological all-calls-across-all-deals for a customer."""
    rows = db.execute(
        text(
            """
            SELECT
                c.id                           AS call_id,
                c.deal_id                      AS deal_id,
                d.id                           AS deal_id_for_ref,
                c.call_type                    AS call_type,
                COALESCE(c.completed_at, c.created_at) AS completed_at,
                c.score                        AS score,
                c.compliant                    AS compliant,
                d.rejection_category           AS rejection_category,
                c.agent_name                   AS agent_name,
                d.created_at                   AS deal_created_at
            FROM calls c
            LEFT JOIN customer_deals d ON d.id = c.deal_id
            WHERE LOWER(TRIM(COALESCE(d.customer_name, c.customer_name))) = :slug
            ORDER BY COALESCE(c.completed_at, c.created_at) DESC NULLS LAST
            """
        ),
        {"slug": slug},
    ).fetchall()

    timeline = []
    for r in rows:
        deal_ref = None
        if r.deal_id_for_ref is not None:
            deal_ref = _deal_ref(str(r.deal_id_for_ref), r.deal_created_at)
        timeline.append(
            {
                "call_id": str(r.call_id),
                "deal_id": str(r.deal_id) if r.deal_id else None,
                "deal_ref": deal_ref,
                "call_type": r.call_type,
                "completed_at": _isoformat_loose(r.completed_at),
                "score": r.score,
                "compliant": r.compliant,
                "rejection_category": r.rejection_category,
                "agent_name": r.agent_name,
            }
        )

    return {"timeline": timeline}
