"""Content Classifier — segment a transcript into the 4 canonical
compliance segments (lead_gen / pre_sales / verbal / loa).

Background
----------
The old pipeline picked one rubric per recording based on the reviewer's
manual call_type tag. Reality: a single recording can contain multiple
segments stitched together (e.g. an E.ON closer recording with
pre_sales → verbal → loa back-to-back) OR just one segment.

This agent reads the transcript ONCE and returns 1-4 segments with
word-index boundaries. The downstream pipeline then routes each segment
to its own rubric via ``rubric_router.route_for_segment`` and grades
each independently, aggregating to a single call-level verdict.

Per Aly's spec confirmed 2026-05-12:
  - The 88-rule phrase pack grades BOTH lead_gen and pre_sales segments
    (different content, identical rule set).
  - Verbal segments grade against the supplier-specific verbal-contract
    script (E.ON NHH+HH = 26 cps; British Gas Acquisition = 21; …).
  - LOA segments grade against the supplier's LOA script (E.ON only —
    non-E.ON LOAs are paper/DocuSign and shouldn't appear in audio).
  - If the classifier returns [], the pipeline halts the call with
    ``status="needs_classification"`` for reviewer manual triage.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from app.analysis import _call_llm
from app.logger import log


# Canonical segment types — MUST match _CALL_TYPE_TO_PHASE keys in
# deal_lifecycle.py and the CallType enum in the frontend.
VALID_SEGMENT_TYPES: frozenset[str] = frozenset(
    {"lead_gen", "pre_sales", "verbal", "loa"}
)


@dataclass(frozen=True)
class Segment:
    """One detected segment inside a recording."""

    segment_type: str               # lead_gen | pre_sales | verbal | loa
    start_word_idx: int             # 0-based, inclusive
    end_word_idx: int               # 0-based, inclusive
    confidence: float               # 0.0 - 1.0
    reasoning: str                  # short human-readable explanation


CONTENT_CLASSIFIER_PROMPT = """You are reading a UK energy-broker compliance call transcript and identifying which compliance-relevant SEGMENTS are inside.

The four canonical segment types and their distinguishing signals:

1. **lead_gen** — FIRST contact taken by the lead-generation agent. Cold/warm intro, qualification, decision-maker capture. Signals: "is that [name]?", "I'm calling from Watt Utilities", "are you the decision maker", "your current energy contract", "shall I send across prices", "I'll pass you to my colleague".

2. **pre_sales** — Warm-up at the START of the closer call. A SECOND closer agent re-introduces themselves after the lead-gen handover, re-confirms identity/authority, and prepares for the verbal contract. Signals: "thanks for taking my colleague's call", "let me re-confirm a few details before we start the recording", "are you still the decision maker", "before we begin the legally binding contract".

3. **verbal** — The LEGALLY BINDING verbal-contract reading. Closer reads supplier-mandated verbatim script: contract length, unit rate p/kWh, standing charge, VAT/CCL, cooling-off, Ombudsman. Explicit customer "yes/I agree" responses. Signals: explicit rate + standing charge, "this is a legally binding contract", "do you agree to be bound by these terms", "is that correct" with customer "yes".

4. **loa** — E.ON ONLY. Letter of Authority wording bundled inside the closer recording for E.ON Next. Customer authorises Watt to act on their behalf. Signals: "do you authorise Watt to act on your behalf", "letter of authority", "this gives us 12 months", "to obtain information about your account from [supplier]", "to negotiate with [supplier]". For every other supplier the LOA is a DocuSign paper document — NEVER emit a loa segment unless the supplier is E.ON (or E.ON Next / EON).

The transcript is shown below with WORD INDICES on the left of each block of 10 words. Use those indices to mark segment boundaries.

OUTPUT FORMAT
Return a JSON array. Each element is one detected segment object with EXACTLY these keys:

  "segment_type"   — one of: "lead_gen", "pre_sales", "verbal", "loa"
  "start_word_idx" — integer; inclusive, 0-based word index where this segment STARTS
  "end_word_idx"   — integer; inclusive, 0-based word index where this segment ENDS
  "confidence"     — float 0.0 to 1.0; how sure you are this segment is present and bounded correctly
  "reasoning"      — 1-2 sentences explaining which signals drove your decision

RULES
- The Watt workflow has only TWO top-level call stages:
    Opener = the lead_gen recording (one segment: lead_gen)
    Closer = the closer recording. For non-E.ON suppliers the closer
             contains pre_sales + verbal (2 segments, NO loa — LOA is a
             DocuSign document for non-E.ON). For E.ON Next the closer
             contains pre_sales + verbal + loa (3 segments, LOA bundled).
- Common detected shapes:
  * Just lead_gen (the opener recording).
  * pre_sales + verbal (a non-E.ON closer recording).
  * pre_sales + verbal + loa (an E.ON closer recording).
  * Just one of any (reviewer uploaded that segment in isolation).
- DO NOT emit a loa segment unless the supplier is E.ON.
- Segments must be NON-OVERLAPPING and listed in transcript order.
- end_word_idx of segment N must be < start_word_idx of segment N+1.
- If you cannot identify ANY compliance-relevant segment (e.g. transcript is too short, foreign language, test tone, music), return [].
- Use double quotes (JSON). No code fences. No prose outside the array.
- Be conservative: only emit a segment if confidence >= 0.5 in your own judgement.

