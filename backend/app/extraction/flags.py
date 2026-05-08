"""Checkpoint-results -> flags translator (Pillar 2 / L2).

Walks the per-checkpoint results stored on the call, converts every
non-passing entry into a `Flag` ORM row, and joins each flag to its
nearest detected segment so reviewers can jump straight to the relevant
section of the transcript. Severity + risk_tag come from the rules
catalog and `risk_tag_rules.RISK_TAG_MAP`.

Also implements the **structural missing-stage detector**: if a closer
call has no `verbal` segment, we emit a synthetic CRITICAL flag with
rule_id `STRUCTURAL-MISSING-VERBAL` (per gates Step 4 + L2 design).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.extraction.pricing import extract_rates
from app.models import CallSegment, Flag, Script
from app.risk_tag_rules import RISK_TAG_MAP

# Tolerance for pricing-mismatch comparator. Anything ≤ this (in pence)
# is rounding noise and not flagged. Per W3.A spec.
_PRICING_TOLERANCE_P = 0.1

log = logging.getLogger(__name__)

# W3.C — vulnerable-customer rule_id. The actual Flag row is built by
# ``app.extraction.vulnerability.detect_vulnerability`` and appended in
# ``pipeline._write_extraction_outputs``; we expose the constant here so
# the rule_id is discoverable next to the other flag rule_ids and so
# tests can ``from app.extraction.flags import VULNERABLE_CUSTOMER_RULE_ID``.
VULNERABLE_CUSTOMER_RULE_ID = "VULNERABLE_CUSTOMER"

# Resolve catalog path relative to this file so test runners and the
# uvicorn process both read the same canonical seed.
_CATALOG_PATH = Path(__file__).resolve().parent.parent / "rules_catalog.json"


def _load_catalog() -> list[dict[str, Any]]:
    try:
        with _CATALOG_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log.warning("rules_catalog load failed: %s", exc)
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.values())
    return []


_CATALOG: list[dict[str, Any]] = _load_catalog()


def _name_to_rule(name: str | None) -> dict[str, Any] | None:
    """Best-effort match of a checkpoint name to a catalog rule.

    Match strategy (in order):
      1. exact rule.id match
      2. exact rule.name match
      3. case-insensitive substring of rule.name
    """
    if not name or not _CATALOG:
        return None
    for rule in _CATALOG:
        if rule.get("id") == name:
            return rule
    for rule in _CATALOG:
        if rule.get("name") == name:
            return rule
    needle = name.lower()
    for rule in _CATALOG:
        rname = rule.get("name", "").lower()
        if needle and rname and (needle in rname or rname in needle):
            return rule
    return None


def _nearest_segment(segments: list[CallSegment], cp: dict[str, Any]) -> CallSegment | None:
    """Find the segment whose [start_s, end_s] contains the checkpoint's
    timestamp; fall back to the closest segment by midpoint distance.

    We accept any of `start_s`, `start_ts`, `word_start_s`, or
    `word_index` on the checkpoint result — different upstream callers
    have used different keys over the v1→v2 transition.
    """
    if not segments:
        return None

    # Pull a timestamp anchor off the checkpoint.
    anchor = (
        cp.get("start_s")
        or cp.get("start_ts")
        or cp.get("word_start_s")
        or cp.get("ts")
    )

    if anchor is None:
        return segments[0]

    try:
        a = float(anchor)
    except (TypeError, ValueError):
        return segments[0]

    # Containing segment first.
    for seg in segments:
        s = seg.start_s if seg.start_s is not None else 0.0
        e = seg.end_s if seg.end_s is not None else s
        if s <= a <= e:
            return seg

    # Otherwise nearest by midpoint distance.
    def mid(seg: CallSegment) -> float:
        s = seg.start_s if seg.start_s is not None else 0.0
        e = seg.end_s if seg.end_s is not None else s
        return (s + e) / 2.0

    return min(segments, key=lambda seg: abs(mid(seg) - a))


def derive_flags(
    call_id: str,
    checkpoint_results: list[dict[str, Any]],
    segments: list[CallSegment],
    script: Script | None,
    call_type: str | None = None,
) -> list[Flag]:
    """Translate non-passing checkpoints into Flag ORM rows.

    Output is deterministic for a given input — running this function
    twice with identical args produces the same row count and content,
    which is what the idempotent finalize writer relies on.
    """
    flags: list[Flag] = []

    # 1) One flag per non-passing checkpoint.
    for cp in checkpoint_results or []:
        if not isinstance(cp, dict):
            continue
        status = (cp.get("status") or cp.get("verdict") or "").lower()
        passed = cp.get("passed")
        if status == "pass" or passed is True:
            continue
        # Skip explicitly-skipped checkpoints (no verdict to flag).
        if status in {"skip", "skipped", "n/a"}:
            continue

        cp_name = cp.get("name") or cp.get("checkpoint") or cp.get("rule_text") or ""
        rule = _name_to_rule(cp_name)
        rule_id = (rule or {}).get("id") or cp_name or "UNKNOWN"
        category = (rule or {}).get("category") or "disclosure"
        severity = (rule or {}).get("severity") or "high"

        # Cap severity to the locked enum so the DB CHECK never fails.
        if severity not in {"critical", "high", "medium"}:
            severity = "high"

        nearest = _nearest_segment(segments, cp)
        risk_tag = RISK_TAG_MAP.get((category, severity))

        flags.append(
            Flag(
                call_id=call_id,
                rule_id=rule_id,
                severity=severity,
                family=category,
                reason=cp.get("reason") or cp.get("excerpt") or cp.get("notes"),
                segment_id=getattr(nearest, "id", None),
                source="auto",
                risk_tag=risk_tag,
            )
        )

    # 2) Structural missing-stage detector. The L2 contract specifies
    #    closer calls — if there's no `verbal` segment, that's a CRITICAL
    #    structural fail, regardless of what the per-checkpoint LLM said.
    # Use caller-supplied call_type first (from L7 intake), fall back to
    # script attribute hint, finally to segment heuristic.
    if call_type is None and script is not None:
        call_type = getattr(script, "call_type", None)

    # Heuristic fallback: if any segment is `verbal`, treat as closer.
    has_verbal = any(seg.stage == "verbal" for seg in segments)
    looks_like_closer = (call_type == "closer") or (
        call_type is None and any(seg.stage in {"transfer", "verbal"} for seg in segments)
    )

    if call_type == "closer" and not has_verbal:
        flags.append(
            Flag(
                call_id=call_id,
                rule_id="STRUCTURAL-MISSING-VERBAL",
                severity="critical",
                family="terms",
                reason="Verbal contract stage absent from closer call",
                segment_id=None,
                source="auto",
                risk_tag="ombudsman",
            )
        )
    elif call_type is None and looks_like_closer and not has_verbal:
        # Same flag, but raised from heuristic (transfer-without-verbal).
        flags.append(
            Flag(
                call_id=call_id,
                rule_id="STRUCTURAL-MISSING-VERBAL",
                severity="critical",
                family="terms",
                reason="Verbal contract stage absent from closer call",
                segment_id=None,
                source="auto",
                risk_tag="ombudsman",
            )
        )

    return flags


# ─── W3.A — pricing-mismatch flag detector ──────────────────────────────────


def _reference_rates_from_script(script: Script | None) -> dict[str, float] | None:
    """Pull a ``_reference_rates`` sentinel entry from the script's
    checkpoints JSON. Shape:

        {"_reference_rates": {"unit_rate_p_per_kwh": 10.5,
                              "standing_charge_p_per_day": 30.0}}

    Returns None when missing/unparseable so the comparator no-ops
    (pre-W3.A scripts have no reference rates yet — the feature ships
    silent until rates are wired in via the script editor).
    """
    if script is None:
        return None
    raw = getattr(script, "checkpoints", None)
    if not raw:
        return None
    try:
        cps = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(cps, list):
        return None
    for entry in cps:
        if isinstance(entry, dict) and isinstance(entry.get("_reference_rates"), dict):
            ref = entry["_reference_rates"]
            out: dict[str, float] = {}
            for key in ("unit_rate_p_per_kwh", "standing_charge_p_per_day"):
                v = ref.get(key)
                if isinstance(v, (int, float)):
                    out[key] = float(v)
            return out or None
    return None


def derive_pricing_mismatch_flags(
    call_id: str,
    transcript: str,
    script: Script | None,
    segments: list[CallSegment],
) -> list[Flag]:
    """Compare extracted agent-stated rates vs script reference rates.
    Emit one ``PRICING_MISMATCH`` flag per rate kind that drifts beyond
    ``_PRICING_TOLERANCE_P`` (0.1p). Severity is always ``high`` and
    family is ``pricing`` so the existing RISK_TAG_MAP routes it to
    'mis-selling' for the reviewer dashboard.

    Returns an empty list (no-op) when:
      - script is None
      - script has no _reference_rates entry
      - extractor finds no rates in transcript
      - all extracted rates are within tolerance of the reference
    """
    refs = _reference_rates_from_script(script)
    if not refs:
        return []

    extracted = extract_rates(transcript or "")
    flags: list[Flag] = []

    # First-extracted-rate-wins — calls usually quote the unit rate once
    # in the verbal stage; if there are multiple matches we pick the
    # earliest by char_offset so the segment lookup lands sensibly.
    def _first_segment_for_offset(offset: int) -> CallSegment | None:
        # Best-effort: pick the segment whose midpoint corresponds to a
        # comparable transcript position. We don't have char→time map
        # here, so fall back to the first segment.
        return segments[0] if segments else None

    ref_unit = refs.get("unit_rate_p_per_kwh")
    if ref_unit is not None and extracted["unit_rates"]:
        first = extracted["unit_rates"][0]
        diff = abs(first["value_p_per_kwh"] - ref_unit)
        if diff > _PRICING_TOLERANCE_P:
            seg = _first_segment_for_offset(first["char_offset"])
            risk_tag = RISK_TAG_MAP.get(("pricing", "high"))
            flags.append(
                Flag(
                    call_id=call_id,
                    rule_id="PRICING_MISMATCH",
                    severity="high",
                    family="pricing",
                    reason=(
                        f"Pricing mismatch — agent quoted "
                        f"{first['value_p_per_kwh']:g}p/kWh, script says "
                        f"{ref_unit:g}p/kWh"
                    ),
                    evidence=first["raw_text"],
                    segment_id=getattr(seg, "id", None),
                    source="auto",
                    risk_tag=risk_tag,
                )
            )

    ref_sc = refs.get("standing_charge_p_per_day")
    if ref_sc is not None and extracted["standing_charges"]:
        first = extracted["standing_charges"][0]
        diff = abs(first["value_p_per_day"] - ref_sc)
        if diff > _PRICING_TOLERANCE_P:
            seg = _first_segment_for_offset(first["char_offset"])
            risk_tag = RISK_TAG_MAP.get(("pricing", "high"))
            flags.append(
                Flag(
                    call_id=call_id,
                    rule_id="PRICING_MISMATCH",
                    severity="high",
                    family="pricing",
                    reason=(
                        f"Pricing mismatch — agent quoted "
                        f"{first['value_p_per_day']:g}p/day standing charge, "
                        f"script says {ref_sc:g}p/day"
                    ),
                    evidence=first["raw_text"],
                    segment_id=getattr(seg, "id", None),
                    source="auto",
                    risk_tag=risk_tag,
                )
            )

    return flags
