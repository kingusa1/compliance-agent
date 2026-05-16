"""Audio-upload pipeline.

Refactored in D02 from a single 350-line monolith into six step functions so
the durable workflow at `app.workflows.process_call` can wrap each one in a
`ctx.step.run` boundary. The behavior of the sync entrypoint `process_call`
is unchanged — it just delegates to the steps in order.

Step boundaries (also enumerated in `.planning/durability-tasks/D02-...json`):
  1. download_audio       — pull audio from Storage if needed
  2. transcribe           — parallel asyncio.gather across 5 STT engines
  3. detect_metadata      — names, supplier, script variant, filename rename
  4. analyze_checkpoints  — LLM batch analysis; IDEMPOTENT delete-then-insert
                            of CallCheckpoint rows
  5. score                — derive call.score / compliant / status / reason
  6. finalize             — derive_compliance for HITL routing + commit

Each step writes its results back to the Call row before returning, so a step
can be re-run independently (after a crash, the next attempt reads the prior
step's output from the DB rather than recomputing).
"""

import asyncio
import json
import os
import tempfile
import time
from datetime import datetime
from app._clock import utcnow

from sqlalchemy.orm import Session

from app.analysis import (
    analyze_compliance_v1,
    detect_call_type,
    detect_names,
    detect_script_variant,
    detect_supplier,
)
from app.watt_compliance.script_detect import canonicalize_supplier
from app.watt_compliance.taxonomy import SUPPLIER_LABELS


def _names_overlap(a: str, b: str) -> bool:
    """Token-set overlap heuristic for human names.

    Two names "match" when they share at least 2 non-trivial tokens
    (length >= 3, lowercased) in the same order. Catches:
      "Christopher Neil Bank"  ↔ "Christopher Neil Banks"   (3 shared)
      "Jay Fitzsimons"         ↔ "J. Fitzsimons"            (1 shared = no match — too risky)
      "John Smith"             ↔ "Johnson Smith"            (1 shared = no match)
    Returns False on weak signals so we don't over-merge unrelated calls.
    """
    if not a or not b:
        return False
    a_toks = [t for t in a.lower().replace(".", " ").split() if len(t) >= 3]
    b_toks = [t for t in b.lower().replace(".", " ").split() if len(t) >= 3]
    if not a_toks or not b_toks:
        return False
    shared = set(a_toks) & set(b_toks)
    return len(shared) >= 2
from app.assemblyai_transcription import transcribe_audio_assemblyai
from app.business_detect import detect_business_name, fuzzy_match_customer
from app.checkpoint_analyzer import analyze_all_checkpoints
from app.cohere_transcription import transcribe_audio_cohere
from app.compliance import derive_compliance
from app.field_sources import can_overwrite, set_source
from app.groq_transcription import transcribe_audio_groq
from app.logger import log
from app.models import Call, CallCheckpoint, Script
from app.rejection_factory import build_rejection_for_call, should_create_rejection
from app.storage import download_audio
from app.transcription import transcribe_audio_full, transcribe_audio_gemini
from app.verification import _escape_ilike


_STEP_DONE_EVENTS = {
    "transcribe": "transcribe_done",
    "detect_metadata": "detect_metadata_done",
    "classify_content": "segments_detected",
    "analyze_checkpoints": "checkpoints_scored",
    "score": "score_ready",
    "finalize": "finalized",
}


async def _trace_step(call_id: str, step_name: str, fn, *args, **kwargs):
    """Wrap a _step_* call to write a pipeline_step_log row at start +
    finish, mirroring what app.workflows.process_call._logged_step does
    for the Inngest path. Lets /observability render the live waterfall +
    terminal feed even when DISABLE_INNGEST_EMIT=1 routes to this legacy
    pipeline. Failures here never break the verdict — same swallow-and-log
    policy as agent_traces.

    2026-05-16 — also fans out an event to app.realtime so SSE subscribers
    on /api/calls/events (+ /api/calls/{id}/events) get push notifications
    at every step boundary. Frontend uses these to invalidate React Query
    keys instead of polling.
    """
    import inspect, time as _time
    from app.workflows.process_call import _persist_step_running, _persist_step_done
    from app import realtime

    started = _time.time()
    row_id = _persist_step_running(call_id, step_name, args, kwargs)
    realtime.publish(call_id, "step_started", {"step": step_name})
    try:
        raw = fn(*args, **kwargs)
        result = await raw if inspect.isawaitable(raw) else raw
        elapsed_ms = int((_time.time() - started) * 1000)
        _persist_step_done(row_id, step_name, "ok", result, None, elapsed_ms)
        realtime.publish(
            call_id,
            "step_ok",
            {"step": step_name, "duration_ms": elapsed_ms},
        )
        named = _STEP_DONE_EVENTS.get(step_name)
        if named:
            realtime.publish(call_id, named, {"step": step_name, "duration_ms": elapsed_ms})
        return result
    except Exception as e:
        elapsed_ms = int((_time.time() - started) * 1000)
        _persist_step_done(row_id, step_name, "err", None, repr(e), elapsed_ms)
        realtime.publish(
            call_id,
            "step_err",
            {"step": step_name, "duration_ms": elapsed_ms, "error": repr(e)[:300]},
        )
        raise


async def process_call(call_id: str, file_path: str, db: Session, script_id: str | None = None) -> None:
    """Sync orchestration entrypoint.

    Calls the 6 step functions in order. Wraps the whole thing in a single
    try/except that marks the Call as failed on error (the durable workflow
    has its own per-step retry/error path).
    """
    pipeline_start = time.time()
    log.info(f"\U0001f504 PIPELINE start call_id={call_id}")

    local_audio: str | None = None
    try:
        audio_path, local_audio = await _trace_step(
            call_id, "download_audio", _step_download_audio, call_id, file_path, db
        )
        transcript_data = await _trace_step(
            call_id, "transcribe", _step_transcribe, call_id, audio_path, db
        )
        await _trace_step(
            call_id, "detect_metadata", _step_detect_metadata, call_id, transcript_data, db, script_id
        )
        # 2026-05-12 taxonomy rebuild: classify_content runs BEFORE
        # analyze so per-segment routing knows which segments exist.
        classify_result = await _trace_step(
            call_id, "classify_content", _step_classify_content, call_id, transcript_data, db
        )
        if classify_result.get("halted"):
            # Zero-segment recording — halt and let reviewer triage.
            log.warning(f"\U0001f6d1 PIPELINE halted call_id={call_id} status=needs_classification")
            return
        analysis = await _trace_step(
            call_id, "analyze_checkpoints", _step_analyze_checkpoints, call_id, transcript_data, db
        )
        await _trace_step(call_id, "score", _step_score, call_id, analysis, db)
        # 2026-05-12: AI-auto rejection creation is DISABLED. Per the
        # client-feedback PDF, the /rejections module should only contain
        # rejections the human reviewer themselves opened in the queue —
        # not AI-non-compliant calls. The AI verdict stays on the Call
        # row (compliance_status / bucket); reviewers create Rejection
        # rows manually via the reviewer flow. Old _maybe_create_rejection
        # call was here and is intentionally removed.
        await _trace_step(call_id, "finalize", _step_finalize, call_id, db)

        # Tracker-autofill specialist agents (2026-05-10):
        # 1. DateExtractorAgent  — fills CustomerDeal.expected_live_date
        # 2. RejectionAdvisorAgent — fills Rejection.category + fix_required
        # 3. DeadlineComputerAgent — fills Rejection.deadline (uses #2's severity)
        # All wrapped in try/except so a transient agent failure NEVER breaks
        # a successfully-scored call. Stale autofill is cheap to backfill.
        try:
            from app.agents.date_extractor import DateExtractorAgent
            await DateExtractorAgent(call_id, db)
        except Exception as agent_err:
            log.warning(f"date_extractor skipped call_id={call_id}: {agent_err}")

        try:
            from app.agents.rejection_advisor import (
                RejectionAdvisorAgent,
                advise_rejection,
            )
            from app.agents.deadline_computer import DeadlineComputerAgent
            from app.models import Rejection as _Rej, CustomerDeal as _Deal

            # Cache the call's verdict once (instead of re-running per Rejection)
            call_for_advice = db.query(Call).filter_by(id=call_id).first()
            advisor_verdict: dict = {}
            if call_for_advice and call_for_advice.compliant is False:
                advisor_verdict = await advise_rejection(call_for_advice) or {}

            rejs = db.query(_Rej).filter_by(call_id=call_id).all()
            for rej in rejs:
                # Apply RejectionAdvisor's verdict to fields that are NULL.
                if advisor_verdict and not (rej.category and rej.fix_required):
                    rej.category = advisor_verdict.get("category", rej.category)
                    rej.fix_required = advisor_verdict.get(
                        "fix_required", rej.fix_required
                    )
                # Compute deadline from severity + expected_live_date.
                if not rej.deadline and rej.rejected_at:
                    sev = advisor_verdict.get("severity") or "MEDIUM"
                    parent_deal = (
                        db.query(_Deal)
                        .filter_by(id=call_for_advice.deal_id)
                        .first()
                        if call_for_advice and call_for_advice.deal_id
                        else None
                    )
                    expected_live = (
                        parent_deal.expected_live_date if parent_deal else None
                    )
                    rej.deadline = DeadlineComputerAgent(
                        rejected_at=rej.rejected_at,
                        severity=sev,
                        expected_live_date=expected_live,
                    )
            db.commit()
        except Exception as agent_err:
            log.warning(f"rejection_advisor/deadline skipped call_id={call_id}: {agent_err}")
            db.rollback()

        # Quality AI Agent — auto-runs after every upload to merge any
        # sibling calls of the same customer that landed on different
        # stub deals. Failure here never breaks the call (the per-call
        # verdict is already persisted); a stale customer-rollup is
        # cheap to fix later via /api/admin/quality-resolve.
        try:
            from app.quality_agent import auto_resolve_for_call
            change = await auto_resolve_for_call(call_id, db)
            if change:
                db.commit()
                log.info(
                    f"\U0001f916 QUALITY_AGENT auto-merged {change.get('bucket_size')} calls "
                    f"→ deal={change.get('survivor_deal','')[:8]} "
                    f"customer=\"{change.get('canonical_name')}\" "
                    f"confidence={change.get('confidence')}"
                )
        except Exception as qe:
            log.warning(f"quality agent skipped call_id={call_id}: {qe}")

        log.info(f"\U0001f4ca COMPLETE call_id={call_id} → {time.time()-pipeline_start:.1f}s total")
    except Exception as e:
        log.error(f"\U0001f4a5 ERROR call_id={call_id} → {str(e)}")
        call = db.query(Call).filter_by(id=call_id).first()
        if call:
            call.status = "failed"
            call.reason = f"Processing error: {str(e)}"
            db.commit()
    finally:
        if local_audio and os.path.exists(local_audio):
            try:
                os.unlink(local_audio)
            except OSError as cleanup_err:
                log.warning(f"⚠️ cleanup temp audio failed call_id={call_id}: {cleanup_err}")


