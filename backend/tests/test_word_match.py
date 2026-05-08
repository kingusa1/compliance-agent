"""Tests for word_match.find_word_range — map an evidence quote to AssemblyAI word timestamps."""

import pytest

from app.word_match import find_word_range


@pytest.fixture
def words():
    """Simulated AssemblyAI word stream (start/end in SECONDS — matches what
    `assemblyai_transcription.py` persists to the DB after dividing the raw
    AssemblyAI ms by 1000). `find_word_range` multiplies back to ms for the
    `(start_ms, end_ms)` return contract."""
    return [
        {"word": "just", "start": 40.0, "end": 40.3, "speaker": "A"},
        {"word": "so", "start": 40.4, "end": 40.6, "speaker": "A"},
        {"word": "you", "start": 40.7, "end": 40.9, "speaker": "A"},
        {"word": "know", "start": 41.0, "end": 41.2, "speaker": "A"},
        {"word": "the", "start": 42.0, "end": 42.2, "speaker": "A"},
        {"word": "prices", "start": 42.3, "end": 42.7, "speaker": "A"},
        {"word": "include", "start": 42.8, "end": 43.1, "speaker": "A"},
        {"word": "VAT", "start": 43.2, "end": 43.5, "speaker": "A"},
        {"word": "at", "start": 43.6, "end": 43.7, "speaker": "A"},
        {"word": "the", "start": 43.8, "end": 43.9, "speaker": "A"},
        {"word": "prevailing", "start": 44.0, "end": 44.5, "speaker": "A"},
        {"word": "rate", "start": 44.6, "end": 44.9, "speaker": "A"},
    ]


def test_exact_match_returns_first_and_last_word_timestamps(words):
    start, end = find_word_range("the prices include VAT at the prevailing rate", words)
    assert start == 42_000
    assert end == 44_900


def test_paraphrase_still_matches_when_token_overlap_above_threshold(words):
    # "include VAT at prevailing rate" — 5/5 significant tokens match.
    start, end = find_word_range("include VAT at prevailing rate", words)
    assert start == 42_800
    assert end == 44_900


def test_wrapped_evidence_with_speaker_labels_and_quotes(words):
    # LLM sometimes returns Agent said: "…"
    start, end = find_word_range('Agent said: "the prices include VAT"', words)
    assert start == 42_000
    assert end == 43_500


def test_empty_evidence_returns_none(words):
    assert find_word_range("", words) == (None, None)
    assert find_word_range("   ", words) == (None, None)


def test_no_word_data_returns_none():
    assert find_word_range("some quote", []) == (None, None)
    assert find_word_range("some quote", None) == (None, None)


def test_unicode_curly_quotes_are_normalized(words):
    start, end = find_word_range("\u201cthe prices include VAT\u201d", words)
    assert start == 42_000
    assert end == 43_500


def test_low_overlap_returns_none(words):
    # Only "the" matches — below the 60% threshold.
    assert find_word_range("the fibrillating unicorn", words) == (None, None)


def test_single_word_evidence_still_matches(words):
    start, end = find_word_range("prevailing", words)
    assert start == 44_000
    assert end == 44_500
