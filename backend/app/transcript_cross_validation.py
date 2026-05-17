"""Two-layer transcript cross-validation: Deepgram vs AssemblyAI.

Both Universal-3 Pro (AssemblyAI) and Nova-3 (Deepgram) run on every
upload in parallel via ``pipeline._step_transcribe``. AssemblyAI is the
primary used downstream for checkpoint scoring. Deepgram is kept as a
second independent transcript — Watt's compliance reviewers need to
know when the two engines disagree, because a disagreement on a
business name, supplier, or verbal-confirmation phrase is the most
common source of false-pass / false-fail verdicts.

This module produces a structured agreement report from the two
transcripts so the reviewer UI can surface a "transcription divergence
detected" chip and link the reviewer straight to the disagreement
window.

Design constraints:

- Pure Python stdlib (``difflib.SequenceMatcher``) — no extra LLM call
  on the hot path. Cost per call: O(n) where n = word count, typically
  ~3-5ms on a 10-min call.
- Speaker labels and ``[MM:SS]`` timestamps differ in formatting
  between the two engines, so we normalise both transcripts down to
  word-only token streams before diffing.
- The report intentionally caps ``disagreement_samples`` at 8 — enough
  for a reviewer to spot-check the worst windows without bloating the
  ``Call.meta`` JSONB blob.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# Common stopwords / filler tokens that disagree between engines for
# style reasons (one redacts "uhh", the other keeps it) — excluding
# these from agreement scoring stops style noise from dominating the
# real signal (names, suppliers, key phrases).
_STYLE_FILLER: frozenset[str] = frozenset(
    {
        "um", "uh", "uhh", "umm", "uhm", "er", "ah", "ahh", "mm",
        "mmhmm", "mmm", "yeah", "yep", "ok", "okay", "right", "so",
        "well", "like", "just", "you", "know",
    }
)

# Strip speaker labels and timestamps. Both engines emit slightly
# different shapes:
#   Deepgram:  "[00:12] Agent: hello"
#   AssemblyAI raw text:  no labels, sentences punctuated.
# This regex removes a leading "[MM:SS]" + optional "Agent:" / "Customer:"
# / "Speaker A:" so the remaining content is comparable.
_SPEAKER_LINE_RE = re.compile(
    r"^\s*(?:\[\d{1,3}:\d{2}\]\s*)?"
    r"(?:agent|customer|speaker\s*[a-z0-9]+)\s*:\s*",
    re.IGNORECASE | re.MULTILINE,
)

# Token = run of alphanumerics. Collapses everything else (punctuation,
# brackets, redaction markers, slashes) to whitespace.
_TOKEN_RE = re.compile(r"[a-z0-9']+", re.IGNORECASE)

# Below this WER-style agreement, the divergence is loud enough to
# warrant a UI chip on the reviewer tab. 0.85 is the empirical floor
# from the L9 benchmark briefing — AAI vs DG on Watt's 14-model corpus.
# Override via env var ``TRANSCRIPT_AGREEMENT_FLOOR``.
DEFAULT_AGREEMENT_FLOOR: float = 0.85


def get_agreement_floor() -> float:
    """Read the floor from settings; falls back to the module default."""
    try:
        from app.config import settings

        return float(settings.transcript_agreement_floor)
    except Exception:
        return DEFAULT_AGREEMENT_FLOOR


def _normalise(transcript: str) -> list[str]:
    """Return the lowercase token stream with speaker labels stripped.

    Filler / stopwords are kept here — agreement is computed on the
    full stream first, then we report the disagreement windows using
    the filler-aware indices so the reviewer sees natural-English
    excerpts. Filler is filtered only in the score calculation below.
    """
    if not transcript:
        return []
    body = _SPEAKER_LINE_RE.sub(" ", transcript)
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(body)]


def _content_tokens(tokens: list[str]) -> list[str]:
    """Drop style fillers — used for the headline agreement score so
    that a reviewer doesn't get a low-score chip just because one
    engine kept the "umm"s and the other didn't."""
    return [t for t in tokens if t not in _STYLE_FILLER]