# ── Step 1: download_audio ───────────────────────────────────────────────
async def _step_download_audio(call_id: str, file_path: str, db: Session) -> tuple[str, str | None]:
    """If the Call has an audio_storage_key, download to a temp file and
    return (local_temp_path, local_temp_path). Otherwise return (file_path, None).

    The second return value is the path to clean up; callers that get None
    don't need to clean up (the file lives outside the temp dir).
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise RuntimeError(f"download_audio: call {call_id} not found")
    if call.audio_storage_key:
        ext = os.path.splitext(call.filename or "")[1] or ".mp3"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            local_audio = tmp.name
        download_audio(call.audio_storage_key, local_audio)
        log.info(f"☁️  STORAGE download key={call.audio_storage_key} → {local_audio}")
        return local_audio, local_audio
    return file_path, None


# ── Step 2: transcribe ───────────────────────────────────────────────────
async def _step_transcribe(call_id: str, audio_path: str, db: Session) -> dict:
    """Parallel asyncio.gather across all enabled STT engines, write every
    variant to the Call row, pick a primary by priority AAI > Gemini > Deepgram,
    commit, and return {transcript, source}.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise RuntimeError(f"transcribe: call {call_id} not found")

    runtime_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime_settings.json"
    )
    default_enabled = ["assemblyai", "groq_whisper", "cohere", "deepgram", "gemini"]
    enabled = set(default_enabled)
    if os.path.exists(runtime_file):
        try:
            with open(runtime_file) as f:
                rt = json.load(f)
            if isinstance(rt.get("transcription_enabled"), list) and rt["transcription_enabled"]:
                enabled = set(rt["transcription_enabled"])
        except Exception:
            pass

    log.info(f"\U0001f399️ TRANSCRIBE start call_id={call_id} enabled={sorted(enabled)}")
    t0 = time.time()

    async def _aai():
        if "assemblyai" not in enabled:
            return None
        try:
            # L9: supplier is detected AFTER transcribe (step 3), so we
            # pass None and the base WATT_BASE_TERMS glossary applies.
            supplier_hint = None
            return await transcribe_audio_assemblyai(audio_path, supplier_hint=supplier_hint)
        except Exception as e:
            log.warning(f"⚠️ ASSEMBLYAI failed: {type(e).__name__}: {e}")
            return None

    async def _dg():
        if "deepgram" not in enabled:
            return None
        try:
            return await transcribe_audio_full(audio_path)
        except Exception as e:
            log.warning(f"⚠️ DEEPGRAM failed: {e}")
            return None

    async def _gm():
        if "gemini" not in enabled:
            return None
        try:
            return await transcribe_audio_gemini(audio_path)
        except Exception as e:
            log.warning(f"⚠️ GEMINI transcription failed: {e}")
            return None

    async def _gq():
        # 2026-05-14 audit fix: previously bare — a Groq HTTP 5xx would
        # propagate to asyncio.gather as an exception object and get
        # assigned directly to call.groq_whisper_transcript, corrupting
        # the Text column with a Python exception repr. Mirror the
        # try/except pattern already used for Deepgram / Gemini / AAI.
        if "groq_whisper" not in enabled:
            return None
        try:
            return await transcribe_audio_groq(audio_path)
        except Exception as e:
            log.warning(f"⚠️ GROQ transcription failed: {e}")
            return None

    async def _co():
        if "cohere" not in enabled:
            return None
        try:
            return await transcribe_audio_cohere(audio_path)
        except Exception as e:
            log.warning(f"⚠️ COHERE transcription failed: {e}")
            return None

    aai_result, dg_result, gm_result, gq_result, co_result = await asyncio.gather(
        _aai(), _dg(), _gm(), _gq(), _co(),
    )

    # Defence-in-depth: even if the try/except above is removed, never
    # write a non-string into these Text columns.
    call.groq_whisper_transcript = gq_result if isinstance(gq_result, str) else None
    call.cohere_transcript = co_result if isinstance(co_result, str) else None

    if isinstance(dg_result, dict):
        call.transcript = dg_result["transcript"]
        call.word_data = json.dumps(dg_result["words"])
        if dg_result.get("metadata"):
            call.deepgram_metadata = dg_result["metadata"]
            # Authoritative duration from Deepgram's container probe — populates
            # call.duration_seconds so the audio player uses the real value
            # (some VBR MP3s without a Xing header report a wrong duration to
            # the browser <audio> element). Fall back to the last word's end
            # timestamp if Deepgram didn't expose duration on this response.
            try:
                dg_dur = (
                    (dg_result["metadata"].get("metadata") or {}).get("duration")
                    or (dg_result["metadata"].get("results") or {}).get("duration")
                    or dg_result["metadata"].get("duration")
                )
                if not dg_dur and dg_result.get("words"):
                    dg_dur = dg_result["words"][-1].get("end")
                if isinstance(dg_dur, (int, float)) and dg_dur > 0:
                    call.duration_seconds = float(dg_dur)
            except Exception as e:
                log.warning(f"DEEPGRAM duration extract failed: {e}")
        deepgram_transcript = dg_result["transcript"]
    elif dg_result:
        call.transcript = dg_result
        deepgram_transcript = dg_result
    else:
        deepgram_transcript = ""

    call.gemini_transcript = gm_result

    if aai_result:
        # L9 fallback: when redact_pii=True, AAI sometimes flips status to
        # "completed" before the redacted text materializes. The full text
        # is mirrored into metadata["text"]. assemblyai_transcription.py
        # re-polls 5x for this race; this fallback is belt-and-suspenders.
        aai_text = aai_result.get("transcript") or ""
        aai_md = aai_result.get("metadata") or {}
        if not aai_text and isinstance(aai_md, dict):
            aai_text = aai_md.get("text") or ""
        call.assemblyai_transcript = aai_text
        call.word_data = json.dumps(aai_result["words"])
        if aai_md:
            call.assemblyai_metadata = aai_md

    if aai_result:
        transcript = aai_result.get("transcript") or (aai_result.get("metadata") or {}).get("text") or ""
        source = "assemblyai"
    elif gm_result:
        transcript = gm_result
        source = "gemini"
    else:
        transcript = deepgram_transcript
        source = "deepgram"

    dg_lines = deepgram_transcript.count("\n") + 1
    gm_lines = gm_result.count("\n") + 1 if gm_result else 0
    log.info(
        f"\U0001f399️ TRANSCRIBE done call_id={call_id} → "
        f"AAI:{'OK' if aai_result else 'FAIL'} DG:{dg_lines} lines GM:{gm_lines} lines "
        f"using {source}, {time.time()-t0:.1f}s"
    )
    db.commit()
    return {"transcript": transcript, "source": source}


def _maybe_merge_into_existing_deal(
    call: Call,
    db: Session,
    *,
    override_customer_name: str | None = None,
) -> None:
    """After detect_metadata writes detected_supplier + customer_name,
    look for an existing open Deal under the same customer with the same
    supplier. If found, re-attach the call and delete the stub Deal that
    upload-time auto-created (only if the stub has no other calls).

    ``override_customer_name`` lets the post-business-detect pass invoke
    this with the BUSINESS name rather than the person name on
    ``call.customer_name``. The 2026-05-16 upload test showed that the
    first pass (matching person name against deal's business name) never
    fires; this second pass with the business name catches sibling
    deals like "Awais Mustafa Ta Shah's Palace" vs "Shah's Palace".

    2026-05-16: loosened the customer-name predicate from exact match to
    fuzzy match (case-insensitive, dedups Ltd/Limited variations, accepts
    >= 0.80 SequenceMatcher ratio + substring containment). Lowered
    threshold from 0.85 to 0.80 because real transcription drift on
    business names can wobble more than that ("Trading As" vs "Ta",
    apostrophes dropped, etc.).
    """
    from app.models import CustomerDeal
    from difflib import SequenceMatcher

    detected_supplier = (call.detected_supplier or "").strip()
    detected_customer = (override_customer_name or call.customer_name or "").strip()
    # 2026-05-16 audit Bug 5 fix: relax the supplier-required guard. Lead-gen
    # calls with no script match end up with empty `detected_supplier`; the
    # old guard bailed early and never merged them even when the customer
    # name matched an existing deal. The per-candidate loop below has its
    # own supplier filter (`if cand_supplier and cand_supplier != detected_supplier: continue`)
    # which correctly handles the empty case — it permits cross-merge when
    # ONE side is empty and the other matches. So the entry guard only
    # needs the customer + deal_id requirements.
    if not detected_customer:
        return
    if not call.deal_id:
        return
    stub = db.query(CustomerDeal).filter_by(id=call.deal_id).first()
    if stub is None:
        return

    def _normalise_for_compare(raw: str) -> str:
        s = (raw or "").lower().strip()
        # Strip common legal-form suffixes so "Shah's Palace Limited" matches
        # "Shah's Palace". Keeps the discriminating tokens intact.
        for suffix in (" limited", " ltd", " plc", " llc", " llp", " inc"):
            if s.endswith(suffix):
                s = s[: -len(suffix)].rstrip(",.; ")
        # Collapse multiple spaces, drop punctuation that varies per
        # transcription pass.
        s = " ".join(s.replace("'", "").replace(",", " ").replace(".", " ").split())
        return s

    # 2026-05-16 — Metaphone bridge for transcription drift.
    # Same business spoken into different mics / transcribers can come back as
    # "Awais Mustafa Trading As Shah's Palace" vs "Waste Master Trading As
    # Charles Palace". Pure SequenceMatcher floor at 0.80 won't collapse those.
    # Compare first 2 tokens' phonetic keys + the full-name token-set overlap;
    # if either lights up we lower the SequenceMatcher floor to 0.60.
    from app.intake.matcher import _metaphone as _mp_key
    _STOP_TOKENS = {
        "the", "ltd", "limited", "plc", "and", "of", "for", "trading", "as",
        # Single-letter remnants after slash/punctuation normalisation
        # (e.g. "T/A" → "t a", "d/b/a" → "d b a").
        "t", "a", "b", "d",
    }

    def _token_metaphones(s: str) -> list[str]:
        out: list[str] = []
        for tok in s.split():
            if tok in _STOP_TOKENS:
                continue
            m = _mp_key(tok)
            if m:
                out.append(m)
        return out

    target_norm = _normalise_for_compare(detected_customer)
    target_mp = _token_metaphones(target_norm)
    target_first2_mp = set(target_mp[:2])
    target_all_mp = set(target_mp)

    candidates = db.query(CustomerDeal).filter(
        CustomerDeal.id != stub.id,
        CustomerDeal.status.in_(("open", "in_progress")),
    ).order_by(CustomerDeal.created_at.desc()).all()

    best: CustomerDeal | None = None
    best_score = 0.0
    for cand in candidates:
        cand_supplier = (cand.supplier or "").strip()
        # If the candidate has a supplier set, it must match the detected one.
        # Allow a candidate with NO supplier to match (matched on customer
        # alone) so the supplier-detect-failed case still collapses.
        if cand_supplier and cand_supplier != detected_supplier:
            continue
        cand_norm = _normalise_for_compare(cand.customer_name or "")
        if not cand_norm:
            continue
        # Exact (post-normalisation) match wins outright.
        if cand_norm == target_norm:
            best = cand
            best_score = 1.0
            break
        # Substring containment (either direction) is a strong signal.
        if target_norm in cand_norm or cand_norm in target_norm:
            score = 0.95
        else:
            score = SequenceMatcher(None, target_norm, cand_norm).ratio()

        # Metaphone uplift: if first-2-tokens phonetic keys overlap OR
        # the all-token phonetic set has >= 50% Jaccard, lower the floor
        # to 0.60 (catches "Awais Mustafa" ↔ "Waste Master" drift).
        cand_mp_all = set(_token_metaphones(cand_norm))
        cand_mp_first2 = set(_token_metaphones(cand_norm)[:2])
        phonetic_first2_hit = bool(target_first2_mp & cand_mp_first2)
        phonetic_jaccard = (
            len(target_all_mp & cand_mp_all) / max(len(target_all_mp | cand_mp_all), 1)
            if (target_all_mp or cand_mp_all)
            else 0.0
        )

        # 2026-05-16 — Trailing-tokens shortcut. Business names tend to
        # END with the brand ("Trading As Charles Palace", "Mustafa
        # Trading As Charles Palace"). If the last 2 non-stopword tokens
        # match EXACTLY between the two names, that's a same-business
        # signal strong enough to override fuzzy / phonetic — it catches
        # cases where AssemblyAI mis-transcribes the trading-as prefix
        # ("Awais Mustafa" vs "Waste Master T/A") but renders the actual
        # brand identically.
        def _tail2(s: str) -> tuple[str, ...]:
            toks = [t for t in s.split() if t not in _STOP_TOKENS]
            return tuple(toks[-2:]) if len(toks) >= 2 else tuple(toks)
        trailing_match = (
            len(_tail2(target_norm)) >= 2
            and _tail2(target_norm) == _tail2(cand_norm)
        )

        phonetic_strong = (
            phonetic_first2_hit or phonetic_jaccard >= 0.5 or trailing_match
        )
        # Trailing-2 exact match is the strongest signal — lower floor to 0.40.
        # Other phonetic signals → 0.60. No signal → 0.80.
        if trailing_match:
            floor = 0.40
        elif phonetic_strong:
            floor = 0.60
        else:
            floor = 0.80

        if score >= floor and score > best_score:
            best = cand
            best_score = score
            if phonetic_strong:
                log.info(
                    f"\U0001f517 PHONETIC_UPLIFT call_id={call.id} "
                    f"score={score:.2f} floor={floor:.2f} "
                    f"first2={phonetic_first2_hit} jaccard={phonetic_jaccard:.2f} "
                    f"trailing={trailing_match} "
                    f"target={target_norm!r} cand={cand_norm!r}"
                )

    if best is None:
        return

    log.info(
        f"\U0001f517 DEAL MERGE call_id={call.id} stub={stub.id} "
        f"→ existing={best.id} score={best_score:.2f} "
        f"target={detected_customer!r} match={best.customer_name!r}"
    )
    call.deal_id = best.id
    # Stub had only this one call; delete it. If the stub still has
    # other calls attached we leave it alone — orphan Deals are safer
    # than accidentally clobbering an unrelated workflow.
    other_calls = db.query(Call).filter(
        Call.deal_id == stub.id, Call.id != call.id,
    ).count()
    if other_calls == 0:
        db.delete(stub)


