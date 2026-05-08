"""D2 — Tracker XLSX (Compliance tracker example.xlsx) ingest tests.

The MARCH 26 + APRIL 2026 sheets should each produce one chunk per
non-empty Customer-Name row, namespaced via the ``[source=tracker:...]``
marker on the chunk text. ``_embed`` is patched so the test stays
hermetic (no OpenAI calls).

The fixture xlsx mirrors the real 16-column MARCH 26 schema. Uses
``test_db`` (the repo's standard fixture from conftest.py — there is no
``db`` fixture in this codebase).
"""
from __future__ import annotations

from unittest.mock import patch

# Force model registration so the test_db fixture's
# Base.metadata.create_all() actually emits rejection_chunks on SQLite.
import app.models  # noqa: F401
from app.rag.ingest_rejections import ingest_tracker_xlsx


def test_tracker_ingest_produces_one_chunk_per_row(test_db, tmp_path):
    xlsx = tmp_path / "tracker.xlsx"
    # Build a 5-row fixture XLSX with the same MARCH 26 schema.
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MARCH 26"
    ws.append([
        "Customer Name", "MPAN / MPRN", "Expected Live date", "Deal Value (£)",
        "Supplier", "Rejected at", "Sales Agent", "Rejection Reason", "Category",
        "Fix Required", "Fixed BY", "Status", "Last Action Date", "Deadline",
        "Outcome", "Notes",
    ])
    for i in range(5):
        ws.append([
            f"Customer {i}", "1234567890", "2026-04-30", 1000.0, "E.ON Next",
            "2026-04-15", "Sammy", f"Reason {i}", "VERBAL SALES ERROR",
            "AMENDMENT_CALL", "Lewis", "FIXED", "2026-04-16", "2026-04-17",
            "FIXED_AND_SUBMITTED", "narrative",
        ])
    wb.save(xlsx)

    with patch("app.rag.ingest_rejections._embed", return_value=[0.1] * 1536):
        n = ingest_tracker_xlsx(test_db, xlsx_path=str(xlsx))
    assert n == 5
