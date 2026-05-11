"""LLM-driven extractor that turns a raw supplier-script markdown file
into the canonical ``Script.checkpoints`` JSON shape.

Background
----------
The seeded ``Script`` rows in prod were missing per-rule checkpoints
(every row had ``checkpoints = '[]'``). That forced the pipeline to fall
through to the V1 third-party-disclosure analyzer for EVERY call —
producing scores of N/3 against just 3 universal rules instead of
against the 20-30 supplier-specific rules each verbal contract actually
contains.

This module asks Opus 4.7 to read each script markdown and emit the
checkpoint JSON the analyzer expects. Run via the admin endpoint
``POST /api/admin/ingest-script-checkpoints?apply=true`` to write it
across every ``Script`` row that has an empty ``checkpoints`` array.

The canonical checkpoint shape (mirrors BRAIN/02_Domain/Scripts.md):

    {
      "section": int,                    # 1-based section id in the script
      "name": str,                       # short human-readable rule name
      "required": str,                   # plain-English description of
                                         #   what the agent must say/do
      "key_phrases": [str, ...],         # 3-6 phrases analyzer can grep
      "customer_response_required": bool,# does the rule need a customer ack
      "strictness": "mandatory"          # mandatory | verbatim | customer_yes
        | "verbatim"
        | "customer_yes",
      "line_number": int | None          # line in the source script
    }
"""
from __future__ import annotations

import json
import re

from app.analysis import _call_llm
from app.logger import log


SCRIPT_CHECKPOINT_EXTRACT_PROMPT = """You are a UK energy-broker compliance auditor (TPI/Ofgem). You are reading a SUPPLIER VERBAL-CONTRACT or LOA SCRIPT and extracting the per-checkpoint rules that compliance reviewers will grade calls against.

Supplier: {supplier}
Script name: {script_name}
Script type: {script_type}

For every distinct compliance rule in the script — every statement the agent MUST make, every customer confirmation that MUST be captured, every disclosure that MUST land — emit ONE checkpoint object. Capture every numbered/lettered item plus any verbatim block that's part of the legally-binding script.

Aim for 8-30 checkpoints. Skip purely informational lines (e.g. "say hello") but capture every binding rule.

OUTPUT: a single JSON array of checkpoint objects. NO prose, NO markdown fences. Each object MUST have:

  "section":      1-based integer; gives a stable display order
  "name":         5-15 words; short rule label the reviewer will see
  "required":     1-3 sentences plain English describing what the agent
                  must say or do for this rule to PASS
  "key_phrases":  array of 3-6 lower-case strings — distinctive phrases
                  the analyzer can phrase-match against the transcript
                  to find evidence. Use phrases the agent (not the
                  customer) would say. NO common filler words.
  "customer_response_required": boolean. true when the rule REQUIRES the
                  customer to give an explicit yes/no/spoken affirmation
                  (e.g. "do you agree?", legal authority confirmations);
                  false when the agent just has to read a statement.
  "strictness":   one of:
                    "verbatim"      — the script must be read word-for-word
                    "mandatory"     — the agent must convey the meaning
                    "customer_yes"  — both the wording AND a clean customer
                                       affirmation are required
  "line_number":  integer line number in the script if visible (a numbered
                  item, page heading, etc.); null if not derivable.

CRITICAL RULES
- Output VALID JSON only. No surrounding text. No code fences.
- Use double quotes (JSON style), never single.
- "key_phrases" values must be lowercase, distinctive (not "the", "of").
- Do NOT invent rules — only encode what's in the script.
- Preserve the script's section/numbering order in "section".

SCRIPT MARKDOWN:
{script_md}

JSON ARRAY:"""


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        # Drop ```json or ``` opener and the closing fence.
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _coerce_checkpoint(raw: dict, fallback_section: int) -> dict | None:
    """Normalise one LLM-emitted object to the canonical shape."""
    if not isinstance(raw, dict):
        return None
    name = (raw.get("name") or raw.get("title") or "").strip()
    required = (raw.get("required") or raw.get("requirement") or raw.get("description") or "").strip()
    if not name or not required:
        return None

    section = raw.get("section")
    try:
        section = int(section) if section is not None else fallback_section
    except (TypeError, ValueError):
        section = fallback_section

    key_phrases = raw.get("key_phrases") or raw.get("phrases") or []
    if not isinstance(key_phrases, list):
        key_phrases = []
    key_phrases = [
        str(p).strip().lower()
        for p in key_phrases
        if isinstance(p, (str, int))
    ]
    key_phrases = [p for p in key_phrases if p][:8]

    strictness = (raw.get("strictness") or "mandatory").strip().lower()
    if strictness not in {"verbatim", "mandatory", "customer_yes"}:
        strictness = "mandatory"

    line_no = raw.get("line_number")
    try:
        line_no = int(line_no) if line_no is not None else None
    except (TypeError, ValueError):
        line_no = None

    return {
        "section": section,
        "name": name[:200],
        "required": required[:1200],
        "key_phrases": key_phrases,
        "customer_response_required": bool(raw.get("customer_response_required", False)),
        "strictness": strictness,
        "line_number": line_no,
    }


async def extract_checkpoints_from_markdown(
    *,
    script_md: str,
    supplier: str,
    script_name: str,
    script_type: str | None = None,
    timeout: float = 90.0,
) -> list[dict]:
    """Ask Opus 4.7 to convert one script's markdown into the canonical
    checkpoint JSON shape. Returns ``[]`` on any failure so callers can
    keep the old (empty) value rather than dropping into a broken state.

    The markdown is truncated to ~25k chars defensively — supplier scripts
    in our corpus all fit comfortably under that.
    """
    if not script_md:
        return []
    md = script_md[:25_000]
    prompt = (
        SCRIPT_CHECKPOINT_EXTRACT_PROMPT
        .replace("{supplier}", supplier or "Unknown")
        .replace("{script_name}", script_name or "Unknown")
        .replace("{script_type}", script_type or "acquisition")
        .replace("{script_md}", md)
    )
    try:
        raw = await _call_llm(prompt, timeout=timeout)
    except Exception as e:
        log.warning(f"\U0001f4cb extract_checkpoints LLM failed: {e}")
        return []

    body = _strip_fences(raw)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        # Best-effort: try to clip from first '[' to last ']' to survive
        # the LLM occasionally trailing tokens after the array.
        start = body.find("[")
        end = body.rfind("]")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(body[start : end + 1])
            except json.JSONDecodeError:
                log.warning(
                    f"\U0001f4cb extract_checkpoints failed to parse JSON: {e}"
                )
                return []
        else:
            log.warning(f"\U0001f4cb extract_checkpoints unparseable response: {body[:200]!r}")
            return []

    if not isinstance(parsed, list):
        log.warning("\U0001f4cb extract_checkpoints LLM returned non-array")
        return []

    canon: list[dict] = []
    for i, item in enumerate(parsed):
        c = _coerce_checkpoint(item, fallback_section=i + 1)
        if c:
            canon.append(c)
    if not canon:
        log.warning("\U0001f4cb extract_checkpoints produced zero valid rows")
    return canon
