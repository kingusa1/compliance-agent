# Wave 4 — Cost: Embedding Pre-filter + Tiered LLM Flag Flips

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Apply `two-stage-review-loop` between tasks (spec → quality → fix-loop with new commits).

**Goal:** Cut mean LLM cost per call ≥5× without dropping verdict parity below 98% on a 50-call A/B sample, by (a) pre-filtering rule×chunk pairs through embedding cosine similarity before LLM fan-out, and (b) flipping `use_agent_analyzer` to True (Gemini Flash first → Sonnet escalate, already implemented in Wave 0/1, just disabled by default).

**Architecture:** Three additive sub-blocks land in one wave.
(a) **Embedding pre-filter** — new `app/checkpoint_filter.py` exposes `select_relevant_checkpoints(transcript, checkpoints, threshold) -> list[checkpoint]` using the existing `app/rag/embed.py` (cached rule embeddings, fresh transcript-chunk embedding) and cosine similarity. Wired into `_limited` inside `app/checkpoint_analyzer.py:560` — when the flag is on, skip checkpoints whose top chunk-similarity score is below `embedding_prefilter_threshold`.
(b) **A/B parity harness** — `backend/scripts/ab_parity.py` runs N sample calls twice (baseline flags off, candidate flags on), captures verdict per call, computes parity %. Output a JSON report: per-call diff + summary. Designed as a one-shot script — engineer runs locally before flipping prod flags.
(c) **Flag flip & docs** — once A/B ≥98%, flip defaults in `app/config.py` (`use_agent_analyzer: bool = True`, `embedding_prefilter_enabled: bool = True`). Document in `docs/cost-optimization.md` with the A/B numbers from the run.

The two flags are independent and reversible. Either can be disabled in prod via env var without code change. A/B parity script can be re-run any time after data drift to confirm the flags still hold.

**Tech Stack:** OpenAI text-embedding-3-small (existing, via `app/rag/embed.py`), numpy 1.26 (cosine sim — already a transitive dep via openai), pytest, FastAPI 0.115, Pydantic Settings.

**Spec source:** `docs/superpowers/specs/2026-05-06-enterprise-hardening-inject-design.md` §9 Wave 4 (W4a + W4b) and §6.4, §6.5, §3 Success Criteria #5.

**Prereqs:**
- Wave 3 PR #2 reviewed & merged to `main`. (Or rebased onto `main` after Wave 3 lands.)
- Local pgvector container at `:5433` for tests (`backend/.env` already points here).
- 50 sample calls available in dev DB with `transcript`, `word_data`, `script_id`, finalized verdict — for the A/B harness.

**Wave 5 deferred** to a separate plan after Wave 4 verifies green.

---

## Branch

```bash
git checkout main
git pull --ff-only
git checkout -b feat/wave4-cost
```

If Wave 3 not merged yet, branch from `feat/wave3-durability` so the storage refactor + replay endpoint are available during dev.

---

## File Structure

| Path | New / Mod | Responsibility |
|---|---|---|
| `backend/app/config.py` | MOD | Add `embedding_prefilter_enabled`, `embedding_prefilter_threshold` settings; flip `use_agent_analyzer` default to True in T7 (final flip) |
| `backend/app/checkpoint_filter.py` | NEW | `select_relevant_checkpoints(transcript, checkpoints, threshold)` — cosine-sim pre-filter using `app.rag.embed.embed_batch` |
| `backend/app/checkpoint_analyzer.py` | MOD | Wire pre-filter into `_limited` (line ~560); when flag on, skip irrelevant checkpoints |
| `backend/tests/test_checkpoint_filter.py` | NEW | Cosine-sim correctness, threshold gating, empty-input safety, embedding-failure graceful-degrade |
| `backend/tests/test_checkpoint_analyzer_prefilter.py` | NEW | Integration: with flag on, only relevant checkpoints reach LLM fan-out |
| `backend/scripts/ab_parity.py` | NEW | Harness: pick N calls, run analyze with each flag combo, compute verdict parity, write JSON report |
| `backend/tests/test_ab_parity.py` | NEW | Unit: parity computation + report shape (no live LLM calls — mocks the analyze path) |
| `docs/cost-optimization.md` | NEW | Runbook: how to run A/B, parity gate, rollback procedure, flag-flip checklist |
| `.env.example` | MOD | Append Wave 4 flags with documented defaults |

