"""Unit tests for app.agents.content_classifier.

The classifier itself wraps an Opus 4.7 call. To keep tests offline we
monkeypatch ``app.agents.content_classifier._call_llm`` and assert that
the post-processing pipeline (validation, dedup, non-E.ON LOA drop,
confidence filter, halt on empty) behaves as specified for the 2026-05-12
taxonomy rebuild.
"""

from __future__ import annotations

import json

import pytest

from app.agents import content_classifier
from app.agents.content_classifier import (
    Segment,
    VALID_SEGMENT_TYPES,
    _build_indexed_transcript,
    _coerce_segment,
    classify_content,
)


def _word_data(n: int) -> list[dict]:
    return [{"punctuated_word": f"w{i}", "word": f"w{i}"} for i in range(n)]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_valid_segment_types_locked_to_four():
    assert VALID_SEGMENT_TYPES == frozenset(
        {"lead_gen", "pre_sales", "verbal", "loa"}
    )


# ---------------------------------------------------------------------------
# Pure-function coercion
# ---------------------------------------------------------------------------


def test_coerce_segment_drops_invalid_type():
    raw = {"segment_type": "closer", "start_word_idx": 0, "end_word_idx": 10, "confidence": 0.9}
    assert _coerce_segment(raw, max_word_idx=100) is None


def test_coerce_segment_clamps_out_of_bounds():
    raw = {
        "segment_type": "verbal",
        "start_word_idx": 0,
        "end_word_idx": 9999,
        "confidence": 0.9,
        "reasoning": "ok",
    }
    seg = _coerce_segment(raw, max_word_idx=100)
    assert seg is not None
    assert seg.end_word_idx == 100
    assert seg.start_word_idx == 0


def test_coerce_segment_rejects_inverted_bounds():
    raw = {"segment_type": "verbal", "start_word_idx": 50, "end_word_idx": 10, "confidence": 0.9}
    assert _coerce_segment(raw, max_word_idx=100) is None


def test_coerce_segment_clamps_confidence():
    raw = {
        "segment_type": "lead_gen",
        "start_word_idx": 0,
        "end_word_idx": 10,
        "confidence": 1.7,
    }
    seg = _coerce_segment(raw, max_word_idx=100)
    assert seg is not None
    assert seg.confidence == 1.0


# ---------------------------------------------------------------------------
# _build_indexed_transcript
# ---------------------------------------------------------------------------


def test_build_indexed_transcript_marks_every_ten_words():
    wd = _word_data(25)
    out = _build_indexed_transcript(wd)
    # Word indices [0], [10], [20] must all appear.
    assert "[0]" in out
    assert "[10]" in out
    assert "[20]" in out
    assert "w0" in out
    assert "w24" in out


def test_build_indexed_transcript_truncates_at_max_words():
    wd = _word_data(50)
    out = _build_indexed_transcript(wd, max_words=30)
    assert "w29" in out
    assert "w35" not in out


# ---------------------------------------------------------------------------
# classify_content — happy paths, edge cases, supplier filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_returns_empty_for_short_transcript(monkeypatch):
    monkeypatch.setattr(content_classifier, "_call_llm", _never_called)
    out = await classify_content("hi", _word_data(2))
    assert out == []


@pytest.mark.asyncio
async def test_classify_returns_empty_when_word_data_missing(monkeypatch):
    monkeypatch.setattr(content_classifier, "_call_llm", _never_called)
    out = await classify_content("a" * 200, word_data=[])
    assert out == []


