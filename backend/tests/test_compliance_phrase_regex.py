"""Tests for app.watt_compliance.phrase_regex — the cheap pre-pass.

The patterns drive both BLOCK escalation and the LLM evidence stream;
we pin them so tightening or loosening a regex is a deliberate diff.
"""
from __future__ import annotations

import pytest

from app.watt_compliance.phrase_regex import (
    PHRASE_RULES,
    PhraseHit,
    PatternMode,
    hit_summary,
    scan,
)
from app.watt_compliance.taxonomy import Severity


def _matched_rule_ids(hits: list[PhraseHit]) -> set[str]:
    return {h.rule_id for h in hits}


# ── Identity / Standard 1 ────────────────────────────────────────


def test_clean_watt_identity_passes():
    transcript = "Hi, my name is Sarah and I'm calling from Watt Utilities about your business energy renewal."
    hits = scan(transcript)
    # Identity-presence (C1-01) requires Watt to appear — present here, so
    # no R01 absence hit.
    assert "C1-01" not in _matched_rule_ids(hits)


def test_missing_watt_identity_fires_r01_absence():
    transcript = "Hi, this is Sarah, I'm calling about your gas contract."
    hits = scan(transcript)
    assert "C1-01" in _matched_rule_ids(hits), "must flag missing Watt identity"
    r01 = next(h for h in hits if h.rule_id == "C1-01")
    assert r01.severity is Severity.CRITICAL
    assert r01.reason.code == "R01"


def test_supplier_impersonation_fires_r02():
    transcript = "Good morning, I'm calling from E.ON about your electricity account. Watt Utilities."
    hits = scan(transcript)
    assert "C1-02" in _matched_rule_ids(hits)


def test_renewal_department_lie_fires():
    transcript = "Hi, we are your renewal department calling about your business energy. Watt Utilities."
    hits = scan(transcript)
    assert "C1-04" in _matched_rule_ids(hits)


# ── Pricing / Standard 3 ─────────────────────────────────────────


def test_guarantee_phrase_fires_r09():
    transcript = "Watt Utilities here. I can guarantee this is the cheapest you'll get on the market."
    hits = scan(transcript)
    assert "C3-01" in _matched_rule_ids(hits)
    crit = [h for h in hits if h.rule_id == "C3-01"][0]
    assert crit.severity is Severity.CRITICAL


def test_will_save_money_fires():
    transcript = "Watt Utilities. We will save you money on this renewal."
    hits = scan(transcript)
    assert "C3-02" in _matched_rule_ids(hits)


def test_definitely_going_up_fires():
    transcript = "Watt Utilities. Prices are definitely going up next quarter, lock in now."
    hits = scan(transcript)
    assert "C3-02" in _matched_rule_ids(hits)


# ── Market scope / Standard 3 ────────────────────────────────────


def test_whole_market_claim_fires_r08():
    transcript = "Watt Utilities. I have checked the whole market and this is the best."
    hits = scan(transcript)
    assert "C4-01" in _matched_rule_ids(hits)


def test_searched_everywhere_fires():
    transcript = "Watt Utilities. We've searched everywhere and nobody can beat this."
    hits = scan(transcript)
    assert "C4-01" in _matched_rule_ids(hits)


# ── Script framing — only on verbal call_type ────────────────────


def test_just_a_formality_fires_only_on_verbal():
    transcript = "Watt Utilities. This is just a formality — please confirm yes."
    # On a lead-gen call this rule is out of scope (focus is on identity/qualification).
    lead_gen_hits = scan(transcript, call_type="lead_gen")
    assert "C7-01" not in _matched_rule_ids(lead_gen_hits)
    # On a verbal call it fires CRITICAL.
    verbal_hits = scan(transcript, call_type="verbal")
    assert "C7-01" in _matched_rule_ids(verbal_hits)


def test_just_locking_in_prices_fires_on_closer_too():
    transcript = "Watt Utilities. We're just locking the prices in for you today."
    closer_hits = scan(transcript, call_type="closer")
    assert "C7-01" in _matched_rule_ids(closer_hits)


# ── Commission disclosure / Standard 3g ──────────────────────────


def test_you_dont_pay_anything_fires_r07():
    transcript = "Watt Utilities. You don't pay anything for our service, it's free."
    hits = scan(transcript)
    assert "C8-02" in _matched_rule_ids(hits)


# ── Pressure / vulnerability / Standard 2 ────────────────────────


def test_customer_says_not_interested_flags():
    transcript = (
        "Watt Utilities. I'm not interested. — Well let me explain how this saves you money."
    )
    hits = scan(transcript)
    assert "C5-01" in _matched_rule_ids(hits)


# ── Hit summary ──────────────────────────────────────────────────


def test_hit_summary_counts_by_severity():
    transcript = (
        "Hi, I'm calling about your contract. We will save you money. "
        "I have checked the whole market."
    )
    hits = scan(transcript)
    summary = hit_summary(hits)
    # At least one CRITICAL (missing Watt + will-save) and one HIGH (whole market).
    assert summary[Severity.CRITICAL.value] >= 1
    assert summary[Severity.HIGH.value] >= 1


def test_empty_transcript_returns_empty_list():
    assert scan("") == []
    assert scan(None) == []  # type: ignore[arg-type]


# ── Rule catalogue invariants ────────────────────────────────────


def test_every_rule_has_a_known_reason_code():
    from app.watt_compliance.taxonomy import REJECTION_REASONS_BY_CODE
    for rule in PHRASE_RULES:
        assert rule.reason_code in REJECTION_REASONS_BY_CODE, (
            f"phrase rule {rule.rule_id} references unknown reason {rule.reason_code}"
        )


def test_rule_modes_are_valid():
    valid = {PatternMode.PRESENCE, PatternMode.ABSENCE}
    for rule in PHRASE_RULES:
        assert rule.mode in valid


@pytest.mark.parametrize("rule", list(PHRASE_RULES))
def test_each_rule_has_a_human_readable_why(rule):
    assert rule.why and len(rule.why) > 10
