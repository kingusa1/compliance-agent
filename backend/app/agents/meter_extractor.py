"""MPAN / MPRN extractor — pure regex pre-pass.

MPAN (electricity meter): 13 or 21 digits. The 13-digit "core" is what
agents read on calls; the 21-digit "S0..." form has profile/MTC/LLF
prefixes that almost never show up in a transcribed voice call.

MPRN (gas meter): 6 to 10 digits, always all-numeric.

Both are typically spoken digit-by-digit or in 2-3 digit groups
("zero-one-five-three-three..."). Deepgram smart-format collapses
those into either spaced groups or run-together digits. The regex
below tolerates both shapes.

No LLM, no Opus calls — runs at upload time and on demand from the
admin backfill endpoint. < 1ms per call.
"""
from __future__ import annotations

import re
from typing import Optional

# 13-digit MPAN core (or 21-digit full form) tolerating one or more spaces
# between groups. The lookarounds prevent matching inside longer digit
# sequences (e.g. a 14-digit phone number).
_MPAN_RE = re.compile(
    r"(?<!\d)(?:(?:\d{2,4}[ \-]?){4,7}\d{2,4})(?!\d)"
)

# Heuristic MPRN: 6 to 10 contiguous digits AFTER the word "MPRN" or "gas"
_MPRN_NEAR = re.compile(
    r"(?:M\s*P\s*R\s*N|gas\s+meter|gas\s+supply)[^\d]{0,40}(\d[\d\s\-]{5,18}\d)",
    re.IGNORECASE,
)


def _normalise_digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def extract_mpan(transcript: str) -> Optional[str]:
    """Return a 13-digit MPAN core if the transcript clearly mentions one.

    Prefers a number prefixed by "MPAN" / "supply number" / "MP number" but
    falls back to any 13-digit numeric run if exactly one is present.
    """
    if not transcript:
        return None
    t = transcript

    # 1. Cued match — "MPAN 12 34 56 78 9 01 23"
    cued = re.search(
        r"(?:M\s*P\s*A\s*N|supply\s+number|meter\s+point|MPN|mp\s+number|mpa)"
        r"[^\d]{0,40}(\d[\d\s\-]{10,30}\d)",
        t,
        re.IGNORECASE,
    )
    if cued:
        digits = _normalise_digits(cued.group(1))
        if len(digits) in (13, 21):
            return digits[-13:]  # 21-digit form ends with the 13-digit core

    # 2. Loose scan for any 13-digit run with reasonable separation. Only
    #    return if exactly one candidate is found — otherwise we'd guess.
    candidates: set[str] = set()
    for m in _MPAN_RE.finditer(t):
        d = _normalise_digits(m.group(0))
        if len(d) == 13:
            candidates.add(d)
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def extract_mprn(transcript: str) -> Optional[str]:
    """Return an MPRN (6-10 digits) if the transcript clearly cues one."""
    if not transcript:
        return None
    m = _MPRN_NEAR.search(transcript)
    if not m:
        return None
    digits = _normalise_digits(m.group(1))
    if 6 <= len(digits) <= 10:
        return digits
    return None


def extract_meters(transcript: str | None) -> dict[str, Optional[str]]:
    """Convenience: extract both at once for the upload-time backfill."""
    return {
        "mpan": extract_mpan(transcript or ""),
        "mprn": extract_mprn(transcript or ""),
    }
