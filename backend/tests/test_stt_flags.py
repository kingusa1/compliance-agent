"""L9 STT-enhancement gates.

Five gates per the L9 contract success_criteria:
  1. word_boost merges supplier-specific terms onto the WATT base.
  2. Unknown supplier falls back to base terms only.
  3. PII redaction policy list is the UK-context 5-tuple.
  4. sentiment_analysis + entity_detection flags are still True (preserved).
  5. AssemblyAI entity_detection results flow into ExtractedEntity rows
     with source='word_match' and confidence=0.85.

Tests stub the AssemblyAI submit payload by inspecting the function source
+ the imported module — no live API call. Test 5 imports `extract_entities`
the same way `test_extraction.py` does (lightweight ORM stand-ins).
"""
from __future__ import annotations

import asyncio
import inspect

import app.models as _models
from app.assemblyai_transcription import PII_POLICIES, transcribe_audio_assemblyai
from app.glossaries.loader import load_supplier_glossary
from app.glossaries.suppliers import SUPPLIER_TERMS
from app.glossaries.watt_terms import WATT_BASE_TERMS


# Mirror the stub-injection from test_extraction.py so this file can be
# run in isolation before the rest of the suite warms up the modules.
class _Row:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        if not hasattr(self, "id"):
            self.id = None


for _name in ("CallSegment", "Flag", "ExtractedEntity"):
    if not hasattr(_models, _name):
        setattr(_models, _name, type(_name, (_Row,), {}))

from app.extraction.entities import extract_entities  # noqa: E402
from app.models import ExtractedEntity  # noqa: E402


# ─── Gate 1: word_boost loaded for known supplier ───────────────────────────
def test_word_boost_loaded_for_supplier():
    terms = load_supplier_glossary("E.ON Next Energy")
    # Supplier-specific terms present
    for t in SUPPLIER_TERMS["E.ON Next Energy"]:
        assert t in terms, f"missing supplier term: {t}"
    # Base terms still present
    for t in ("LOA", "MOP", "MPAN", "OFGEM"):
        assert t in terms, f"missing base term: {t}"


# ─── Gate 2: unknown supplier falls back to base only ──────────────────────
def test_word_boost_unknown_supplier_falls_back():
    terms = load_supplier_glossary("UnknownSup")
    base_set = set(WATT_BASE_TERMS)
    assert set(terms) == base_set, "unknown supplier must yield base only"
    # And None is the same fallback path.
    assert set(load_supplier_glossary(None)) == base_set


# ─── Gate 3: PII policy 4-tuple is the UK context set ──────────────────────
# 2026-05-18: `person_name` removed. Internal compliance review tool —
# the system audits WHO said WHAT to WHOM, so redacting agent + customer
# names actively defeats the workflow. Payment / banking / contact-PII
# redaction remains.
def test_pii_policies_present():
    assert set(PII_POLICIES) == {
        "phone_number",
        "email_address",
        "credit_card_number",
        "banking_information",
    }, f"unexpected PII policy set: {PII_POLICIES}"


def test_pii_policies_excludes_person_name() -> None:
    """Defence-in-depth: assert the explicit non-inclusion of person_name
    so a future refactor can't silently re-add it without tripping CI."""
    assert "person_name" not in set(PII_POLICIES)


# ─── Gate 4: sentiment + entity flags preserved in submit payload ──────────
def test_sentiment_entity_flags_enabled():
    """The transcribe function builds a JSON submit_payload — assert the
    AssemblyAI feature flags are still True (we did not regress them) and
    that the L9 additions appear in the source verbatim."""
    src = inspect.getsource(transcribe_audio_assemblyai)
    # Existing intelligence flags preserved
    assert '"sentiment_analysis": True' in src
    assert '"entity_detection": True' in src
    # L9 additions — universal-3-pro uses `keyterms_prompt` instead of
    # the legacy `word_boost`/`boost_param` pair (AAI 400 contract:
    # '"word_boost" is not compatible with universal-3-pro').
    assert '"keyterms_prompt"' in src
    assert '"redact_pii": True' in src
    assert '"redact_pii_audio": False' in src
    assert '"redact_pii_policies": PII_POLICIES' in src


# ─── Gate 5: word_match source flows from AAI metadata into rows ───────────
def test_word_match_entity_source_extracted():
    """Given AssemblyAI metadata with entities, extract_entities must emit
    ExtractedEntity rows with source='word_match' and confidence=0.85
    for the keys regex didn't already cover."""
    transcript = "thanks for confirming, that completes the call"  # no MPAN regex hit
    aai_metadata = {
        "entities": [
            {"entity_type": "quantity", "text": "1234567890123", "start": 0, "end": 1000},  # 13 digits → MPAN
            {"entity_type": "location", "text": "M1 4AB", "start": 1000, "end": 2000},
        ],
    }

    rows = asyncio.run(
        extract_entities(
            call_id="call-l9",
            transcript=transcript,
            assemblyai_metadata=aai_metadata,
        )
    )

    word_rows = [r for r in rows if getattr(r, "source", None) == "word_match"]
    assert word_rows, (
        "expected at least one word_match row, "
        f"got sources: {[getattr(r, 'source', None) for r in rows]}"
    )
    keys = {r.key for r in word_rows}
    assert "mpan" in keys, f"mpan must be word_match-extracted, got keys: {keys}"
    for r in word_rows:
        assert isinstance(r, ExtractedEntity)
        assert r.confidence == 0.85
        assert r.call_id == "call-l9"
