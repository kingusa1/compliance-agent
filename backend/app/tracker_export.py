"""XLSX export — regenerates Watt's `Compliance tracker example.xlsx`
schema as a downloadable .xlsx file. Reviewer can fall back to their
original workflow whenever they want.
"""
from __future__ import annotations

import io
from datetime import datetime

import openpyxl
from sqlalchemy.orm import Session

from app.tracker_aggregator import build_tracker_rows


_HEADERS = [
    "Customer Name",
    "MPAN / MPRN",
    "Expected Live date ",  # trailing space matches source
    "Deal Value (£)",
    "Supplier",
    "Rejected at",
    "Sales Agent",
    "Rejection Reason",
    "Category",
    "Fix Required",
    "Fixed BY ",  # trailing space matches source
    "Status",
    "Last Action Date",
    "Deadline",
    "Outcome",
    "Notes",
]


def _row_values(row: dict) -> list:
    def _d(v):
        if isinstance(v, datetime):
            return v.replace(tzinfo=None)
        return v
    return [
        row.get("customer_name"),
        row.get("mpan_mprn"),
        _d(row.get("expected_live_date")),
        row.get("deal_value_gbp"),
        row.get("supplier"),
        _d(row.get("rejected_at")),
        row.get("sales_agent"),
        row.get("rejection_reason"),
        row.get("category"),
        row.get("fix_required"),
        row.get("fix_assignee_id"),
        row.get("status"),
        _d(row.get("last_action_date")),
        _d(row.get("deadline")),
        row.get("outcome"),
        # XLSX col P = "Notes". Sourced from outcome_narrative
        # post 2026-05-14 aggregator rename.
        row.get("outcome_narrative"),
    ]


def build_xlsx(db: Session) -> bytes:
    wb = openpyxl.Workbook()
    sheet_specs = [
        ("MARCH 26",         build_tracker_rows(db, tab="active", month=None)),
        ("APRIL 2026",       []),  # placeholder — month-specific rows would go here
        ("FIXED REJECTIONS", build_tracker_rows(db, tab="fixed")),
        ("DEAD REJECTIONS",  build_tracker_rows(db, tab="dead")),
    ]
    first = True
    for name, rows in sheet_specs:
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        first = False
        ws.append(_HEADERS)
        for row in rows:
            ws.append(_row_values(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
