"""Embedding pre-filter — keep only checkpoints whose intent is plausibly
present in the transcript. Threshold-gated cosine similarity over chunked
transcript text vs. checkpoint name + description.
"""
from unittest.mock import patch

import pytest

from app.checkpoint_filter import select_relevant_checkpoints


def _checkpoint(name: str, description: str = "") -> dict:
    return {"name": name, "description": description, "section": 1}


def test_returns_all_checkpoints_when_threshold_zero():
    """At threshold 0.0, every checkpoint passes — pre-filter is no-op."""
    transcript = "We discussed the supply contract at length."
    cps = [_checkpoint("foo"), _checkpoint("bar"), _checkpoint("baz")]
    with patch("app.checkpoint_filter.embed_batch") as mock_embed:
        # Return distinct vectors so cosine is well-defined
        mock_embed.side_effect = [
            [[1.0, 0.0, 0.0]],  # transcript chunk
            [[0.5, 0.5, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],  # 3 cps
        ]
        out = select_relevant_checkpoints(transcript, cps, threshold=0.0)
    assert len(out) == 3
    assert [c["name"] for c in out] == ["foo", "bar", "baz"]


def test_filters_below_threshold():
    """Checkpoints whose top chunk-similarity is below the threshold are dropped."""
    transcript = "We discussed the supply contract at length."
    cps = [_checkpoint("contract"), _checkpoint("weather")]
    with patch("app.checkpoint_filter.embed_batch") as mock_embed:
        mock_embed.side_effect = [
            [[1.0, 0.0]],            # transcript
            [[0.95, 0.0], [0.0, 0.95]],  # contract very similar, weather orthogonal
        ]
        out = select_relevant_checkpoints(transcript, cps, threshold=0.5)
    assert len(out) == 1
    assert out[0]["name"] == "contract"


def test_empty_checkpoints_returns_empty():
    out = select_relevant_checkpoints("anything", [], threshold=0.5)
    assert out == []


def test_empty_transcript_returns_empty():
    out = select_relevant_checkpoints("", [_checkpoint("x")], threshold=0.5)
    assert out == []


def test_embedding_failure_returns_all_checkpoints_unfiltered():
    """If the embedding API fails, fall back to ALL checkpoints (graceful degrade).
    Never silently drop checkpoints due to infra failure — that would create false
    passes in compliance verdicts."""
    transcript = "anything"
    cps = [_checkpoint("a"), _checkpoint("b")]
    with patch("app.checkpoint_filter.embed_batch", side_effect=RuntimeError("boom")):
        out = select_relevant_checkpoints(transcript, cps, threshold=0.5)
    assert len(out) == 2