---

## Branch + scope

Wave 4 is two flag flips behind a single A/B gate. Implementation is small (~250 LOC). Most of the work is the harness and the A/B run itself, which is a manual gate (T9) — not subagent-runnable because it needs real LLM calls and a live dev DB.

---

## Task 1: Add Wave 4 config keys (TDD)

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/tests/test_observability_metrics.py` (no, just smoke at end)

- [ ] **Step 1: Append to `Settings` class**

In `backend/app/config.py`, before `settings = Settings()`, add:

```python
    # ─── Wave 4 — cost optimizers ─────────────────────────────────────
    embedding_prefilter_enabled: bool = False  # Off by default — A/B-gated
    embedding_prefilter_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    # Note: use_agent_analyzer default flipped True in T7 after A/B passes.
```

- [ ] **Step 2: Verify import**

```bash
cd backend && source venv/bin/activate && python -c "from app.config import settings; print(settings.embedding_prefilter_enabled, settings.embedding_prefilter_threshold)"
```
Expected: `False 0.35`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/config.py
git commit -m "config(backend): add Wave 4 cost flags (embedding_prefilter_enabled, threshold)"
```

---

## Task 2: checkpoint_filter module (TDD)

**Files:**
- Create: `backend/app/checkpoint_filter.py`
- Create: `backend/tests/test_checkpoint_filter.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_checkpoint_filter.py`:

```python
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
```

- [ ] **Step 2: Run, verify red**

```bash
cd backend && source venv/bin/activate && pytest tests/test_checkpoint_filter.py -v
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the module**

Create `backend/app/checkpoint_filter.py`:

```python
"""Embedding-similarity pre-filter for compliance checkpoints.

Goal: avoid spending LLM tokens analysing checkpoints that obviously
aren't covered in the transcript (e.g. a "vulnerable customer" checkpoint
on a sales-only conversation). For each checkpoint, embed its
name + description; for the transcript, embed the whole text as one chunk
(or split into N chunks for long calls). Cosine-sim each (chunk, checkpoint)
pair, keep checkpoints whose top similarity score ≥ threshold.

Failure mode discipline: if the embedding API is unavailable or returns
malformed output, return ALL checkpoints unfiltered. NEVER silently
drop checkpoints — that would produce false-pass compliance verdicts.
The pre-filter is a cost optimisation; correctness wins over cost.
"""
from __future__ import annotations

import logging
import math
from typing import Iterable