# ── Step 3: detect_metadata ──────────────────────────────────────────────
async def _step_detect_metadata(
    call_id: str,
    transcript_data: dict,
    db: Session,
    script_id_arg: str | None,
) -> None:
    """Detect agent + customer names, supplier, script variant; rename file.
    Writes everything back to the Call row and commits. No useful return —
    next step reads from Call.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise RuntimeError(f"detect_metadata: call {call_id} not found")

    transcript = transcript_data["transcript"]

    try:
        det_agent, det_customer = await detect_names(transcript)
        if det_agent and det_agent != "Unknown":
            # Canonicalise transcription drift (Alex Fitton vs Alex Pitton,
            # Parat vs Paras, Afak vs Afaq). audit-late B8.
            try:
                from app.agents.name_normaliser import canonicalise_agent
                det_agent = canonicalise_agent(det_agent, db, exclude_call_id=str(call.id)) or det_agent
            except Exception as _e:
                log.warning(f"agent normalisation skipped: {_e}")
            call.agent_name = det_agent
        if det_customer and det_customer != "Unknown":
            call.customer_name = det_customer
            # Auto-detect backfill: if the linked CustomerDeal was created
            # without a customer_name (auto-detect upload path leaves the
            # deal name blank), inherit the detected name. Same for the
            # Customer row's legal_name.
            try:
                from app.models import Customer, CustomerDeal as _Deal
                if call.deal_id:
                    deal = db.query(_Deal).filter_by(id=call.deal_id).first()
                    if deal and (not deal.customer_name or deal.customer_name.strip() == ""):
                        if can_overwrite(deal, "customer_name", "ai"):
                            deal.customer_name = det_customer
                            set_source(deal, "customer_name", "ai")
                    if deal and deal.customer_id:
                        cust = db.query(Customer).filter_by(id=deal.customer_id).first()
                        if cust and (not cust.legal_name or cust.legal_name.strip() == ""):
                            cust.legal_name = det_customer
            except Exception as e:
                log.warning(f"name backfill skipped: {e}")
    except Exception as e:
        log.warning(f"\U0001f464 DETECT names skipped: {e}")

    # ── AI call_type classifier ────────────────────────────────────
    # Replaces the previous filename pre-pass with a content-aware LLM
    # call. Only writes when the call's current call_type is missing or
    # `full` (which means "no explicit choice yet") — so reviewer-set
    # values via the L7 envelope are preserved as ground truth.
    try:
        existing_ct = (call.call_type or "").strip().lower()
        if existing_ct in ("", "full"):
            ai_ct = await detect_call_type(transcript)
            if ai_ct:
                call.call_type = ai_ct
                log.info(
                    f"\U0001f3af call_type classifier set call_id={call_id} "
                    f"call_type={ai_ct!r} (was {existing_ct or 'unset'!r})"
                )
            else:
                log.info(
                    f"\U0001f3af call_type classifier returned None; "
                    f"leaving call_type as {existing_ct or 'full'!r}"
                )
    except Exception as e:
        log.warning(f"\U0001f3af DETECT call_type skipped: {e}")

    script: Script | None = None
    if script_id_arg:
        script = db.query(Script).filter_by(id=script_id_arg, active=True).first()

    if not script:
        log.info(f"\U0001f50d DETECT start call_id={call_id}")
        t0 = time.time()
        detected_raw = await detect_supplier(transcript)
        # Canonicalise so "E.ON Next" / "EON" / "e.on next energy" all map
        # to Supplier.EON_NEXT and we ILIKE-match seed scripts regardless of
        # how they were named at insert time.
        canon = canonicalize_supplier(detected_raw)
        detected = SUPPLIER_LABELS[canon] if canon else detected_raw

        # Sibling-supplier inheritance — when the LLM can't identify a
        # supplier on this call (Closer / LOA-only calls often skip the
        # "with E.ON" intro because the customer already knows), borrow
        # from another call. Two passes:
        #   1) any other call on the SAME deal (cheap, certain)
        #   2) any other call sharing the same HUMAN customer name (catches
        #      pre-stitching uploads where 3 sibling calls each landed on
        #      their own stub deal — without this, the supplier=Unknown
        #      sticks until manual cleanup).
        if canon is None or detected_raw in ("Unknown", "", None):
            try:
                sibling = None
                if call.deal_id:
                    sibling = (
                        db.query(Call)
                        .filter(
                            Call.deal_id == call.deal_id,
                            Call.id != call.id,
                            Call.detected_supplier.isnot(None),
                            Call.detected_supplier != "Unknown",
                            Call.detected_supplier != "",
                        )
                        .order_by(Call.created_at.desc())
                        .first()
                    )
                if sibling is None and call.customer_name and len(call.customer_name.strip()) >= 4:
                    human = call.customer_name.strip()
                    candidates = (
                        db.query(Call)
                        .filter(
                            Call.id != call.id,
                            Call.customer_name.isnot(None),
                            Call.customer_name != "",
                            Call.detected_supplier.isnot(None),
                            Call.detected_supplier != "Unknown",
                            Call.detected_supplier != "",
                        )
                        .order_by(Call.created_at.desc())
                        .limit(50)
                        .all()
                    )
                    h_lower = human.lower()
                    for cand in candidates:
                        cn = (cand.customer_name or "").lower().strip()
                        if cn and (
                            h_lower in cn or cn in h_lower or _names_overlap(h_lower, cn)
                        ):
                            sibling = cand
                            break
                if sibling and sibling.detected_supplier:
                    inherited_canon = canonicalize_supplier(sibling.detected_supplier)
                    if inherited_canon:
                        detected = SUPPLIER_LABELS[inherited_canon]
                        canon = inherited_canon
                        log.info(
                            f"\U0001f504 SUPPLIER_INHERITED call_id={call_id} "
                            f"from sibling call={sibling.id} → \"{detected}\""
                        )
            except Exception as e:
                log.warning(f"sibling-supplier inherit skipped: {e}")

        log.info(
            f"\U0001f50d DETECT done call_id={call_id} → raw=\"{detected_raw}\" "
            f"canonical=\"{detected}\", {time.time()-t0:.1f}s"
        )
        call.detected_supplier = detected

        # Auto-detect backfill: if the linked CustomerDeal has no supplier
        # (auto-detect upload path), promote the detected value so the
        # rejection workflow + portal-batches grouping work end-to-end.
        try:
            from app.models import CustomerDeal as _Deal
            if (
                detected
                and detected != "Unknown"
                and call.deal_id
            ):
                deal = db.query(_Deal).filter_by(id=call.deal_id).first()
                if deal and (not deal.supplier or deal.supplier.strip() == ""):
                    if can_overwrite(deal, "supplier", "ai"):
                        deal.supplier = detected
                        set_source(deal, "supplier", "ai")
                        log.info(f"\U0001f504 BACKFILL deal supplier call_id={call_id} → \"{detected}\"")
        except Exception as e:
            log.warning(f"supplier backfill skipped: {e}")

        # L3: when the call has a known call_type, prefer Script rows
        # whose lifecycle_phase matches that phase (e.g. a 'closer'
        # call should pick the Closer script, not the Lead Gen one).
        # Backwards-compat: rows with NULL lifecycle_phase are still
        # eligible — older Scripts that pre-date the L3 migration
        # behave exactly as before.
        from app.deal_lifecycle import call_type_to_phase as _ct_to_phase
        phase = _ct_to_phase(call.call_type) if call.call_type else None

        # Match on the CANONICAL Supplier enum so seed rows like "EON" /
        # "E.ON" / "E.ON Next" / "eon_next" all resolve to the same set.
        # SQL ILIKE alone can't bridge these (the punctuation set differs)
        # so we pull the active scripts and canonicalise in Python — the
        # supplier table is at most a few dozen rows so the cost is trivial.
        all_active: list[Script] = (
            db.query(Script).filter(Script.active == True).all()
        )

        def _matches_canon(s: "Script") -> bool:
            if canon is None:
                return False
            return canonicalize_supplier(s.supplier_name) == canon

        matching: list[Script] = [s for s in all_active if _matches_canon(s)]

        # Fallback path for unknown / unmapped suppliers — keep the prior
        # ILIKE behaviour so anything outside the alias map still gets a
        # chance to match (e.g. a freshly-added supplier the alias map
        # hasn't learned yet).
        if not matching and detected_raw:
            safe = _escape_ilike(detected_raw)
            matching = [
                s for s in all_active
                if s.supplier_name and safe.lower() in s.supplier_name.lower()
            ]

        if phase and matching and hasattr(Script, "lifecycle_phase"):
            phase_filtered = [
                s for s in matching
                if getattr(s, "lifecycle_phase", None) in (phase, None)
            ]
            if phase_filtered:
                matching = phase_filtered

        if len(matching) == 1:
            script = matching[0]
            log.info(f"\U0001f3af SCRIPT single match call_id={call_id} → \"{script.script_name}\"")
        elif len(matching) > 1:
            log.info(f"\U0001f3af SCRIPT {len(matching)} variants for \"{detected}\" call_id={call_id}, picking best...")
            t1 = time.time()
            options = [{"index": i, "id": s.id, "script_name": s.script_name} for i, s in enumerate(matching)]
            best_idx = await detect_script_variant(transcript, detected, options)
            script = matching[best_idx]
            log.info(f"\U0001f3af SCRIPT variant picked call_id={call_id} → \"{script.script_name}\", {time.time()-t1:.1f}s")

        if detected and detected != "Unknown" and script:
            original_name = call.filename
            safe_supplier = detected.replace(" ", "_").replace(".", "")
            safe_script = script.script_name.replace(" ", "_") if script else "Unknown"
            ext = os.path.splitext(original_name)[1]
            base = os.path.splitext(original_name)[0]
            new_name = f"{safe_supplier}__{safe_script}__{base}{ext}"
            call.filename = new_name
            log.info(f"\U0001f4dd RENAME call_id={call_id} → \"{new_name}\"")

            if not call.audio_storage_key:
                src = call.file_path
                if src and os.path.exists(src):
                    new_path = os.path.join(os.path.dirname(src), new_name)
                    try:
                        os.rename(src, new_path)
                        call.file_path = new_path
                    except OSError as e:
                        log.warning(f"⚠️ RENAME file failed call_id={call_id}: {e}")

    if script:
        call.script_id = script.id
        # Always render the canonical SUPPLIER_LABELS string regardless of
        # how the seed row spelled it. Earlier runs may have persisted the
        # raw seed value ("EON") — every downstream consumer (rejections,
        # tracker, RAG namespace) expects "E.ON Next".
        script_canon = canonicalize_supplier(script.supplier_name)
        if script_canon is not None:
            call.detected_supplier = SUPPLIER_LABELS[script_canon]
        elif not call.detected_supplier:
            call.detected_supplier = script.supplier_name

    # Sprint v3-C1 — collapse stub Deal into existing open Deal when
    # (detected supplier + customer name) match. Wrapped so a merge
    # failure never breaks the pipeline; worst-case the stub Deal stays
    # and a human can merge later from /deals.
    try:
        _maybe_merge_into_existing_deal(call, db)
    except Exception as e:
        log.warning(f"deal merge skipped: {e}")

    # ── Phase A: business-name detection + stub-merge / stub-rename ───
    # detect_supplier already wrote call.detected_supplier above. The merge
    # branch runs ALWAYS so re-runs can stitch sibling calls that the LLM
    # gave slightly different business names ("The Church" / "Evangelical
    # Church" / "St. Peter's Benfleet Church" — all the same physical
    # customer). The rename branch only runs while the deal name still
    # carries the stub label.
    try:
        from app.models import Customer as _Customer, CustomerDeal as _Deal
        from app.intake.upsert import _slugify as slugify
        current_deal = db.query(_Deal).filter_by(id=call.deal_id).first() if call.deal_id else None
        is_stub = bool(
            current_deal
            and (current_deal.customer_name or "").startswith("(auto-detect pending")
        )
        if current_deal:
            # 2026-05-16: Opus 4.7 mandate across all detectors (Mohamed).
            business_name = await detect_business_name(transcript)
            # Last-resort fallback: when no business name surfaces, fall back to
            # the detected customer's name so we never leave the stub label.
            if not business_name and call.customer_name and call.customer_name.strip():
                business_name = call.customer_name.strip()

            # 2026-05-16 second-pass deal merge using the BUSINESS name
            # (the first pass at line ~724 only had the person name on
            # call.customer_name, which never matches deal.customer_name
            # because the latter is the business name). This is what
            # actually collapses Awais's 4 sequential uploads into one
            # deal when business detection agrees within 0.80 fuzzy
            # ratio across transcripts.
            if business_name and call.deal_id:
                try:
                    _maybe_merge_into_existing_deal(
                        call, db, override_customer_name=business_name
                    )
                    # Re-load the (possibly relocated) deal so the rename
                    # branch below targets the right row.
                    current_deal = db.query(_Deal).filter_by(id=call.deal_id).first()
                    is_stub = bool(
                        current_deal
                        and (current_deal.customer_name or "").startswith("(auto-detect pending")
                    )
                except Exception as _e:
                    log.warning(f"second-pass deal merge skipped: {_e}")

            if business_name:
                matched = fuzzy_match_customer(business_name, db, threshold=0.6)

                # Customer-human-name stitch: if business-name fuzzy didn't
                # find a match, look up *Calls* (not Deals) carrying the same
                # human customer name (e.g. "Christopher Neil Banks"). The
                # human name lives on Call.customer_name; deal.customer_name
                # is the BUSINESS name and the LLM often returns slightly
                # different business names per call ("The Church" vs
                # "Evangelical Church"). The human name is far more stable.
                if not matched and call.customer_name and call.customer_name.strip():
                    human = call.customer_name.strip()
                    if len(human) >= 4:
                        # Bidirectional human-name match — find any other
                        # Call whose customer_name CONTAINS or IS CONTAINED
                        # IN this one. Catches near-equal variants:
                        #   "Christopher Neil Bank" ↔ "Christopher Neil Banks"
                        #   "Jay" ↔ "J. Fitzsimons"
                        # Uses Python-side comparison after a coarse first-3
                        # word ILIKE filter to keep the SQL cheap.
                        first_three = " ".join(human.split()[:3])
                        coarse = first_three[:30]  # cap for ILIKE perf
                        candidates = (
                            db.query(Call)
                            .filter(
                                Call.customer_name.isnot(None),
                                Call.customer_name != "",
                                Call.id != call.id,
                                Call.deal_id.isnot(None),
                                Call.deal_id != current_deal.id,
                                Call.customer_name.ilike(f"%{coarse.split()[0] if coarse else human[:8]}%"),
                            )
                            .order_by(Call.created_at.desc())
                            .limit(50)
                            .all()
                        )
                        h_lower = human.lower()
                        sibling_call = None
                        for cand in candidates:
                            cand_name = (cand.customer_name or "").strip().lower()
                            # Bidirectional substring match
                            if cand_name and (
                                h_lower in cand_name
                                or cand_name in h_lower
                                or _names_overlap(h_lower, cand_name)
                            ):
                                sibling_call = cand
                                break
                        if sibling_call and sibling_call.deal_id:
                            sibling_deal = (
                                db.query(_Deal)
                                .filter_by(id=sibling_call.deal_id)
                                .first()
                            )
                            if sibling_deal and sibling_deal.customer_id:
                                matched = (
                                    db.query(_Customer)
                                    .filter_by(id=sibling_deal.customer_id)
                                    .first()
                                )
                                if matched:
                                    log.info(
                                        f"\U0001f50d HUMAN_NAME_MATCH call_id={call_id} "
                                        f"human=\"{human}\" → sibling_call={sibling_call.id} "
                                        f"customer={matched.id}"
                                    )

                if matched:
                    # Find the matched customer's most-recent open deal
                    target = (
                        db.query(_Deal)
                        .filter(_Deal.customer_id == matched.id, _Deal.status != "closed")
                        .order_by(_Deal.created_at.desc())
                        .first()
                    )
                    if target and target.id != current_deal.id:
                        log.info(
                            f"\U0001f504 STUB_MERGE call_id={call_id} "
                            f"stub={current_deal.id} -> existing_deal={target.id} "
                            f"(business=\"{business_name}\" matched=\"{matched.legal_name}\")"
                        )
                        old_stub_id = current_deal.id
                        call.deal_id = target.id
                        # Delete the orphaned stub if no other calls reference it
                        other = db.query(Call).filter(Call.deal_id == old_stub_id, Call.id != call.id).count()
                        if other == 0:
                            db.delete(current_deal)
                elif is_stub:
                    # No existing customer match AND the deal still carries
                    # the auto-detect stub label — rename it in place so it
                    # stops showing "(auto-detect pending …)" in the UI.
                    # On retries the deal is already named, so we skip.
                    current_deal.customer_name = business_name
                    set_source(current_deal, "customer_name", "ai")
                    log.info(
                        f"\U0001f504 STUB_RENAME deal={current_deal.id} → \"{business_name}\""
                    )
                    if current_deal.customer_id:
                        cust = db.query(_Customer).filter_by(id=current_deal.customer_id).first()
                        if cust:
                            if not cust.legal_name or cust.legal_name.strip() == "":
                                cust.legal_name = business_name
                            if (cust.slug or "").startswith("(auto-detect pending"):
                                # Re-slug from the canonical business name; fall
                                # back to a uniqued variant if the new slug
                                # collides with an existing row.
                                base = slugify(business_name) or f"customer-{cust.id[:8]}"
                                slug = base
                                n = 2
                                while db.query(_Customer).filter(
                                    _Customer.slug == slug, _Customer.id != cust.id
                                ).first():
                                    slug = f"{base}-{n}"
                                    n += 1
                                cust.slug = slug
                                log.info(
                                    f"\U0001f504 CUSTOMER_RESLUG cust={cust.id} → \"{slug}\""
                                )
    except Exception as e:
        log.warning(f"stub-merge/rename skipped call_id={call_id}: {e}")

    db.commit()


# ── Step 3.5: classify_content (NEW 2026-05-12) ──────────────────────────
async def _step_classify_content(
    call_id: str,
    transcript_data: dict,
    db: Session,
) -> dict:
    """Run the AI content classifier and persist its segments.

    Returns ``{"halted": True}`` when no segment was identified — caller
    should bail the pipeline and surface ``call.status = needs_classification``
    so reviewer can manually triage.

    Otherwise writes 1-4 CallSegment rows (one per detected segment) and
    returns ``{"halted": False, "segments": N}``.
    """
    from app.agents.content_classifier import classify_content as _classify
    from app.models import CallSegment as _CallSegment, AgentTrace as _AgentTrace
    import uuid as _uuid

    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise RuntimeError(f"classify_content: call {call_id} not found")

    transcript = transcript_data.get("transcript") or ""
    word_data: list[dict] = []
    if call.word_data:
        try:
            word_data = json.loads(call.word_data) or []
        except Exception:
            word_data = []

    segments = await _classify(
        transcript,
        word_data,
        supplier=call.detected_supplier,
    )

    # Idempotency — wipe existing CallSegment rows before inserting fresh.
    db.query(_CallSegment).filter_by(call_id=call_id).delete()
    db.flush()

    # Fallbacks when the classifier returns [] — we'd rather grade with
    # a sensible default than halt the call:
    #   (a) Manual override: reviewer (or a legacy upload path) set
    #       call_type to one of the 4 canonical values → use it.
    #   (b) Explicit supplier script (call.script_id set, e.g. via the
    #       upload form's manual script override): treat as a "verbal"
    #       segment so route_for_segment hits the supplier-specific
    #       checkpoints attached to that script.
    #   (c) Short / sparse transcript: the classifier bails on
    #       transcripts < 50 chars or with no word_data, which is the
    #       shape every deterministic unit test uses. Default to a
    #       single ``lead_gen`` segment so those tests + any genuinely
    #       short live recordings still grade against SOMETHING (lead_gen
    #       is the most permissive bucket — no binding contract reading).
    #   Otherwise → halt with needs_classification for manual triage.
    _VALID_STAGES = {"lead_gen", "pre_sales", "verbal", "loa"}
    fallback_stage: str | None = None
    fallback_reason: str = ""
    # Per Aly's 2026-05-14 clarification: for any non-E.ON supplier, the LOA
    # is a DocuSign-signed paper document — there is no audio LOA stage.
    # Strip LOA fallbacks for those suppliers so we never grade a non-E.ON
    # call against the LOA rubric.
    _supplier_str = (call.detected_supplier or "").lower()
    _is_eon = "eon" in _supplier_str or "e.on" in _supplier_str
    if not segments:
        stage = (call.call_type or "").strip().lower()
        if stage in _VALID_STAGES:
            if stage == "loa" and not _is_eon:
                # Non-E.ON LOA manual override → downgrade to verbal so the
                # supplier-script verbal-contract rubric drives grading.
                fallback_stage = "verbal"
                fallback_reason = (
                    "manual loa override on non-E.ON supplier — LOA is a "
                    "document, not a recording; collapsing to verbal"
                )
            else:
                fallback_stage = stage
                fallback_reason = "manual call_type override; classifier returned []"
        elif getattr(call, "script_id", None):
            fallback_stage = "verbal"
            fallback_reason = (
                "explicit script_id override — single verbal segment so the "
                "supplier-script rubric drives grading"
            )
        elif (not transcript) or len(transcript.strip()) < 50 or len(word_data) < 20:
            fallback_stage = "lead_gen"
            fallback_reason = (
                "short/sparse transcript — classifier bailed; defaulting to "
                "lead_gen (least-binding rubric)"
            )

    if not segments and fallback_stage:
        from app.agents.content_classifier import Segment as _Segment
        last_idx = max(0, len(word_data) - 1)
        segments = [
            _Segment(
                segment_type=fallback_stage,
                start_word_idx=0,
                end_word_idx=last_idx,
                confidence=1.0,
                reasoning=fallback_reason,
            )
        ]
        log.info(
            f"\U0001f3af classify_content fallback → single-segment "
            f"{fallback_stage!r} for call_id={call_id} ({fallback_reason})"
        )

    if not segments:
        call.status = "needs_classification"
        call.reason = (
            "AI couldn't identify any compliance-relevant segment in this "
            "recording. Reviewer please classify manually."
        )
        db.commit()
        log.warning(
            f"\U0001f3af classify_content empty call_id={call_id} → needs_classification"
        )
        return {"halted": True, "segments": 0}

    # Persist one row per segment. Record start/end timestamps from
    # word_data when available so the UI can deep-link audio.
    for idx, seg in enumerate(segments):
        start_s = None
        end_s = None
        if seg.start_word_idx < len(word_data):
            start_s = word_data[seg.start_word_idx].get("start")
        if seg.end_word_idx < len(word_data):
            end_s = word_data[seg.end_word_idx].get("end")
        # Build the segment's transcript excerpt slice for quick UI preview.
        seg_words = word_data[seg.start_word_idx : seg.end_word_idx + 1]
        excerpt = " ".join(
            (w.get("punctuated_word") or w.get("word") or "").strip() for w in seg_words
        )
        db.add(
            _CallSegment(
                call_id=call_id,
                idx=idx,
                stage=seg.segment_type,
                transcript_excerpt=excerpt[:2000],
                start_word_idx=seg.start_word_idx,
                end_word_idx=seg.end_word_idx,
                start_s=start_s,
                end_s=end_s,
                confidence=seg.confidence,
                classifier_reasoning=seg.reasoning,
            )
        )

    # Audit row.
    try:
        db.add(
            _AgentTrace(
                id=str(_uuid.uuid4()),
                call_id=call_id,
                run_id=str(_uuid.uuid4()),
                turn=0,
                role="tool",
                tool_name="content_classifier",
                tool_input=json.dumps({"transcript_chars": len(transcript)}),
                tool_output=json.dumps(
                    [
                        {
                            "stage": s.segment_type,
                            "start_word_idx": s.start_word_idx,
                            "end_word_idx": s.end_word_idx,
                            "confidence": s.confidence,
                        }
                        for s in segments
                    ]
                ),
                content=f"{len(segments)} segment(s) detected",
                model="opus-4.7",
            )
        )
    except Exception as _e:
        log.warning(f"agent_trace skipped for classify_content: {_e}")

    db.commit()
    return {"halted": False, "segments": len(segments)}


# ── Step 4 helper: checkpoint-result reconciliation ──────────────────────


def _normalize_checkpoint_results(
    all_results: list[dict],
    template_index: dict[tuple, dict],
) -> list[dict]:
    """Reconcile per-segment analyzer outputs against the union of script
    templates so ``Call.checkpoint_results`` ends up with exactly ONE row
    per template CP.

    Why this exists: the per-segment analyzer occasionally either:
    * emits the same CP twice (when two segments use the same script and
      both manage to score the same rule against their slice), OR
    * silently omits a CP (when the segment slice was too short to hit
      the rule's anchor phrases — the analyzer's name-keyed map drops it).

    Both bugs surface as user-visible "Not yet scored" labels in the UI
    plus a wrong score denominator. The fix happens here, at the merge
    step, so we never trust the per-segment list to be complete.

    Algorithm:
      1. Dedupe ``all_results`` by case-insensitive ``name``; first entry
         with a non-null ``status`` wins.
      2. For every template entry missing from the dedupe, append a
         synthetic ``status="not_scored"`` row.
      3. Sort by ``section`` asc so the UI ordering matches the script.

    The synthetic row's shape mirrors what the analyzer would have emitted
    so downstream consumers (CheckpointCard, rejection_factory) don't have
    to special-case missing data — they just see ``status="not_scored"``.
    """
    norm = lambda s: (s or "").strip().lower()
    deduped: dict[str, dict] = {}
    for r in all_results or []:
        name = (r.get("name") or "").strip()
        if not name:
            continue
        key = norm(name)
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = r
            continue
        # Prefer the entry with a real status over a missing/blank one.
        existing_status = (existing.get("status") or "").strip().lower()
        new_status = (r.get("status") or "").strip().lower()
        if (not existing_status or existing_status == "not_scored") and new_status:
            deduped[key] = r

    # Append synthetic rows for any template CP the analyzer didn't cover.
    for (section, name), tcp in template_index.items():
        if norm(name) in deduped:
            continue
        deduped[norm(name)] = {
            "section": section,
            "name": name,
            "status": "not_scored",
            "evidence": None,
            "notes": (
                "Checkpoint not evaluated by the AI — likely outside the "
                "detected segment boundary. A reviewer can re-run analysis "
                "on this segment to surface a verdict."
            ),
            "confidence": "n/a",
            "needs_review": True,
            "script_line_number": tcp.get("line_number") or tcp.get("section"),
            "similar_rejection_id": None,
            "suggested_category": None,
            "suggested_fix_required": None,
            "category_confidence": None,
            "ai_rejection_reason": None,
            "ai_narrative_notes": None,
            "severity": tcp.get("severity") or "medium",
            "category": tcp.get("category"),
        }

    out = list(deduped.values())
    out.sort(key=lambda r: (
        # Sort by section asc; rows with no section land at the end.
        r.get("section") if isinstance(r.get("section"), int) else 99999
    ))
    return out


# ── Step 4: analyze_checkpoints ──────────────────────────────────────────
async def _step_analyze_checkpoints(
    call_id: str,
    transcript_data: dict,
    db: Session,
) -> dict:
    """Per-segment analyzer (2026-05-12 rebuild).

    Loops over the CallSegment rows _step_classify_content just wrote;
    routes each segment to its rubric via ``route_for_segment``; grades the
    segment's transcript slice via ``analyze_all_checkpoints``; persists
    a per-segment verdict back onto the CallSegment row + a CallCheckpoint
    row per rule with ``segment_id`` set.

    Returns ``{"mode": "segments", "segments": [...summaries], "results": [...flat]}``.
    """
    from app.agents.rubric_router import route_for_segment
    from app.models import CallSegment as _CallSegment, AgentTrace as _AgentTrace
    import uuid as _uuid

    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise RuntimeError(f"analyze_checkpoints: call {call_id} not found")

    transcript = transcript_data.get("transcript") or ""
    word_data: list[dict] = []
    if call.word_data:
        try:
            word_data = json.loads(call.word_data) or []
        except Exception:
            word_data = []

    # Idempotency — wipe existing CallCheckpoint rows for the call.
    db.query(CallCheckpoint).filter_by(call_id=call_id).delete()
    db.flush()

    segments_rows = (
        db.query(_CallSegment)
        .filter_by(call_id=call_id)
        .order_by(_CallSegment.idx.asc())
        .all()
    )

    if not segments_rows:
        # Defensive — classify_content should have halted, but just in case.
        log.warning(
            f"\U0001f4cb analyze_checkpoints: no segments for call_id={call_id} — "
            "treating as halted, no analysis run"
        )
        return {"mode": "segments", "segments": [], "results": []}

    segment_summaries: list[dict] = []
    all_results: list[dict] = []
    # 2026-05-15: collect every script checkpoint template encountered across
    # all segments so the merge step can guarantee one result row per template
    # CP. Indexed by (section, name) so we keep duplicates from the same script
    # collapsed but preserve different script entries that happen to share a
    # section number. Used by _normalize_checkpoint_results below.
    template_index: dict[tuple, dict] = {}

    for seg in segments_rows:
        rubric = route_for_segment(seg.stage, call, db)
        log.info(
            f"\U0001f4cb segment={seg.stage}[{seg.idx}] rubric={rubric.kind} · {rubric.reason}"
        )

        # Slice the transcript text for this segment from word_data.
        try:
            start_idx = int(seg.start_word_idx or 0)
            end_idx = int(seg.end_word_idx or 0)
        except (TypeError, ValueError):
            start_idx, end_idx = 0, len(word_data) - 1
        seg_words = word_data[start_idx : end_idx + 1] if word_data else []
        seg_transcript = " ".join(
            (w.get("punctuated_word") or w.get("word") or "").strip() for w in seg_words
        )
        if not seg_transcript.strip():
            seg_transcript = transcript  # safety net — grade against full

        script = rubric.script
        checkpoints_def: list = []
        if script:
            try:
                checkpoints_def = json.loads(script.checkpoints or "[]") or []
            except Exception:
                checkpoints_def = []
        # 2026-05-15: index every template CP we encounter so the merge
        # step can backfill any rule the analyzer silently dropped (e.g.
        # because the segment transcript was too short / the rule's
        # key_phrases didn't appear in the slice). Keys on (section, name)
        # so multiple segments using the same script don't duplicate; the
        # union across segments is the canonical "every rule that should
        # have produced a result for this call".
        for tcp in checkpoints_def:
            sec = tcp.get("section") or tcp.get("line_number") or 0
            name = (tcp.get("name") or "").strip()
            if not name:
                continue
            template_index[(sec, name)] = tcp

        if not (script and checkpoints_def):
            # No supplier-specific rubric matched (or empty checkpoints).
            # Fall through to the V1 third-party-disclosure analyzer for
            # this segment — same legacy behavior as the pre-rewrite
            # _step_analyze_checkpoints. Emits CallCheckpoint rows + a
            # per-segment verdict so the UI keeps grading the call
            # rather than stubbing it out.
            #
            # 2026-05-14 audit fix: wrap the LLM call in try/except so a
            # JSONDecodeError / HTTP 5xx on this segment doesn't lose the
            # work already done on earlier segments. Marks this segment
            # as `review` bucket with a clear reason and continues.
            try:
                v1 = await analyze_compliance_v1(seg_transcript)
            except Exception as e:
                log.warning(
                    f"⚠️ V1 fallback failed for segment {seg.idx} "
                    f"({seg.stage!r}): {type(e).__name__}: {str(e)[:200]}"
                )
                seg.script_id = None
                seg.score = "0/0"
                seg.compliant = False
                seg.bucket = "review"
                seg.compliance_status = "pending"
                seg.critical_breaches = 0
                seg.high_breaches = 0
                seg.medium_breaches = 0
                seg.reason = (
                    f"V1 fallback errored ({type(e).__name__}). Reviewer "
                    "must grade this segment manually."
                )
                seg.checkpoint_results = json.dumps([])
                segment_summaries.append(
                    {
                        "stage": seg.stage,
                        "passed": 0,
                        "total": 0,
                        "bucket": seg.bucket,
                        "critical_breaches": 0,
                        "high_breaches": 0,
                        "medium_breaches": 0,
                        "compliant": False,
                    }
                )
                continue
            # Don't let a segment-local agent_name override the call-level
            # one set by detect_metadata. Only fill it when the call has
            # nothing yet — fixes the "last-segment wins" overwrite bug.
            if v1.agent_name and v1.agent_name != "Unknown" and not (call.agent_name or "").strip():
                call.agent_name = v1.agent_name
            if v1.customer_name and v1.customer_name != "Unknown" and not (call.customer_name or "").strip():
                call.customer_name = v1.customer_name
            if not call.excerpt and v1.excerpt:
                call.excerpt = v1.excerpt

            v1_total = len(v1.checkpoints)
            v1_passed = sum(1 for cp in v1.checkpoints if cp.passed)
            seg.script_id = None
            seg.score = f"{v1_passed}/{v1_total}" if v1_total else "0/0"
            seg.compliant = bool(v1.compliant)
            seg.bucket = "pass" if (v1_total and v1_passed == v1_total) else (
                "review" if v1_total else "pass"
            )
            seg.compliance_status = (
                "compliant" if seg.bucket == "pass" else "pending"
            )
            seg.critical_breaches = 0
            seg.high_breaches = 0 if seg.bucket != "review" else max(0, v1_total - v1_passed)
            seg.medium_breaches = 0
            seg.reason = v1.reason or (
                f"V1 fallback (no supplier script matched for "
                f"supplier={call.detected_supplier!r})."
            )
            seg.checkpoint_results = json.dumps(
                [
                    {
                        "name": cp.rule,
                        "status": "pass" if cp.passed else "fail",
                        "evidence": cp.excerpt,
                    }
                    for cp in v1.checkpoints
                ]
            )

            for cp in v1.checkpoints:
                db.add(
                    CallCheckpoint(
                        call_id=call_id,
                        segment_id=seg.id,
                        rule_text=cp.rule,
                        passed=cp.passed,
                        excerpt=cp.excerpt,
                        confidence="high",
                        needs_review=False,
                    )
                )
                all_results.append(
                    {"name": cp.rule, "status": "pass" if cp.passed else "fail"}
                )

            segment_summaries.append(
                {
                    "stage": seg.stage,
                    "passed": v1_passed,
                    "total": v1_total,
                    "bucket": seg.bucket,
                    "critical_breaches": 0,
                    "high_breaches": seg.high_breaches,
                    "medium_breaches": 0,
                    "compliant": seg.compliant,
                }
            )
            continue

        # Grade against the rubric.
        result = await analyze_all_checkpoints(
            seg_transcript,
            checkpoints_def,
            script.mode,
            supplier=script.supplier_name,
            word_data=word_data,  # full array for global timestamp resolution
            agent_speaker_label="A",
            customer_speaker_label="B",
            db=db,
            call_id=call_id,
        )

        # 2026-05-14 audit fix: don't overwrite a non-empty agent/customer
        # name set by detect_metadata or an earlier segment. Previously
        # the last segment iterated would silently clobber the prior
        # name with whatever the last LLM call returned.
        if (
            result.get("agent_name")
            and result["agent_name"] != "Unknown"
            and not (call.agent_name or "").strip()
        ):
            call.agent_name = result["agent_name"]
        if (
            result.get("customer_name")
            and result["customer_name"] != "Unknown"
            and not (call.customer_name or "").strip()
        ):
            call.customer_name = result["customer_name"]

        verified = result["results"]
        summary = result["summary"]

        # Persist per-segment verdict.
        seg.script_id = str(script.id)
        seg.score = summary.get("score") or "0/0"
        seg.compliant = bool(summary.get("compliant"))
        seg.bucket = summary.get("bucket", "review")
        seg.critical_breaches = summary.get("critical_breaches", 0)
        seg.high_breaches = summary.get("high_breaches", 0)
        seg.medium_breaches = summary.get("medium_breaches", 0)
        if seg.bucket == "pass":
            seg.compliance_status = "compliant"
            seg.reason = f"Score: {seg.score}. All checkpoints passed."
        elif seg.bucket == "coaching":
            seg.compliance_status = "compliant"
            seg.reason = (
                f"Score: {seg.score}. {seg.medium_breaches} medium issue(s) "
                "logged for coaching; no Critical or High breaches."
            )
        elif seg.bucket == "review":
            seg.compliance_status = "pending"
            seg.reason = (
                f"Score: {seg.score}. {seg.high_breaches} High-severity "
                "breach(es) — reviewer must decide."
            )
        else:  # blocked
            seg.compliance_status = "non_compliant"
            seg.reason = (
                f"Score: {seg.score}. {seg.critical_breaches} Critical "
                "breach(es) — auto-blocked."
            )
        seg.checkpoint_results = json.dumps(verified)

        # Insert per-rule CallCheckpoint rows with segment_id linkage.
        for cp in verified:
            db.add(
                CallCheckpoint(
                    call_id=call_id,
                    segment_id=seg.id,
                    rule_text=cp["name"],
                    passed=cp["status"] == "pass",
                    excerpt=cp.get("evidence"),
                    confidence=cp.get("confidence", "high"),
                    needs_review=cp.get("needs_review", False),
                    line_number=cp.get("script_line_number"),
                    ai_category=cp.get("suggested_category"),
                    ai_fix_required=cp.get("suggested_fix_required"),
                    ai_category_confidence=cp.get("category_confidence"),
                    ai_rejection_reason=cp.get("ai_rejection_reason"),
                    ai_narrative_notes=cp.get("ai_narrative_notes"),
                )
            )

        segment_summaries.append(
            {
                "stage": seg.stage,
                "passed": summary.get("passed", 0),
                "total": summary.get("total", 0),
                "errors": int(summary.get("error", 0) or 0),
                "bucket": seg.bucket,
                "critical_breaches": seg.critical_breaches,
                "high_breaches": seg.high_breaches,
                "medium_breaches": seg.medium_breaches,
                "compliant": seg.compliant,
            }
        )
        all_results.extend(verified)

    # 2026-05-15 — Normalize ``all_results`` against the script templates
    # before persisting so every rule that COULD have been scored shows up
    # exactly once in ``call.checkpoint_results``. Real incident: Andrew
    # call (2652a095) — script had 37 CPs, analyzer emitted 37 entries
    # but with duplicates of sections 1-11 and a gap on sections 20 + 27-37.
    # CP20 "Confirm Microbusiness/Small Business status" rendered with
    # "Not yet scored" because the analyzer's per-segment slicing dropped it.
    #
    # Algorithm:
    #   1. Dedupe ``all_results`` by case-insensitive name — keep the
    #      first entry with a non-null status (analyzer truth wins).
    #   2. For every template CP missing from the dedupe, append a
    #      synthetic ``status="not_scored"`` row so the UI renders the
    #      rule with a clear placeholder instead of silently omitting it.
    #   3. Sort by section asc so the UI ordering matches the script.
    normalized: list[dict] = _normalize_checkpoint_results(all_results, template_index)
    call.checkpoint_results = json.dumps(normalized) if normalized else None

    db.commit()
    return {
        "mode": "segments",
        "segments": segment_summaries,
        "results": all_results,
    }


# ── Legacy helper (kept for back-compat — not used by new flow) ──────────
async def _legacy_analyze_checkpoints_unused(
    call_id: str,
    transcript_data: dict,
    db: Session,
) -> dict:
    """LEGACY single-rubric analyzer — kept only for reference. The new
    pipeline uses _step_analyze_checkpoints (per-segment) above. This
    function is unreferenced after the 2026-05-12 rebuild and exists
    only to make the diff reviewable.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise RuntimeError(f"analyze_checkpoints: call {call_id} not found")

    transcript = transcript_data["transcript"]

    db.query(CallCheckpoint).filter_by(call_id=call_id).delete()
    db.flush()

    from app.agents.rubric_router import route as _route_rubric
    from app.models import AgentTrace as _AgentTrace
    import uuid as _uuid

    rubric = _route_rubric(call, db)
    log.info(f"\U0001f4cb RUBRIC call_id={call_id} → {rubric.kind} · {rubric.reason}")

    # Audit row so the call-detail UI can show "Stage X: Rubric router
    # picked phrase_pack/lead_gen (88 rules) because call_type=lead_gen".
    # Best-effort — failure here must not block the pipeline.
    try:
        db.add(
            _AgentTrace(
                id=str(_uuid.uuid4()),
                call_id=call_id,
                run_id=str(_uuid.uuid4()),
                turn=0,
                role="tool",
                tool_name="rubric_router",
                tool_input=json.dumps({"call_type": rubric.call_type}),
                tool_output=json.dumps(
                    {
                        "kind": rubric.kind,
                        "script_id": str(rubric.script.id) if rubric.script else None,
                        "script_name": (rubric.script.script_name if rubric.script else None),
                        "checkpoint_count": (
                            len(json.loads(rubric.script.checkpoints or "[]") or [])
                            if rubric.script
                            else 0
                        ),
                    }
                ),
                content=rubric.reason,
                model="deterministic",
            )
        )
        db.flush()
    except Exception as _e:
        log.warning(f"agent_trace write skipped for rubric_router: {_e}")

    script = rubric.script
    checkpoints_def: list = []
    if script:
        try:
            checkpoints_def = json.loads(script.checkpoints or "[]") or []
        except Exception:
            checkpoints_def = []
        if not checkpoints_def:
            # Synthetic phrase-pack with empty rules, or supplier script
            # row that exists but has no cps yet. Fall through to V1.
            log.warning(
                f"\U0001f4cb RUBRIC matched script \"{script.script_name}\" "
                "but checkpoints empty → V1 fallback"
            )
            script = None
        if script and checkpoints_def:
            parsed_word_data = []
            if call.word_data:
                try:
                    parsed_word_data = json.loads(call.word_data)
                except Exception:
                    parsed_word_data = []

            result = await analyze_all_checkpoints(
                transcript,
                checkpoints_def,
                script.mode,
                supplier=script.supplier_name,
                word_data=parsed_word_data,
                agent_speaker_label="A",
                customer_speaker_label="B",
                db=db,
                call_id=call_id,
            )

            if result.get("agent_name") and result["agent_name"] != "Unknown":
                call.agent_name = result["agent_name"]
            if result.get("customer_name") and result["customer_name"] != "Unknown":
                call.customer_name = result["customer_name"]

            verified = result["results"]
            call.checkpoint_results = json.dumps(verified)
            for cp in verified:
                # W4.4 + W4.7 — persist the 5 AI-suggested fields onto the
                # CallCheckpoint row. All nullable; the checkpoint analyzer
                # always populates these keys (None when LLM didn't supply
                # or the value failed enum validation), so .get() is safe.
                db.add(CallCheckpoint(
                    call_id=call_id,
                    rule_text=cp["name"],
                    passed=cp["status"] == "pass",
                    excerpt=cp.get("evidence"),
                    confidence=cp.get("confidence", "high"),
                    needs_review=cp.get("needs_review", False),
                    line_number=cp.get("script_line_number"),
                    ai_category=cp.get("suggested_category"),
                    ai_fix_required=cp.get("suggested_fix_required"),
                    ai_category_confidence=cp.get("category_confidence"),
                    # Sprint A1 — AI-populated rejection narrative.
                    ai_rejection_reason=cp.get("ai_rejection_reason"),
                    ai_narrative_notes=cp.get("ai_narrative_notes"),
                ))
            db.commit()
            return {"mode": "script", "results": verified, "summary": result["summary"]}

    # No-script-match fallback — runs the legacy heuristic analyzer.
    log.info(f"\U0001f4cb SCRIPT no match call_id={call_id} → fallback (no_script_match)")
    v1 = await analyze_compliance_v1(transcript)
    if v1.agent_name and v1.agent_name != "Unknown":
        call.agent_name = v1.agent_name
    # Only overwrite customer_name when the analyzer actually extracted one;
    # otherwise we'd clobber the form-supplied value (e.g. ObservabilitySmoke)
    # with "Unknown" and produce a misleading observability summary.
    if v1.customer_name and v1.customer_name != "Unknown":
        call.customer_name = v1.customer_name
    call.excerpt = v1.excerpt
    for cp in v1.checkpoints:
        db.add(CallCheckpoint(
            call_id=call_id,
            rule_text=cp.rule,
            passed=cp.passed,
            excerpt=cp.excerpt,
        ))
    # Persist the verdict on the call row so downstream score/finalize
    # steps (and the observability evidence panel) see real values
    # instead of nulls. Mirrors the script-path behavior.
    if v1.checkpoints:
        passed = sum(1 for cp in v1.checkpoints if cp.passed)
        call.score = f"{passed}/{len(v1.checkpoints)}"
        call.compliant = all(cp.passed for cp in v1.checkpoints)
    else:
        call.compliant = v1.compliant
    call.reason = v1.reason
    db.commit()
    return {"mode": "no_script_match", "v1": v1}


