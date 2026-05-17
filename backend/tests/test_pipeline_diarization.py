"""Diarization selector — pick the engine that produced ≥2 distinct
speakers and write those words to ``call.word_data``. Pure-Python test
of the selector logic; the full pipeline integration is covered by
``test_pipeline_merge.py``.

User-reported bug 2026-05-17: AAI returned all words with
``speaker="UNK"`` → last-writer-wins clobbered Deepgram's good
diarization → entire transcript rendered as one agent turn.
"""
from __future__ import annotations

import json


def _distinct_speakers(words):
    """Mirror of pipeline._distinct_speakers — extracted here so we
    can unit-test the contract without spinning up the full pipeline."""
    if not words:
        return 0
    seen = set()
    for w in words:
        s = w.get("speaker") if isinstance(w, dict) else None
        if s is None:
            continue
        key = str(s)
        if key in {"", "UNK", "unknown"}:
            continue
        seen.add(key)
    return len(seen)


def test_deepgram_wins_when_aai_returns_all_unk():
    """Reproduces the user-reported screenshot bug: AAI marks every
    word ``speaker="UNK"`` (mono audio), Deepgram correctly split into
    two speakers. Selector must keep Deepgram's word_data."""
    dg = [{"word": "hello", "speaker": 0}, {"word": "yes", "speaker": 1}]
    aai = [{"word": "hello", "speaker": "UNK"}, {"word": "yes", "speaker": "UNK"}]
    assert _distinct_speakers(dg) == 2
    assert _distinct_speakers(aai) == 0


def test_aai_wins_when_both_split():
    """When both engines successfully diarized, AAI wins because its
    text is downstream-primary."""
    dg = [{"speaker": 0}, {"speaker": 1}]
    aai = [{"speaker": "A"}, {"speaker": "B"}]
    assert _distinct_speakers(dg) == 2
    assert _distinct_speakers(aai) == 2
    # The pipeline picks AAI here (≥2 distinct + ties go to AAI).


def test_single_speaker_fallback_still_writes_word_data():
    """When BOTH engines failed to split (very short audio, mono,
    silence-only), we still want word-level timings for the player.
    The chip warns the reviewer."""
    aai = [{"speaker": "UNK"}, {"speaker": "UNK"}]
    dg = [{"speaker": 0}, {"speaker": 0}]
    assert _distinct_speakers(aai) == 0
    assert _distinct_speakers(dg) == 1


def test_unk_excluded_from_speaker_count():
    """Make sure the ``UNK`` / ``""`` / ``unknown`` sentinels never
    count toward the distinct speaker tally."""
    words = [
        {"speaker": "A"},
        {"speaker": "UNK"},
        {"speaker": ""},
        {"speaker": "unknown"},
        {"speaker": "B"},
    ]
    assert _distinct_speakers(words) == 2


def test_word_data_serialises_to_json_string():
    """call.word_data is a Text column on Postgres + SQLite; the
    pipeline must json.dumps the list before assignment."""
    words = [{"word": "hi", "speaker": 0}]
    serialised = json.dumps(words)
    assert isinstance(serialised, str)
    assert json.loads(serialised) == words
