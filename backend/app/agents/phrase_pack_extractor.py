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


PHRASE_PACK_EXTRACT_PROMPT = """You are converting Watt Utilities phrase-detection dataset rows into structured compliance rules. You will emit EXACTLY ONE checkpoint object per input row — same count, same order, same source IDs. Do not merge similar rows. Do not skip rows. Do not consolidate or paraphrase the dataset down to fewer rules. The count of output objects MUST equal the count of input rows shown below ({row_count} rows).

Stage label: {stage_label}
Target call_types: {call_types}

Each input row has 7 pipe-delimited cells:
  ID | Stage | Category | Severity-and-Trigger | Why-flagged | Approved-wording | Action

For EACH row, emit one checkpoint with EVERY field below. Be terse. Be 1:1.

  "section":      copy the row's ID number (the first cell, an integer)
  "name":         5-15 words derived from the trigger or category
  "required":     One sentence describing what the agent must DO to PASS this specific rule. If the source is "no mention of Watt Utilities in first 20s", the requirement is "Agent must say 'Watt Utilities' or the company name within first 20 seconds." Phrase positively.
  "key_phrases":  3-6 lower-case distinctive phrases. Include both forbidden trigger phrases and approved-alternative phrases when both are in the source. NO filler words.
  "customer_response_required": boolean
  "strictness":   "mandatory" (default) or "verbatim" or "customer_yes"
  "line_number":  null
  "severity":     "critical" | "high" | "medium" — read the leading word in cell 4
  "category":     copy cell 3 verbatim
  "approved_alternative": copy cell 6 (truncate to 200 chars) or null
  "action":       copy cell 7 or null

OUTPUT FORMAT
- Single JSON array starting `[` and ending `]`.
- No prose, no code fences, no commentary outside the array.
- Use double quotes (JSON).
- {row_count} input rows MUST produce {row_count} output objects.

ROWS ({row_count} total):
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


async def _extract_chunk(
    *,
    rows: list[str],
    stage_label: str,
    call_types: str,
    timeout: float,
    section_offset: int,
) -> list[dict]:
    """Run one LLM call against a chunk of rows. Returns canonical checkpoint
    dicts with section numbering offset so the caller can concatenate
    multiple chunks without collisions.
    """
    if not rows:
        return []
    prompt = (
        PHRASE_PACK_EXTRACT_PROMPT
        .replace("{stage_label}", stage_label)
        .replace("{call_types}", call_types)
        .replace("{row_count}", str(len(rows)))
        .replace("{rows_markdown}", "\n".join(rows))
    )
    try:
        raw = await _call_llm(prompt, timeout=timeout)
    except Exception as e:
        log.warning(f"📋 extract chunk LLM failed (rows={len(rows)}): {e}")
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
                log.warning("📋 extract chunk JSON unparseable")
                return []
        else:
            return []
    if not isinstance(parsed, list):
        return []
    canon: list[dict] = []
    for i, item in enumerate(parsed):
        cp = _coerce_checkpoint(item, fallback_section=section_offset + i + 1)
        if cp:
            cp["section"] = section_offset + i + 1
            canon.append(cp)
    return canon


async def extract_phrase_pack(
    *,
    markdown: str,
    stage_label: str,
    call_types: str,
    stage_filter: str,
    timeout: float = 180.0,
    chunk_size: int = 20,
) -> list[dict]:
    rows_by_stage = _split_dataset_by_stage(markdown)
    rows = rows_by_stage.get(stage_filter, [])
    if not rows:
        log.warning(f"📋 phrase_pack: no rows for stage_filter={stage_filter!r}")
        return []
    log.info(
        f"📋 phrase_pack starting: stage={stage_filter!r} rows={len(rows)} "
        f"chunk_size={chunk_size}"
    )

    # 88 rows in a single Opus call routinely times out around 120s and the
    # LLM truncates the JSON. Chunk to keep each request small enough to
    # respond cleanly, then concatenate.
    all_cps: list[dict] = []
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start : start + chunk_size]
        cps = await _extract_chunk(
            rows=chunk,
            stage_label=stage_label,
            call_types=call_types,
            timeout=timeout,
            section_offset=len(all_cps),
        )
        log.info(
            f"📋 phrase_pack chunk {start // chunk_size + 1}/"
            f"{(len(rows) + chunk_size - 1) // chunk_size}: "
            f"rows={len(chunk)} → {len(cps)} cps"
        )
        all_cps.extend(cps)
    log.info(f"📋 phrase_pack done: stage={stage_filter!r} total={len(all_cps)} cps")
    return all_cps


def pack_definitions() -> list[dict]:
    """Public — exposed for the admin endpoint."""
    return list(_PACK_DEFS)