@pytest.mark.asyncio
async def test_classify_parses_three_segments(monkeypatch):
    """E.ON closer shape: pre_sales + verbal + loa."""
    fake = [
        {"segment_type": "pre_sales", "start_word_idx": 0,   "end_word_idx": 50,  "confidence": 0.9, "reasoning": "intro"},
        {"segment_type": "verbal",    "start_word_idx": 51,  "end_word_idx": 200, "confidence": 0.95, "reasoning": "binding"},
        {"segment_type": "loa",       "start_word_idx": 201, "end_word_idx": 280, "confidence": 0.85, "reasoning": "loa"},
    ]
    monkeypatch.setattr(content_classifier, "_call_llm", _llm_returning(fake))
    out = await classify_content("x" * 600, _word_data(300), supplier="E.ON Next")
    assert [s.segment_type for s in out] == ["pre_sales", "verbal", "loa"]
    assert all(isinstance(s, Segment) for s in out)


@pytest.mark.asyncio
async def test_classify_drops_loa_for_non_eon(monkeypatch):
    fake = [
        {"segment_type": "pre_sales", "start_word_idx": 0,   "end_word_idx": 50,  "confidence": 0.9, "reasoning": "intro"},
        {"segment_type": "verbal",    "start_word_idx": 51,  "end_word_idx": 200, "confidence": 0.95, "reasoning": "binding"},
        {"segment_type": "loa",       "start_word_idx": 201, "end_word_idx": 280, "confidence": 0.85, "reasoning": "anomaly"},
    ]
    monkeypatch.setattr(content_classifier, "_call_llm", _llm_returning(fake))
    out = await classify_content("x" * 600, _word_data(300), supplier="British Gas")
    assert [s.segment_type for s in out] == ["pre_sales", "verbal"]


@pytest.mark.asyncio
async def test_classify_drops_low_confidence(monkeypatch):
    fake = [
        {"segment_type": "lead_gen", "start_word_idx": 0, "end_word_idx": 100, "confidence": 0.3, "reasoning": "weak"},
        {"segment_type": "verbal",   "start_word_idx": 101, "end_word_idx": 200, "confidence": 0.9, "reasoning": "strong"},
    ]
    monkeypatch.setattr(content_classifier, "_call_llm", _llm_returning(fake))
    out = await classify_content("x" * 600, _word_data(250))
    assert [s.segment_type for s in out] == ["verbal"]


@pytest.mark.asyncio
async def test_classify_dedupes_overlapping_segments(monkeypatch):
    fake = [
        {"segment_type": "lead_gen", "start_word_idx": 0,   "end_word_idx": 100, "confidence": 0.9, "reasoning": "first"},
        {"segment_type": "verbal",   "start_word_idx": 50,  "end_word_idx": 200, "confidence": 0.9, "reasoning": "overlap"},
    ]
    monkeypatch.setattr(content_classifier, "_call_llm", _llm_returning(fake))
    out = await classify_content("x" * 600, _word_data(250))
    assert [s.segment_type for s in out] == ["lead_gen"]


@pytest.mark.asyncio
async def test_classify_handles_code_fenced_json(monkeypatch):
    body = "```json\n" + json.dumps([
        {"segment_type": "lead_gen", "start_word_idx": 0, "end_word_idx": 100, "confidence": 0.9, "reasoning": "ok"},
    ]) + "\n```"

    async def _llm(prompt, timeout=60.0, **kwargs):  # accept cheap= etc
        return body

    monkeypatch.setattr(content_classifier, "_call_llm", _llm)
    out = await classify_content("x" * 600, _word_data(150))
    assert len(out) == 1
    assert out[0].segment_type == "lead_gen"


@pytest.mark.asyncio
async def test_classify_returns_empty_on_unparseable_response(monkeypatch):
    async def _llm(prompt, timeout=60.0, **kwargs):
        return "not json at all"

    monkeypatch.setattr(content_classifier, "_call_llm", _llm)
    out = await classify_content("x" * 600, _word_data(150))
    assert out == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _llm_returning(payload):
    async def _llm(prompt, timeout=60.0, **kwargs):  # accept cheap= etc
        return json.dumps(payload)

    return _llm


async def _never_called(prompt, timeout=60.0, **kwargs):
    raise AssertionError("LLM should not have been called")
