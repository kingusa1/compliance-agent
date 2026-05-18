"""Supplier regex pre-pass + agent job-title smell test (2026-05-18 Westbury).

Westbury Village Hall lead-gen surfaced two reliability gaps:

  1. ``detect_supplier`` is LLM-only; Deepgram occasionally drops the dot/space
     in multi-word names ("eonext" / "britishgas") and the LLM returned
     "Unknown" even though the broker named the target supplier in a
     canonical "agreed with <X>" / "<X> energy supply" context.

  2. ``detect_names`` lets the LLM win over the regex pre-pass for the agent
     slot. When the LLM glued together an interlocutor cue ("speaking to
     Art Engineer") into "Art Engineer", the regex's high-confidence "James"
     capture from "i am james" got discarded.

Both are now defended in `app.analysis` and covered here.
"""
from __future__ import annotations

from app.analysis import (
    _llm_agent_smells_fabricated,
    _supplier_regex_prepass,
)


# --- supplier regex pre-pass ---------------------------------------------


def test_supplier_prepass_eonext_collapsed_spelling_with_target_cue() -> None:
    """Westbury transcript: 'eonext energy supply' must canonicalise to E.ON Next."""
    t = (
        "i am james calling from watt utilities in regards to eonext "
        "energy supply at [location_address_1]"
    )
    assert _supplier_regex_prepass(t) == "E.ON Next"


def test_supplier_prepass_eon_next_canonical_with_target_cue() -> None:
    t = "working on behalf of E.ON Next on your renewal"
    assert _supplier_regex_prepass(t) == "E.ON Next"


def test_supplier_prepass_british_gas_lite_beats_british_gas() -> None:
    """When both BGL and 'british gas' could match, the more specific BGL
    pattern must win because it sorts first in the prepass tuple."""
    t = "agreed with British Gas Lite for the new contract"
    assert _supplier_regex_prepass(t) == "BGL"


def test_supplier_prepass_returns_none_for_departing_supplier() -> None:
    """The customer's CURRENT supplier mention (no target cue) must NOT
    surface as the broker target — that picks the wrong supplier in
    the verbal contract preamble."""
    t = "your contract with British Gas ends next month nothing else"
    assert _supplier_regex_prepass(t) is None


def test_supplier_prepass_target_cue_required_for_brand_pickup() -> None:
    t = "we are pricing some options today nothing relevant here"
    assert _supplier_regex_prepass(t) is None


def test_supplier_prepass_handles_empty_input() -> None:
    assert _supplier_regex_prepass("") is None
    assert _supplier_regex_prepass(None) is None  # type: ignore[arg-type]


# --- agent job-title smell test -------------------------------------------


def test_llm_agent_smells_fabricated_catches_engineer_suffix() -> None:
    assert _llm_agent_smells_fabricated("Art Engineer") is True


def test_llm_agent_smells_fabricated_catches_manager_suffix() -> None:
    assert _llm_agent_smells_fabricated("Sam Manager") is True


def test_llm_agent_smells_fabricated_passes_real_two_word_names() -> None:
    assert _llm_agent_smells_fabricated("Tom Kelly") is False
    assert _llm_agent_smells_fabricated("Sam Escrich") is False


def test_llm_agent_smells_fabricated_passes_single_first_name() -> None:
    assert _llm_agent_smells_fabricated("James") is False


def test_llm_agent_smells_fabricated_handles_falsy_values() -> None:
    assert _llm_agent_smells_fabricated(None) is False
    assert _llm_agent_smells_fabricated("") is False
    assert _llm_agent_smells_fabricated("Unknown") is False
