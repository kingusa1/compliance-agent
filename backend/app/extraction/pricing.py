"""Pricing-rate extractor (W3.A — pricing mismatch flag).

Pulls agent-stated unit rates and standing charges out of a diarized
transcript using regex + a small spelled-number lexicon. Returns a
structured dict that ``flags.derive_pricing_mismatch_flags`` can diff
against script reference rates.

Surface forms we accept (case-insensitive):

  unit rate
    "11p per kWh"            "11 p/kwh"
    "11 pence per kWh"       "eleven pence per kilowatt hour"
    "unit rate of 11p"       "rate is eleven pence"

  standing charge
    "standing charge of 30p per day"
    "standing charge is forty pence per day"
    "30p daily standing charge"

We deliberately keep the regex narrow — false positives here become
spurious red banners, which is worse than missing a rate (the failure-
mode plan tolerates regex misses behind the ``pricing_mismatch_enabled``
feature flag).
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, TypedDict

log = logging.getLogger(__name__)


class RateMatch(TypedDict):
    value_p_per_kwh: float
    raw_text: str
    char_offset: int


class StandingChargeMatch(TypedDict):
    value_p_per_day: float
    raw_text: str
    char_offset: int


class ExtractedRates(TypedDict):
    unit_rates: list[RateMatch]
    standing_charges: list[StandingChargeMatch]


# Spelled-number -> integer. Closer-call rates rarely exceed two digits
# (typical UK business unit rate range 8-40 p/kWh), so we cap at 99.
_NUMBER_WORDS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}

_TENS_WORDS = {"twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"}
_UNITS_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
}

# Match either "11" / "11.5" or a spelled number (incl. "twenty-one" /
# "twenty one"). Wrapped in a non-capturing group so callers can chain.
_NUMBER_PAT = (
    r"(?:"
    r"\d+(?:\.\d+)?"  # 11, 11.5
    r"|"
    + r"(?:" + "|".join(sorted(_TENS_WORDS, key=len, reverse=True)) + r")(?:[-\s]+(?:" + "|".join(sorted(_UNITS_WORDS, key=len, reverse=True)) + r"))?"  # twenty-one / twenty one
    + r"|"
    + r"(?:" + "|".join(sorted(_NUMBER_WORDS.keys(), key=len, reverse=True)) + r")"  # eleven, ten, one ...
    + r")"
)

_PENCE_TOKEN = r"(?:p|pence)"
_KWH_TOKEN = r"(?:kwh|k\.?w\.?h\.?|kilowatt[\s-]?hours?)"
_DAY_TOKEN = r"(?:day|daily)"

# Unit rate patterns. The first capture group is the number string.
_UNIT_RATE_PATTERNS: list[re.Pattern[str]] = [
    # "11p per kWh", "11 pence per kilowatt hour", "eleven p / kWh"
    re.compile(
        rf"({_NUMBER_PAT})\s*{_PENCE_TOKEN}\s*(?:per|/|a)\s*{_KWH_TOKEN}",
        re.IGNORECASE,
    ),
    # "unit rate of 11p", "unit rate is eleven pence"
    re.compile(
        rf"unit\s*rate\s*(?:of|is|at|=)?\s*({_NUMBER_PAT})\s*{_PENCE_TOKEN}",
        re.IGNORECASE,
    ),
    # "rate of 11p per kWh" (catches "rate of" without "unit")
    re.compile(
        rf"\brate\s*(?:of|is|at|=)?\s*({_NUMBER_PAT})\s*{_PENCE_TOKEN}\s*(?:per|/|a)\s*{_KWH_TOKEN}",
        re.IGNORECASE,
    ),
]

# Standing-charge patterns. First capture group is the number string.
_STANDING_CHARGE_PATTERNS: list[re.Pattern[str]] = [
    # "standing charge of 30p per day", "standing charge is forty pence per day"
    re.compile(
        rf"standing\s*charge\s*(?:of|is|at|=)?\s*({_NUMBER_PAT})\s*{_PENCE_TOKEN}(?:\s*(?:per|/|a)\s*{_DAY_TOKEN})?",
        re.IGNORECASE,
    ),
    # "30p per day standing charge", "forty pence daily standing charge"
    re.compile(
        rf"({_NUMBER_PAT})\s*{_PENCE_TOKEN}\s*(?:per|/|a)?\s*{_DAY_TOKEN}\s*standing\s*charge",
        re.IGNORECASE,
    ),
]


def _word_to_number(token: str) -> float | None:
    """Parse a number token (digit string OR spelled-out word/compound) → float."""
    token = token.strip().lower().replace("-", " ")

    # Digits first.
    try:
        return float(token)
    except ValueError:
        pass

    parts = token.split()
    if not parts:
        return None

    # Single word: direct lookup.
    if len(parts) == 1:
        return float(_NUMBER_WORDS[parts[0]]) if parts[0] in _NUMBER_WORDS else None

    # Two words: "twenty one" / "thirty four" — sum tens + units.
    if len(parts) == 2 and parts[0] in _TENS_WORDS and parts[1] in _UNITS_WORDS:
        return float(_NUMBER_WORDS[parts[0]] + _NUMBER_WORDS[parts[1]])

    return None


def _scan(transcript: str, patterns: Iterable[re.Pattern[str]]) -> list[tuple[float, str, int]]:
    """Run a pattern set; return [(value, raw_text, char_offset), ...]
    deduped so overlapping patterns don't double-count the same
    utterance. We collect every match first, then keep one hit per
    (value, number-token-position) — multiple regexes that surround the
    same digit/word with different lead-in text all hit the same
    underlying token, which is what we collapse."""
    raw_hits: list[tuple[float, str, int, int]] = []  # (value, raw_text, match_start, num_token_start)

    for pat in patterns:
        for m in pat.finditer(transcript):
            number_token = m.group(1)
            value = _word_to_number(number_token)
            if value is None:
                continue
            raw_hits.append((value, m.group(0), m.start(), m.start(1)))

    # Dedup by (value, num_token_start) — same number token reached by
    # multiple regexes is one utterance.
    seen: set[tuple[float, int]] = set()
    deduped: list[tuple[float, str, int]] = []
    for value, raw, start, num_start in raw_hits:
        key = (value, num_start)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((value, raw, start))

    deduped.sort(key=lambda h: h[2])
    return deduped


def extract_rates(transcript: str) -> ExtractedRates:
    """Extract unit rates and standing charges from a transcript.

    Returns a dict with two lists. Empty lists when nothing matches —
    callers should treat that as "no extractable pricing in this call"
    (NOT "agent quoted zero").
    """
    if not transcript:
        return {"unit_rates": [], "standing_charges": []}

    unit_hits = _scan(transcript, _UNIT_RATE_PATTERNS)
    sc_hits = _scan(transcript, _STANDING_CHARGE_PATTERNS)

    # Unit-rate offsets that are inside a standing-charge match would
    # double-count the same number ("standing charge of 30p" matches the
    # SC pattern; we don't want it firing as a unit rate too). Filter.
    sc_ranges = [(off, off + len(raw)) for _, raw, off in sc_hits]

    def _inside_sc(off: int) -> bool:
        return any(s <= off < e for s, e in sc_ranges)

    unit_rates: list[RateMatch] = [
        {"value_p_per_kwh": v, "raw_text": raw, "char_offset": off}
        for v, raw, off in unit_hits
        if not _inside_sc(off)
    ]
    standing_charges: list[StandingChargeMatch] = [
        {"value_p_per_day": v, "raw_text": raw, "char_offset": off}
        for v, raw, off in sc_hits
    ]

    log.debug(
        "PRICING_EXTRACT unit_rates=%d standing_charges=%d",
        len(unit_rates), len(standing_charges),
    )
    return {"unit_rates": unit_rates, "standing_charges": standing_charges}


__all__ = [
    "extract_rates",
    "ExtractedRates",
    "RateMatch",
    "StandingChargeMatch",
]
