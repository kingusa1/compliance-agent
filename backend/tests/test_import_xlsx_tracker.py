"""XLSX → TrackerRow normalisation."""
import io
import openpyxl
from app.import_xlsx_tracker import (
    parse_xlsx,
    TrackerXlsxRow,
    NORMALISE_CATEGORY,
    NORMALISE_STATUS,
    NORMALISE_SUPPLIER,
)


def _make_fixture_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MARCH 26"
    ws.append([
        "Customer Name", "MPAN / MPRN", "Expected Live date ", "Deal Value (£)",
        "Supplier", "Rejected at", "Sales Agent", "Rejection Reason", "Category",
        "Fix Required", "Fixed BY ", "Status", "Last Action Date", "Deadline",
        "Outcome", "Notes",
    ])
    ws.append([
        "Acme Industrial Ltd", "1234567890", "2026-04-30", 42000.0,
        "Pozative",  # typo
        "2026-04-15",
        "Sammy",
        "Agent did not state rates correctly",
        "VERBAL SALES ERROR",
        "AMENDMENT_CALL",
        "Lewis",
        "Fixed and approved",  # de-facto status
        "2026-04-16",
        "2026-04-17",
        "FIXED_AND_SUBMITTED",
        "Coaching narrative...",
    ])
    ws.append([
        "Beta Ltd", "9876543210", "2026-05-01", 18000.0,
        "BG Lite",
        "2026-04-10",
        "Jack Shaw",
        "BACS rejected",
        "priocess failure",  # typo
        "RESELL_TO_OTHER_SUPPLIER",
        None,
        "Dead",
        "2026-04-12",
        "2026-04-12",
        "CUSTOMER_LOST",
        "in contract till 2030",  # dead reason embedded in Notes
    ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_xlsx_returns_normalised_rows(tmp_path):
    p = tmp_path / "fixture.xlsx"
    p.write_bytes(_make_fixture_xlsx())

    rows = parse_xlsx(str(p))
    assert len(rows) == 2

    r1 = rows[0]
    assert r1.sheet == "MARCH 26"
    assert r1.row_idx == 2  # 1-based, after header
    assert r1.customer_name == "Acme Industrial Ltd"
    assert r1.mpan_or_mprn == "1234567890"
    assert r1.deal_value_gbp == 42000.0
    assert r1.supplier == "Pozitive"  # typo normalised
    assert r1.sales_agent == "Sammy"
    assert r1.category == "VERBAL_SALES_ERROR"
    assert r1.fix_required == "AMENDMENT_CALL"
    assert r1.status == "FIXED_AND_APPROVED"  # XLSX free text → enum
    assert r1.outcome == "FIXED_AND_SUBMITTED"
    assert r1.notes == "Coaching narrative..."

    r2 = rows[1]
    assert r2.supplier == "British Gas Lite"  # "BG Lite" canonicalised
    assert r2.category == "PROCESS_FAILURE"  # "priocess failure" typo
    assert r2.status == "DEAD"
    assert r2.dead_reason == "in_contract"  # extracted from Notes


def test_normalise_category_handles_typos():
    assert NORMALISE_CATEGORY("priocess failure") == "PROCESS_FAILURE"
    assert NORMALISE_CATEGORY("DOCUISGN ERROR") == "DOCUSIGN_ERROR"
    assert NORMALISE_CATEGORY("Pricing Error") == "PRICING_ERROR"
    assert NORMALISE_CATEGORY("Pricing Issue") == "PRICING_ISSUE"
    assert NORMALISE_CATEGORY("compliance error") == "COMPLIANCE_ERROR"
    assert NORMALISE_CATEGORY("VERBAL SALES ERROR") == "VERBAL_SALES_ERROR"
    assert NORMALISE_CATEGORY(None) is None


def test_normalise_status_maps_xlsx_to_enum():
    assert NORMALISE_STATUS("Fixed") == "FIXED"
    assert NORMALISE_STATUS("Fixed and approved") == "FIXED_AND_APPROVED"
    assert NORMALISE_STATUS("In progress") == "IN_PROGRESS"
    assert NORMALISE_STATUS("Not started") == "NOT_STARTED"
    assert NORMALISE_STATUS("Dead") == "DEAD"
    assert NORMALISE_STATUS(None) is None


import pytest
from app.import_xlsx_tracker import import_rows, ImportCounts


def test_import_rows_idempotent(test_db, tmp_path):
    p = tmp_path / "fixture.xlsx"
    p.write_bytes(_make_fixture_xlsx())
    rows = parse_xlsx(str(p))

    counts1 = import_rows(rows, db=test_db)
    assert counts1.customers_created == 2
    assert counts1.deals_created == 2
    assert counts1.rejections_created == 2
    assert counts1.calls_created == 2
    assert counts1.audit_log_created == 2

    # Re-run — nothing new written.
    counts2 = import_rows(rows, db=test_db)
    assert counts2.customers_created == 0
    assert counts2.deals_created == 0
    assert counts2.rejections_created == 0
    assert counts2.calls_created == 0
    assert counts2.audit_log_created == 0


def test_import_rows_writes_dead_reason(test_db, tmp_path):
    p = tmp_path / "fixture.xlsx"
    p.write_bytes(_make_fixture_xlsx())
    rows = parse_xlsx(str(p))
    import_rows(rows, db=test_db)

    from app.models import Rejection
    dead = test_db.query(Rejection).filter_by(status="DEAD").first()
    assert dead is not None
    assert dead.dead_reason == "in_contract"
    assert dead.outcome == "CUSTOMER_LOST"
