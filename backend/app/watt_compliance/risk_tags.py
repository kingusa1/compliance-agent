"""Risk-tag normalisation + validation helpers.

The Watt system spec defines exactly 4 risk tags (TS §9). The DB column
``calls.risk_tags`` is a free-form text array, so callers can write
anything — this helper coerces inbound values to the canonical 4 and
drops anything else with a debug log.

Usage in ingestion paths:

    from app.watt_compliance.risk_tags import normalize_risk_tags
    call.risk_tags = normalize_risk_tags(llm_output.get("risk_tags") or [])
"""
from __future__ import annotations

import logging
from typing import Iterable

from app.watt_compliance.taxonomy import RiskTag

log = logging.getLogger(__name__)


# Common variants the LLM (or legacy code) may produce, mapped to the
# canonical RiskTag enum value. Lookup is case-insensitive and ignores
# whitespace and underscores/dashes.
_ALIASES: dict[str, RiskTag] = {
    "ombudsmanrisk": RiskTag.OMBUDSMAN_RISK,
    "ombudsman": RiskTag.OMBUDSMAN_RISK,
    "energyombudsman": RiskTag.OMBUDSMAN_RISK,
    "missellingrisk": RiskTag.MIS_SELLING_RISK,
    "misselling": RiskTag.MIS_SELLING_RISK,
    "mis-selling": RiskTag.MIS_SELLING_RISK,
    "mis_selling": RiskTag.MIS_SELLING_RISK,
    "complaintrisk": RiskTag.COMPLAINT_RISK,
    "complaint": RiskTag.COMPLAINT_RISK,
    "cancellationrisk": RiskTag.CANCELLATION_RISK,
    "cancellation": RiskTag.CANCELLATION_RISK,
    "cot": RiskTag.CANCELLATION_RISK,  # ops shorthand for change-of-tenancy
}


def _key(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def _coerce_one(value: object) -> RiskTag | None:
    """Map a single inbound value to a RiskTag or None if unrecognised."""
    if isinstance(value, RiskTag):
        return value
    if not isinstance(value, str):
        return None
    # Direct enum value match.
    for tag in RiskTag:
        if value == tag.value:
            return tag
    # Alias lookup.
    return _ALIASES.get(_key(value))


def normalize_risk_tags(values: Iterable[object]) -> list[str]:
    """Return a deduplicated list of canonical RiskTag values.

    Unknown values are dropped (with a debug log) rather than raised so a
    stray LLM hallucination doesn't crash the pipeline.
    """
    seen: list[str] = []
    for v in values or []:
        coerced = _coerce_one(v)
        if coerced is None:
            log.debug("dropping unknown risk_tag value: %r", v)
            continue
        if coerced.value not in seen:
            seen.append(coerced.value)
    return seen


def validate_risk_tags_strict(values: Iterable[object]) -> list[str]:
    """Same as ``normalize_risk_tags`` but raises ValueError on unknown
    values. Use in tests / scripts where strict validation matters."""
    out: list[str] = []
    for v in values or []:
        coerced = _coerce_one(v)
        if coerced is None:
            raise ValueError(f"unknown risk_tag value: {v!r}")
        if coerced.value not in out:
            out.append(coerced.value)
    return out
