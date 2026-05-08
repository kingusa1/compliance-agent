"""Segment detector — splits a transcript into Watt's 6-stage taxonomy rows.

Algorithm:
  1. Read anchor phrases per stage from script.checkpoints (JSON). Each
     checkpoint can declare a `stage` and an `anchor_phrases` list. We
     fall back to a small built-in vocabulary if the script doesn't
     declare anchors so detection still produces something useful when
     the supplier hasn't been onboarded.
  2. Walk the per-word stream in order. For each word, build a 5-word
     window (word ± 2) and check (case-insensitive substring) whether
     any anchor phrase for any stage appears in it. First hit per stage
     transitions the active stage and closes the previous segment.
  3. Speaker boundaries within a stage only split sub-segments when the
     same speaker holds for >3 consecutive words (so quick interjections
     don't fragment the row).

Stage vocabulary is locked: intro | qualification | pitch | transfer |
verbal | close (per gates Step 4 + digest §4). Don't add or rename
without coordinating with rules_catalog.json and the writer in
process_call.py — rule routing depends on these exact strings.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from app.models import CallSegment, Script

# Locked Watt taxonomy. The order is the *expected* call flow but
# detection doesn't enforce ordering — a closer call can legitimately
# skip qualification and jump straight to verbal.
STAGES: tuple[str, ...] = (
    "intro",
    "qualification",
    "pitch",
    "transfer",
    "verbal",
    "close",
)

# Fallback anchors used when the script doesn't declare its own.
# Keep these short and high-signal — these only fire when no per-supplier
# anchors are defined, so they need to be obvious.
_DEFAULT_ANCHORS: dict[str, list[str]] = {
    "intro":         ["good morning", "good afternoon", "hello", "calling from"],
    "qualification": ["bill payer", "decision maker", "contract end", "renewal"],
    "pitch":         ["save you", "better rate", "lower price", "we can offer"],
    "transfer":      ["putting you through", "transferring you", "verification team"],
    "verbal":        ["verbal contract", "i confirm", "to confirm", "unit rate"],
    "close":         ["thank you", "have a good", "speak soon", "goodbye"],
}


def _anchors_from_script(script: Script | None) -> dict[str, list[str]]:
    """Pull anchor phrases per stage from a script, falling back to defaults.

    Each checkpoint in `script.checkpoints` may carry a `stage` and an
    optional `anchor_phrases` list; we union those across checkpoints so
    a stage with multiple checkpoints contributes all its anchors.
    """
    anchors: dict[str, list[str]] = {s: list(_DEFAULT_ANCHORS.get(s, [])) for s in STAGES}
    if script is None or not getattr(script, "checkpoints", None):
        return anchors

    try:
        checkpoints = json.loads(script.checkpoints)
    except (TypeError, ValueError, json.JSONDecodeError):
        return anchors

    if not isinstance(checkpoints, list):
        return anchors

    for cp in checkpoints:
        if not isinstance(cp, dict):
            continue
        stage = cp.get("stage")
        if stage not in STAGES:
            continue
        phrases = cp.get("anchor_phrases") or []
        if isinstance(phrases, list):
            for p in phrases:
                if isinstance(p, str) and p.strip():
                    anchors[stage].append(p.strip())
    return anchors


def _windowed_text(words: list[dict], idx: int, radius: int = 2) -> str:
    """Build the lower-cased text of words[idx-radius .. idx+radius]."""
    lo = max(0, idx - radius)
    hi = min(len(words), idx + radius + 1)
    parts: list[str] = []
    for w in words[lo:hi]:
        token = w.get("punctuated_word") or w.get("word") or ""
        if token:
            parts.append(str(token))
    return " ".join(parts).lower()


def _match_stage(window: str, anchors: dict[str, list[str]]) -> str | None:
    """Return the first stage whose anchor phrase appears in the window."""
    for stage in STAGES:
        for phrase in anchors[stage]:
            if not phrase:
                continue
            # Substring match is sufficient and cheaper than full regex —
            # but anchors may contain regex metacharacters in the future,
            # so we escape defensively.
            if re.search(re.escape(phrase.lower()), window):
                return stage
    return None


def _dominant_speaker(words: Iterable[dict]) -> str | None:
    counts: dict[str, int] = {}
    for w in words:
        spk = w.get("speaker")
        if spk is None:
            continue
        key = str(spk)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


def _speaker_run_at(words: list[dict], idx: int) -> int:
    """Length of the consecutive same-speaker run starting at idx."""
    if idx >= len(words):
        return 0
    target = words[idx].get("speaker")
    if target is None:
        return 0
    run = 0
    for w in words[idx:]:
        if w.get("speaker") == target:
            run += 1
        else:
            break
    return run


def _excerpt(words: list[dict]) -> str:
    parts: list[str] = []
    for w in words:
        token = w.get("punctuated_word") or w.get("word") or ""
        if token:
            parts.append(str(token))
    return " ".join(parts).strip()


def _emit(call_id: str, idx: int, stage: str, words: list[dict]) -> CallSegment | None:
    """Materialise a CallSegment row from a slice of the word stream."""
    if not words:
        return None
    start = words[0].get("start")
    end = words[-1].get("end")
    return CallSegment(
        call_id=call_id,
        idx=idx,
        stage=stage,
        transcript_excerpt=_excerpt(words)[:2000],
        speaker=_dominant_speaker(words),
        start_s=float(start) if start is not None else None,
        end_s=float(end) if end is not None else None,
    )


def detect_segments(
    call_id: str,
    transcript: str,
    word_data: list[dict],
    script: Script | None,
) -> list[CallSegment]:
    """Detect Watt-vocab segments for one call.

    Returns a list of unsaved `CallSegment` ORM rows. The caller (the
    finalize-step writer in workflows/process_call.py) is responsible
    for the idempotent delete-then-insert.
    """
    if not word_data:
        return []

    anchors = _anchors_from_script(script)
    segments: list[CallSegment] = []
    seg_idx = 0

    current_stage: str | None = None
    current_buf: list[dict] = []
    current_speaker_run_start: int = 0  # word index where current speaker's run began

    for i, word in enumerate(word_data):
        window = _windowed_text(word_data, i)
        matched = _match_stage(window, anchors)

        # 1) Stage transition (matched a new anchor for a different stage).
        if matched is not None and matched != current_stage:
            seg = _emit(call_id, seg_idx, current_stage or matched, current_buf)
            if seg is not None and current_stage is not None:
                segments.append(seg)
                seg_idx += 1
            current_stage = matched
            current_buf = [word]
            current_speaker_run_start = i
            continue

        # 2) Speaker boundary inside same stage. Only split when the new
        #    speaker holds for >3 consecutive words to avoid splitting on
        #    quick "uh huh" interjections.
        prev_speaker = current_buf[-1].get("speaker") if current_buf else None
        this_speaker = word.get("speaker")
        if (
            current_stage is not None
            and prev_speaker is not None
            and this_speaker is not None
            and prev_speaker != this_speaker
            and _speaker_run_at(word_data, i) > 3
        ):
            seg = _emit(call_id, seg_idx, current_stage, current_buf)
            if seg is not None:
                segments.append(seg)
                seg_idx += 1
            current_buf = [word]
            current_speaker_run_start = i
            continue

        # 3) Default — extend the current buffer.
        current_buf.append(word)

    # Flush trailing buffer.
    if current_buf and current_stage is not None:
        tail = _emit(call_id, seg_idx, current_stage, current_buf)
        if tail is not None:
            segments.append(tail)

    return segments