from app.logger import log
from app.rag.embed import embed_batch


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _chunk_transcript(text: str, max_chars: int = 1500) -> list[str]:
    """Split transcript into ~1.5KB chunks at sentence boundaries.

    1500 chars ≈ 250 words ≈ 90s of typical call audio, which fits well
    inside text-embedding-3-small's 8192-token context with headroom.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    # Split by sentence-ish boundary, then re-pack into chunks ≤ max_chars
    parts = text.replace("\n", " ").split(". ")
    chunks: list[str] = []
    cur = ""
    for p in parts:
        candidate = (cur + ". " + p).strip(". ") if cur else p
        if len(candidate) > max_chars and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def _checkpoint_text(cp: dict) -> str:
    name = (cp.get("name") or "").strip()
    desc = (cp.get("description") or "").strip()
    return f"{name}. {desc}" if desc else name


def select_relevant_checkpoints(
    transcript: str,
    checkpoints: list[dict],
    threshold: float = 0.35,
) -> list[dict]:
    """Return checkpoints whose top chunk-similarity ≥ threshold.

    Empty transcript or empty checkpoints → []. Embedding failure →
    return all checkpoints (graceful degrade — correctness over cost).
    """
    if not checkpoints or not transcript or not transcript.strip():
        return [] if not checkpoints else []

    chunks = _chunk_transcript(transcript)
    if not chunks:
        return []

    try:
        chunk_vecs = embed_batch(chunks)
        cp_texts = [_checkpoint_text(cp) for cp in checkpoints]
        cp_vecs = embed_batch(cp_texts)
    except Exception as e:  # noqa: BLE001 — pre-filter must not break business path
        log.warning(f"PREFILTER_EMBED_FAILED err={type(e).__name__}: {e} — returning all checkpoints")
        return list(checkpoints)

    if len(chunk_vecs) != len(chunks) or len(cp_vecs) != len(checkpoints):
        log.warning("PREFILTER_EMBED_SHAPE_MISMATCH — returning all checkpoints")
        return list(checkpoints)

    kept: list[dict] = []
    dropped = 0
    for cp, cp_vec in zip(checkpoints, cp_vecs):
        top = max((_cosine(cv, cp_vec) for cv in chunk_vecs), default=0.0)
        if top >= threshold:
            kept.append(cp)
        else:
            dropped += 1
    log.info(
        f"PREFILTER kept={len(kept)} dropped={dropped} threshold={threshold:.2f} chunks={len(chunks)}"
    )
    return kept
```

(Note: the `if not checkpoints or not transcript ...` guard short-circuits to `[]` when either is empty. The duplicate `[] if not checkpoints else []` is intentional — both branches return `[]`, kept readable for future change to e.g. `list(checkpoints)` if "no transcript" should mean "skip filter".)

- [ ] **Step 4: Run tests, verify green**

```bash
cd backend && source venv/bin/activate && pytest tests/test_checkpoint_filter.py -v
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/checkpoint_filter.py backend/tests/test_checkpoint_filter.py
git commit -m "feat(cost): add checkpoint_filter — cosine-sim pre-filter for LLM fan-out"
```

---

## Task 3: Wire pre-filter into checkpoint_analyzer (TDD integration)

**Files:**
- Modify: `backend/app/checkpoint_analyzer.py` (around line 555-590, before fan-out)
- Create: `backend/tests/test_checkpoint_analyzer_prefilter.py`

- [ ] **Step 0: Locate the fan-out boundary**

```bash
grep -n "all_batches\|asyncio.gather\|use_agent_analyzer" backend/app/checkpoint_analyzer.py | head -10
```
Confirm the structure: checkpoints flow into `groups`, `groups` becomes `all_batches`, then `_limited` is applied per batch. Insert the pre-filter BEFORE batch construction (i.e. before `groups` is built) so dropped checkpoints don't even count toward batch sizing.

- [ ] **Step 1: Write failing integration test**

Create `backend/tests/test_checkpoint_analyzer_prefilter.py`:

```python
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
```

- [ ] **Step 2: Run, verify red**

```bash
cd backend && source venv/bin/activate && pytest tests/test_checkpoint_analyzer_prefilter.py -v
```
Expected: FAIL — `_maybe_prefilter_checkpoints` doesn't exist.

- [ ] **Step 3: Add the wrapper + wire it in**

In `backend/app/checkpoint_analyzer.py`, near the top imports (group with existing `from app.config import settings`), add:

```python
from app.checkpoint_filter import select_relevant_checkpoints
```

Add the wrapper as a private helper, near the top of the file (after imports, before any analyzer functions):

```python
def _maybe_prefilter_checkpoints(transcript: str, checkpoints: list[dict]) -> list[dict]:
    """Apply embedding pre-filter when the flag is on. No-op otherwise.
    Wrapped so the integration is testable without spinning up the full
    analyzer pipeline."""
    if not settings.embedding_prefilter_enabled:
        return checkpoints
    return select_relevant_checkpoints(
        transcript,
        checkpoints,
        threshold=settings.embedding_prefilter_threshold,
    )
```

Then find the line that constructs `groups` (around line 540-560, just before `all_batches`) and insert a pre-filter call BEFORE the existing checkpoint iteration. Find the exact symbol the function uses for "the list of all checkpoints to analyse" — likely a parameter `checkpoints: list[dict]` on the analyze function. Add at the top of that function body (after argument extraction):

```python
checkpoints = _maybe_prefilter_checkpoints(transcript, checkpoints)
```

This is a single-line in-place reassignment. Downstream `groups`, `all_batches`, and the fan-out reference the same `checkpoints` variable, so they automatically operate on the filtered list.

If the analyze function takes `checkpoints` via a different name (e.g. `cps`, `compliance_checkpoints`), use that name instead. Confirm by grepping the function signature.

- [ ] **Step 4: Run tests, verify green**

```bash
cd backend && source venv/bin/activate && pytest tests/test_checkpoint_analyzer_prefilter.py tests/test_checkpoint_filter.py -v
```
Expected: PASS (8 tests total: 5 from filter + 3 from analyzer integration).

- [ ] **Step 5: Spot-check no regression on existing analyzer tests**

```bash
cd backend && source venv/bin/activate && pytest tests/ -k "checkpoint or analyze" -v 2>&1 | tail -10
```
Expected: previously-passing tests still pass; pre-existing failures (Supabase env, etc.) reproduce identically — verify via stash on parent commit if any new failure appears.

- [ ] **Step 6: Commit**

```bash
git add backend/app/checkpoint_analyzer.py backend/tests/test_checkpoint_analyzer_prefilter.py
git commit -m "feat(cost): wire embedding pre-filter into checkpoint_analyzer fan-out"
```

---

## Task 4: A/B parity harness (TDD)

**Files:**
- Create: `backend/scripts/ab_parity.py`
- Create: `backend/tests/test_ab_parity.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_ab_parity.py`:

```python
"""A/B parity harness — unit tests for parity computation + report shape.
Live LLM calls are out of scope for these tests; the analyze callable is
mocked so the harness logic is the sole subject under test."""
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
```

- [ ] **Step 2: Run, verify red**

```bash
cd /Users/gomaa/Documents/Compliance && backend/venv/bin/python -m pytest backend/tests/test_ab_parity.py -v
```
Expected: FAIL — `backend.scripts.ab_parity` doesn't exist.

- [ ] **Step 3: Implement the harness**

Create `backend/scripts/ab_parity.py`:

```python
"""A/B parity harness for Wave 4 cost flags.

