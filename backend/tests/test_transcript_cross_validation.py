"""Cross-validation between Deepgram and AssemblyAI transcripts.

Pure-Python, no DB, no network — fast.
"""
from __future__ import annotations

from app.transcript_cross_validation import (
    DEFAULT_AGREEMENT_FLOOR,
    cross_validate,
)


def test_identical_transcripts_score_one():
    dg = "[00:01] Agent: hello this is sam from broker calling"
    aai = "Hello this is Sam from Broker calling."
    report = cross_validate(dg, aai)
    assert report["agreement"] == 1.0
    assert report["below_floor"] is False
    assert report["disagreement_samples"] == []
    assert report["skipped_reason"] is None


def test_speaker_labels_and_timestamps_are_stripped():
    """Deepgram emits "[MM:SS] Agent:" prefixes; AssemblyAI doesn't.
    Normalisation must strip both so the same content scores 1.0."""
    dg = (
        "[00:00] Agent: hello\n"
        "[00:02] Customer: yes speaking\n"
        "[00:03] Agent: calling about your gas supply"
    )
    aai = "Hello. Yes speaking. Calling about your gas supply."
    report = cross_validate(dg, aai)
    assert report["agreement"] == 1.0


def test_real_content_disagreement_drops_score_and_surfaces_sample():
    """The most common Watt failure mode: business name mis-heard."""
    dg = "Agent: thanks Awais this confirms your contract with Eon Next"
    aai = "Agent: thanks Charles this confirms your contract with Eon Next"
    report = cross_validate(dg, aai)
    assert report["agreement"] < 1.0
    assert any(
        ("awais" in (s["deepgram_only"] or "")) and ("charles" in (s["assemblyai_only"] or ""))
        for s in report["disagreement_samples"]
    )


def test_filler_disagreement_does_not_dominate_score():
    """One engine kept "umm", the other dropped it — agreement on
    content tokens must still score 1.0."""
    dg = "Agent: umm hello yeah this is sam from broker"
    aai = "Hello this is Sam from Broker."
    report = cross_validate(dg, aai)
    assert report["agreement"] == 1.0


def test_below_floor_flag_fires_when_agreement_is_low():
    dg = "Agent: alpha bravo charlie delta echo foxtrot golf hotel"
    aai = "Agent: zulu yankee xray whiskey victor uniform tango sierra"
    report = cross_validate(dg, aai)
    assert report["below_floor"] is True
    assert report["floor"] == DEFAULT_AGREEMENT_FLOOR
    assert report["disagreement_samples"], "expected at least one disagreement sample"


def test_missing_transcript_returns_skipped_reason_not_crash():
    report = cross_validate("", "Hello world")
    assert report["agreement"] is None
    assert report["skipped_reason"] == "deepgram_missing"
    assert report["below_floor"] is False

    report = cross_validate("Hello world", "")
    assert report["skipped_reason"] == "assemblyai_missing"


def test_disagreement_samples_capped_at_eight():
    """Sanity check: a divergent transcript can't blow up the JSONB
    column size."""
    dg_words = [f"word{i}" for i in range(200)]
    aai_words = [f"other{i}" for i in range(200)]
    dg = "Agent: " + " ".join(dg_words)
    aai = "Agent: " + " ".join(aai_words)
    report = cross_validate(dg, aai)
    assert len(report["disagreement_samples"]) <= 8


def test_floor_overridable():
    """Caller can override the floor — used by the pipeline to honour
    the ``TRANSCRIPT_AGREEMENT_FLOOR`` env var."""
    dg = "Agent: alpha beta gamma delta"
    aai = "Agent: alpha beta gamma echo"
    # Strict floor — divergence trips the flag.
    strict = cross_validate(dg, aai, agreement_floor=0.99)
    assert strict["below_floor"] is True
    # Loose floor — same divergence does not trip the flag.
    loose = cross_validate(dg, aai, agreement_floor=0.5)
    assert loose["below_floor"] is False
