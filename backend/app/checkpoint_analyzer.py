"""Batched checkpoint analysis with calibrated prompts.

Sends checkpoints in batches of 6, grouped by strictness type, using
per-supplier calibrated prompts. Each batch gets the full transcript +
enriched metadata from AssemblyAI/Deepgram.

Proven optimal through 15+ benchmark tests:
- Batch size 6 = best accuracy (tested 4, 6, 8, 12)
- Calibrated prompts = +2.2% over generic (tested 18 approaches)
- Enriched metadata = 92.2% high-confidence accuracy
- Per-checkpoint calls = 87% token waste (eliminated)

W4.4 + W4.7 (v3-watt-coverage): the prompt is augmented with
  - line_number (W1 backfill on script checkpoints) + a line-citation
    request so Claude can return the exact script line each FAIL/PARTIAL
    failed against, and
  - the full Watt vocabulary (8 REJECTION_CATEGORIES + 10
    REMEDIATION_ACTIONS, with one-line glosses) so Claude returns its own
    suggested category + fix_required + confidence on every FAIL/PARTIAL.
Output JSON gains 5 optional keys (script_line_number,
similar_rejection_id, suggested_category, suggested_fix_required,
category_confidence). Persisted onto each result dict (and through to
``call_checkpoints`` via the pipeline write site).
"""

import asyncio
import json
import logging
import time

from app.agent.escalation import run_batch_tiered
from app.agent.tool_handlers import ToolContext
from app.word_match import find_word_range
from app.analysis import _call_llm
from app.checkpoint_filter import select_relevant_checkpoints
from app.config import settings
from app.logger import log
from app.prompts import get_prompt, format_checkpoints_for_prompt
from app.verification import fuzzy_match

# Concurrency limits per provider (free tier vs paid)
_CONCURRENCY_LIMITS = {
    "gemini": 3,
    "openrouter": 25,
    "anthropic": 10,
    "openai": 15,
}

logger = logging.getLogger(__name__)

BATCH_SIZE = 6
# Batch timeout. 90s was tight for OpenRouter slow paths on 17K-char
# transcripts — half the checkpoints came back as "Batch error" (empty
# str(asyncio.TimeoutError)) in production. Raised to 180s; the durable
# Inngest workflow's outer step timeout for analyze_checkpoints is 420s
# so this still leaves 240s of slack for the wrapper.
BATCH_TIMEOUT = 180.0  # seconds per batch


# ─── W4.7 Watt vocabulary (mirrors alembic b1d4f7e2c903 + glosses) ──────────
#
# 8 rejection categories + 10 remediation actions, each with a one-line
# gloss derived from the XLSX deep-dive §2.4-2.7. The glosses are what the
# LLM uses to disambiguate similar-looking buckets (e.g. COMPLIANCE_ISSUE
# vs COMPLIANCE_ERROR — both involve compliance, but the former is a
# missing-disclosure failure, the latter is a malformed disclosure).
#
# Kept here (not imported from rejections_routes) on purpose:
#   - rejections_routes.REJECTION_CATEGORIES is a *set* (auth-time validation
#     of API payloads), this needs ordered lists with glosses for the prompt;
#   - importing rejections_routes from here would create a top-level cycle
#     (rejections_routes → app.models → analysis chain → checkpoint_analyzer).
# The two definitions are unit-tested for parity in
# tests/test_ai_category_suggestion.py.

WATT_REJECTION_CATEGORIES: list[tuple[str, str]] = [
    ("ADMIN_ERROR", "wrong/typo customer detail (name, address, MPAN/MPRN, DOB)"),
    ("PROCESS_FAILURE", "BACS/DD rejected, expired DocuSign envelope, in-contract block"),
    ("VERBAL_SALES_ERROR", "agent missed a required statement on the call"),
    ("COMPLIANCE_ISSUE", "broker/TPI/Ombudsman/Watt disclosure missing or weak"),
    ("COMPLIANCE_ERROR", "VAT, CCL, or Green Deal exclusion clause is wrong/malformed"),
    ("PRICING_ISSUE", "pricing logic concern needing review (margin/dispute/recheck)"),
    ("PRICING_ERROR", "concrete wrong price quoted vs supplier (rate/uplift/standing-charge mismatch)"),
    ("DOCUSIGN_ERROR", "DocuSign envelope problem (signing failure, wrong signer)"),
    ("FAILED_CREDIT_CHECK", "supplier credit check came back as a fail / decline"),
]