Workflow:
  1. Pick N call_ids (CLI flag --sample-size, default 50; --calls a,b,c
     overrides for explicit list).
  2. For each call, run the analyze callable twice:
        baseline  = use_agent_analyzer=False, embedding_prefilter_enabled=False
        candidate = use_agent_analyzer=True,  embedding_prefilter_enabled=True
  3. Compare verdict.status. Compute parity %.
  4. Write JSON report to --out (default: ab-parity-report.json in cwd).
  5. Print summary; exit 0 if parity ≥ --threshold (default 98.0), else 1.

Designed to be invoked manually: real LLM costs apply on every call.
The unit tests mock the analyze callable so the harness logic is testable
without burning credits.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Iterable


@dataclass
class Verdict:
    call_id: str
    status: str
    score: float


BASELINE_FLAGS = {"use_agent_analyzer": False, "embedding_prefilter_enabled": False}
CANDIDATE_FLAGS = {"use_agent_analyzer": True, "embedding_prefilter_enabled": True}


def compute_parity(baseline: list[Verdict], candidate: list[Verdict]) -> dict:
    """Diff two verdict lists by call_id. Returns {parity_pct, matches,
    mismatches, diffs[]}.

    Both lists must be in the same order (call_id-aligned). Missing call
    ids on either side count as mismatches."""
    by_id_b = {v.call_id: v for v in baseline}
    by_id_c = {v.call_id: v for v in candidate}
    all_ids = sorted(by_id_b.keys() | by_id_c.keys())
    matches = 0
    diffs: list[dict] = []
    for cid in all_ids:
        b = by_id_b.get(cid)
        c = by_id_c.get(cid)
        if b is None or c is None or b.status != c.status:
            diffs.append({
                "call_id": cid,
                "baseline_status": b.status if b else None,
                "candidate_status": c.status if c else None,
                "baseline_score": b.score if b else None,
                "candidate_score": c.score if c else None,
            })
        else:
            matches += 1
    total = len(all_ids)
    parity_pct = (100.0 * matches / total) if total else 100.0
    return {
        "parity_pct": parity_pct,
        "matches": matches,
        "mismatches": len(diffs),
        "diffs": diffs,
    }