def _disagreement_windows(
    matcher: SequenceMatcher,
    dg_tokens: list[str],
    aai_tokens: list[str],
    *,
    max_samples: int = 8,
    context: int = 6,
) -> list[dict[str, Any]]:
    """Pull the worst N disagreement windows out of the diff op codes.

    Each sample is the raw replace/insert/delete op enriched with a
    short context window from each transcript so the reviewer can
    quickly see what each engine heard.
    """
    samples: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        # Skip pure-filler disagreements — they're stylistic, not
        # transcription errors.
        dg_span = dg_tokens[i1:i2]
        aai_span = aai_tokens[j1:j2]
        if all(t in _STYLE_FILLER for t in dg_span + aai_span):
            continue
        dg_left = dg_tokens[max(0, i1 - context):i1]
        dg_right = dg_tokens[i2:min(len(dg_tokens), i2 + context)]
        aai_left = aai_tokens[max(0, j1 - context):j1]
        aai_right = aai_tokens[j2:min(len(aai_tokens), j2 + context)]
        samples.append(
            {
                "tag": tag,
                "deepgram": " ".join(dg_left + ["»"] + dg_span + ["«"] + dg_right),
                "assemblyai": " ".join(aai_left + ["»"] + aai_span + ["«"] + aai_right),
                "deepgram_only": " ".join(dg_span) or None,
                "assemblyai_only": " ".join(aai_span) or None,
            }
        )
    # Stable sort: longest-span disagreements first — most likely to
    # contain compliance-relevant phrases.
    samples.sort(
        key=lambda s: len(((s["deepgram_only"] or "") + (s["assemblyai_only"] or "")).split()),
        reverse=True,
    )
    return samples[:max_samples]


def cross_validate(
    deepgram_transcript: str,
    assemblyai_transcript: str,
    *,
    agreement_floor: float = DEFAULT_AGREEMENT_FLOOR,
) -> dict[str, Any]:
    """Compare the two transcripts and return an agreement report.

    Returns a dict with shape::

        {
          "agreement": 0.0-1.0,           # SequenceMatcher.ratio on content tokens
          "agreement_full": 0.0-1.0,      # same, including filler — diagnostic
          "deepgram_word_count": int,
          "assemblyai_word_count": int,
          "below_floor": bool,            # agreement < agreement_floor
          "floor": float,
          "disagreement_samples": [       # capped at 8
              {"tag", "deepgram", "assemblyai", "deepgram_only", "assemblyai_only"},
              ...
          ],
          "skipped_reason": str | None,   # set when one transcript missing
        }

    The function never raises — callers may store the report directly
    on ``Call.meta["transcript_agreement"]`` without wrapping in
    try/except. When either transcript is missing we return a report
    with ``skipped_reason`` set so the UI can render a "comparison
    unavailable" chip instead of pretending agreement is perfect.
    """
    dg_full = _normalise(deepgram_transcript)
    aai_full = _normalise(assemblyai_transcript)

    if not dg_full or not aai_full:
        return {
            "agreement": None,
            "agreement_full": None,
            "deepgram_word_count": len(dg_full),
            "assemblyai_word_count": len(aai_full),
            "below_floor": False,
            "floor": agreement_floor,
            "disagreement_samples": [],
            "skipped_reason": (
                "deepgram_missing" if not dg_full else "assemblyai_missing"
            ),
        }

    dg_content = _content_tokens(dg_full)
    aai_content = _content_tokens(aai_full)

    # Headline score on content tokens (the one the UI surfaces).
    content_match = SequenceMatcher(a=dg_content, b=aai_content, autojunk=False)
    agreement = round(content_match.ratio(), 4)

    # Diagnostic score on the full stream — lets ops see when filler
    # noise is the only divergence vs a real content mismatch.
    full_match = SequenceMatcher(a=dg_full, b=aai_full, autojunk=False)
    agreement_full = round(full_match.ratio(), 4)

    samples = _disagreement_windows(full_match, dg_full, aai_full)

    return {
        "agreement": agreement,
        "agreement_full": agreement_full,
        "deepgram_word_count": len(dg_full),
        "assemblyai_word_count": len(aai_full),
        "below_floor": agreement < agreement_floor,
        "floor": agreement_floor,
        "disagreement_samples": samples,
        "skipped_reason": None,
    }
