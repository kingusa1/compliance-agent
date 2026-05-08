"""Map an LLM-provided evidence quote back to AssemblyAI word-level timestamps.

The checkpoint analyzer returns a free-text `evidence` string like
`'Agent said: "the prices include VAT"'`. This module finds the densest
contiguous region in the call's word stream that covers the evidence tokens,
returning that region's millisecond boundaries. The frontend uses these to
seek audio precisely when a director clicks a checkpoint — no runtime
substring matching in the browser.

Algorithm: sliding window of size ~len(needle_tokens). For each window
position, count how many DISTINCT needle tokens appear. The window with
the highest count wins; ties break on narrower span. This avoids the
"scattered common-words" trap — a phrase like "can you confirm ... happy
to proceed" won't latch onto an early greeting that also happens to
contain 'you' and 'can', because that region covers fewer of the rare
tokens (confirm, proceed, understood, etc.).

Falls back to (None, None) when the best window covers fewer than
MIN_OVERLAP_RATIO of the needle's unique tokens — that way the frontend
knows to use a proportional fallback instead of a bad exact seek.
"""

from __future__ import annotations

import re

MIN_TOKEN_LENGTH = 3
MIN_OVERLAP_RATIO = 0.40
# Window is scaled slightly larger than the needle to allow for filler
# words the LLM didn't quote (e.g., "Yeah. Perfect. That's fine." between
# sentences). Empirically 1.8x gives room for natural speech without
# letting the window drift into neighboring topics.
WINDOW_SCALE = 1.8
MIN_WINDOW_WORDS = 8

_QUOTE_CHARS = "\"'\u201c\u201d\u2018\u2019\u201e\u201a\u00ab\u00bb`"
_SPEAKER_PREFIX = re.compile(r"^\s*(agent|customer|speaker [ab]|[ab])(\s+said)?\s*:\s*", re.IGNORECASE)


def _normalize(text: str) -> list[str]:
    """Lowercase, strip speaker prefixes + quotes, tokenize to significant words."""
    cleaned = _SPEAKER_PREFIX.sub("", text.strip())
    cleaned = cleaned.translate(str.maketrans("", "", _QUOTE_CHARS))
    tokens = re.findall(r"[a-z0-9]+", cleaned.lower())
    return [t for t in tokens if len(t) >= MIN_TOKEN_LENGTH]


def find_word_range(
    evidence: str,
    words: list[dict] | None,
) -> tuple[int | None, int | None]:
    """Return (start_ms, end_ms) of the matched evidence inside `words`.

    Returns (None, None) when evidence is empty, words is empty, or no
    window meets MIN_OVERLAP_RATIO.
    """
    if not evidence or not evidence.strip() or not words:
        return (None, None)

    needle = _normalize(evidence)
    if not needle:
        return (None, None)
    needle_set = set(needle)

    # Pre-compute the set of significant tokens per word so the sliding
    # window doesn't re-normalize each word on every window slide.
    word_token_sets: list[set[str]] = [set(_normalize(w.get("word", ""))) for w in words]

    window_size = max(MIN_WINDOW_WORDS, int(len(needle) * WINDOW_SCALE))
    if window_size > len(words):
        window_size = len(words)

    # For each window position, count DISTINCT needle tokens present.
    # Track the best (coverage, -span_len) so ties on coverage favor
    # the narrowest window — which collapses the needle to the tightest
    # neighborhood in the call.
    best_coverage = 0
    best_span: tuple[int, int] | None = None
    best_span_len = len(words) + 1

    for i in range(len(words) - window_size + 1):
        covered: set[str] = set()
        for j in range(i, i + window_size):
            hits = word_token_sets[j] & needle_set
            if hits:
                covered |= hits
        coverage = len(covered)
        if coverage == 0:
            continue
        # Trim leading/trailing words that don't contribute any needle
        # token so the returned timestamps bracket only the matching
        # region, not the padding in the window.
        left = i
        right = i + window_size - 1
        while left <= right and not (word_token_sets[left] & needle_set):
            left += 1
        while right >= left and not (word_token_sets[right] & needle_set):
            right -= 1
        span_len = right - left + 1

        if coverage > best_coverage or (coverage == best_coverage and span_len < best_span_len):
            best_coverage = coverage
            best_span = (left, right)
            best_span_len = span_len

    if best_span is None:
        return (None, None)
    if best_coverage / len(needle_set) < MIN_OVERLAP_RATIO:
        return (None, None)

    first_idx, last_idx = best_span
    first_word = words[first_idx]
    last_word = words[last_idx]
    # AssemblyAI returns ms, but assemblyai_transcription.py divides by 1000
    # before persisting, so word.start/end in the DB are SECONDS (floats).
    # Convert back to ms for the `start_ms`/`end_ms` contract.
    return (int(first_word["start"] * 1000), int(last_word["end"] * 1000))