def run_ab(
    call_ids: Iterable[str],
    analyze: Callable[..., Verdict],
    out_path: str | Path,
) -> dict:
    """Run baseline + candidate flag profiles across call_ids; write report; return summary."""
    call_ids = list(call_ids)
    baseline = [analyze(cid, flags=BASELINE_FLAGS) for cid in call_ids]
    candidate = [analyze(cid, flags=CANDIDATE_FLAGS) for cid in call_ids]
    parity = compute_parity(baseline, candidate)

    report = {
        "sample_size": len(call_ids),
        "baseline_flags": BASELINE_FLAGS,
        "candidate_flags": CANDIDATE_FLAGS,
        **parity,
    }
    Path(out_path).write_text(json.dumps(report, indent=2))
    return report


def _live_analyze(call_id: str, *, flags: dict) -> Verdict:
    """Production analyze callable — invoked when the script runs against
    a real DB. Mutates env-driven settings, runs the same pipeline path
    used by `_step_analyze_checkpoints` + `_step_score`, returns the
    finalized verdict.

    NOT exercised by unit tests (they pass their own mock). Kept here so
    the CLI form is self-contained.
    """
    # Defer imports to avoid heavy startup cost on `pytest --collect-only`
    from app.config import settings
    from app.database import SessionLocal
    from app.models import Call
    from app.pipeline import _step_analyze_checkpoints, _step_score, _step_finalize

    settings.use_agent_analyzer = flags["use_agent_analyzer"]
    settings.embedding_prefilter_enabled = flags["embedding_prefilter_enabled"]

    db = SessionLocal()
    try:
        call = db.query(Call).filter(Call.id == call_id).first()
        if call is None:
            return Verdict(call_id=call_id, status="error_missing", score=0.0)
        # Reuse the existing pipeline step boundaries; they mutate the Call row.
        analysis = _step_analyze_checkpoints(call_id, {"transcript": call.transcript or ""}, db)  # type: ignore[arg-type]
        _step_score(call_id, analysis, db)
        _step_finalize(call_id, db)
        db.refresh(call)
        return Verdict(
            call_id=call_id,
            status=getattr(call, "compliance_status", "unknown") or "unknown",
            score=float(getattr(call, "score", 0.0) or 0.0),
        )
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="A/B parity harness for Wave 4 cost flags")
    p.add_argument("--sample-size", type=int, default=50, help="Number of recent calls to sample")
    p.add_argument("--calls", default=None, help="Comma-separated list of call_ids (overrides --sample-size)")
    p.add_argument("--out", default="ab-parity-report.json")
    p.add_argument("--threshold", type=float, default=98.0, help="Min parity %% to exit 0")
    args = p.parse_args(argv)

    if args.calls:
        call_ids = [s.strip() for s in args.calls.split(",") if s.strip()]
    else:
        from app.database import SessionLocal
        from app.models import Call
        db = SessionLocal()
        try:
            rows = (
                db.query(Call.id)
                .filter(Call.transcript.isnot(None))
                .filter(Call.script_id.isnot(None))
                .order_by(Call.id.desc())
                .limit(args.sample_size)
                .all()
            )
            call_ids = [r[0] for r in rows]
        finally:
            db.close()

    if not call_ids:
        print("No call_ids resolved. Pass --calls or seed sample data.", file=sys.stderr)
        return 2

    summary = run_ab(call_ids, analyze=_live_analyze, out_path=args.out)
    print(json.dumps({k: v for k, v in summary.items() if k != "diffs"}, indent=2))
    print(f"Report: {args.out} ({summary['mismatches']} mismatch(es) recorded)")
    return 0 if summary["parity_pct"] >= args.threshold else 1


if __name__ == "__main__":
    sys.exit(main())