WATT_REMEDIATION_ACTIONS: list[tuple[str, str]] = [
    ("AMENDMENT_CALL", "call customer back to amend a single statement on the recording"),
    ("CONFIRMATION_CALL", "call customer back to re-confirm consent / a yes-response"),
    ("NEW_LOA", "send + collect a fresh Letter of Authority"),
    ("NEW_DOCUSIGN", "send a new DocuSign envelope and re-sign"),
    ("DD_MANDATE", "collect a Direct Debit mandate / bank details"),
    ("RESELL_TO_OTHER_SUPPLIER", "re-quote the customer onto a different supplier"),
    ("PRICE_RECHECK", "re-pull and re-confirm pricing with the supplier"),
    ("COT_CHANGE_OF_TENANCY", "process a change-of-tenancy event"),
    ("CONTRACT_LENGTH_LIMIT", "trim the contract length to satisfy supplier rules"),
    ("MANUAL_ADMIN_SUBMISSION", "admin types submission directly into the supplier portal"),
]

# Pre-rendered for prompt injection (computed once, reused per batch).
_WATT_CATEGORY_BLOCK = "\n".join(
    f"  - {name}: {gloss}" for name, gloss in WATT_REJECTION_CATEGORIES
)
_WATT_FIX_BLOCK = "\n".join(
    f"  - {name}: {gloss}" for name, gloss in WATT_REMEDIATION_ACTIONS
)


def _build_w4_addendum(
    similar_rejections: list[dict] | None,
) -> str:
    """W4.4 + W4.7 prompt extension appended after the supplier prompt.

    Asks Claude for 5 extra optional fields per FAIL/PARTIAL checkpoint:
      - script_line_number      (cite the line the agent failed against)
      - similar_rejection_id    (pick from the provided RAG context, or null)
      - suggested_category      (one of WATT_REJECTION_CATEGORIES, or null)
      - suggested_fix_required  (one of WATT_REMEDIATION_ACTIONS, or null)
      - category_confidence     (float 0-1, how sure of the bucket)

    The list of "similar past rejections" is supplied by the caller (RAG
    layer in W4.A). When empty, the similar_rejection_id field must be
    null — the prompt is explicit about this so the LLM can't hallucinate
    rejection ids that don't exist.
    """
    if similar_rejections:
        # Cap context size — we only need the top-3 to give Claude a frame
        # of reference. More than 3 hurts the prompt budget without helping
        # bucket selection (bench: 3 vs 5 vs 10 was a wash on 60 calls).
        top3 = similar_rejections[:3]
        rag_lines = [
            f"  - id={(r.get('id') or '')[:36]}: "
            f"{(r.get('summary') or r.get('rejection_reason') or '')[:140]}"
            for r in top3
        ]
        rag_block = "SIMILAR PAST REJECTIONS (for similar_rejection_id):\n" + "\n".join(
            rag_lines
        )
    else:
        rag_block = (
            "SIMILAR PAST REJECTIONS: (none provided — return null for "
            "similar_rejection_id)"
        )

    return f"""

W4 ADDITIONAL OUTPUT — applies to every FAIL or PARTIAL checkpoint
(append these 5 fields to the same JSON object you already return for
each checkpoint; PASS checkpoints may omit them or set them to null):

1. script_line_number  (integer or null) — cite the script line the
   agent failed against. Use the per-checkpoint ``line_number`` shown
   inline with each CHECKPOINT below when available; otherwise null.

2. similar_rejection_id (string or null) — id of the most similar past
   rejection from the list below. Choose at most one. Null if none of
   the listed rejections is a clear match.

3. suggested_category (string or null) — one of these exact tokens
   (NOT free text — the value is matched against the Watt enum):
{_WATT_CATEGORY_BLOCK}

4. suggested_fix_required (string or null) — one of these exact tokens:
{_WATT_FIX_BLOCK}

5. category_confidence (number, 0.0 — 1.0) — your own confidence the
   suggested_category is correct. Use 0.7+ only when you're sure. Below
   0.7 the system falls back to a keyword heuristic, so be honest —
   over-confidence here pushes a wrong bucket into the queue.

6. ai_rejection_reason (string or null) — ONE-LINE summary of why this
   checkpoint failed, suitable as the headline on the rejection tracker
   row. <120 chars. Plain English. No marketing language.
   Example: "Agent quoted 29.7p/kWh; script reference is 31.9p/kWh"

7. ai_narrative_notes (string or null) — full coaching text the reviewer
   would otherwise type into the tracker's Notes column. 2-4 sentences.
   What went wrong, what the agent should have said, what fix is needed.
   Example: "The agent stated the unit rate as 29.7p but the verbal-
   contract script line 14 says 31.9p (base 29.9p + 2p commission uplift).
   This is a verbal mis-quote and an amendment call is required to re-
   state the correct rate before the contract goes live."

{rag_block}
"""


