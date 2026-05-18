"""Supplier-name canonicalization for free-text inputs at the intake layer.

This file owns the L7-layer alias map. The pipeline's own extraction layer
(``app.extraction``) has its own canonicalization for transcript-detected
supplier mentions; the two are intentionally separate because intake-time
manual entries are typed by reviewers (different distribution of typos and
abbreviations) than what falls out of LLM extraction.

CRITICAL DECISION (gates Step 3 + extraction-pass-2 verdict):
``E.ON`` and ``E.ON Next Energy`` are DISTINCT canonical keys with
different LOA models. ``canonicalize`` MUST NOT collapse "E.On Next
Energy Ltd" → "E.ON". They are distinct, separate from E.ON.
"""

from __future__ import annotations

from typing import Dict, List

from app.intake.payload_schema import SupplierEnum


# Canonical key list — order matches SUPPLIER_DISPLAY_ORDER in payload_schema.
SUPPLIER_KEYS: List[str] = [s.value for s in SupplierEnum]


# Alias map — observed strings → canonical key. Keys are lower-cased here so
# we can do a simple dict lookup after normalizing the input.
#
# DO NOT add "e.on next" → "E.ON" or any equivalent; E.ON Next Energy is
# distinct, separate from E.ON. Aliases for E.ON Next must canonicalize to
# ``E.ON Next Energy``, never ``E.ON``.
SUPPLIER_ALIASES: Dict[str, str] = {
    # E.ON (bundled-LOA model) — only the bare-E.ON variants.
    "e.on": SupplierEnum.EON.value,
    "eon": SupplierEnum.EON.value,
    "e on": SupplierEnum.EON.value,
    "e.on energy solutions": SupplierEnum.EON.value,
    "e.on energy solutions ltd": SupplierEnum.EON.value,
    "eon energy solutions": SupplierEnum.EON.value,
    # E.ON Next Energy (standalone-LOA model) — distinct from E.ON above.
    "e.on next": SupplierEnum.EON_NEXT.value,
    "e.on next energy": SupplierEnum.EON_NEXT.value,
    "e.on next energy ltd": SupplierEnum.EON_NEXT.value,
    "eon next": SupplierEnum.EON_NEXT.value,
    "eon next energy": SupplierEnum.EON_NEXT.value,
    "e on next": SupplierEnum.EON_NEXT.value,
    "e on next energy": SupplierEnum.EON_NEXT.value,
    # 2026-05-18 Westbury audit: ASR collapses the space, gives "eonext".
    "eonext": SupplierEnum.EON_NEXT.value,
    "eonext energy": SupplierEnum.EON_NEXT.value,
    # British Gas — four distinct sub-products.
    "british gas core": SupplierEnum.BG_CORE.value,
    "bg core": SupplierEnum.BG_CORE.value,
    "british gas lite": SupplierEnum.BG_LITE.value,
    "british gas lite ltd": SupplierEnum.BG_LITE.value,
    "bg lite": SupplierEnum.BG_LITE.value,
    "bgl": SupplierEnum.BG_LITE.value,
    "british gas business": SupplierEnum.BG_BUSINESS.value,
    "british gas buisness": SupplierEnum.BG_BUSINESS.value,  # observed typo
    "bg business": SupplierEnum.BG_BUSINESS.value,
    "bgb": SupplierEnum.BG_BUSINESS.value,
    "british gas trading": SupplierEnum.BG_TRADING.value,
    "british gas trading ltd": SupplierEnum.BG_TRADING.value,
    "bg trading": SupplierEnum.BG_TRADING.value,
    # Other matrix suppliers.
    "pozitive": SupplierEnum.POZITIVE.value,
    "pozitive energy": SupplierEnum.POZITIVE.value,
    "yu energy": SupplierEnum.YU_ENERGY.value,
    "yu energy retail ltd": SupplierEnum.YU_ENERGY.value,
    "smartest": SupplierEnum.SMARTEST.value,
    "smartest energy": SupplierEnum.SMARTEST.value,
    "affect": SupplierEnum.AFFECT.value,
    "affect energy": SupplierEnum.AFFECT.value,
    "britannia": SupplierEnum.BRITANNIA.value,
    "britannia gas": SupplierEnum.BRITANNIA.value,
    "united gas & power": SupplierEnum.UNITED_GP.value,
    "united gas and power": SupplierEnum.UNITED_GP.value,
    "ugp": SupplierEnum.UNITED_GP.value,
    # Out-of-matrix.
    "totalenergies": SupplierEnum.TOTAL_ENERGIES.value,
    "totalenergies gas & power ltd": SupplierEnum.TOTAL_ENERGIES.value,
    "total gas and power ltd": SupplierEnum.TOTAL_ENERGIES.value,
    "total gas and power": SupplierEnum.TOTAL_ENERGIES.value,
}


def _normalize(s: str) -> str:
    """Lower-case and collapse internal whitespace; preserve punctuation
    (E.ON vs EON matters for matching)."""
    return " ".join(s.lower().split())


def canonicalize(raw: str | None) -> str:
    """Map a free-text supplier name to one of the 13 canonical keys.

    Returns the matching :class:`SupplierEnum` value if a mapping exists,
    otherwise falls back to ``"Other"``. Never raises — unknown names
    bucket to ``Other`` so the pipeline can keep flowing while the reviewer
    fixes the typo on the next pass.

    Multi-step match:
      1. Exact lookup against ``SUPPLIER_ALIASES`` (the fast path).
      2. Exact match against the canonical key itself
         (e.g. ``"E.ON Next Energy"`` typed verbatim).
      3. Substring containment — longest match wins so "E.ON Next" beats
         "E.ON" when both could match the same input.
    """
    if not raw:
        return SupplierEnum.OTHER.value
    norm = _normalize(raw)
    # 1) exact alias hit
    if norm in SUPPLIER_ALIASES:
        return SUPPLIER_ALIASES[norm]
    # 2) exact canonical name match (case-insensitive)
    for key in SUPPLIER_KEYS:
        if norm == key.lower():
            return key
    # 3) longest substring containment, biased toward more-specific keys.
    # Sort aliases by descending length so "e.on next energy" wins over
    # "e.on" for input "E.On Next Energy Ltd".
    candidates = sorted(SUPPLIER_ALIASES.items(), key=lambda kv: -len(kv[0]))
    for alias, canonical in candidates:
        if alias in norm:
            return canonical
    return SupplierEnum.OTHER.value