TRANSCRIPT (with word indices):
{indexed_transcript}

JSON ARRAY:"""


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _build_indexed_transcript(word_data: list[dict], max_words: int = 3000) -> str:
    """Render the transcript with word-index annotations every 10 words so
    the LLM can emit accurate boundary indices. Caps at max_words to keep
    the prompt under model limits.
    """
    if not word_data:
        return ""
    pieces: list[str] = []
    chunk: list[str] = []
    for i, w in enumerate(word_data[:max_words]):
        if i % 10 == 0:
            if chunk:
                pieces.append(" ".join(chunk))
                chunk = []
            pieces.append(f"[{i}]")
        token = (w.get("punctuated_word") or w.get("word") or "").strip()
        if token:
            chunk.append(token)
    if chunk:
        pieces.append(" ".join(chunk))
    return " ".join(pieces)


def _coerce_segment(raw: dict, max_word_idx: int) -> Optional[Segment]:
    if not isinstance(raw, dict):
        return None
    seg_type = str(raw.get("segment_type") or "").strip().lower().replace("-", "_")
    if seg_type not in VALID_SEGMENT_TYPES:
        return None
    try:
        start = int(raw.get("start_word_idx", -1))
        end = int(raw.get("end_word_idx", -1))
    except (TypeError, ValueError):
        return None
    if start < 0 or end < start:
        return None
    # Clamp to actual transcript bounds.
    start = max(0, min(start, max_word_idx))
    end = max(start, min(end, max_word_idx))
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reasoning = str(raw.get("reasoning") or "")[:500]
    return Segment(
        segment_type=seg_type,
        start_word_idx=start,
        end_word_idx=end,
        confidence=confidence,
        reasoning=reasoning,
    )


async def classify_content(
    transcript: str,
    word_data: list[dict],
    *,
    supplier: str | None = None,
    timeout: float = 60.0,
    min_confidence: float = 0.5,
) -> list[Segment]:
    """Read a transcript + per-word data and return 1-4 detected segments.

    Returns [] if:
      - the transcript is too short to be useful (< 50 chars),
      - the LLM call fails for any reason,
      - the LLM returns valid JSON but no segment meets the min_confidence
        threshold.

    For non-E.ON suppliers, any `loa` segment in the LLM output is
    dropped + warned — LOA audio shouldn't exist for non-E.ON per Aly's
    spec (always paper/DocuSign).
    """
    if not transcript or len(transcript.strip()) < 50:
        log.info("📍 classify_content: transcript too short, returning []")
        return []
    if not word_data:
        log.info("📍 classify_content: no word_data, returning []")
        return []

    indexed = _build_indexed_transcript(word_data)
    prompt = CONTENT_CLASSIFIER_PROMPT.replace("{indexed_transcript}", indexed)

    try:
        raw = await _call_llm(prompt, timeout=timeout)
    except Exception as e:
        log.warning(f"📍 classify_content LLM failed: {e}")
        return []

    body = _strip_fences(raw)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        start_idx, end_idx = body.find("["), body.rfind("]")
        if 0 <= start_idx < end_idx:
            try:
                parsed = json.loads(body[start_idx : end_idx + 1])
            except json.JSONDecodeError:
                log.warning(
                    f"📍 classify_content unparseable JSON: {body[:200]!r}"
                )
                return []
        else:
            log.warning(
                f"📍 classify_content no JSON array in response: {body[:200]!r}"
            )
            return []

    if not isinstance(parsed, list):
        log.warning("📍 classify_content LLM returned non-array")
        return []

    max_idx = max(0, len(word_data) - 1)
    canon: list[Segment] = []
    for item in parsed:
        seg = _coerce_segment(item, max_idx)
        if seg is None:
            continue
        if seg.confidence < min_confidence:
            log.info(
                f"📍 dropping low-confidence {seg.segment_type} segment "
                f"(conf={seg.confidence:.2f} < {min_confidence})"
            )
            continue
        canon.append(seg)

    # Non-E.ON LOA segments are anomalies — LOAs are paper/DocuSign for
    # non-E.ON suppliers per Aly's spec. Drop them and warn.
    if supplier and "eon" not in supplier.lower() and "e.on" not in supplier.lower():
        before = len(canon)
        canon = [s for s in canon if s.segment_type != "loa"]
        if len(canon) < before:
            log.warning(
                f"📍 dropped {before - len(canon)} LOA segment(s) for "
                f"non-E.ON supplier {supplier!r} — LOA audio not expected"
            )

    # Sort by start index + de-overlap (rare but possible).
    canon.sort(key=lambda s: s.start_word_idx)
    deduped: list[Segment] = []
    last_end = -1
    for seg in canon:
        if seg.start_word_idx <= last_end:
            # Overlap — skip the later one; LLM occasionally double-counts.
            continue
        deduped.append(seg)
        last_end = seg.end_word_idx

    log.info(
        f"📍 classify_content: {len(deduped)} segment(s) detected — "
        + ", ".join(
            f"{s.segment_type}[{s.start_word_idx}..{s.end_word_idx} c={s.confidence:.2f}]"
            for s in deduped
        )
    )
    return deduped