_VALID_CATEGORY_NAMES = {name for name, _ in WATT_REJECTION_CATEGORIES}
_VALID_FIX_NAMES = {name for name, _ in WATT_REMEDIATION_ACTIONS}


def _coerce_w4_fields(raw: dict, status: str) -> dict[str, object]:
    """Validate + coerce the 5 W4 fields off the LLM JSON response.

    Returns a dict with always-present keys (None when invalid / missing)
    so downstream persistence has a uniform shape. Status-aware: PASS
    checkpoints get all-None (the AI category/fix only makes sense for
    failed checkpoints — a PASS doesn't need a remediation action).
    """
    if status not in ("fail", "partial", "unverified"):
        return {
            "script_line_number": None,
            "similar_rejection_id": None,
            "suggested_category": None,
            "suggested_fix_required": None,
            "category_confidence": None,
            "ai_rejection_reason": None,
            "ai_narrative_notes": None,
        }

    line_no = raw.get("script_line_number")
    if not isinstance(line_no, int):
        line_no = None

    sim_id = raw.get("similar_rejection_id")
    if not isinstance(sim_id, str) or not sim_id.strip():
        sim_id = None
    else:
        sim_id = sim_id.strip()[:64]

    cat = raw.get("suggested_category")
    if not isinstance(cat, str) or cat not in _VALID_CATEGORY_NAMES:
        cat = None

    fix = raw.get("suggested_fix_required")
    if not isinstance(fix, str) or fix not in _VALID_FIX_NAMES:
        fix = None

    conf = raw.get("category_confidence")
    if isinstance(conf, (int, float)):
        conf = max(0.0, min(1.0, float(conf)))
    else:
        conf = None

    # If we don't have a category, confidence is meaningless — collapse
    # both to None so the downstream `>= 0.7` gate evaluates to False
    # without any special-casing.
    if cat is None:
        conf = None

    # Sprint A1 — AI-populated rejection narrative. Trim defensively so
    # a runaway LLM response can't blow out a TEXT row.
    ai_reason_raw = raw.get("ai_rejection_reason")
    if isinstance(ai_reason_raw, str) and ai_reason_raw.strip():
        ai_reason = ai_reason_raw.strip()[:500]
    else:
        ai_reason = None

    ai_notes_raw = raw.get("ai_narrative_notes")
    if isinstance(ai_notes_raw, str) and ai_notes_raw.strip():
        ai_notes = ai_notes_raw.strip()[:4000]
    else:
        ai_notes = None

    return {
        "script_line_number": line_no,
        "similar_rejection_id": sim_id,
        "suggested_category": cat,
        "suggested_fix_required": fix,
        "category_confidence": conf,
        "ai_rejection_reason": ai_reason,
        "ai_narrative_notes": ai_notes,
    }


def _maybe_prefilter_checkpoints(transcript: str, checkpoints: list[dict]) -> list[dict]:
    """Apply embedding pre-filter when the flag is on. No-op otherwise.
    Wrapped so the integration is testable without spinning up the full
    analyzer pipeline."""
    if not settings.embedding_prefilter_enabled:
        return checkpoints
    return select_relevant_checkpoints(
        transcript,
        checkpoints,
        threshold=settings.embedding_prefilter_threshold,
    )


