"""Phrase-detection regex pre-pass.

Cheap synchronous filter that runs BEFORE any LLM call. If it fires on a
critical violation, the system can short-circuit straight to BLOCK
without paying the LLM round-trip cost. Inspired by the 6 seed regexes
recommended in `.planning/phase2-analysis/C-phrase-dataset.md` §6 and
expanded with patterns observed in the actual rejection list XLSX.

Each rule maps a regex to:
- a RejectionReason code (R01..R27)
- the recommended Severity
- a brief `why` label for audit logs

The pre-pass produces *candidate* hits; the LLM analysis layer is still
responsible for the final verdict (which may downgrade or override based
on broader context). Treat regex hits as evidence, not as truth.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from app.watt_compliance.taxonomy import (
    REJECTION_REASONS_BY_CODE,
    RejectionReason,
    Severity,
)


class PatternMode(str, Enum):
    PRESENCE = "presence"  # Pattern present → fail
    ABSENCE = "absence"    # Pattern absent within scope → fail


@dataclass(frozen=True)
class PhraseRule:
    rule_id: str            # e.g. "C1-02" — matches the C-Phrase taxonomy
    reason_code: str        # e.g. "R01" — links to RejectionReason
    severity: Severity
    pattern: re.Pattern[str]
    mode: PatternMode
    why: str
    # Optional applicability filter — None means apply to every transcript.
    # Examples: {"call_type": "verbal"} runs the rule only on verbal calls.
    applies_to: tuple[tuple[str, str], ...] = ()


def _r(pat: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    return re.compile(pat, flags)


# ---------------------------------------------------------------------------
# 6 seed regexes from C-Phrase-Dataset §6 + extensions from rejection list.
# ---------------------------------------------------------------------------
PHRASE_RULES: tuple[PhraseRule, ...] = (
    # ── Identity / Standard 1 ─────────────────────────────────────
    PhraseRule(
        rule_id="C1-01",
        reason_code="R01",
        severity=Severity.CRITICAL,
        pattern=_r(r"\b(watt utilities?|watt utility|watt limited)\b"),
        mode=PatternMode.ABSENCE,
        why='Agent must say "Watt Utilities" — absence in transcript is a Standard 1 breach.',
    ),
    PhraseRule(
        rule_id="C1-02",
        reason_code="R02",
        severity=Severity.CRITICAL,
        # Catches "I'm from E.ON", "I'm calling from E.ON", "we are with British Gas",
        # "I am calling on behalf of Scottish Power", "from your energy provider", etc.
        pattern=_r(
            r"\b(?:i'?m|we'?re|i am|we are)\s+(?:calling\s+)?"
            r"(?:from|with|on\s+behalf\s+of|representing)\s+"
            r"(?:E\.?\s*ON(?:\s+Next)?|British\s+Gas|Scottish\s+Gas|Scottish\s+Power|"
            r"EDF|Pozitive|"
            r"your\s+(?:supplier|provider|energy\s+provider))\b"
        ),
        mode=PatternMode.PRESENCE,
        why="Supplier-impersonation phrase detected.",
    ),
    PhraseRule(
        rule_id="C1-04",
        reason_code="R02",
        severity=Severity.HIGH,
        pattern=_r(r"\b(?:we are|i am|i'?m from)\s+your\s+(?:renewal|account)\s+(?:department|team)\b"),
        mode=PatternMode.PRESENCE,
        why="False-authority phrase ('your renewal department').",
    ),
    # ── Pricing / Standard 3 ──────────────────────────────────────
    PhraseRule(
        rule_id="C3-01",
        reason_code="R09",
        severity=Severity.CRITICAL,
        pattern=_r(r"\b(?:i\s+can\s+)?guarantee[ds]?\s+(?:this\s+is\s+)?(?:the\s+)?(?:cheapest|lowest|best)\b"),
        mode=PatternMode.PRESENCE,
        why='Hard-guarantee phrase about pricing — Standard 3 breach.',
    ),
    PhraseRule(
        rule_id="C3-02",
        reason_code="R09",
        severity=Severity.CRITICAL,
        pattern=_r(r"\b(?:we|i)\s+will\s+(?:save|get you cheaper)\b|\bsave\s+you\s+money\b|\bdefinitely\s+(?:go|going)\s+up\b"),
        mode=PatternMode.PRESENCE,
        why='Absolute-savings or absolute-rise prediction.',
    ),
    PhraseRule(
        rule_id="C4-01",
        reason_code="R08",
        severity=Severity.HIGH,
        pattern=_r(r"\b(?:checked|searched)\s+(?:the\s+)?(?:whole|entire|everywhere|all)\s+(?:market|suppliers)\b|\bnobody\s+can\s+beat\s+this\b|\bbest\s+price\s+in\s+the\s+market\b"),
        mode=PatternMode.PRESENCE,
        why='Whole-market claim without explicit panel scope qualifier.',
    ),
    # ── Script framing / Standard 7 (verbal calls only) ──────────
    PhraseRule(
        rule_id="C7-01",
        reason_code="R17",
        severity=Severity.CRITICAL,
        pattern=_r(r"\b(?:just\s+a\s+formality|just\s+(?:lock(?:ing)?|locking)\s+(?:in\s+)?(?:the\s+)?(?:prices?|rates?))\b"),
        mode=PatternMode.PRESENCE,
        why='Downplays the legally-binding nature of the verbal contract.',
        applies_to=(("call_type", "verbal"), ("call_type", "closer"), ("call_type", "full")),
    ),
    # ── Commission disclosure / Standard 3g ──────────────────────
    PhraseRule(
        rule_id="C8-02",
        reason_code="R07",
        severity=Severity.HIGH,
        pattern=_r(r"\byou\s+don'?t\s+pay\s+(?:anything|us|for)\b|\bour\s+service\s+is\s+free\b"),
        mode=PatternMode.PRESENCE,
        why='Hides that commission is embedded in the unit rate.',
    ),
    # ── Pressure / vulnerability / Standard 2 ────────────────────
    PhraseRule(
        rule_id="C5-01",
        reason_code="R03",
        severity=Severity.HIGH,
        pattern=_r(r"\b(?:not\s+interested|leave\s+me\s+alone|stop\s+calling|please\s+remove\s+me)\b"),
        mode=PatternMode.PRESENCE,
        why='Customer objection signal — agent must stop after one final attempt; manual review recommended.',
    ),
    # ── Pressure / urgency ───────────────────────────────────────
    PhraseRule(
        rule_id="C3-06",
        reason_code="R05",
        severity=Severity.HIGH,
        pattern=_r(r"\byou'?d\s+be\s+mad\s+not\s+to\b|\block\s+this\s+in\s+(?:now|today)\s+before\s+the\s+market\b"),
        mode=PatternMode.PRESENCE,
        why='High-pressure / urgency framing.',
    ),
)


@dataclass(frozen=True)
class PhraseHit:
    rule_id: str
    reason: RejectionReason
    severity: Severity
    matched_text: str
    span: tuple[int, int]
    why: str


def _rule_applies(rule: PhraseRule, context: dict[str, str]) -> bool:
    """Filter rule by call_type / supplier / etc. context."""
    if not rule.applies_to:
        return True
    return any(context.get(k) == v for k, v in rule.applies_to)


def scan(transcript: str, *, call_type: str | None = None,
         supplier: str | None = None) -> list[PhraseHit]:
    """Run the regex pre-pass over a transcript.

    Returns a list of PhraseHit. Empty list = no candidates fired.
    The LLM analysis layer takes this as evidence and produces the final
    verdict — do not block on regex hits alone.
    """
    if not transcript:
        return []
    context = {
        "call_type": (call_type or "").lower(),
        "supplier": (supplier or "").lower(),
    }
    hits: list[PhraseHit] = []
    for rule in PHRASE_RULES:
        if not _rule_applies(rule, context):
            continue
        if rule.mode == PatternMode.PRESENCE:
            for m in rule.pattern.finditer(transcript):
                reason = REJECTION_REASONS_BY_CODE[rule.reason_code]
                hits.append(PhraseHit(
                    rule_id=rule.rule_id,
                    reason=reason,
                    severity=rule.severity,
                    matched_text=m.group(0),
                    span=(m.start(), m.end()),
                    why=rule.why,
                ))
        else:  # ABSENCE
            if not rule.pattern.search(transcript):
                reason = REJECTION_REASONS_BY_CODE[rule.reason_code]
                hits.append(PhraseHit(
                    rule_id=rule.rule_id,
                    reason=reason,
                    severity=rule.severity,
                    matched_text="",
                    span=(0, 0),
                    why=rule.why,
                ))
    return hits


def hit_summary(hits: Iterable[PhraseHit]) -> dict[str, int]:
    """Count hits by severity for quick verdict gating."""
    out: dict[str, int] = {Severity.CRITICAL.value: 0, Severity.HIGH.value: 0, Severity.MEDIUM.value: 0}
    for h in hits:
        out[h.severity.value] = out.get(h.severity.value, 0) + 1
    return out
