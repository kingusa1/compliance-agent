"""Convert Watt's phrase-detection dataset markdown into per-call_type
phrase packs the analyzer can grade against.

Source: `.planning/phase2-docs/compliance_xai__watt_ai_phrase_detection_dataset_1.md`

The dataset has 120 rules total — 88 for Lead Generation + 32 for Verbal
Confirmation — across categories (Identity, Pricing, Authority, etc.).
The markdown stores them as one big table with columns:

    ID | Stage | Category | Severity-and-Trigger | Why | Approved | Action

We split them into call-type-keyed packs:

    lead_gen        → Lead Gen rules (88)
    passover        → Lead Gen subset relevant to handover (~10)
    c_call          → confirmation callback rules (~8 derived)
    amendment       → amendment-specific rules (~5 derived)
    verbal/closer   → Verbal Confirmation rules (32) — these supplement
                       the per-supplier script checkpoints

Each rule becomes a canonical "checkpoint" dict so the analyzer's
existing JSON-loader path works unchanged:

    {
      "section":     int,
      "name":        str,              # short label
      "required":    str,              # plain-english what-should-happen
      "key_phrases": list[str],        # trigger phrase + semantic equivs
      "customer_response_required": bool,
      "strictness":  "mandatory" | "verbatim" | "customer_yes",
      "line_number": int | None,
      # Phrase-pack specifics (analyzer ignores unknown fields):
      "severity":    "critical" | "high" | "medium",
      "category":    str,              # "Identity", "Pricing", etc.
      "approved_alternative": str | None,
      "action":      str | None,
    }
"""
from __future__ import annotations

import json
import re

from app.analysis import _call_llm
from app.logger import log


PHRASE_PACK_EXTRACT_PROMPT = """You are converting Watt Utilities' phrase-detection dataset into a structured rule pack a compliance analyzer can grade against.

You are processing rules for the stage: **{stage_label}**
Target call_types these rules grade: **{call_types}**

The dataset rows you must process are pasted below. Each row encodes one risk pattern — a phrase the agent said, OR a behaviour, OR a missing required statement — at a severity (Critical / High / Medium).

Convert EVERY row that applies to "{stage_label}" into a checkpoint object:

  "section":      1-based integer; preserve original ID order
  "name":         5-15 words; short rule label
  "required":     1-2 sentences describing what the agent must do (NOT what they must avoid) for the rule to PASS. Phrase it as a positive obligation: "Agent must state…", "Agent must avoid…", "Agent must confirm…"
  "key_phrases":  array of 3-8 lower-case distinctive strings the analyzer can grep — include the trigger AND semantic equivalents. For absence-of-statement rules ("no mention of Watt"), include the phrases the agent OUGHT to say (so the analyzer can verify presence). For say-this-not-that rules, include both forbidden and approved phrases.
  "customer_response_required": boolean
  "strictness":   "mandatory" | "verbatim" | "customer_yes"
  "line_number":  null
  "severity":     "critical" | "high" | "medium" (from the source row)
  "category":     short text — preserve from source (Identity / Pricing / etc.)
  "approved_alternative": from the source row or null
  "action":       from the source row or null

OUTPUT: JSON array only. No prose, no code fences, no surrounding text.

CRITICAL
- One checkpoint per applicable rule row.
- Skip rows that don't match the target stage.
- Lowercase key_phrases, no common filler words.
- Use double quotes (JSON).
- Preserve source ID ordering in "section".

ROWS:
{rows_markdown}

JSON ARRAY:"""