async def analyze_single_checkpoint(
    transcript: str,
    checkpoint: dict,
    script_mode: str,
) -> dict:
    """Analyze a single checkpoint. Used for per-checkpoint retry only."""
    section = checkpoint.get("section", 0)
    name = checkpoint.get("name", "")
    strictness = checkpoint.get("strictness", "mandatory")

    prompt_template = get_prompt("Unknown", strictness)
    cp_text = format_checkpoints_for_prompt([checkpoint])
    prompt = prompt_template.format(checkpoints_text=cp_text, transcript=transcript)

    try:
        content = await asyncio.wait_for(
            _call_llm(prompt, timeout=BATCH_TIMEOUT),
            timeout=BATCH_TIMEOUT + 5,
        )
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        parsed_list = json.loads(content)
        parsed = parsed_list[0] if isinstance(parsed_list, list) and parsed_list else parsed_list

        # Verify quotes
        verified = True
        similarity = 1.0
        if parsed.get("status") in ("pass", "partial"):
            evidence_raw = parsed.get("evidence", "") or ""
            match = fuzzy_match(transcript, evidence_raw)
            verified = match["verified"]
            similarity = match["similarity"]
            if match.get("missing_quote") and not evidence_raw.strip():
                parsed["confidence"] = "low"
                orig = (parsed.get("notes") or "").strip()
                parsed["notes"] = (
                    "AI returned a " + parsed["status"] + " verdict but could not cite a "
                    "transcript quote to back it up. Needs a human to listen to the call "
                    "and confirm."
                    + (f" Original reasoning: {orig}" if orig else "")
                )
            elif not verified:
                parsed["status"] = "unverified"
                pct = round(similarity * 100)
                orig = (parsed.get("notes") or "").strip()
                parsed["notes"] = (
                    f"Needs human review: the AI's quoted evidence only matched the transcript at {pct}% "
                    f"similarity, so it may have paraphrased instead of quoting exactly."
                    + (f" Original reasoning: {orig}" if orig else "")
                )

        confidence = parsed.get("confidence", "high")

        return {
            "section": section,
            "name": name,
            "status": parsed.get("status", "fail"),
            "evidence": parsed.get("evidence", ""),
            "notes": parsed.get("notes"),
            "confidence": confidence,
            "needs_review": confidence == "low",
            "agent_name": parsed.get("agent_name", "Unknown"),
            "customer_name": parsed.get("customer_name", "Unknown"),
            "verified": verified,
            "similarity": similarity,
        }

    except Exception as err:
        err_repr = repr(err) or type(err).__name__
        logger.warning("Single checkpoint %s failed: %s", name, err_repr, exc_info=True)
        return {
            "section": section,
            "name": name,
            "status": "error",
            "evidence": f"Analysis error: {err_repr}",
            "notes": f"Failed: {err_repr}",
            "confidence": "low",
            "needs_review": True,
            "agent_name": "Unknown",
            "customer_name": "Unknown",
            "verified": False,
            "similarity": 0,
        }


def _format_checkpoints_with_line_numbers(batch: list[dict]) -> str:
    """W4.4 — augment ``format_checkpoints_for_prompt`` with the per-checkpoint
    line_number (W1.6 backfill) so Claude can answer the
    ``script_line_number`` field accurately. Stays additive — when no
    line_number is present we render the same string as the original
    helper, so existing prompt-version hashes don't drift on calls without
    line-numbered scripts.
    """
    text = ""
    for cp in batch:
        text += f"\nCHECKPOINT: {cp.get('name', '')}\n"
        ln = cp.get("line_number")
        if isinstance(ln, int):
            text += f"  Script line: {ln}\n"
        text += f"  Required: {cp.get('required', '')}\n"
        text += f"  Key phrases (guides): {', '.join(cp.get('key_phrases', []))}\n"
        if cp.get("customer_response_required"):
            text += "  ⚠️ Customer must explicitly confirm\n"
    return text