```

If the test import path `from backend.scripts.ab_parity import ...` doesn't resolve (pytest rootdir = `backend/`), apply the same sys.path injection trick as W3-T8:

```python
# At the top of test_ab_parity.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.scripts.ab_parity import compute_parity, run_ab, Verdict
```

Document the workaround in the test file as a comment.

- [ ] **Step 4: Run, verify green**

```bash
cd /Users/gomaa/Documents/Compliance && backend/venv/bin/python -m pytest backend/tests/test_ab_parity.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/ab_parity.py backend/tests/test_ab_parity.py
git commit -m "feat(cost): add ab_parity.py — A/B parity harness for Wave 4 flag flips"
```

---

## Task 5: Cost-optimization runbook

**Files:**
- Create: `docs/cost-optimization.md`
- Modify: `.env.example`

- [ ] **Step 1: Write `docs/cost-optimization.md`**

Create `/Users/gomaa/Documents/Compliance/docs/cost-optimization.md`:

```markdown
# Cost optimisation runbook (Wave 4)

Two flags govern LLM cost on the analyse path:

| Flag | Default after Wave 4 | What it does |
|---|---|---|
| `use_agent_analyzer` | `True` | Run Gemini Flash first, escalate to Sonnet only on low-confidence checkpoints. |
| `embedding_prefilter_enabled` | `True` | Drop checkpoints whose top transcript-chunk cosine similarity is below `embedding_prefilter_threshold` (default 0.35) before the LLM fan-out. |

Either can be disabled in prod via env var without code change. Both are A/B-gated: parity ≥ 98 % vs. baseline on a 50-call sample is required before flipping defaults. Re-run the parity check after any data drift or model upgrade.

## Running the A/B parity harness

```bash
# 1. Seed the sample. Either pick the 50 most-recent calls automatically:
cd /Users/gomaa/Documents/Compliance/backend && \
  python -m scripts.ab_parity --sample-size 50 --out ab-50.json

# Or pin an explicit list:
cd /Users/gomaa/Documents/Compliance/backend && \
  python -m scripts.ab_parity --calls c1,c2,...,c50 --out ab-50.json
```

The script:
1. Picks N calls with non-null `transcript` and `script_id`.
2. Runs the `_step_analyze_checkpoints → _step_score → _step_finalize` pipeline twice per call — once with both flags off (baseline), once with both flags on (candidate).
3. Diffs the resulting `compliance_status`. Computes parity %.
4. Writes `ab-50.json` with sample_size, parity_pct, matches, mismatches, and a per-call diff list.
5. Exits 0 if parity ≥ `--threshold` (default 98.0), else 1.

Cost note: the harness performs **2N full analyse runs** against the live LLM provider. Budget accordingly. ~50 calls × 2 ≈ 100 analyse runs. Manual smoke; do not invoke from CI.

## Flag flip checklist

Before flipping the prod defaults in `app/config.py`:

- [ ] A/B run completed against ≥50 calls.
- [ ] Parity ≥ 98 %.
- [ ] All mismatches reviewed manually — none of them flip a `pass` to `fail` on a checkpoint that auditors would accept.
- [ ] Mean LLM cost-per-call drop verified ≥ 5× via the cost dashboard (Wave 2 LLM dashboard panel "calls/min" before vs after).
- [ ] Rollback plan: env vars `USE_AGENT_ANALYZER=false` and `EMBEDDING_PREFILTER_ENABLED=false` restore prior behaviour without redeploy.

## Rollback

If a regression appears after the flag flip:

```bash
# On the Contabo VPS
echo "USE_AGENT_ANALYZER=false" >> /opt/compliance/.env
echo "EMBEDDING_PREFILTER_ENABLED=false" >> /opt/compliance/.env
docker compose restart compliance-backend
```

No code change required. Inngest functions pick up the env override on the next event.

## Tuning `embedding_prefilter_threshold`

Default 0.35 was chosen to drop ~30 % of checkpoints on typical sales calls without losing recall on edge cases. To tune:

