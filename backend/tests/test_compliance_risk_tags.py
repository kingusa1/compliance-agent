"""Tests for app.watt_compliance.risk_tags — alias coercion +
deduplication + strict validation."""
from __future__ import annotations

import pytest

from app.watt_compliance.risk_tags import (
    normalize_risk_tags,
    validate_risk_tags_strict,
)
from app.watt_compliance.taxonomy import RiskTag


def test_canonical_values_pass_through():
    out = normalize_risk_tags(["ombudsman_risk", "mis_selling_risk"])
    assert out == ["ombudsman_risk", "mis_selling_risk"]


def test_alias_misselling_variants():
    for v in ["misselling", "mis-selling", "MisSelling", "Mis_Selling"]:
        out = normalize_risk_tags([v])
        assert out == ["mis_selling_risk"], f"variant {v!r} failed"


def test_alias_ombudsman():
    for v in ["ombudsman", "Ombudsman", "energy ombudsman"]:
        out = normalize_risk_tags([v])
        assert out == ["ombudsman_risk"], f"variant {v!r} failed"


def test_cot_alias_maps_to_cancellation_risk():
    """Ops team shorthand: 'COT' = change-of-tenancy = cancellation risk."""
    assert normalize_risk_tags(["COT"]) == ["cancellation_risk"]


def test_unknown_values_dropped_silently():
    out = normalize_risk_tags(["nonsense_tag", "ombudsman", "another_bogus"])
    assert out == ["ombudsman_risk"]


def test_dedup_across_aliases():
    out = normalize_risk_tags([
        "ombudsman_risk", "ombudsman", "Ombudsman", "ENERGY OMBUDSMAN",
    ])
    assert out == ["ombudsman_risk"]


def test_enum_values_directly_accepted():
    out = normalize_risk_tags([RiskTag.COMPLAINT_RISK, RiskTag.CANCELLATION_RISK])
    assert out == ["complaint_risk", "cancellation_risk"]


def test_empty_iterable_returns_empty_list():
    assert normalize_risk_tags([]) == []
    assert normalize_risk_tags(None) == []  # type: ignore[arg-type]


def test_strict_raises_on_unknown():
    with pytest.raises(ValueError, match="unknown risk_tag"):
        validate_risk_tags_strict(["nonsense_tag"])


def test_strict_passes_on_canonical():
    out = validate_risk_tags_strict(["complaint_risk", "cancellation_risk"])
    assert out == ["complaint_risk", "cancellation_risk"]


def test_non_string_values_dropped_in_lenient_mode():
    out = normalize_risk_tags([42, None, "ombudsman", {}])
    assert out == ["ombudsman_risk"]
