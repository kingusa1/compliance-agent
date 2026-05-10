"""Auto-detect supplier + script_type + call_class from a transcript.

Implements the deterministic keyword-match strategy from
`.planning/phase2-analysis/D-supplier-scripts.md` §5. If no signal
fires, the caller can fall back to vector-similarity search via the
existing RAG layer (see app/rag/embed.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.watt_compliance.taxonomy import CallClass, ScriptType, Supplier


# Compile once at import time — these are cheap but called per transcript.
_SUPPLIER_PATTERNS: list[tuple[Supplier, re.Pattern[str]]] = [
    (Supplier.BGL, re.compile(
        r"\b(?:british\s+gas\s+lite|bgl)\b|britishgaslite\.co\.uk|webchat\s+only", re.I)),
    (Supplier.BRITISH_GAS, re.compile(
        r"\b(?:british\s+gas|scottish\s+gas)\b(?!\s+lite)", re.I)),
    (Supplier.EDF, re.compile(
        r"\b(?:edf|fixed\s+for\s+business\s+online|h3083)\b|edfenergy\.com", re.I)),
    (Supplier.EON_NEXT, re.compile(
        # Catches "E.ON Next", "EON Next", "E ON Next", "E. ON Next" — voice
        # transcripts produce all of these depending on pronunciation.
        r"\b(?:e\.?\s*on\s+next|eon\s+next)\b|eonnext\.com|\{\{brokerage", re.I)),
    (Supplier.POZITIVE, re.compile(
        r"\bpozitive\b|pozitive\.energy", re.I)),
    (Supplier.SCOTTISH_POWER, re.compile(
        r"\bscottish\s+power\b|scottishpower\.co\.uk|0345\s*058\s*0002|for\s+business\s+tariff", re.I)),
]

_CALL_CLASS_PATTERNS: list[tuple[CallClass, re.Pattern[str]]] = [
    (CallClass.HH, re.compile(r"\bhalf[\s-]hourly\b|\bHH\b|\bASC\s+charge\b", re.I)),
    (CallClass.NHH, re.compile(r"\b(?:nhh|non[\s-]half[\s-]hourly|AMR)\b", re.I)),
    (CallClass.DUAL, re.compile(r"\bMPAN\b.*\bMPRN\b|\bMPRN\b.*\bMPAN\b|\bgas\s+and\s+electricity\b|\bdual\s+fuel\b", re.I | re.S)),
    (CallClass.GAS, re.compile(r"\bMPRN\b|\bgas\b", re.I)),
    (CallClass.ELEC, re.compile(r"\bMPAN\b|\belectricity\b|\belec\b|\bkVA\b", re.I)),
]

_SCRIPT_TYPE_PATTERNS: list[tuple[ScriptType, re.Pattern[str]]] = [
    (ScriptType.LOA, re.compile(
        r"\bletter\s+of\s+authority\b|\bLOA\b|authorise\s+(?:watt|us)\s+to\s+(?:obtain|act|negotiate)|"
        r"act\s+on\s+your\s+behalf|termination\s+notice", re.I)),
    (ScriptType.RENEWAL, re.compile(
        r"\brenewal\b|\brenew\s+your\b|arrange\s+the\s+renewal|current\s+(?:contract\s+)?ends?", re.I)),
    (ScriptType.UPGRADE, re.compile(
        r"\bupgrade\b|\bdeemed\b|\bbackdat", re.I)),
    (ScriptType.AMENDMENT, re.compile(
        r"\bamendment\b|amendment\s+(?:script|call)|please\s+do\s+an\s+amendment", re.I)),
    (ScriptType.PREAMBLE, re.compile(
        r"\bpreamble\b|preamble\s+script", re.I)),
    (ScriptType.ACQUISITION, re.compile(
        r"\bacquisition\b|new\s+contract|arrange\s+the\s+switch|new\s+supply\s+agreement", re.I)),
]


@dataclass(frozen=True)
class DetectionResult:
    supplier: Supplier | None
    script_type: ScriptType | None
    call_class: CallClass | None
    # Hit details for audit logging — useful when reviewers question the call.
    supplier_evidence: str | None = None
    script_type_evidence: str | None = None
    call_class_evidence: str | None = None


def detect(transcript: str) -> DetectionResult:
    """Run all three detectors over the transcript."""
    if not transcript:
        return DetectionResult(None, None, None)

    sup, sup_ev = _first_match(transcript, _SUPPLIER_PATTERNS)
    cc, cc_ev = _first_match(transcript, _CALL_CLASS_PATTERNS)
    st, st_ev = _first_match(transcript, _SCRIPT_TYPE_PATTERNS)

    return DetectionResult(
        supplier=sup,
        script_type=st,
        call_class=cc,
        supplier_evidence=sup_ev,
        script_type_evidence=st_ev,
        call_class_evidence=cc_ev,
    )


def _first_match(text: str, patterns: list[tuple]) -> tuple[object | None, str | None]:
    """Return the first (enum, matched_text) tuple, or (None, None)."""
    for value, pat in patterns:
        m = pat.search(text)
        if m:
            return value, m.group(0)
    return None, None


def supplier_namespace(supplier: Supplier, script_type: ScriptType,
                       call_class: CallClass) -> str:
    """Compute the pgvector / RAG namespace per D-supplier-scripts.md §4."""
    return f"scripts:{supplier.value}:{script_type.value}:{call_class.value}"


# Tracker / CRM-input alias map → canonical Supplier enum.
# Source: supplier-spec-handout.pdf §3.5a — variants observed in the
# operations tracker that today's three alias maps don't all capture.
# Canonicalise BEFORE the (supplier × call_type) matrix lookup.
_SUPPLIER_ALIAS_MAP: dict[str, Supplier] = {
    # British Gas Lite (BG Lite / BGL)
    "bgl": Supplier.BGL,
    "bg lite": Supplier.BGL,
    "british gas lite": Supplier.BGL,
    # British Gas (Core / Business)
    "bg core": Supplier.BRITISH_GAS,
    "bg business": Supplier.BRITISH_GAS,
    "british gas core": Supplier.BRITISH_GAS,
    "british gas business": Supplier.BRITISH_GAS,
    "british gas buisness": Supplier.BRITISH_GAS,  # tracker typo
    "british gas": Supplier.BRITISH_GAS,
    # E.ON Next casing variants
    "eon": Supplier.EON_NEXT,
    "e.on": Supplier.EON_NEXT,
    "e on": Supplier.EON_NEXT,
    "eon next": Supplier.EON_NEXT,
    "e.on next": Supplier.EON_NEXT,
    "e.on next energy": Supplier.EON_NEXT,
    "e.on energy solutions": Supplier.EON_NEXT,
    "e.on energy solutions ltd": Supplier.EON_NEXT,
    # EDF
    "edf": Supplier.EDF,
    "edf energy": Supplier.EDF,
    # Pozitive
    "pozitive": Supplier.POZITIVE,
    "pozitive energy": Supplier.POZITIVE,
    # Scottish Power
    "sp": Supplier.SCOTTISH_POWER,
    "scottish power": Supplier.SCOTTISH_POWER,
    "scottishpower": Supplier.SCOTTISH_POWER,
}


def canonicalize_supplier(raw: str | None) -> Supplier | None:
    """Map a free-text supplier name (LLM output, CRM string, tracker cell)
    to the canonical ``Supplier`` enum.

    Returns ``None`` for unknown / empty values so the caller can route to
    the universal-floor fallback. Whitespace + casing + punctuation are
    normalised before lookup.
    """
    if not raw:
        return None
    key = re.sub(r"\s+", " ", raw.strip().lower())
    key = key.replace(".", ".").replace(",", "").replace("ltd", "").strip()
    # Try exact, then progressive fallback strips
    if key in _SUPPLIER_ALIAS_MAP:
        return _SUPPLIER_ALIAS_MAP[key]
    # Trim trailing single words ("ltd", "limited", "energy", "uk")
    parts = key.split()
    while parts:
        cand = " ".join(parts)
        if cand in _SUPPLIER_ALIAS_MAP:
            return _SUPPLIER_ALIAS_MAP[cand]
        parts.pop()
    return None
