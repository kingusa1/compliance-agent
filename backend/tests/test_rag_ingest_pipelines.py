"""L10 ingest-pipeline tests.

The 5 new ORM classes (LoaChunk, SupplierDocChunk, GateChunk, RuleChunk,
RejectionChunk) don't exist on app.models yet — main session adds them
after Lane D ships. Until then every ingester must:

  - try/except the ORM import,
  - log a warning,
  - return 0 rows written.

Plus we verify the rejection-tracker anonymizer replaces customer names
with the [CUSTOMER] token before any chunk text is built.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# Force model registration so the test_db fixture's
# Base.metadata.create_all() actually emits the rag tables on SQLite.
import app.models  # noqa: F401

_ORM_NOW_PRESENT = (
    "ORM now present, see lane E migration b8c9d0e1f2a3 — "
    "the *_skips_when_orm_missing contract no longer applies."
)


# ─── Build-time pipelines ─────────────────────────────────────────────────

@pytest.mark.skip(reason=_ORM_NOW_PRESENT)
def test_ingest_gates_skips_when_orm_missing(test_db):
    from app.rag.ingest_gates import ingest_gates

    rows = ingest_gates(test_db)
    assert rows == 0


@pytest.mark.skip(reason=_ORM_NOW_PRESENT)
def test_ingest_rules_skips_when_orm_missing(test_db):
    from app.rag.ingest_rules import ingest_rules

    rows = ingest_rules(test_db)
    assert rows == 0


@pytest.mark.skip(reason=_ORM_NOW_PRESENT)
def test_ingest_rejections_skips_when_orm_missing(test_db):
    from app.rag.ingest_rejections import ingest_rejections

    rows = ingest_rejections(test_db)
    assert rows == 0


# ─── Catalog parsing (independent of ORM) ─────────────────────────────────

def test_ingest_rules_reads_catalog_json(test_db):
    """Even when ORM is missing, importing the module + accessing the catalog
    path should work (no hard FileNotFoundError)."""
    from app.rag import ingest_rules as mod

    assert mod.RULES_CATALOG_PATH.exists(), \
        f"rules_catalog.json must live at {mod.RULES_CATALOG_PATH}"
    rules = json.loads(mod.RULES_CATALOG_PATH.read_text())
    assert isinstance(rules, list)
    assert len(rules) >= 1
    # Every rule has the keys we'll embed.
    for r in rules[:5]:
        assert "name" in r
        assert "id" in r


# ─── PII anonymization for rejections ─────────────────────────────────────

def test_ingest_rejections_anonymizes_pii():
    """Rejection-tracker rows get [CUSTOMER] in place of the customer-name
    column before being chunked. Tested directly on the row→chunk helper so
    we don't need the ORM class to be present.
    """
    from app.rag.ingest_rejections import _row_to_chunk

    sample_row = {
        "Customer Name": "KDMAC Limited",
        "MPAN / MPRN": "1712943483620",
        "Supplier": "British Gas Lite",
        "Sales Agent": "Ethan Leech",
        "Rejection Reason": "wrong name on the account",
        "Category": "ADMIN ERROR",
        "Fix Required": "do contract with kevin",
    }

    text, meta = _row_to_chunk(sample_row)
    assert "[CUSTOMER]" in text
    assert "KDMAC Limited" not in text
    # Operational data is preserved.
    assert meta["agent_name"] == "Ethan Leech"
    assert meta["supplier"] == "British Gas Lite"
    assert meta["category"] == "ADMIN ERROR"
    assert meta["fix"] == "do contract with kevin"


def test_ingest_rejections_table_parser_finds_rows():
    """The markdown-table parser should pick up the rejection-tracker
    rows when the digest doc exists in the project tree."""
    from app.rag.ingest_rejections import _parse_tables, _resolve_doc_path

    doc = _resolve_doc_path()
    if doc is None:
        # Document is optional in some environments; skip cleanly.
        import pytest

        pytest.skip("rejection-tracker digest not present in this checkout")

    md = doc.read_text(errors="ignore")
    rows = _parse_tables(md)
    assert len(rows) > 0
    # First-row sanity: at least one cell containing 'Customer' header was matched.
    first = rows[0]
    has_customer_col = any("customer" in k.lower() for k in first.keys())
    assert has_customer_col


# ─── LOA / supplier_docs no-op when ORM missing ──────────────────────────

@pytest.mark.skip(reason=_ORM_NOW_PRESENT)
def test_ingest_loa_skips_when_orm_missing(test_db):
    from app.rag.ingest_loa import ingest_loa

    rows = ingest_loa("E.ON Next Energy", "Sample LOA preamble text.", test_db)
    assert rows == 0


@pytest.mark.skip(reason=_ORM_NOW_PRESENT)
def test_ingest_supplier_docs_skips_when_orm_missing(test_db):
    from app.rag.ingest_supplier_docs import ingest_supplier_docs

    rows = ingest_supplier_docs(
        "British Gas Lite", "contract", "Sample contract terms.", test_db
    )
    assert rows == 0
