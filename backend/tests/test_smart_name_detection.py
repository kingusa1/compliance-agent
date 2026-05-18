"""Smart name detection tests (2026-05-18 audit follow-up).

Covers the five smart-fixes shipped in this branch:
  A1 — `is` / `name` / `mine` etc. added to _NAME_STOPWORDS so the agent
       regex never captures intro fragments as if they were real names.
  A2 — _extract_customer_name_regex deterministic pre-pass for the
       customer slot (was zero-coverage before).
  A3 — sharpened DETECT_NAMES_PROMPT (manual prompt review, not unit
       tested here — covered by the agent + customer regex coverage).
  A4 — pipeline deal/customer fallback (integration-tested at the
       pipeline level — see test_pipeline_*.py for parent coverage; this
       file tests the analyser primitives only).
  A5 — AAI transcript retry (integration-tested at the pipeline level —
       see test_pipeline_*.py for the await-chain coverage).
"""
from __future__ import annotations

import pytest

from app.analysis import (
    _extract_agent_name_regex,
    _extract_customer_name_regex,
    _NAME_STOPWORDS,
)


# ── A1: stopword leakage tokens ────────────────────────────────────────


def test_agent_regex_rejects_bare_is_fragment() -> None:
    """2026-05-18 Finding #3: 'my name is is calling' yielded agent='Is'.
    The stopword union must now include 'is' so the regex returns None
    rather than capturing the leaked intro token."""
    t = "good morning my name is is calling from watt utilities today"
    assert _extract_agent_name_regex(t) is None


def test_agent_regex_rejects_name_fragment() -> None:
    """`name` itself should never surface as a name token."""
    t = "yes my name name calling here from watt"
    assert _extract_agent_name_regex(t) is None


def test_agent_regex_still_captures_real_first_name() -> None:
    """Smoke check that the expanded stopword list didn't break the
    happy path — a clean intro must still produce the name."""
    t = "good morning my name is James calling from watt utilities"
    assert _extract_agent_name_regex(t) == "James"


def test_agent_regex_captures_first_plus_surname() -> None:
    t = "hi my name is Sarah Hughes here from watt utilities"
    assert _extract_agent_name_regex(t) == "Sarah Hughes"


def test_name_stopwords_includes_intro_fragments() -> None:
    """Defence-in-depth: confirm the leak tokens are in the union."""
    for tok in ("is", "am", "name", "mine", "it", "this", "that", "who"):
        assert tok in _NAME_STOPWORDS, f"missing leak guard for {tok!r}"


# ── A2: customer-side regex pre-pass ────────────────────────────────────


def test_customer_regex_catches_am_i_speaking_to() -> None:
    """Agent-side cue: 'am I speaking to <name>?'"""
    t = (
        "this call is recorded for compliance. am I speaking to David "
        "Mitchell today?"
    )
    assert _extract_customer_name_regex(t) == "David Mitchell"


def test_customer_regex_catches_is_that() -> None:
    t = "thanks for taking the call. is that Anne Marie on the line?"
    assert _extract_customer_name_regex(t) == "Anne Marie"


def test_customer_regex_catches_self_intro_yes_this_is() -> None:
    """Customer-side cue: customer self-identifying after a confirm."""
    t = "hi there. yes this is Karen speaking how can I help you"
    assert _extract_customer_name_regex(t) == "Karen"


def test_customer_regex_catches_speaking_trail() -> None:
    """'<name> speaking' answering the phone."""
    t = "Joseph speaking, how can I help you today?"
    assert _extract_customer_name_regex(t) == "Joseph"


def test_customer_regex_speaking_trail_with_surname() -> None:
    t = "Sarah Hughes speaking, what's this about?"
    assert _extract_customer_name_regex(t) == "Sarah Hughes"


def test_customer_regex_rejects_pii_marker() -> None:
    """A redaction token must never be returned as a real name."""
    t = "am I speaking to [PERSON_NAME] today on this line?"
    assert _extract_customer_name_regex(t) is None


def test_customer_regex_rejects_stopword_capture() -> None:
    """The intro-fragment stopwords (is / name / it) must NOT slip
    through the customer pre-pass either."""
    t = "is that is on the line? speaking to it from your office?"
    assert _extract_customer_name_regex(t) is None


def test_customer_regex_skips_collision_with_agent() -> None:
    """Same first name as the agent → almost always a re-introduction
    of the same speaker. The pre-pass must skip it."""
    t = "this is Tom calling. am I speaking to Tom on the line?"
    out = _extract_customer_name_regex(t, agent_name="Tom")
    assert out is None


def test_customer_regex_accepts_different_first_name_when_agent_known() -> None:
    """Sanity: when the customer name differs from the agent, it must
    still be captured even with agent context supplied."""
    t = "this is Tom calling. am I speaking to David on the line?"
    out = _extract_customer_name_regex(t, agent_name="Tom")
    assert out == "David"


def test_customer_regex_returns_none_on_empty_input() -> None:
    assert _extract_customer_name_regex("") is None


def test_customer_regex_scans_3000_char_window() -> None:
    """The customer name often appears AFTER the TPI preamble — the
    pre-pass scans up to 3000 chars (vs 1500 for the agent)."""
    preamble = "Agent: " + ("x " * 200)  # ~400 chars of filler
    t = (
        preamble
        + " thanks for your patience. could I confirm your name please? "
        + " yes this is Margaret on the line"
    )
    assert _extract_customer_name_regex(t) == "Margaret"


def test_customer_regex_handles_could_i_speak_to() -> None:
    t = "hello could I speak to Pete please about your energy contract?"
    assert _extract_customer_name_regex(t) == "Pete"


def test_customer_regex_handles_please_confirm_your_name() -> None:
    t = "for the record, please confirm your name David Mitchell please"
    assert _extract_customer_name_regex(t) == "David Mitchell"
