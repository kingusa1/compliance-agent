"""Pillar 2 (L2) — extraction unit tests.

These tests run BEFORE the main session adds CallSegment / Flag /
ExtractedEntity to app.models.py. They install lightweight stand-ins
on the `app.models` module before importing the extraction modules so
the ORM constructors used by the extraction code resolve to plain
Python objects we can introspect — no DB, no Alembic.

Once main writes the real ORM classes, this stub-injection is a no-op
because we only attach if the attribute is missing.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import app.models as _models


# ─── ORM stand-ins (only installed if main hasn't added them yet) ───────────
class _Row:
    """Generic attribute-bag stand-in for an ORM row."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        # ORM rows usually have an `id`; fake one so flags can reference it.
        if not hasattr(self, "id"):
            self.id = None

    def __repr__(self):
        return f"{type(self).__name__}({self.__dict__})"


for _name in ("CallSegment", "Flag", "ExtractedEntity"):
    if not hasattr(_models, _name):
        cls = type(_name, (_Row,), {})
        setattr(_models, _name, cls)


# Now import the extraction modules — they bind to whatever
# `app.models.CallSegment` etc. point to (real ORM if present, stub otherwise).
from app.extraction.segments import detect_segments  # noqa: E402
from app.extraction.entities import extract_entities  # noqa: E402
from app.extraction.flags import derive_flags  # noqa: E402
from app.models import CallSegment, ExtractedEntity, Flag  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────────────


def _word(text: str, start: float, end: float, speaker: str = "agent") -> dict:
    return {
        "word": text,
        "punctuated_word": text,
        "start": start,
        "end": end,
        "speaker": speaker,
    }


def _intro_words() -> list[dict]:
    """Synthetic word stream that opens with a 'good morning' anchor (intro stage)."""
    return [
        _word("good", 0.0, 0.3, "agent"),
        _word("morning", 0.3, 0.7, "agent"),
        _word("this", 0.7, 0.9, "agent"),
        _word("is", 0.9, 1.0, "agent"),
        _word("calling", 1.0, 1.4, "agent"),
        _word("from", 1.4, 1.6, "agent"),
        _word("Watt", 1.6, 1.9, "agent"),
        _word("Utilities", 1.9, 2.5, "agent"),
    ]


# ─── Tests ──────────────────────────────────────────────────────────────────


def test_segment_detector_intro_anchor():
    """A transcript whose first words match the 'good morning' anchor must
    produce at least one CallSegment whose stage == 'intro'."""
    words = _intro_words()
    transcript = " ".join(w["word"] for w in words)
    segments = detect_segments(call_id="call-1", transcript=transcript, word_data=words, script=None)

    assert segments, "expected at least one detected segment"
    assert any(seg.stage == "intro" for seg in segments), (
        f"expected an 'intro' segment, got stages: {[s.stage for s in segments]}"
    )

    # Verify the row is the right ORM type and carries the call_id.
    intro = next(seg for seg in segments if seg.stage == "intro")
    assert isinstance(intro, CallSegment)
    assert intro.call_id == "call-1"


def test_entity_regex_mpan():
    """An MPAN regex hit must produce an ExtractedEntity with key='mpan',
    source='regex', confidence=0.95.

    2026-05-24 — MPAN guard now requires exactly 13 digits (the real-world
    length); the previous 10-digit fixture was a near-miss that the PII
    guard correctly rejects. Use a real 13-digit MPAN core here so the
    regex extractor + the post-regex validation both accept it.
    """
    transcript = "the supply number is 2000023456789 thanks"
    rows = asyncio.run(extract_entities(call_id="call-2", transcript=transcript))

    mpans = [r for r in rows if r.key == "mpan"]
    assert mpans, f"expected an mpan row, got: {[(r.key, r.value) for r in rows]}"
    row = mpans[0]
    assert isinstance(row, ExtractedEntity)
    assert row.value == "2000023456789"
    assert row.source == "regex"
    assert row.confidence == 0.95
    assert row.call_id == "call-2"


def test_flags_missing_verbal_critical():
    """A closer call with no `verbal` segment must yield a CRITICAL
    STRUCTURAL-MISSING-VERBAL flag tied to the whole call."""
    # Build a tiny segment list that simulates intro+transfer but no verbal.
    segments = [
        CallSegment(call_id="call-3", idx=0, stage="intro", transcript_excerpt="hello",
                    speaker="agent", start_s=0.0, end_s=2.0),
        CallSegment(call_id="call-3", idx=1, stage="transfer", transcript_excerpt="putting you through",
                    speaker="agent", start_s=2.0, end_s=4.0),
    ]

    # Stub a Script that the writer would have stashed call_type onto.
    script = types.SimpleNamespace(checkpoints=json.dumps([]), call_type="closer")

    flags = derive_flags(call_id="call-3", checkpoint_results=[], segments=segments, script=script)

    structural = [f for f in flags if f.rule_id == "STRUCTURAL-MISSING-VERBAL"]
    assert structural, f"expected STRUCTURAL-MISSING-VERBAL, got: {[(f.rule_id, f.severity) for f in flags]}"
    f = structural[0]
    assert f.severity == "critical"
    assert f.segment_id is None
    assert f.source == "auto"
    assert f.risk_tag == "ombudsman"


def test_idempotency():
    """Running derive_flags twice with identical inputs must yield the
    same row count and same rule_ids — that's what the finalize writer's
    delete-then-insert pattern relies on for safe re-runs."""
    segments = [
        CallSegment(call_id="call-4", idx=0, stage="intro", transcript_excerpt="hello",
                    speaker="agent", start_s=0.0, end_s=2.0),
    ]
    checkpoint_results = [
        {"name": "R-001", "status": "fail", "reason": "no Watt mention"},
        {"name": "R-006", "status": "needs_review", "reason": "decision-maker unclear"},
    ]
    script = types.SimpleNamespace(checkpoints=json.dumps([]), call_type="lead_gen")

    first = derive_flags(call_id="call-4", checkpoint_results=checkpoint_results,
                         segments=segments, script=script)
    second = derive_flags(call_id="call-4", checkpoint_results=checkpoint_results,
                          segments=segments, script=script)

    assert len(first) == len(second), (
        f"row count drifted: first={len(first)} second={len(second)}"
    )
    assert sorted(f.rule_id for f in first) == sorted(f.rule_id for f in second)
    # And every emitted flag must be a real Flag instance.
    for f in first:
        assert isinstance(f, Flag)
