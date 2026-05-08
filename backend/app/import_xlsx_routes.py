"""Admin endpoint to trigger XLSX tracker import from the dashboard."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import current_user
from app.database import get_db
from app.import_xlsx_tracker import (
    DEFAULT_XLSX,
    import_rows,
    parse_xlsx,
    _reset_imported,
)

import_xlsx_router = APIRouter()


@import_xlsx_router.post("/api/admin/import-tracker-xlsx")
def trigger_import(
    reset: bool = False,
    db: Session = Depends(get_db),
    user=Depends(current_user),
):
    if user.get("role") != "admin":
        raise HTTPException(403, "admin only")
    if reset:
        deleted = _reset_imported(db)
    else:
        deleted = 0
    rows = parse_xlsx(DEFAULT_XLSX)
    counts = import_rows(rows, db=db)
    return {
        "parsed_rows": len(rows),
        "deleted_calls": deleted,
        "customers_created": counts.customers_created,
        "deals_created": counts.deals_created,
        "rejections_created": counts.rejections_created,
        "calls_created": counts.calls_created,
        "audit_log_created": counts.audit_log_created,
        "rows_skipped_no_category": counts.rows_skipped_no_category,
    }
