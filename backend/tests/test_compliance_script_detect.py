"""Tests for app.watt_compliance.script_detect — supplier + script_type +
call_class detection from transcript text.

These cover the §5 deterministic strategy from D-supplier-scripts.md.
Real test audio transcripts will exercise the same code path; these
tests use representative snippets to pin behaviour now.
"""
from __future__ import annotations

from app.watt_compliance.script_detect import (
    detect,
    supplier_namespace,
)
from app.watt_compliance.taxonomy import CallClass, ScriptType, Supplier


# ── Supplier detection ──────────────────────────────────────────


def test_bgl_keywords_resolve_to_bgl():
    t = "We're moving you over to British Gas Lite — the BGL portal is webchat only."
    assert detect(t).supplier is Supplier.BGL


def test_british_gas_core_resolves_correctly():
    t = "British Gas will email your contract pack within 10 working days."
    assert detect(t).supplier is Supplier.BRITISH_GAS


def test_eon_next_resolves():
    t = "Your new supplier will be E.ON Next; eonnext.com has the policies."
    assert detect(t).supplier is Supplier.EON_NEXT


def test_eon_next_with_dot_or_space_variants():
    for s in ["E.ON Next", "Eon Next", "E ON Next"]:
        assert detect(s).supplier is Supplier.EON_NEXT, f"missed variant: {s!r}"


def test_edf_resolves():
    t = "EDF will administer this. The H3083 reference is on your contract."
    assert detect(t).supplier is Supplier.EDF


def test_scottish_power_resolves():
    t = "Scottish Power For Business — the contact line is 0345 058 0002."
    assert detect(t).supplier is Supplier.SCOTTISH_POWER


def test_pozitive_resolves():
    t = "Pozitive will manage your customer portal access."
    assert detect(t).supplier is Supplier.POZITIVE


def test_no_supplier_keyword_returns_none():
    t = "Hi, this is a regular call about your business energy contract."
    assert detect(t).supplier is None


# ── Script-type detection ──────────────────────────────────────


def test_loa_keyword_wins_over_acquisition_lookalike():
    t = "Letter of Authority — please confirm you authorise Watt to act on your behalf."
    res = detect(t)
    assert res.script_type is ScriptType.LOA


def test_renewal_keyword():
    t = "We're calling about your contract renewal — your current contract ends in October."
    assert detect(t).script_type is ScriptType.RENEWAL


def test_amendment_keyword():
    t = "Please do an amendment on lines 11 to 14 of the EON script."
    assert detect(t).script_type is ScriptType.AMENDMENT


def test_acquisition_default():
    t = "We are arranging a new contract for your business energy supply."
    assert detect(t).script_type is ScriptType.ACQUISITION


# ── Call-class detection ───────────────────────────────────────


def test_dual_fuel_when_both_meter_refs():
    t = "Your MPAN is 1234567890123 and your MPRN is 9876543210."
    assert detect(t).call_class is CallClass.DUAL


def test_gas_only_via_mprn():
    t = "Your MPRN is 9876543210; gas supply only."
    assert detect(t).call_class is CallClass.GAS


def test_elec_only_via_mpan():
    t = "Your MPAN is 1234567890123 and the kVA is documented in your bill."
    assert detect(t).call_class is CallClass.ELEC


def test_hh_meter_class():
    t = "This is a half-hourly meter with an ASC charge."
    assert detect(t).call_class is CallClass.HH


# ── Evidence capture ───────────────────────────────────────────


def test_evidence_captured_when_match():
    t = "We're calling from E.ON Next about your renewal — MPRN supplied."
    res = detect(t)
    assert res.supplier_evidence is not None
    assert res.script_type_evidence is not None
    assert res.call_class_evidence is not None


def test_no_evidence_when_no_match():
    res = detect("Just a friendly chat.")
    assert res.supplier_evidence is None


# ── Namespace builder ──────────────────────────────────────────


def test_namespace_format():
    ns = supplier_namespace(Supplier.EON_NEXT, ScriptType.ACQUISITION, CallClass.GAS)
    assert ns == "scripts:eon_next:acquisition:gas"


def test_namespace_for_loa_any():
    ns = supplier_namespace(Supplier.EON_NEXT, ScriptType.LOA, CallClass.ANY)
    assert ns == "scripts:eon_next:loa:any"


# ── Empty input ────────────────────────────────────────────────


def test_empty_transcript_returns_all_none():
    res = detect("")
    assert res.supplier is None
    assert res.script_type is None
    assert res.call_class is None