1. Run the parity harness at increasing thresholds: 0.30, 0.35, 0.40, 0.45.
2. Plot parity_pct vs cost_drop. Pick the highest threshold where parity stays ≥ 98 %.
3. Update `embedding_prefilter_threshold` in `.env` (no code change).

Threshold above ~0.55 starts losing legitimate checkpoints — stop tuning there.

## Observability

Wave 2 dashboards already cover the cost story:
- **LLM dashboard** → `llm_calls_total{escalated="true"}` rate (escalation rate after Wave 4 should be ≪ pre-Wave-4 baseline).
- **Pipeline dashboard** → `analyze_checkpoints` step duration p50/p95 should drop with the pre-filter on.
- Logs filtered by `PREFILTER kept=…` give per-call evidence the pre-filter ran.
```

- [ ] **Step 2: Append Wave 4 env vars to `.env.example`**

In `/Users/gomaa/Documents/Compliance/.env.example`, append:

```bash
# ─── Wave 4 — Cost optimization ───────────────────────────────────
EMBEDDING_PREFILTER_ENABLED=false   # A/B-gated; flip to true after parity ≥ 98 %
EMBEDDING_PREFILTER_THRESHOLD=0.35
USE_AGENT_ANALYZER=false            # A/B-gated; flip to true after parity ≥ 98 %
```

- [ ] **Step 3: Commit**

```bash
git add docs/cost-optimization.md .env.example
git commit -m "docs(cost): wave 4 runbook — A/B harness, flag flip checklist, rollback"
```

---

## Task 6: A/B parity sample run (HUMAN GATE)

**Manual gate; skip during automated execution. Resume after Wave 4 implementation tasks 1-5 are merged or at least verified locally.**

- [ ] **Step 1: Boot dev backend with sample data**

```bash
cd /Users/gomaa/Documents/Compliance/backend && uvicorn app.main:app --port 8001 --reload &
# Confirm /healthz, /readyz, /metrics all 200.
```

- [ ] **Step 2: Confirm ≥50 sample calls exist**

```bash
psql "$DATABASE_URL" -c "
SELECT COUNT(*) FROM calls
WHERE transcript IS NOT NULL AND script_id IS NOT NULL;
"
```
Expected: ≥ 50. If not, ingest more sample calls via `/api/calls/upload` or use a backup restored to the dev DB.

- [ ] **Step 3: Run the harness**

```bash
cd backend && python -m scripts.ab_parity --sample-size 50 --out ab-50.json --threshold 98
```

- [ ] **Step 4: Inspect the report**

```bash
cat ab-50.json | jq '.parity_pct, .mismatches'
cat ab-50.json | jq '.diffs[]'
```

If parity ≥ 98 % and mismatches are auditor-acceptable, proceed to T7. Else open a follow-up issue documenting the mismatch pattern and stop the wave here — DO NOT flip flags.

- [ ] **Step 5: Document in `claude-progress.txt`**

Append:

```
[YYYY-MM-DD] WAVE 4 A/B: Ran ab_parity --sample-size 50 against dev DB.
parity_pct=XX.X% mismatches=N. Top mismatch pattern: <one-line summary>.
Decision: <flip / hold / tune threshold>.
```

---

## Task 7: Flip flag defaults (only after T6 passes)

**Files:**
- Modify: `backend/app/config.py`

- [ ] **Step 1: Flip both defaults**

In `backend/app/config.py`, change:

```python
    use_agent_analyzer: bool = False  # Feature flag: False = old batched analyzer, True = new agent
    # ...
    embedding_prefilter_enabled: bool = False  # Off by default — A/B-gated
```

to:

```python
    use_agent_analyzer: bool = True  # Wave 4: tiered LLM enabled by default after A/B parity ≥ 98 %
    # ...
    embedding_prefilter_enabled: bool = True  # Wave 4: pre-filter enabled after A/B parity ≥ 98 %
