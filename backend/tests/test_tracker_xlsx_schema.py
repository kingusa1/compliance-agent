"""Lock down the tracker XLSX schema against Watt's
``Compliance tracker example.xlsx``. This test ensures any future
Rejection-model change that drops a column or reorders headers
trips CI immediately.

The expected headers match the source XLSX byte-for-byte (including
the trailing spaces on "Expected Live date " and "Fixed BY ").
"""
from __future__ import annotations

import openpyxl

from app.tracker_export import _HEADERS


# Sourced verbatim from `Compliance tracker example.xlsx` row 1
# (extracted via backend/scripts/extract_phase2_xlsx.py).
EXPECTED_HEADERS = [
    "Customer Name",
    "MPAN / MPRN",
    "Expected Live date ",
    "Deal Value (£)",
    "Supplier",
    "Rejected at",
    "Sales Agent",
    "Rejection Reason",
    "Category",
    "Fix Required",
    "Fixed BY ",
    "Status",
    "Last Action Date",
    "Deadline",
    "Outcome",
    "Notes",
]


def test_export_headers_match_source_xlsx_byte_for_byte():
    assert _HEADERS == EXPECTED_HEADERS, (
        "tracker_export._HEADERS drifted from the source "
        "Compliance tracker example.xlsx schema"
    )


def test_export_header_count_is_16():
    assert len(_HEADERS) == 16


def test_trailing_space_quirks_preserved():
    """The source XLSX has trailing spaces on two headers; preserving
    them keeps round-tripping clean for users who paste exports back
    into the original sheet."""
    assert _HEADERS[2] == "Expected Live date "  # trailing space
    assert _HEADERS[10] == "Fixed BY "            # trailing space


def test_can_round_trip_through_openpyxl():
    """Smoke — openpyxl accepts our header list and produces a workbook
    that can be re-read."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_HEADERS)
    import io
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    rb = openpyxl.load_workbook(buf)
    rs = rb.active
    assert [c.value for c in rs[1]] == _HEADERS
