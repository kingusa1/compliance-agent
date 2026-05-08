"""Integration: with embedding_prefilter_enabled=True, irrelevant checkpoints
are dropped before LLM fan-out. With flag off, behaviour is unchanged."""
from unittest.mock import patch

import pytest

from app.checkpoint_analyzer import _maybe_prefilter_checkpoints


def test_prefilter_off_returns_all_checkpoints(monkeypatch):
    monkeypatch.setattr("app.checkpoint_analyzer.settings.embedding_prefilter_enabled", False)
    cps = [{"name": "a"}, {"name": "b"}]
    out = _maybe_prefilter_checkpoints("anything", cps)
    assert out == cps  # unchanged


def test_prefilter_on_drops_irrelevant(monkeypatch):
    monkeypatch.setattr("app.checkpoint_analyzer.settings.embedding_prefilter_enabled", True)
    monkeypatch.setattr("app.checkpoint_analyzer.settings.embedding_prefilter_threshold", 0.5)
    cps = [{"name": "contract"}, {"name": "weather"}]
    with patch("app.checkpoint_filter.embed_batch") as mock_embed:
        mock_embed.side_effect = [
            [[1.0, 0.0]],
            [[0.95, 0.0], [0.0, 0.95]],
        ]
        out = _maybe_prefilter_checkpoints("we discussed the contract", cps)
    assert len(out) == 1
    assert out[0]["name"] == "contract"


def test_prefilter_on_with_no_matches_returns_empty(monkeypatch):
    """If everything fails the threshold, return empty — caller decides what to do.
    The analyzer's existing all-batches loop handles len(checkpoints)==0 fine."""
    monkeypatch.setattr("app.checkpoint_analyzer.settings.embedding_prefilter_enabled", True)
    monkeypatch.setattr("app.checkpoint_analyzer.settings.embedding_prefilter_threshold", 0.99)
    cps = [{"name": "a"}, {"name": "b"}]
    with patch("app.checkpoint_filter.embed_batch") as mock_embed:
        mock_embed.side_effect = [
            [[1.0, 0.0]],
            [[0.0, 1.0], [0.0, 1.0]],
        ]
        out = _maybe_prefilter_checkpoints("anything", cps)
    assert out == []
