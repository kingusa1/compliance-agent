"""Structured-entity extractor for Pillar 2 (L2).

Two-phase strategy (per L2 design + digest §4):
  Phase 1 — deterministic regex over the transcript. Cheap, no LLM, and
            high-precision for keys with stable surface forms (MPAN,
            MPRN, £ values). Confidence 0.95, source='regex'.
  Phase 2 — LLM fallback (single OpenRouter call) ONLY for keys regex
            didn't find. Saves ~70% of LLM spend vs. extracting all
            keys via LLM. Confidence 0.7, source='llm'.

The keys we currently extract (per the L2 contract):
  mpan, mprn, deal_value_gbp, expected_live_date, commission, annual_cost.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.models import ExtractedEntity

log = logging.getLogger(__name__)

# Phase-1 regex set. First match per key wins (we want one row per key).
# Each pattern's first capture group is the value we persist.
#
# 2026-05-24 — `deal_value_gbp` previously required a literal "£" which
# missed common transcript shapes:
#   "67k" / "67 k"          (informal kilo)
#   "200 thousand" / "200 grand"
#   "1.5 million" / "1.5m"
# The new pattern catches all of those + the legacy `£` form. The
# `_money_to_gbp` helper in the writer (entities.py:_write_entities)
# normalises the captured value to a numeric GBP figure.
_REGEX_PATTERNS: dict[str, re.Pattern[str]] = {
    "mpan":           re.compile(r"(?:supply number|MPAN|meter point)[^0-9]{0,50}(\d{6,13})", re.IGNORECASE),
    "mprn":           re.compile(r"(?:gas|MPRN)[^0-9]{0,50}(\d{10})", re.IGNORECASE),
    "deal_value_gbp": re.compile(
        r"(£\s*[\d,]+(?:\.\d+)?(?:\s*[kKmM])?"               # £67,000 or £67k / £1.5m
        r"|\b[\d,]+(?:\.\d+)?\s*(?:k|K|million|mil|thousand|grand)\b"  # 67k, 67 thousand, 1.5 million
        r")"
    ),
    "commission":     re.compile(r"(\d+(?:\.\d+)?)\s*%[^.]{0,40}commission", re.IGNORECASE),
}

# Date / proximity-bound matchers handled separately (regex alone isn't
# enough — we need windowed matching around context cues).
_DATE_KEYWORDS = ("go live", "live date", "switch date")
_ANNUAL_KEYWORDS = ("annual", "per year", "cost")

_LLM_KEYS: tuple[str, ...] = (
    "mpan",
    "mprn",
    "deal_value_gbp",
    "expected_live_date",
    "commission",
    "annual_cost",
)


def _regex_pass(transcript: str) -> dict[str, str]:
    """Run the Phase-1 regex set; return {key: value} for the keys that hit."""
    found: dict[str, str] = {}
    for key, pat in _REGEX_PATTERNS.items():
        m = pat.search(transcript)
        if m and m.group(1):
            found[key] = m.group(1).strip()
    return found


def _proximity_match(transcript: str, value_pat: re.Pattern[str], keywords: tuple[str, ...], radius: int = 40) -> str | None:
    """Find a `value_pat` match within `radius` chars of any keyword.

    Used for `annual_cost` (any £-amount near 'annual'/'per year'/'cost')
    and the date matcher below. Returns the value (group 1) of the first
    qualifying match.
    """
    lower = transcript.lower()
    for m in value_pat.finditer(transcript):
        start = m.start()
        window = lower[max(0, start - radius): start + radius]
        if any(k in window for k in keywords):
            return m.group(1).strip()
    return None


def _expected_live_date(transcript: str) -> str | None:
    """Parse an ISO date near the go-live keywords.

    Uses dateutil if available (preferred for flexible formats); falls
    back to a tight ISO regex if not.
    """
    try:
        from dateutil import parser as dtparser  # type: ignore
    except Exception:  # pragma: no cover — dateutil is in requirements
        dtparser = None

    lower = transcript.lower()
    # Hunt around each keyword occurrence within ±30 chars (per design).
    for kw in _DATE_KEYWORDS:
        for match in re.finditer(re.escape(kw), lower):
            start = max(0, match.start() - 30)
            end = min(len(transcript), match.end() + 30)
            window = transcript[start:end]
            # Try dateutil's fuzzy parse first.
            if dtparser is not None:
                try:
                    parsed = dtparser.parse(window, fuzzy=True, default=None)
                    if parsed is not None:
                        return parsed.date().isoformat()
                except (ValueError, OverflowError, TypeError):
                    pass
            # Fallback: explicit YYYY-MM-DD or DD/MM/YYYY.
            iso = re.search(r"(\d{4}-\d{2}-\d{2})", window)
            if iso:
                return iso.group(1)
            uk = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", window)
            if uk:
                return uk.group(1)
    return None


async def _llm_fallback(transcript: str, already_found: dict[str, str]) -> dict[str, Any]:
    """Single LLM call to fill the keys regex didn't catch.

    Failures are swallowed and produce {} — extraction is best-effort
    and a transient OpenRouter blip must not break the finalize step.
    """
    missing = [k for k in _LLM_KEYS if k not in already_found]
    if not missing:
        return {}

    try:
        # Defer the import: avoids a circular dep when this module is
        # imported by tests that don't need LLM at all.
        from app.analysis import _call_llm  # type: ignore
    except Exception as exc:  # pragma: no cover
        log.warning("entities LLM fallback unavailable: %s", exc)
        return {}

    skeleton = {k: None for k in _LLM_KEYS}
    prompt = (
        "System: Extract structured energy deal data. Return JSON only: "
        + json.dumps(skeleton)
        + "\nUser: "
        + transcript[:4000]
        + "\nAlready extracted: "
        + json.dumps(already_found)
    )

    try:
        raw = await _call_llm(prompt, timeout=30.0)
    except Exception as exc:
        log.warning("entities LLM fallback failed: %s", exc)
        return {}

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("entities LLM fallback returned non-JSON")
        return {}

    if not isinstance(data, dict):
        return {}
    # Only keep keys we asked for and which weren't already filled.
    return {k: v for k, v in data.items() if k in missing and v not in (None, "", [])}


# L9: AssemblyAI entity_detection -> ExtractedEntity (source='word_match')
# confidence=0.85 sits between regex (0.95) and LLM (0.7). MPAN/MPRN are
# validated against their digit-count regexes before being accepted.
_MPAN_RE = re.compile(r"^\d{13}$")
_MPRN_RE = re.compile(r"^\d{8,10}$")


def _word_match_pass(aai_entities: list[dict] | None) -> dict[str, str]:
    """Map AssemblyAI entity_detection results onto our extraction keys.

    AssemblyAI entity_type values we care about:
      - person_name  -> we DON'T persist (PII; checkpoint_analyzer reads
                        raw transcript anyway).
      - location     -> address_postcode candidate (not in _LLM_KEYS yet,
                        so emitted as-is for downstream consumers).
      - quantity / number / numeric_value / occupation_or_role ->
                        validate against MPAN (13-digit) / MPRN (8-10 digit)
                        and assign accordingly.
    """
    if not aai_entities:
        return {}
    found: dict[str, str] = {}
    for ent in aai_entities:
        etype = (ent.get("entity_type") or "").lower()
        text = (ent.get("text") or "").strip()
        if not text:
            continue
        if etype in ("location",):
            # Surface location as a candidate; first match wins.
            found.setdefault("address_postcode", text)
        elif etype in ("quantity", "number", "numeric_value", "money_amount"):
            digits = re.sub(r"\D", "", text)
            if "mpan" not in found and _MPAN_RE.match(digits):
                found["mpan"] = digits
            elif "mprn" not in found and _MPRN_RE.match(digits):
                found["mprn"] = digits
    return found


async def extract_entities(
    call_id: str,
    transcript: str,
    assemblyai_metadata: dict | None = None,
) -> list[ExtractedEntity]:
    """Run Phase-1 regex + Phase-1.5 word_match + Phase-2 LLM fallback.

    Phase order matters for confidence weighting:
      regex (0.95) > word_match (0.85) > llm (0.7).
    `assemblyai_metadata` is the call's stored AAI metadata dict; if its
    `entities` array is present we mine it before falling through to LLM.
    Returns a list of unsaved `ExtractedEntity` rows.
    """
    if not transcript:
        return []

    found = _regex_pass(transcript)

    # Proximity-based regex extras (annual_cost + expected_live_date).
    annual = _proximity_match(transcript, re.compile(r"£\s*([\d,]+(?:\.\d{2})?)"), _ANNUAL_KEYWORDS)
    if annual:
        found["annual_cost"] = annual
    live_date = _expected_live_date(transcript)
    if live_date:
        found["expected_live_date"] = live_date

    rows: list[ExtractedEntity] = []
    for key, value in found.items():
        rows.append(
            ExtractedEntity(
                call_id=call_id,
                key=key,
                value=str(value),
                confidence=0.95,
                source="regex",
            )
        )

    # Phase-1.5: AssemblyAI entity_detection — only fill keys regex missed.
    aai_entities = (assemblyai_metadata or {}).get("entities") if isinstance(assemblyai_metadata, dict) else None
    word_extra = _word_match_pass(aai_entities)
    for key, value in word_extra.items():
        if key in found:
            continue
        rows.append(
            ExtractedEntity(
                call_id=call_id,
                key=key,
                value=str(value),
                confidence=0.85,
                source="word_match",
            )
        )
        found[key] = value  # block LLM from re-filling the same key

    # Phase-2: LLM fallback for missing keys.
    llm_extra = await _llm_fallback(transcript, found)
    for key, value in llm_extra.items():
        if value in (None, "", []):
            continue
        rows.append(
            ExtractedEntity(
                call_id=call_id,
                key=key,
                value=str(value),
                confidence=0.7,
                source="llm",
            )
        )

    return rows
