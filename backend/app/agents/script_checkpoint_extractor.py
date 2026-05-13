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

Aim for 10-30 checkpoints. Skip purely informational lines (e.g. "say hello") but capture every binding rule.

HOW TO READ DIFFERENT SCRIPT SHAPES
- NUMBERED scripts (1., 2., a), i)) — each numbered item is one checkpoint.
- BULLETED scripts (• - *) — each bullet is one checkpoint; sub-bullets stay merged into their parent.
- PROSE-ONLY scripts with NO numbering — treat each distinct paragraph or
  each sentence that begins with a directive ("you must", "please confirm",
  "are you happy", "I need to confirm", "do you authorise", "your contract
  start date", "your unit rate", "your standing charge", "your meter
  numbers", "your cancellation rights", "your supplier", "your Ombudsman
  rights") as a separate checkpoint.
- PAGE-HEADERED scripts (## Page 1 / ## Page 2) — pages may contain multiple
  paragraphs; emit one checkpoint per directive within a page.
- PDF→markdown artifacts (broken words like "th at", "complianc y", oddly
  inserted spaces, double whitespace, soft hyphens) are NORMAL — read past
  them and reconstruct the agent's intended sentence.

ALWAYS produce at least 10 checkpoints if the script is longer than 30
sentences — if you can't find 10 in a long script, scan the markdown
again for the directive cues listed above.

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
- If the script truly has fewer than 10 distinct rules, emit what's there;
  never pad with invented content.

SCRIPT MARKDOWN:
{script_md}

JSON ARRAY:"""


def _normalize_markdown(md: str) -> str:
    """Clean up common PDF→markdown extraction artifacts before sending
    the script to the LLM. Strictly cosmetic — no rule invention here.

    - Collapse runs of whitespace within a line to a single space.
    - Strip trailing whitespace on each line.
    - Normalise bullet markers (•, ·, –, —) to a leading "- " so the LLM
      sees a consistent shape across supplier scripts.
    - Replace soft hyphens and NBSPs.
    """
    if not md:
        return md
    # Soft hyphen + NBSP + zero-width joiners → regular space (or empty).
    md = (
        md.replace("­", "")
          .replace("​", "")
          .replace("‌", "")
          .replace(" ", " ")
    )
    out_lines: list[str] = []
    for raw_line in md.splitlines():
        line = raw_line.rstrip()
        # Normalise bullet markers at the start of a line.
        stripped = line.lstrip()
        for bullet in ("•", "·", "–", "—", "*", "○", "►", "▪"):
            if stripped.startswith(bullet + " ") or stripped == bullet:
                stripped = "- " + stripped[len(bullet):].lstrip()
                break
        # Collapse internal runs of whitespace to a single space.
        stripped = re.sub(r"[ \t]{2,}", " ", stripped)
        out_lines.append(stripped)
    return "\n".join(out_lines).strip() + "\n"


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


async def _extract_once(
    *,
    script_md: str,
    supplier: str,
    script_name: str,
    script_type: str,
    timeout: float,
) -> list[dict]:
    """Single LLM round-trip. Caller decides whether to retry on empty."""
    prompt = (
        SCRIPT_CHECKPOINT_EXTRACT_PROMPT
        .replace("{supplier}", supplier or "Unknown")
        .replace("{script_name}", script_name or "Unknown")
        .replace("{script_type}", script_type or "acquisition")
        .replace("{script_md}", script_md)
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
            log.warning(
                f"\U0001f4cb extract_checkpoints unparseable response: {body[:200]!r}"
            )
            return []

    if not isinstance(parsed, list):
        log.warning("\U0001f4cb extract_checkpoints LLM returned non-array")
        return []

    canon: list[dict] = []
    for i, item in enumerate(parsed):
        c = _coerce_checkpoint(item, fallback_section=i + 1)
        if c:
            canon.append(c)
    return canon


def _split_by_pages(md: str) -> list[tuple[str, str]]:
    """Split markdown into page-sized chunks.

    Returns a list of ``(label, body)`` tuples. ``label`` is the heading
    that introduced the chunk (e.g. ``## Page 1``). Falls back to a
    single chunk if no ``## `` headings exist.
    """
    chunks: list[tuple[str, str]] = []
    current_label = ""
    current_lines: list[str] = []
    for line in md.splitlines():
        if line.startswith("## "):
            if current_lines:
                chunks.append((current_label, "\n".join(current_lines).strip()))
            current_label = line.strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        chunks.append((current_label, "\n".join(current_lines).strip()))
    # Drop empty chunks.
    chunks = [(lbl, body) for lbl, body in chunks if body and len(body) > 20]
    return chunks or [("", md)]


_HEADING_RE = re.compile(r"^(?:#{1,3}\s+)(.{2,200})$")
_DIRECTIVE_WORDS = (
    " must ", " confirm", "you understand", "do you", "are you happy",
    "please confirm", "please state", "please can you", "i must", "you agree",
    "this call is recorded", "legally binding", "letter of authority",
    "third party", "broker", "unit rate", "standing charge", "vat",
    "cooling-off", "ombudsman", "consent", "authorise", "credit",
    "direct debit", "tariff", "supplier", "switch", "annual quantity",
)


def _heuristic_checkpoints_from_markdown(md: str) -> list[dict]:
    """Deterministic last-resort extractor: turn each section heading or
    directive-bearing paragraph into a single checkpoint.

    Used only when the LLM extractor returns [] for a long script (the
    EDF/Pozitive/Scottish-Power-Acquisition shape). Better than 0 rules:
    each heading becomes a "must cover this section" checkpoint with the
    paragraph body as the requirement.
    """
    out: list[dict] = []
    seen_names: set[str] = set()
    section_idx = 1

    # Pass 1 — every heading becomes a checkpoint, body is the requirement.
    # Treats both ## markdown headings AND plain-text section labels
    # (short capitalised lines followed by indented/bulleted content) as
    # section anchors so EDF/Pozitive/Scottish-Power scripts with no
    # markdown headings still get carved into proper sections.
    paragraphs: list[tuple[str, str]] = []
    current_heading: str | None = None
    current_body: list[str] = []

    def _is_section_label(line: str, next_line: str) -> bool:
        # 2-60 chars, no trailing colon/period, mostly capitalised,
        # followed by a non-empty indented/bulleted line.
        s = line.strip()
        if not s or len(s) > 60 or len(s) < 2:
            return False
        if s.endswith(("?", ".", ",", ";", ":")):
            return False
        if s.startswith(("-", "•", "*", "·", "▪", "►", "(", "[")):
            return False
        nxt = next_line.strip()
        if not nxt:
            return False
        # Section label: title-case OR all-caps, with at most 3 lowercase
        # connectors (of/to/and/the/for) and at least one capital letter.
        words = s.split()
        if len(words) > 8:
            return False
        capitals = sum(1 for w in words if w[:1].isupper())
        if capitals < max(1, len(words) - 3):
            return False
        return True

    lines = md.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        next_line = lines[i + 1] if i + 1 < len(lines) else ""
        m = _HEADING_RE.match(stripped)
        if m:
            if current_heading:
                paragraphs.append((current_heading, " ".join(current_body).strip()))
            current_heading = m.group(1).strip()
            current_body = []
        elif _is_section_label(stripped, next_line):
            if current_heading:
                paragraphs.append((current_heading, " ".join(current_body).strip()))
            current_heading = stripped
            current_body = []
        elif current_heading is not None:
            if stripped:
                current_body.append(stripped)
    if current_heading:
        paragraphs.append((current_heading, " ".join(current_body).strip()))

    for heading, body in paragraphs:
        if heading.lower() in {"page", "pages"} or heading.lower().startswith("page "):
            # Page anchors aren't real sections — fall through to scan
            # their body for directive paragraphs in pass 2.
            continue
        name = heading[:120]
        if name in seen_names:
            continue
        seen_names.add(name)
        # Mine key phrases from the body (up to 6 distinctive lowercase tokens).
        body_lower = body.lower()
        key_phrases: list[str] = []
        for cue in _DIRECTIVE_WORDS:
            if cue.strip() in body_lower and cue.strip() not in key_phrases:
                key_phrases.append(cue.strip())
            if len(key_phrases) >= 5:
                break
        if not key_phrases:
            # Last fallback: split into 3-word phrases from the body.
            tokens = [t for t in body_lower.split() if len(t) > 4][:5]
            key_phrases = tokens[:3]
        out.append({
            "section": section_idx,
            "name": name,
            "required": (body[:1100] if body else f"Cover the '{name}' section of the script."),
            "key_phrases": key_phrases[:5],
            "customer_response_required": ("agree" in body_lower or "confirm" in body_lower),
            "strictness": "mandatory",
            "line_number": None,
        })
        section_idx += 1

    # Pass 2 — scan for ALL substantive paragraphs (any non-empty block
    # > 50 chars, not a heading). Prefer paragraphs with directive cues
    # but accept anything substantive if pass 1 was thin. Cap at 30
    # rules so we don't drown the rubric in noise.
    target_total = max(15, len(out))
    if len(out) < target_total:
        for para in md.split("\n\n"):
            stripped = para.strip()
            if not stripped or len(stripped) < 50 or stripped.startswith("#"):
                continue
            # Skip pure list-of-bullets blocks (parsed already as section bodies).
            if all(line.strip().startswith("- ") for line in stripped.splitlines() if line.strip()):
                continue
            low = stripped.lower()
            has_cue = any(cue in low for cue in _DIRECTIVE_WORDS)
            # First pass through paragraphs: only directive ones.
            if not has_cue and len(out) >= 6:
                continue
            first_sentence = stripped.split(".")[0][:120]
            if first_sentence in seen_names:
                continue
            seen_names.add(first_sentence)
            out.append({
                "section": section_idx,
                "name": first_sentence,
                "required": stripped[:1100],
                "key_phrases": ([c.strip() for c in _DIRECTIVE_WORDS if c.strip() in low][:5]
                                or [w for w in first_sentence.lower().split() if len(w) > 4][:3]),
                "customer_response_required": ("agree" in low or "confirm" in low),
                "strictness": "mandatory",
                "line_number": None,
            })
            section_idx += 1
            if len(out) >= 30:
                break

    return out


async def _extract_per_page(
    *,
    chunks: list[tuple[str, str]],
    supplier: str,
    script_name: str,
    script_type: str,
    timeout: float,
) -> list[dict]:
    """Run the extractor once per page/section chunk; merge results."""
    merged: list[dict] = []
    next_section = 1
    seen_names: set[str] = set()
    for label, body in chunks:
        if not body or len(body) < 50:
            continue
        sub_name = f"{script_name} — {label}" if label else script_name
        rows = await _extract_once(
            script_md=body,
            supplier=supplier,
            script_name=sub_name,
            script_type=script_type,
            timeout=timeout,
        )
        for r in rows:
            nm = (r.get("name") or "").strip()
            if nm in seen_names:
                continue
            seen_names.add(nm)
            r["section"] = next_section
            next_section += 1
            merged.append(r)
    return merged


async def extract_checkpoints_from_markdown(
    *,
    script_md: str,
    supplier: str,
    script_name: str,
    script_type: str | None = None,
    timeout: float = 90.0,
) -> list[dict]:
    """Ask Opus 4.7 to convert one script's markdown into the canonical
    checkpoint JSON shape. Returns ``[]`` only when the markdown itself
    is empty/unreadable.

    Pipeline:
      1. Normalise PDF→markdown artifacts (whitespace, bullet glyphs,
         soft hyphens).
      2. Truncate defensively to 25k chars (supplier corpus fits).
      3. First LLM round-trip with the canonical prompt.
      4. If zero rows: retry once with prose-mode nudge appended.
      5. If still zero: split by ``## Page`` / section headings and run
         the extractor per-chunk; merge results. Works for scripts where
         the LLM was overwhelmed by length but can extract from a single
         page of structured content.
      6. If still zero: heuristic fallback — each markdown heading
         becomes a single "cover this section" checkpoint. Deterministic;
         never returns [] for a non-trivial script.
    """
    if not script_md:
        return []
    md = _normalize_markdown(script_md)[:25_000]
    s_type = script_type or "acquisition"

    # Pass 1 — whole markdown, strict prompt.
    canon = await _extract_once(
        script_md=md,
        supplier=supplier,
        script_name=script_name,
        script_type=s_type,
        timeout=timeout,
    )
    if canon:
        return canon

    non_empty_lines = sum(1 for line in md.splitlines() if line.strip())
    if non_empty_lines < 20:
        log.warning("\U0001f4cb extract_checkpoints: script too short, []")
        return []

    # Pass 2 — prose-mode hint appended.
    nudged = (
        md
        + "\n\n"
        + "NOTE TO EXTRACTOR: this script is prose-only with no explicit "
        "numbering. Walk through every paragraph above and emit one "
        "checkpoint per directive sentence — every 'you must', 'please "
        "confirm', 'are you happy', 'I need to confirm', 'do you agree', "
        "'do you authorise', 'this is a legally binding contract', "
        "every unit-rate / standing-charge / cancellation-rights / "
        "ombudsman / supplier-name disclosure. Aim for at least 10 rules."
    )
    log.info("\U0001f4cb extract_checkpoints retry with prose-mode hint")
    canon = await _extract_once(
        script_md=nudged,
        supplier=supplier,
        script_name=script_name,
        script_type=s_type,
        timeout=timeout,
    )
    if canon:
        return canon

    # Pass 3 — per-page extraction. Splits by ## headings so the LLM
    # focuses on one chunk at a time.
    chunks = _split_by_pages(md)
    if len(chunks) > 1:
        log.info(
            f"\U0001f4cb extract_checkpoints per-page fallback "
            f"({len(chunks)} chunks)"
        )
        canon = await _extract_per_page(
            chunks=chunks,
            supplier=supplier,
            script_name=script_name,
            script_type=s_type,
            timeout=timeout,
        )
        if canon:
            return canon

    # Pass 4 — deterministic heuristic. Always returns something for a
    # long script with headings or directive paragraphs.
    log.warning(
        "\U0001f4cb extract_checkpoints LLM passes empty — falling back "
        "to deterministic heuristic"
    )
    return _heuristic_checkpoints_from_markdown(md)