# ── Step 5: score (aggregator across per-segment verdicts) ───────────────
_BUCKET_RANK = {"pass": 0, "coaching": 1, "review": 2, "blocked": 3}


def _step_score(call_id: str, analysis: dict, db: Session) -> dict:
    """Aggregate per-segment verdicts to a call-level score + bucket.

    Worst-bucket-wins across all segments: a single Critical breach in
    any segment flips the whole call to ``blocked``. Score is the sum of
    passed / sum of total across all segments. Reason summarises each
    segment's verdict.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise RuntimeError(f"score: call {call_id} not found")

    if analysis.get("mode") != "segments":
        # Defensive: legacy or unknown mode. Reset and bail.
        call.score = None
        call.compliant = None
        call.compliance_status = "pending"
        call.reason = "Unknown analysis mode — manual review required."
        call.status = "needs_manual_review"
        db.commit()
        return {"score": None, "compliant": None, "status": call.status, "reason": call.reason}

    segments = analysis.get("segments", [])

    if not segments:
        # Pipeline should've halted at classify_content; defensive.
        call.score = "0/0"
        call.compliant = None
        call.compliance_status = "pending"
        call.bucket = "review"
        call.reason = "No segments analysed."
        call.status = "needs_manual_review"
        db.commit()
        return {"score": call.score, "compliant": None, "status": call.status, "reason": call.reason}

    total_passed = sum(int(s.get("passed", 0)) for s in segments)
    total_total = sum(int(s.get("total", 0)) for s in segments)
    worst_bucket = "pass"
    for s in segments:
        b = s.get("bucket", "pass")
        if _BUCKET_RANK.get(b, 0) > _BUCKET_RANK.get(worst_bucket, 0):
            worst_bucket = b

    crit = sum(int(s.get("critical_breaches", 0)) for s in segments)
    high = sum(int(s.get("high_breaches", 0)) for s in segments)
    med = sum(int(s.get("medium_breaches", 0)) for s in segments)

    call.score = f"{total_passed}/{total_total}" if total_total > 0 else "0/0"
    call.bucket = worst_bucket
    call.compliant = worst_bucket in {"pass", "coaching"}

    # Map bucket → compliance_status (UI sees only Compliant / Pending / Non-Compliant)
    if worst_bucket == "pass":
        call.compliance_status = "compliant"
    elif worst_bucket == "coaching":
        call.compliance_status = "compliant"
    elif worst_bucket == "review":
        call.compliance_status = "pending"
    else:  # blocked
        call.compliance_status = "non_compliant"

    # Human-readable per-segment breakdown.
    breakdown_bits: list[str] = []
    for s in segments:
        stg = s.get("stage", "?")
        sc = f"{s.get('passed', 0)}/{s.get('total', 0)}"
        b = s.get("bucket", "pass")
        marker = (
            "✓" if b == "pass"
            else "⚠" if b in ("coaching", "review")
            else "✗"
        )
        breakdown_bits.append(f"{stg} {sc} {marker}")
    breakdown = " · ".join(breakdown_bits)

    if worst_bucket == "pass":
        call.reason = f"Score: {call.score}. All segments passed. ({breakdown})"
    elif worst_bucket == "coaching":
        call.reason = (
            f"Score: {call.score}. {med} medium issue(s) logged for coaching; "
            f"no Critical or High breaches. ({breakdown})"
        )
    elif worst_bucket == "review":
        call.reason = (
            f"Score: {call.score}. {high} High-severity breach(es) — reviewer must decide. "
            f"({breakdown})"
        )
    else:  # blocked
        call.reason = (
            f"Score: {call.score}. {crit} Critical breach(es) — auto-blocked. "
            f"({breakdown})"
        )

    # Graceful degradation: if more than half of all checkpoints errored
    # (e.g. LLM timeouts), surface ``needs_manual_review`` so the
    # reviewer triages instead of trusting an under-graded verdict.
    # Mirrors the legacy contract enforced by test_graceful_degradation.
    total_errors = sum(int(s.get("errors", 0) or 0) for s in segments)
    total_processed = total_total + total_errors  # denominator pre-error-exclusion
    if total_processed > 0 and total_errors * 2 > total_processed:
        call.status = "needs_manual_review"
        call.compliant = False
        call.reason = (
            f"Manual review required: {total_errors}/{total_processed} "
            f"checkpoints errored (analyzer failures). ({breakdown})"
        )
    elif call.status != "needs_manual_review":
        call.status = "completed"
    if total_errors > 0:
        # Even at sub-50% error rates, never claim full compliance with
        # missing data.
        call.compliant = False
    db.commit()
    return {
        "score": call.score,
        "compliant": call.compliant,
        "status": call.status,
        "reason": call.reason,
    }


# ── Step 6: finalize ─────────────────────────────────────────────────────
def _step_finalize(call_id: str, db: Session) -> dict:
    """derive_compliance for HITL routing + completed_at + extraction writer
    (L2 enterprise sprint) + commit. Idempotent: derive_compliance is a function
    of current Call state; extraction writer does delete-then-insert per call_id.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise RuntimeError(f"finalize: call {call_id} not found")

    call.completed_at = utcnow()
    derive_compliance(call, db)

    # L3: derive deal lifecycle BEFORE the extraction writer commits so
    # the deal row + extraction outputs land in the same transaction.
    # Wrapped in try/except: a missing column (pre-migration) or any
    # other lifecycle bug must never block call finalisation. Last-
    # writer-wins semantics — every finalize recomputes from current
    # call set so out-of-order uploads converge.
    try:
        if call.deal_id:
            from app.deal_lifecycle import derive_lifecycle_status
            from app.models import CustomerDeal
            deal = db.query(CustomerDeal).filter_by(id=call.deal_id).first()
            if deal is not None:
                deal_calls = db.query(Call).filter_by(deal_id=call.deal_id).all()
                derived = derive_lifecycle_status(deal, deal_calls)
                if hasattr(deal, "lifecycle_status"):
                    deal.lifecycle_status = derived
                log.info(
                    f"L3_LIFECYCLE call_id={call_id} deal_id={call.deal_id} "
                    f"→ {derived}"
                )
    except Exception as e:
        log.error(f"L3_LIFECYCLE_FAILED call_id={call_id} err={e!r}")

    # MPAN/MPRN extractor — pure regex, runs at finalize so any uploaded
    # call gets meter IDs into the deal record. Skips silently if no
    # cue in transcript (most lead-gen calls won't have it; closer + LOA
    # calls do). 2026-05-11.
    try:
        from app.agents.meter_extractor import extract_meters
        if call.deal_id and call.transcript:
            from app.models import CustomerDeal
            deal = db.query(CustomerDeal).filter_by(id=call.deal_id).first()
            if deal:
                meters = extract_meters(call.transcript)
                if meters["mpan"] and not getattr(deal, "mpan_electricity", None):
                    deal.mpan_electricity = meters["mpan"]
                    if not deal.mpan_or_mprn:
                        deal.mpan_or_mprn = meters["mpan"]
                    log.info(f"METER_EXTRACT call_id={call_id} mpan={meters['mpan']}")
                if meters["mprn"] and not getattr(deal, "mprn_gas", None):
                    deal.mprn_gas = meters["mprn"]
                    if not deal.mpan_or_mprn:
                        deal.mpan_or_mprn = meters["mprn"]
                    log.info(f"METER_EXTRACT call_id={call_id} mprn={meters['mprn']}")
    except Exception as e:
        log.error(f"METER_EXTRACT_FAILED call_id={call_id} err={e!r}")

    # L2 extraction writer — segments + flags + entities. Wrapped in try/except
    # so a writer bug never blocks the call from completing. Errors logged for
    # follow-up; the call still finalizes with checkpoint_results intact.
    try:
        _write_extraction_outputs(call, db)
    except Exception as e:
        log.error(f"L2_EXTRACTION_FAILED call_id={call_id} err={e!r}")

    db.commit()
    log.info(f"\U0001f4be SAVED call_id={call_id}")
    return {
        "compliance_status": call.compliance_status,
        "completed_at": call.completed_at.isoformat() if call.completed_at else None,
    }


