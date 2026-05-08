"""Sprint B5: XLSX importer stamps every written field with source='xlsx_import'.

This guards the AI-vs-human-vs-xlsx priority rule (B3): an AI re-run must
not silently clobber data the reviewer originally seeded from the tracker
xlsx. Stamping each write with ``xlsx_import`` makes ``can_overwrite()``
short-circuit later AI writes.
"""
import io

import openpyxl

from app.import_xlsx_tracker import import_rows, parse_xlsx
from app.models import CustomerDeal, Rejection


def _make_fixture_xlsx() -> bytes:
    """One MARCH 26 row with most columns populated so we can assert
    every stamped field. ``Notes`` is left empty so we can also assert the
    importer does NOT stamp fields it didn't write."""
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
        "FIXTURE Test Customer", "1012600130025", "2026-08-26", 58.94,
        "E.On Next Energy",
        "2026-04-15",
        "Test Agent",
        "Test rejection reason",
        "COMPLIANCE_ISSUE",
        "AMENDMENT_CALL",
        "Lewis",
        "In progress",
        "2026-04-16",
        "2026-04-17",
        None,
        None,
    ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_import_stamps_deal_field_sources(test_db, tmp_path):
    p = tmp_path / "fixture.xlsx"
    p.write_bytes(_make_fixture_xlsx())
    rows = parse_xlsx(str(p))
    import_rows(rows, db=test_db)

    deal = test_db.query(CustomerDeal).first()
    assert deal is not None, "no deal imported from fixture"
    fs = deal.field_sources or {}

    # Every populated column on the deal must be stamped xlsx_import.
    assert fs.get("customer_id") == "xlsx_import"
    assert fs.get("customer_name") == "xlsx_import"
    assert fs.get("supplier") == "xlsx_import"
    assert fs.get("mpan_or_mprn") == "xlsx_import"
    assert fs.get("expected_live_date") == "xlsx_import"
    assert fs.get("deal_value_gbp") == "xlsx_import"
    assert fs.get("status") == "xlsx_import"
    # rejection_id back-link gets stamped at rejection-creation time.
    assert fs.get("rejection_id") == "xlsx_import"


def test_xlsx_import_stamps_rejection_field_sources(test_db, tmp_path):
    p = tmp_path / "fixture.xlsx"
    p.write_bytes(_make_fixture_xlsx())
    rows = parse_xlsx(str(p))
    import_rows(rows, db=test_db)

    rej = test_db.query(Rejection).first()
    assert rej is not None, "no rejection imported from fixture"
    fs = rej.field_sources or {}

    # Every populated column on the rejection must be stamped xlsx_import.
    assert fs.get("customer_slug") == "xlsx_import"
    assert fs.get("supplier") == "xlsx_import"
    assert fs.get("sales_agent") == "xlsx_import"
    assert fs.get("category") == "xlsx_import"
    assert fs.get("rejection_reason") == "xlsx_import"
    assert fs.get("fix_required") == "xlsx_import"
    assert fs.get("status") == "xlsx_import"
    assert fs.get("rejected_at") == "xlsx_import"
    assert fs.get("deadline") == "xlsx_import"
    # call_id is the FK to the synthetic placeholder Call — not user-editable
    # data, so we deliberately don't stamp it.
    assert "call_id" not in fs


def test_xlsx_import_does_not_stamp_unwritten_fields(test_db, tmp_path):
    """Notes/outcome are None in the fixture row — importer must NOT stamp
    those keys (otherwise reviewer edits later would look like overwrites
    of an xlsx_import value when in fact the xlsx never wrote anything)."""
    p = tmp_path / "fixture.xlsx"
    p.write_bytes(_make_fixture_xlsx())
    rows = parse_xlsx(str(p))
    import_rows(rows, db=test_db)

    rej = test_db.query(Rejection).first()
    deal = test_db.query(CustomerDeal).first()
    assert rej is not None and deal is not None
    rej_fs = rej.field_sources or {}
    deal_fs = deal.field_sources or {}

    # Outcome/notes were None in the fixture row → must not be stamped.
    assert "outcome" not in rej_fs
    assert "outcome_narrative" not in rej_fs
    assert "resolved_at" not in rej_fs  # status="IN_PROGRESS", not DEAD/FIXED_AND_APPROVED
    assert "dead_reason" not in rej_fs   # row had no dead reason
    # Deal notes column similarly unwritten.
    assert "notes" not in deal_fs
