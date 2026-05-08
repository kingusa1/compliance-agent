"""W3.A — pricing-mismatch flag: extractor + flag-derivation tests.

Covers the regex extractor in ``app.extraction.pricing`` and the
``derive_pricing_mismatch_flags`` helper in ``app.extraction.flags``.
No DB; we use the same lightweight ORM stand-ins as test_extraction.py
(installed there if the real ORM hasn't loaded — here we just import
after that module so the stand-ins are already in place).
"""
from __future__ import annotations

import json
import types

# Reuse the stub-injection done by test_extraction.py so we get the
# same _Row stand-in if real ORM isn't available.
from tests import test_extraction  # noqa: F401 — side-effect import

from app.extraction.pricing import extract_rates
from app.extraction.flags import derive_pricing_mismatch_flags
from app.models import Flag


# ─── extractor tests ────────────────────────────────────────────────────────


def test_extract_rates_digit_unit_rate():
    """Digit form: '11p per kWh'."""
    out = extract_rates("agent: the rate is 11p per kWh, ok?")
    assert len(out["unit_rates"]) == 1
    assert out["unit_rates"][0]["value_p_per_kwh"] == 11.0


def test_extract_rates_spelled_unit_rate():
    """Spelled form: 'eleven pence per kilowatt hour'."""
    out = extract_rates("so that's eleven pence per kilowatt hour going forward")
    assert len(out["unit_rates"]) == 1
    assert out["unit_rates"][0]["value_p_per_kwh"] == 11.0


def test_extract_rates_compound_spelled_number():
    """Compound spelled form: 'twenty-one pence per kWh'."""
    out = extract_rates("the unit rate of twenty-one pence per kWh applies")
    # Either pattern (unit_rate or per-kwh) should fire — at least one match.
    assert len(out["unit_rates"]) >= 1
    assert any(r["value_p_per_kwh"] == 21.0 for r in out["unit_rates"])


def test_extract_rates_standing_charge_digit():
    """Standing charge in digit form: '30p per day'."""
    out = extract_rates("standing charge of 30p per day on top of that")
    assert len(out["standing_charges"]) == 1
    assert out["standing_charges"][0]["value_p_per_day"] == 30.0


def test_extract_rates_standing_charge_spelled():
    """Standing charge spelled: 'forty pence per day'."""
    out = extract_rates("there's a standing charge of forty pence per day")
    assert len(out["standing_charges"]) == 1
    assert out["standing_charges"][0]["value_p_per_day"] == 40.0


def test_extract_rates_no_match():
    """Transcript with no rates returns empty lists, not None."""
    out = extract_rates("good morning, this is John from Watt Utilities, how are you?")
    assert out == {"unit_rates": [], "standing_charges": []}


def test_extract_rates_standing_charge_does_not_double_count_as_unit_rate():
    """The number inside 'standing charge of 30p per day' must not also
    surface in unit_rates — that would create a fake mismatch flag."""
    out = extract_rates("standing charge of 30p per day, fixed for two years")
    assert len(out["standing_charges"]) == 1
    # Critical: 30 must NOT show up as a unit rate.
    assert all(r["value_p_per_kwh"] != 30.0 for r in out["unit_rates"])


def test_extract_rates_decimal_value():
    """Decimal numerics should be preserved."""
    out = extract_rates("that's 11.5p per kWh by the way")
    assert len(out["unit_rates"]) == 1
    assert out["unit_rates"][0]["value_p_per_kwh"] == 11.5


# ─── flag-derivation tests ──────────────────────────────────────────────────


def test_derive_pricing_mismatch_flag_above_tolerance():
    """Agent quotes 11p, script says 10p → diff 1.0p > 0.1p tolerance →
    one HIGH PRICING_MISMATCH flag."""
    transcript = "agent: the rate is eleven pence per kWh"
    script = types.SimpleNamespace(
        checkpoints=json.dumps([
            {"_reference_rates": {"unit_rate_p_per_kwh": 10.0, "standing_charge_p_per_day": 30.0}},
        ]),
    )

    flags = derive_pricing_mismatch_flags(
        call_id="call-PR-1",
        transcript=transcript,
        script=script,
        segments=[],
    )

    assert len(flags) == 1
    f = flags[0]
    assert isinstance(f, Flag)
    assert f.rule_id == "PRICING_MISMATCH"
    assert f.severity == "high"
    assert f.family == "pricing"
    assert f.source == "auto"
    assert "11" in f.reason
    assert "10" in f.reason


def test_derive_pricing_mismatch_no_flag_within_tolerance():
    """Diff of exactly 0.05p (< 0.1p tolerance) → no flag."""
    transcript = "agent: the rate is 10.05p per kWh"
    script = types.SimpleNamespace(
        checkpoints=json.dumps([
            {"_reference_rates": {"unit_rate_p_per_kwh": 10.0}},
        ]),
    )
    flags = derive_pricing_mismatch_flags(
        call_id="call-PR-2",
        transcript=transcript,
        script=script,
        segments=[],
    )
    assert flags == []


def test_derive_pricing_mismatch_no_reference_rates_no_flags():
    """Script has no _reference_rates entry → no flags (graceful no-op)."""
    transcript = "agent: the rate is 11p per kWh"
    script = types.SimpleNamespace(checkpoints=json.dumps([{"section": 1, "name": "Intro"}]))
    flags = derive_pricing_mismatch_flags(
        call_id="call-PR-3",
        transcript=transcript,
        script=script,
        segments=[],
    )
    assert flags == []


def test_derive_pricing_mismatch_no_script_no_flags():
    """Script is None → no flags (graceful no-op)."""
    flags = derive_pricing_mismatch_flags(
        call_id="call-PR-4",
        transcript="11p per kWh",
        script=None,
        segments=[],
    )
    assert flags == []


def test_derive_pricing_mismatch_standing_charge_diff():
    """Standing charge diff above tolerance also flags."""
    transcript = "standing charge of forty pence per day"
    script = types.SimpleNamespace(
        checkpoints=json.dumps([
            {"_reference_rates": {"standing_charge_p_per_day": 30.0}},
        ]),
    )
    flags = derive_pricing_mismatch_flags(
        call_id="call-PR-5",
        transcript=transcript,
        script=script,
        segments=[],
    )
    assert len(flags) == 1
    assert "40" in flags[0].reason
    assert "30" in flags[0].reason