# Maps the synthetic phrase-pack lifecycle_phase to a friendly label
# and a description of what rows apply.
_PACK_DEFS: list[dict] = [
    {
        "phase": "lead_gen",
        "stage_label": "Lead Generation",
        "call_types": "lead_gen, full (when no closer rules apply)",
        "stage_filter": "lead generation",
    },
    {
        "phase": "passover",
        "stage_label": "Lead Generation - handover and authority",
        "call_types": "passover",
        # Passover reuses the Lead Gen rows about authority + handover.
        "stage_filter": "lead generation",
    },
    {
        "phase": "verbal_confirmation",
        "stage_label": "Verbal Confirmation",
        "call_types": "closer, verbal, full (supplements supplier-script cps)",
        "stage_filter": "verbal confirmation",
    },
    {
        "phase": "c_call",
        "stage_label": "Confirmation callback (C-call)",
        "call_types": "c_call",
        # No source rows specifically for c_call — feed Verbal Confirmation
        # so the LLM can derive the relevant subset (identity re-confirm,
        # contract re-state, no-pressure check).
        "stage_filter": "verbal confirmation",
    },
    {
        "phase": "amendment",
        "stage_label": "Amendment call",
        "call_types": "amendment",
        # Same as c_call — derive from Verbal Confirmation.
        "stage_filter": "verbal confirmation",
    },
]


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _split_dataset_by_stage(md: str) -> dict[str, list[str]]:
    """Split the markdown's single table into Lead Generation + Verbal
    Confirmation row groups. We keep the raw table lines because the LLM
    extracts more reliably from the original format than from a parsed
    intermediate.
    """
    rows_by_stage: dict[str, list[str]] = {"lead generation": [], "verbal confirmation": []}
    in_table = False
    for line in md.splitlines():
        if line.startswith("| ID |"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            continue
        # Skip the markdown alignment row
        if re.match(r"^\|\s*-+\s*", line):
            continue
        # Pull stage column (col 2). Cells are pipe-delimited.
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 3:
            continue
        stage = (cells[1] or "").lower()
        if "lead" in stage:
            rows_by_stage["lead generation"].append(line)
        elif "verbal" in stage:
            rows_by_stage["verbal confirmation"].append(line)
    return rows_by_stage


def _coerce_checkpoint(raw: dict, fallback_section: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = (raw.get("name") or "").strip()
    required = (raw.get("required") or "").strip()
    if not name or not required:
        return None
    section = raw.get("section")
    try:
        section = int(section) if section is not None else fallback_section
    except (TypeError, ValueError):
        section = fallback_section
    kps = raw.get("key_phrases") or []
    if not isinstance(kps, list):
        kps = []
    kps = [str(p).strip().lower() for p in kps if isinstance(p, (str, int))]
    kps = [p for p in kps if p][:10]
    strictness = (raw.get("strictness") or "mandatory").strip().lower()
    if strictness not in {"verbatim", "mandatory", "customer_yes"}:
        strictness = "mandatory"
    severity = (raw.get("severity") or "medium").strip().lower()
    if severity not in {"critical", "high", "medium"}:
        severity = "medium"
    return {
        "section": section,
        "name": name[:200],
        "required": required[:1200],
        "key_phrases": kps,
        "customer_response_required": bool(raw.get("customer_response_required", False)),
        "strictness": strictness,
        "line_number": None,
        "severity": severity,
        "category": (raw.get("category") or "").strip()[:80] or None,
        "approved_alternative": (raw.get("approved_alternative") or None),
        "action": (raw.get("action") or None),
    }


async def extract_phrase_pack(
    *,
    markdown: str,
    stage_label: str,
    call_types: str,
    stage_filter: str,
    timeout: float = 120.0,
) -> list[dict]:
    rows_by_stage = _split_dataset_by_stage(markdown)
    rows = rows_by_stage.get(stage_filter, [])
    if not rows:
        log.warning(f"📋 phrase_pack: no rows for stage_filter={stage_filter!r}")
        return []

    rows_md = "\n".join(rows[:200])  # safety cap
    prompt = (
        PHRASE_PACK_EXTRACT_PROMPT
        .replace("{stage_label}", stage_label)
        .replace("{call_types}", call_types)
        .replace("{rows_markdown}", rows_md)
    )
    try:
        raw = await _call_llm(prompt, timeout=timeout)
    except Exception as e:
        log.warning(f"📋 extract_phrase_pack LLM failed: {e}")
        return []
    body = _strip_fences(raw)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        start, end = body.find("["), body.rfind("]")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(body[start : end + 1])
            except json.JSONDecodeError:
                log.warning("📋 extract_phrase_pack JSON unparseable")
                return []
        else:
            return []
    if not isinstance(parsed, list):
        return []
    canon: list[dict] = []
    for i, item in enumerate(parsed):
        cp = _coerce_checkpoint(item, fallback_section=i + 1)
        if cp:
            canon.append(cp)
    return canon


def pack_definitions() -> list[dict]:
    """Public — exposed for the admin endpoint."""
    return list(_PACK_DEFS)
