"""Tracker page endpoint — surfaces XLSX-shape rows for /tracker UI."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import current_user
from app.database import get_db
from app.tracker_aggregator import build_tracker_rows


tracker_router = APIRouter()


@tracker_router.get("/api/tracker/rows")
def list_tracker_rows(
    tab: str = Query("active", regex="^(active|fixed|dead|compliant|awaiting_review)$"),
    month: Optional[str] = Query(None, regex=r"^\d{4}-\d{2}$"),
    category: Optional[str] = Query(None, description="comma-separated category enum keys"),
    supplier: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    user=Depends(current_user),
):
    cats = [c.strip() for c in category.split(",")] if category else None
    rows = build_tracker_rows(
        db,
        tab=tab,
        month=month,
        category=cats,
        supplier=supplier,
        search=search,
        limit=limit,
    )

    def _serialise(r):
        out = dict(r)
        for k in ("expected_live_date", "rejected_at", "last_action_date", "deadline", "confirmed_at"):
            if out.get(k) is not None:
                out[k] = out[k].isoformat()
        return out

    # Inngest observability — emit one event per query so the dashboard
    # shows tab-traffic + filter usage. Fire-and-forget, never blocks.
    try:
        from app.workflows.events import TRACKER_ROWS_QUERIED
        from app.workflows.observability import emit_event
        filter_keys = [
            k for k, v in (
                ("month", month), ("category", category),
                ("supplier", supplier), ("search", search),
            ) if v
        ]
        emit_event(TRACKER_ROWS_QUERIED, {
            "actor_id": user.get("id") if isinstance(user, dict) else None,
            "tab": tab,
            "filter_keys": filter_keys,
            "row_count": len(rows),
        })
    except Exception:
        pass

    return {"tab": tab, "count": len(rows), "rows": [_serialise(r) for r in rows]}


from fastapi.responses import Response
from app.tracker_export import build_xlsx


@tracker_router.get("/api/tracker/export.xlsx")
def export_tracker_xlsx(
    db: Session = Depends(get_db),
    user=Depends(current_user),
):
    data = build_xlsx(db)
    # Inngest observability — emit on every download so the dashboard
    # shows who exported when and how big the file was.
    try:
        from app.workflows.events import TRACKER_XLSX_EXPORTED
        from app.workflows.observability import emit_event
        emit_event(TRACKER_XLSX_EXPORTED, {
            "actor_id": user.get("id") if isinstance(user, dict) else None,
            "byte_count": len(data),
        })
    except Exception:
        pass
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="compliance-tracker.xlsx"'},
    )