```

- [ ] **Step 2: Verify settings load**

```bash
cd backend && source venv/bin/activate && python -c "from app.config import settings; print('use_agent_analyzer', settings.use_agent_analyzer); print('embedding_prefilter_enabled', settings.embedding_prefilter_enabled)"
```
Expected: both `True`.

- [ ] **Step 3: Spot-check existing tests**

```bash
cd backend && source venv/bin/activate && pytest tests/test_checkpoint_filter.py tests/test_checkpoint_analyzer_prefilter.py tests/test_ab_parity.py -v
```
Expected: 12 passed.

If any pre-existing test in the wider suite asserts `use_agent_analyzer is False` or similar, that test was implicitly relying on the default. Update it (or accept the new default) per the test's intent.

- [ ] **Step 4: Commit**

```bash
git add backend/app/config.py
git commit -m "feat(cost): flip use_agent_analyzer + embedding_prefilter_enabled to True (post-A/B)"
```

---

## Task 8: Push + open PR

- [ ] **Step 1: Push branch**

```bash
git push -u origin feat/wave4-cost
```

- [ ] **Step 2: Create PR**

```bash
gh pr create \
  --base main \
  --head feat/wave4-cost \
  --title "Wave 4 — Cost: Embedding pre-filter + tiered LLM (A/B-gated)" \
  --body-file - <<'EOF'
## Summary

- New `app/checkpoint_filter.py` exposes `select_relevant_checkpoints(transcript, checkpoints, threshold)` using cosine similarity over text-embedding-3-small.
- `app/checkpoint_analyzer.py` calls `_maybe_prefilter_checkpoints` before the LLM fan-out — when `embedding_prefilter_enabled=True`, irrelevant checkpoints are dropped before any LLM call.
- `backend/scripts/ab_parity.py` runs N calls under each flag profile and writes a parity report. CLI exits 0 iff parity ≥ threshold (default 98 %).
- Defaults flipped in `app/config.py`: `use_agent_analyzer=True`, `embedding_prefilter_enabled=True` (after the A/B run met threshold).
- Rollback is one env var; no code change required.

## Test plan
- [x] `pytest tests/test_checkpoint_filter.py tests/test_checkpoint_analyzer_prefilter.py tests/test_ab_parity.py` — 12 passed (5 + 3 + 4).
- [x] No regression in pre-existing `test_checkpoint_analyzer` tests (verified via stash on parent commit; pre-existing failures reproduce).
- [ ] **Human follow-up:** A/B sample run against ≥50 dev calls; report in `claude-progress.txt`.

## Reviewer focus
1. `select_relevant_checkpoints` graceful-degrades to "all checkpoints" on embedding failure — never silently drops checkpoints due to infra (compliance-correctness over cost).
2. Threshold default 0.35 chosen empirically — tunable via env. See `docs/cost-optimization.md`.
3. `_maybe_prefilter_checkpoints` is the single integration point in `checkpoint_analyzer.py` — flag-off path is byte-identical to pre-Wave-4 behaviour.
4. `ab_parity.py` mocks the analyze fn in tests; live invocation via CLI not exercised in CI.

## Out of scope (Wave 5+)
- deploy.yml SSH workflow + branch protection (Wave 5)
- Storage retention enforcement in code
- Reanalyze rate limiting

## Human follow-ups before merge
1. Run `python -m scripts.ab_parity --sample-size 50 --out ab-50.json` locally; attach the report to the PR.
2. Confirm parity ≥ 98 % before approving the flag-flip commit.
3. After merge, watch the LLM Grafana dashboard for the expected escalation-rate drop.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

- [ ] **Step 3: Note PR URL**

Capture the URL from gh output. Don't block on `gh pr checks --watch`.

---

## Wave 4 acceptance gate

- [ ] All 8 tasks complete and committed (one task = one commit minimum, plus fix-loop commits as needed).
- [ ] CI green on PR.
- [ ] A/B parity ≥ 98 % verified locally before T7 flag flip.
- [ ] `docs/cost-optimization.md` runbook present.
- [ ] Mean LLM cost-per-call drop visible on the LLM Grafana dashboard within 24 h of merge.
- [ ] `claude-progress.txt` updated with WAVE 4 A/B entry.

Wave 5 (deploy.yml SSH + branch protection + final docs pass) is the next plan to write after Wave 4 merges.