async def _analyze_batch(
    transcript: str,
    batch: list[dict],
    supplier: str,
    strictness: str,
    similar_rejections: list[dict] | None = None,
    call_id: str | None = None,
) -> list[dict]:
    """Analyze a batch of checkpoints (up to 6) with one LLM call.

    W4.4 + W4.7: appends the Watt-vocabulary + line-citation addendum to
    the supplier prompt and parses 5 extra fields per checkpoint
    (script_line_number, similar_rejection_id, suggested_category,
    suggested_fix_required, category_confidence) onto each result dict.

    When call_id is provided, persists agent_traces rows for the user
    prompt + assistant response so the /observability terminal feed can
    render the full analyzer prompt/response for each batch.
    """
    prompt_template = get_prompt(supplier, strictness)
    cp_text = _format_checkpoints_with_line_numbers(batch)
    prompt = prompt_template.format(checkpoints_text=cp_text, transcript=transcript)
    prompt += _build_w4_addendum(similar_rejections)

    import time as _time, uuid as _uuid
    started = _time.perf_counter()

    try:
        content = await asyncio.wait_for(
            _call_llm(prompt, timeout=BATCH_TIMEOUT),
            timeout=BATCH_TIMEOUT + 5,
        )
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        parsed = json.loads(content)

        # Per-checkpoint trace rows so /observability shows ONE input/output
        # pair per checkpoint (not one per batch). The LLM is still called
        # once per batch for cost + accuracy reasons, but the prompt is
        # split into per-checkpoint slices and the parsed JSON array is
        # split into per-checkpoint result objects.
        if call_id:
            try:
                from app.database import SessionLocal
                from app.models import AgentTrace
                latency_ms = int((_time.perf_counter() - started) * 1000)
                # All checkpoints in this batch share the same run_id so the
                # UI can group them together. Per-cp latency is the BATCH
                # latency divided evenly — actual per-cp inference cost is
                # not separable since they came back in one response.
                run_id = str(_uuid.uuid4())
                # Common transcript header so each per-cp prompt is
                # self-contained when expanded in the feed UI.
                hdr = (
                    "[Batched analyzer call — this checkpoint was one of "
                    f"{len(batch)} sent in a single API request. Same transcript "
                    "+ supplier prompt, sliced here for per-checkpoint review.]\n\n"
                )
                _db = SessionLocal()
                try:
                    rows: list[AgentTrace] = []
                    for i, cp in enumerate(batch):
                        cp_id = cp.get("id") or f"unknown:{i}"
                        cp_name = cp.get("name") or cp.get("rule_text") or "checkpoint"
                        # User prompt slice = header + this cp's rule + transcript ref
                        user_content = (
                            hdr
                            + f"Checkpoint #{i+1}: {cp_name}\n"
                            + f"Strictness: {strictness}\n"
                            + f"Rule: {cp.get('rule_text') or cp.get('description') or '(see full prompt)'}\n\n"
                            + f"Full batch prompt:\n{prompt}"
                        )
                        cp_result = parsed[i] if i < len(parsed) else {}
                        assist_content = json.dumps(cp_result, indent=2, ensure_ascii=False)
                        rows.append(AgentTrace(
                            id=str(_uuid.uuid4()),
                            call_id=call_id,
                            checkpoint_id=str(cp_id),
                            run_id=run_id,
                            turn=i * 2,
                            role="user",
                            tool_name="checkpoint_analyzer",
                            content=user_content[:50000],
                        ))
                        rows.append(AgentTrace(
                            id=str(_uuid.uuid4()),
                            call_id=call_id,
                            checkpoint_id=str(cp_id),
                            run_id=run_id,
                            turn=i * 2 + 1,
                            role="assistant",
                            tool_name="checkpoint_analyzer",
                            content=assist_content,
                            latency_ms=latency_ms // max(1, len(batch)),
                        ))
                    _db.add_all(rows)
                    _db.commit()
                finally:
                    _db.close()
            except Exception:
                pass  # never break verdict on trace failure

        # Verify quotes and build results
        results = []
        for i, r in enumerate(parsed):
            cp = batch[i] if i < len(batch) else {}

            verified = True
            similarity = 1.0
            status = r.get("status", "fail")

            if status in ("pass", "partial"):
                evidence_raw = r.get("evidence", "") or ""
                match = fuzzy_match(transcript, evidence_raw)
                verified = match["verified"]
                similarity = match["similarity"]
                if match.get("missing_quote") and not evidence_raw.strip():
                    # AI claimed pass/partial but didn't cite a quote. Downgrade
                    # to needs_review so the UI doesn't show "100% similarity"
                    # on an empty box. The verdict itself is kept — a reviewer
                    # can confirm by listening to the call.
                    r["confidence"] = "low"
                    orig = (r.get("notes") or "").strip()
                    r["notes"] = (
                        "AI returned a " + status + " verdict but could not cite a "
                        "transcript quote to back it up. Needs a human to listen "
                        "to the call and confirm."
                        + (f" Original reasoning: {orig}" if orig else "")
                    )
                elif not verified:
                    status = "unverified"
                    pct = round(similarity * 100)
                    orig = (r.get("notes") or "").strip()
                    r["notes"] = (
                        f"Needs human review: the AI's quoted evidence only matched the transcript at {pct}% "
                        f"similarity, so it may have paraphrased instead of quoting exactly."
                        + (f" Original reasoning: {orig}" if orig else "")
                    )

            confidence = r.get("confidence", "high")
            status_emoji = {"pass": "\u2705", "fail": "\u274c", "partial": "\u26a0\ufe0f", "unverified": "\u2753"}.get(status, "\u2753")
            log.info(f"{status_emoji} CHECKPOINT \"{r.get('name', cp.get('name', '?'))}\" \u2192 {status} ({confidence})")

            # W4.4 + W4.7 \u2014 coerce 5 new fields, fall back to script
            # checkpoint's own line_number when LLM didn't echo it.
            w4 = _coerce_w4_fields(r, status)
            if w4["script_line_number"] is None and isinstance(cp.get("line_number"), int):
                w4["script_line_number"] = cp["line_number"]

            row = {
                "section": cp.get("section", i + 1),
                "name": r.get("name", cp.get("name", f"Checkpoint {i+1}")),
                "status": status,
                "evidence": r.get("evidence", ""),
                "notes": r.get("notes"),
                "confidence": confidence,
                "needs_review": confidence == "low",
                "agent_name": r.get("agent_name", "Unknown"),
                "customer_name": r.get("customer_name", "Unknown"),
                "verified": verified,
                "similarity": similarity,
            }
            row.update(w4)
            if w4["suggested_category"]:
                log.info(
                    f"\U0001f3f7\ufe0f  AI_CATEGORY \"{row['name']}\" \u2192 "
                    f"{w4['suggested_category']} + "
                    f"{w4['suggested_fix_required'] or '\u2014'} "
                    f"(conf={w4['category_confidence']:.2f})"
                )
            results.append(row)

        return results

    except Exception as err:
        # str(asyncio.TimeoutError()) is empty, str(JSONDecodeError) skips the
        # 'Expecting value: …' detail when the prefix isn't included, etc.
        # Use repr() so the type name is always present and exc_info=True so
        # the traceback lands in /tmp/uvicorn-8001.log next to the warning.
        err_repr = repr(err) or type(err).__name__
        logger.warning("Batch analysis failed: %s", err_repr, exc_info=True)
        return [
            {
                "section": cp.get("section", i + 1),
                "name": cp.get("name", f"Checkpoint {i+1}"),
                "status": "error",
                "evidence": f"Batch error: {err_repr}",
                "notes": f"Failed: {err_repr}",
                "confidence": "low",
                "needs_review": True,
                "agent_name": "Unknown",
                "customer_name": "Unknown",
                "verified": False,
                "similarity": 0,
                # W4 fields \u2014 error path leaves them all null so the
                # heuristic auto-create path is the one that fires.
                "script_line_number": cp.get("line_number") if isinstance(cp.get("line_number"), int) else None,
                "similar_rejection_id": None,
                "suggested_category": None,
                "suggested_fix_required": None,
                "category_confidence": None,
                "ai_rejection_reason": None,
                "ai_narrative_notes": None,
            }
            for i, cp in enumerate(batch)
        ]