def _write_extraction_outputs(call: Call, db: Session) -> None:
    """Idempotent extraction writer (L2). Refreshes Flag + ExtractedEntity
    rows for this call. The segment rows are owned by `_step_classify_content`
    (the 4-stage AI classifier) and are NOT touched here — the legacy
    `extraction/segments.detect_segments` emits the obsolete 6-stage taxonomy
    (intro/qualification/pitch/transfer/verbal/close) which fails the
    `ck_call_segments_stage` CHECK constraint and crashed the whole
    finalize step until 2026-05-15.
    """
    import asyncio
    import json as _json
    from app.extraction.entities import extract_entities
    from app.extraction.flags import derive_flags
    from app.extraction.vulnerability import detect_vulnerability
    from app.models import CallSegment, Flag, ExtractedEntity, Script

    checkpoint_results = []
    if call.checkpoint_results:
        try:
            checkpoint_results = _json.loads(call.checkpoint_results)
        except Exception:
            checkpoint_results = []

    script = (
        db.query(Script).filter_by(id=call.script_id).first() if call.script_id else None
    )

    # Idempotent — delete prior Flag + ExtractedEntity rows only. Do NOT delete
    # CallSegment rows; those belong to the classifier step (4-stage taxonomy).
    db.query(Flag).filter_by(call_id=call.id).delete(synchronize_session=False)
    db.query(ExtractedEntity).filter_by(call_id=call.id).delete(synchronize_session=False)
    db.flush()

    # Pull the authoritative segments the classifier wrote so downstream
    # flag/pricing-mismatch detectors keep working unchanged. They only
    # read shape attributes (stage, start_word_idx, end_word_idx); the
    # 4-stage names work fine for those callers.
    segments = (
        db.query(CallSegment)
        .filter_by(call_id=call.id)
        .order_by(CallSegment.start_word_idx)
        .all()
    )

    # Run async extract_entities in a dedicated thread+loop. Always works
    # whether finalize is called from sync (legacy pipeline) or async (Inngest
    # workflow) context. asyncio.run() can't be used inside a running loop
    # and asyncio.new_event_loop().run_until_complete() also fails when an
    # outer loop is active — only the threaded approach is safe in both modes.
    import concurrent.futures

    def _run_extract():
        return asyncio.new_event_loop().run_until_complete(
            extract_entities(call.id, call.transcript or "")
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        entities = pool.submit(_run_extract).result(timeout=30)
    for ent in entities:
        db.add(ent)

    flags = derive_flags(call.id, checkpoint_results, segments, script, call_type=call.call_type)

    # W3.C — vulnerable-customer detector. Runs async like extract_entities;
    # errors degrade silently (returns None) so a flaky LLM never breaks
    # finalize. Appended to the per-checkpoint flag list so it surfaces on
    # the verdict tab alongside the other risk_tags.
    def _run_vuln():
        return asyncio.new_event_loop().run_until_complete(
            detect_vulnerability(call.id, call.transcript or "")
        )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            vuln_flag = pool.submit(_run_vuln).result(timeout=25)
    except Exception as exc:
        log.warning("vulnerability detection failed: %s", exc)
        vuln_flag = None
    if vuln_flag is not None:
        flags.append(vuln_flag)

    for flag in flags:
        db.add(flag)

    # W3.A — pricing-mismatch flags. Behind a feature flag so we can
    # ship even if the extractor is too noisy on real calls.
    from app.config import settings as _settings
    pricing_flag_count = 0
    if getattr(_settings, "pricing_mismatch_enabled", True):
        from app.extraction.flags import derive_pricing_mismatch_flags
        pricing_flags = derive_pricing_mismatch_flags(
            call.id, call.transcript or "", script, segments
        )
        for flag in pricing_flags:
            db.add(flag)
        pricing_flag_count = len(pricing_flags)

    log.info(
        f"L2_EXTRACTION_WRITE call_id={call.id} "
        f"segments={len(segments)} flags={len(flags)} "
        f"pricing_flags={pricing_flag_count} entities={len(entities)} "
        f"vulnerable={'yes' if vuln_flag is not None else 'no'}"
    )

    # ── Deal-attribute backfill from extracted entities ──────────────
    # The tracker columns are useless when MPAN/MPRN/deal-value sit on
    # ExtractedEntity rows but never propagate to CustomerDeal. Lift the
    # high-confidence (regex/LLM-confirmed) values across so the tracker
    # row populates without manual edits.
    try:
        from app.models import CustomerDeal as _DealEnt
        if call.deal_id and entities:
            deal_e = db.query(_DealEnt).filter_by(id=call.deal_id).first()
            if deal_e:
                # Pick the highest-confidence MPAN or MPRN (either lights up
                # the tracker's "MPAN/MPRN" column).
                meter_ents = [
                    e for e in entities
                    if getattr(e, "kind", None) in ("mpan", "mprn")
                    and getattr(e, "value", None)
                ]
                if meter_ents and not deal_e.mpan_or_mprn:
                    best = max(meter_ents, key=lambda e: getattr(e, "confidence", 0) or 0)
                    if can_overwrite(deal_e, "mpan_or_mprn", "ai"):
                        deal_e.mpan_or_mprn = best.value
                        set_source(deal_e, "mpan_or_mprn", "ai")

                # Same for deal value when extractor surfaced a £ figure.
                value_ents = [
                    e for e in entities
                    if getattr(e, "kind", None) in ("deal_value", "value_gbp", "amount_gbp")
                    and getattr(e, "value", None)
                ]
                if value_ents and deal_e.deal_value_gbp is None:
                    best = max(value_ents, key=lambda e: getattr(e, "confidence", 0) or 0)
                    try:
                        cleaned = (
                            str(best.value).replace(",", "").replace("£", "").strip()
                        )
                        if cleaned:
                            deal_e.deal_value_gbp = float(cleaned)
                            set_source(deal_e, "deal_value_gbp", "ai")
                    except (ValueError, TypeError):
                        pass
    except Exception as e:
        log.warning(f"deal backfill skipped call_id={call.id}: {e}")


# ── Sprint A5: rejection auto-create helpers ────────────────────────────
def _parse_score(score_str: str | None) -> tuple[int | None, int | None]:
    """Parse Call.score (e.g. ``'10/24'``) → (passed, total). Returns
    (None, None) when the field is empty or malformed."""
    if not score_str or "/" not in score_str:
        return None, None
    try:
        s, t = score_str.split("/", 1)
        return int(s.strip()), int(t.strip())
    except (ValueError, AttributeError):
        return None, None


async def _maybe_create_rejection(call, db) -> None:
    """Create a ``Rejection`` row for ``call`` when its score sits below the
    threshold defined in :mod:`app.rejection_factory`.

    Idempotent: skips when a Rejection already exists for ``call.id``. Caller
    owns ``db.commit()`` — the helper ``db.add()``'s and lets the orchestrator
    decide when to flush.
    """
    from app.models import Rejection, CustomerDeal as _Deal, Customer as _Cust

    if db.query(Rejection).filter_by(call_id=call.id).first() is not None:
        return  # idempotent — already created on a prior run

    score, total = _parse_score(getattr(call, "score", None))
    if not should_create_rejection(score=score, total=total):
        return

    failing: list[dict] = []
    try:
        if call.checkpoint_results:
            cps = json.loads(call.checkpoint_results)
            failing = [c for c in cps if c.get("status") in ("fail", "partial")]
    except Exception as e:
        log.warning(f"rejection: checkpoint parse failed call_id={call.id}: {e}")
        return

    if not failing:
        return  # below threshold but no failing CPs to summarise — skip

    customer_slug = None
    if call.deal_id:
        deal = db.query(_Deal).filter_by(id=call.deal_id).first()
        if deal and deal.customer_id:
            cust = db.query(_Cust).filter_by(id=deal.customer_id).first()
            if cust:
                customer_slug = cust.slug

    payload = await build_rejection_for_call(
        call_id=str(call.id),
        customer_slug=customer_slug,
        supplier=call.detected_supplier,
        sales_agent=call.agent_name,
        failing_checkpoints=failing,
        db=db,  # enables per-LLM-call agent_traces rows for HITL "AI reasoning" UI
    )
    rej = Rejection(**payload)
    for f in payload.keys():
        if f != "call_id":  # FK, not user-editable
            set_source(rej, f, "ai")
    db.add(rej)
    log.info(
        f"\U0001f6a9 REJECTION_CREATED call_id={call.id} "
        f"customer={customer_slug} category={payload.get('category')}"
    )
