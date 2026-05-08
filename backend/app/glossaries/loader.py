"""Merge base + supplier glossaries into the AssemblyAI word_boost list."""
from __future__ import annotations

from app.glossaries.suppliers import SUPPLIER_TERMS
from app.glossaries.watt_terms import WATT_BASE_TERMS


def load_supplier_glossary(supplier: str | None) -> list[str]:
    """Return the merged base + supplier-specific word_boost list.

    AssemblyAI's word_boost is case-insensitive, so casing differences
    between caller-supplied supplier names and the SUPPLIER_TERMS keys
    don't affect the runtime behaviour — but we still de-duplicate to
    keep the request body small. Returns base terms only when supplier
    is None or unknown.
    """
    terms: list[str] = list(WATT_BASE_TERMS)
    if supplier and supplier in SUPPLIER_TERMS:
        terms.extend(SUPPLIER_TERMS[supplier])
    # Dedupe while preserving deterministic-ish order (sorted is fine —
    # AssemblyAI doesn't care about order, and tests can compare sets).
    return sorted(set(terms))
