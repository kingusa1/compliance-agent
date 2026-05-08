"""A/B parity harness — unit tests for parity computation + report shape.
Live LLM calls are out of scope for these tests; the analyze callable is
mocked so the harness logic is the sole subject under test."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import json
from pathlib import Path

import pytest

from backend.scripts.ab_parity import (
    compute_parity,
    run_ab,
    Verdict,
)


def _v(call_id: str, status: str, score: float = 0.5) -> Verdict:
    return Verdict(call_id=call_id, status=status, score=score)


def test_compute_parity_all_match_returns_100pct():
    a = [_v("c1", "pass"), _v("c2", "fail")]
    b = [_v("c1", "pass"), _v("c2", "fail")]
    p = compute_parity(a, b)
    assert p["parity_pct"] == 100.0
    assert p["matches"] == 2
    assert p["mismatches"] == 0
    assert p["diffs"] == []


def test_compute_parity_one_mismatch_returns_50pct():
    a = [_v("c1", "pass"), _v("c2", "fail")]
    b = [_v("c1", "pass"), _v("c2", "pass")]
    p = compute_parity(a, b)
    assert p["parity_pct"] == 50.0
    assert p["matches"] == 1
    assert p["mismatches"] == 1
    assert p["diffs"][0]["call_id"] == "c2"
    assert p["diffs"][0]["baseline_status"] == "fail"
    assert p["diffs"][0]["candidate_status"] == "pass"


def test_compute_parity_handles_empty_lists():
    p = compute_parity([], [])
    assert p["parity_pct"] == 100.0
    assert p["matches"] == 0
    assert p["mismatches"] == 0


def test_run_ab_writes_report_and_returns_summary(tmp_path: Path, monkeypatch):
    """run_ab calls the supplied analyze fn for each call under each flag profile,
    diffs, and writes the report. Returns the summary dict."""
    call_ids = ["c1", "c2"]

    def fake_analyze(call_id: str, *, flags: dict) -> Verdict:
        # baseline (both flags off) → status "pass"
        # candidate (both flags on) → "pass" for c1, "fail" for c2 (one mismatch)
        if not flags["use_agent_analyzer"]:
            return _v(call_id, "pass")
        return _v(call_id, "pass" if call_id == "c1" else "fail")

    out_path = tmp_path / "report.json"
    summary = run_ab(call_ids, analyze=fake_analyze, out_path=str(out_path))

    assert summary["parity_pct"] == 50.0
    body = json.loads(out_path.read_text())
    assert body["sample_size"] == 2
    assert body["matches"] == 1
    assert body["mismatches"] == 1
    assert body["diffs"][0]["call_id"] == "c2"
    assert "baseline_flags" in body and "candidate_flags" in body