async def analyze_all_checkpoints(
    transcript: str,
    checkpoints: list[dict],
    script_mode: str,
    supplier: str = "Unknown",
    *,
    word_data: list[dict] | None = None,
    agent_speaker_label: str = "A",
    customer_speaker_label: str = "B",
    db=None,
    call_id: str | None = None,
    similar_rejections: list[dict] | None = None,
) -> dict:
    """Analyze all checkpoints using batched, calibrated prompts.

    Groups checkpoints by strictness, sends each group in batches of 6
    to the appropriate calibrated prompt, runs batches in parallel.

    Args:
        transcript: The full call transcript text.
        checkpoints: List of checkpoint dicts from the script.
        script_mode: The global script mode (word_for_word or meaning_for_meaning).
        supplier: Detected supplier name for per-supplier prompt routing.

    Returns:
        dict with keys: results (list), agent_name, customer_name, summary.
    """
    checkpoints = _maybe_prefilter_checkpoints(transcript, checkpoints)
    max_concurrent = _CONCURRENCY_LIMITS.get(settings.active_provider, 10)
    sem = asyncio.Semaphore(max_concurrent)

    # Group by strictness
    groups = {}
    for cp in checkpoints:
        st = cp.get("strictness", "mandatory")
        groups.setdefault(st, []).append(cp)

    # Build all batches with their strictness
    all_batches = []
    for strictness, cps in groups.items():
        for i in range(0, len(cps), BATCH_SIZE):
            batch = cps[i:i + BATCH_SIZE]
            all_batches.append((batch, strictness))

    log.info(
        f"\U0001f4cb CHECKPOINTS analyzing {len(checkpoints)} in {len(all_batches)} batches "
        f"(supplier={supplier}, groups={{{', '.join(f'{k}:{len(v)}' for k,v in groups.items())}}})"
    )

    # Build agent tool context once (only used when agent flag is on)
    _word_data = word_data or []
    _agent_label = agent_speaker_label
    _customer_label = customer_speaker_label

    # Run batches with concurrency limit
    async def _limited(batch, strictness):
        async with sem:
            if settings.use_agent_analyzer:
                ctx = ToolContext(
                    transcript=transcript,
                    word_data=_word_data,
                    supplier=supplier,
                    agent_speaker_label=_agent_label,
                    customer_speaker_label=_customer_label,
                    db=db,
                    call_id=call_id,
                )
                return await run_batch_tiered(ctx, batch)
            return await _analyze_batch(
                transcript,
                batch,
                supplier,
                strictness,
                similar_rejections=similar_rejections,
                call_id=call_id,
            )

    tasks = [_limited(batch, strictness) for batch, strictness in all_batches]
    batch_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Flatten results
    results = []
    for i, br in enumerate(batch_results):
        if isinstance(br, Exception):
            batch, strictness = all_batches[i]
            for j, cp in enumerate(batch):
                results.append({
                    "section": cp.get("section", j + 1),
                    "name": cp.get("name", f"Checkpoint {j+1}"),
                    "status": "error",
                    "evidence": f"Unexpected error: {str(br)}",
                    "notes": f"Unhandled exception: {str(br)}",
                    "confidence": "low",
                    "needs_review": True,
                    "agent_name": "Unknown",
                    "customer_name": "Unknown",
                    "verified": False,
                    "similarity": 0,
                    # W4 fields — error path leaves them all null.
                    "script_line_number": cp.get("line_number") if isinstance(cp.get("line_number"), int) else None,
                    "similar_rejection_id": None,
                    "suggested_category": None,
                    "suggested_fix_required": None,
                    "category_confidence": None,
                    "ai_rejection_reason": None,
                    "ai_narrative_notes": None,
                })
        else:
            results.extend(br)

    # Extract agent_name and customer_name
    agent_name = "Unknown"
    customer_name = "Unknown"
    for r in results:
        if r.get("agent_name") and r["agent_name"] != "Unknown":
            agent_name = r["agent_name"]
        if r.get("customer_name") and r["customer_name"] != "Unknown":
            customer_name = r["customer_name"]

    # Task 3: map each checkpoint's evidence quote to word-level timestamps
    # so the frontend can seek audio precisely on click. Uses the word_data
    # kwarg that's already passed in from pipeline.py + every HITL retry
    # route. No-op when word_data is empty or the LLM didn't cite evidence.
    enriched_count = 0
    for cp in results:
        evidence = cp.get("evidence") or ""
        start_ms, end_ms = find_word_range(evidence, word_data)
        cp["start_ms"] = start_ms
        cp["end_ms"] = end_ms
        if start_ms is not None:
            enriched_count += 1
    log.info(
        f"\U0001f517 ENRICH start_ms populated for {enriched_count}/{len(results)} checkpoints "
        f"(word_data size={len(word_data) if word_data else 0})"
    )

    # Severity passthrough — copy each checkpoint's source severity onto
    # the result row so the verdict logic below can weight breaches by
    # severity. Match by section first (stable), then by name.
    by_section = {cp.get("section"): cp for cp in checkpoints if cp.get("section") is not None}
    by_name = {cp.get("name"): cp for cp in checkpoints}
    for r in results:
        if "severity" in r and r["severity"]:
            continue
        src = by_section.get(r.get("section")) or by_name.get(r.get("name"))
        if src:
            r["severity"] = (src.get("severity") or "medium").lower()
            # Pass through category too — useful for the reviewer queue + audit.
            r["category"] = src.get("category")

    # Aggregate scores (skip "error" checkpoints from denominator)
    non_error = [r for r in results if r["status"] != "error"]
    total = len(non_error)
    passed = sum(1 for r in non_error if r["status"] == "pass")
    partial = sum(1 for r in non_error if r["status"] == "partial")
    failed = sum(1 for r in non_error if r["status"] in ("fail", "unverified"))
    error_count = sum(1 for r in results if r["status"] == "error")
    needs_review_count = sum(1 for r in results if r.get("needs_review"))

    # \u2500\u2500 Severity-weighted verdict (Watt Compliance Dataset, line 8):
    #     "Critical = block / escalate, High = manual review, Medium = coaching"
    #
    # The old binary rule `compliant = (failed == 0 and partial == 0)` flipped
    # otherwise-clean calls to non_compliant on a single Medium partial. That
    # doesn't match the document. New mapping:
    #
    #     worst_severity \u2192 bucket \u2192 DB compliance_status     compliant
    #     critical fail  \u2192 blocked    \u2192 non_compliant         False
    #     high    fail   \u2192 review     \u2192 pending (HITL)        False
    #     medium  fail   \u2192 coaching   \u2192 compliant             True (note logged)
    #     all pass       \u2192 pass       \u2192 compliant             True
    #
    # `non_error` checkpoint rows carry a `severity` field from the source
    # script / phrase pack (defaults to "medium" when absent \u2014 same fallback
    # the phrase_pack_extractor uses).
    def _sev(cp: dict) -> str:
        s = str(cp.get("severity") or "medium").lower()
        return s if s in {"critical", "high", "medium", "low", "info"} else "medium"

    breached = [r for r in non_error if r["status"] in ("fail", "unverified", "partial")]
    critical_hits = [r for r in breached if _sev(r) == "critical"]
    high_hits = [r for r in breached if _sev(r) == "high"]
    medium_hits = [r for r in breached if _sev(r) in ("medium", "low", "info")]

    if critical_hits:
        bucket = "blocked"
        compliant = False
    elif high_hits:
        bucket = "review"
        compliant = False
    elif medium_hits:
        # 2026-05-15: "coaching" means "mostly clean, just 1-2 nudges".
        # When the pass rate is below 50% — even with all-medium-only
        # breaches — the segment is a real review case (e.g. Andrew's
        # LOA segment had 0/11 with 11 mediums and was bucketed
        # "coaching/compliant", which contradicted the obvious failure).
        if total > 0 and (passed / total) < 0.5:
            bucket = "review"
            compliant = False
        else:
            bucket = "coaching"
            compliant = True  # passes with note
    else:
        bucket = "pass"
        compliant = total > 0

    log.info(
        f"\U0001f4ca ANALYSIS done \u2192 {passed}/{total} passed \u00b7 "
        f"breaches: critical={len(critical_hits)} high={len(high_hits)} "
        f"medium={len(medium_hits)} \u00b7 bucket={bucket} compliant={compliant} \u00b7 "
        f"{error_count} errors, {needs_review_count} needs review"
    )

    return {
        "results": results,
        "agent_name": agent_name,
        "customer_name": customer_name,
        "summary": {
            "total": total,
            "passed": passed,
            "partial": partial,
            "failed": failed,
            "error": error_count,
            "needs_review": needs_review_count,
            "compliant": compliant,
            "bucket": bucket,                       # pass | coaching | review | blocked
            "critical_breaches": len(critical_hits),
            "high_breaches": len(high_hits),
            "medium_breaches": len(medium_hits),
            "score": f"{passed}/{total}" if total > 0 else "0/0",
        },
    }
