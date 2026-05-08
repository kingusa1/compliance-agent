"""Smoke tests for app.analysis.analyze_compliance_watt.

The LLM call is mocked — these tests verify the *non-LLM* parts of the
pipeline: script detection, regex pre-pass, evidence packing, the
critical-hit auto-escalation, and the JSON-decode fallback. Real-LLM
integration is exercised manually once the user supplies API keys.
"""
from __future__ import annotations

import json

import pytest


# Sample LLM response that the mock returns when the prompt is "good".
_GOOD_LLM_RESPONSE = json.dumps({
    "verdict": "PASS",
    "score": 95,
    "compliance_status": "compliant",
    "rejections": [],
    "risk_tags": [],
    "summary": "Clean call.",
    "supplier_detected": "eon_next",
    "call_type_detected": "lead_gen",
})


@pytest.mark.asyncio
async def test_clean_transcript_passes_through(monkeypatch):
    """Clean transcript with Watt identity present → no critical regex
    hits → LLM-PASS verdict survives."""
    transcript = (
        "Hi, I'm calling from Watt Utilities about your business energy renewal "
        "with E.ON Next. We compare rates from our supplier panel."
    )

    async def fake_llm(*args, **kwargs):
        return _GOOD_LLM_RESPONSE

    from app import analysis
    monkeypatch.setattr(analysis, "_call_llm", fake_llm)

    result = await analysis.analyze_compliance_watt(transcript, call_type="lead_gen")
    assert result["verdict"] == "PASS"
    # Auto-detection ran and recognised E.ON Next.
    assert result["auto_detected"]["supplier"] == "eon_next"
    # Regex pre-pass attached its summary even when no hits.
    assert "regex_pre_pass" in result
    assert result["regex_pre_pass"]["summary"]["CRITICAL"] == 0


@pytest.mark.asyncio
async def test_critical_regex_hit_forces_block(monkeypatch):
    """Even if the LLM is generous, a CRITICAL regex hit overrides
    the verdict to BLOCK and stashes the LLM verdict for audit."""
    transcript = (
        "Hi, I am calling from E.ON about your renewal — I can guarantee this is "
        "the cheapest you'll get."
    )
    # Note: missing "Watt Utilities" → fires C1-01 absence (CRITICAL R01)
    # PLUS supplier impersonation (CRITICAL R02)
    # PLUS guarantee phrase (CRITICAL R09)

    # LLM is overly forgiving — says PASS.
    async def fake_llm(*args, **kwargs):
        return json.dumps({
            "verdict": "PASS",
            "score": 80,
            "compliance_status": "compliant",
            "rejections": [],
            "risk_tags": [],
            "summary": "Looks fine.",
            "supplier_detected": None,
            "call_type_detected": "lead_gen",
        })

    from app import analysis
    monkeypatch.setattr(analysis, "_call_llm", fake_llm)

    result = await analysis.analyze_compliance_watt(transcript, call_type="lead_gen")

    # The orchestration MUST override.
    assert result["verdict"] == "BLOCK", "critical regex hit must force BLOCK"
    assert result["llm_verdict"] == "PASS", "original LLM verdict preserved for audit"
    assert "regex_pre_pass" in result
    crit_count = result["regex_pre_pass"]["summary"]["CRITICAL"]
    assert crit_count >= 1, "expected at least one CRITICAL hit"


@pytest.mark.asyncio
async def test_malformed_llm_json_falls_back_to_review(monkeypatch):
    """If the LLM returns junk, we degrade to REVIEW rather than crash."""
    transcript = "Watt Utilities clean call."

    async def fake_llm(*args, **kwargs):
        return "not actually json"

    from app import analysis
    monkeypatch.setattr(analysis, "_call_llm", fake_llm)

    result = await analysis.analyze_compliance_watt(transcript, call_type="lead_gen")
    assert result["verdict"] == "REVIEW"
    assert result["compliance_status"] == "non_compliant"


@pytest.mark.asyncio
async def test_supplier_hint_used_when_llm_silent(monkeypatch):
    """When the LLM doesn't supply its own supplier_detected, the
    caller-supplied hint is the fallback. When the LLM DOES supply
    one, the LLM's value wins (it had the full context)."""
    transcript = "Watt Utilities clean call. No supplier mentioned."

    async def fake_llm_silent(*args, **kwargs):
        return json.dumps({
            "verdict": "PASS",
            "score": 95,
            "compliance_status": "compliant",
            "rejections": [],
            "risk_tags": [],
            "summary": "OK.",
            # supplier_detected omitted — hint should fill in.
            "call_type_detected": "lead_gen",
        })

    from app import analysis
    monkeypatch.setattr(analysis, "_call_llm", fake_llm_silent)

    result = await analysis.analyze_compliance_watt(
        transcript, call_type="lead_gen", supplier_hint="bgl"
    )
    assert result["supplier_detected"] == "bgl"


@pytest.mark.asyncio
async def test_script_chunks_are_passed_to_llm(monkeypatch):
    """When the caller provides RAG-retrieved script chunks, the
    prompt assembly must include them."""
    transcript = "Watt Utilities — the call is clean."
    captured: dict[str, object] = {}

    async def fake_llm(user_message, *args, **kwargs):
        captured["user_message"] = user_message
        return _GOOD_LLM_RESPONSE

    from app import analysis
    monkeypatch.setattr(analysis, "_call_llm", fake_llm)

    await analysis.analyze_compliance_watt(
        transcript,
        call_type="verbal",
        script_chunks=["CHUNK A: must say Watt Utilities at start.",
                       "CHUNK B: must obtain customer yes."],
    )
    assert "CHUNK A" in str(captured["user_message"])
    assert "CHUNK B" in str(captured["user_message"])


@pytest.mark.asyncio
async def test_regex_evidence_block_present_in_llm_prompt(monkeypatch):
    """Even when no hits, the prompt must include the empty-evidence
    line so the LLM knows the regex pre-pass ran."""
    transcript = "Watt Utilities calling about your contract."

    captured: dict[str, object] = {}

    async def fake_llm(user_message, *args, **kwargs):
        captured["user_message"] = user_message
        return _GOOD_LLM_RESPONSE

    from app import analysis
    monkeypatch.setattr(analysis, "_call_llm", fake_llm)

    await analysis.analyze_compliance_watt(transcript, call_type="lead_gen")
    msg = str(captured["user_message"])
    assert "Pre-pass regex evidence" in msg
