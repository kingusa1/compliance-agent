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

from sqlalchemy.orm import Session

from app.analysis import analyze_compliance_v1, detect_names, detect_script_variant, detect_supplier
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


async def _trace_step(call_id: str, step_name: str, fn, *args, **kwargs):
    """Wrap a _step_* call to write a pipeline_step_log row at start +
    finish, mirroring what app.workflows.process_call._logged_step does
    for the Inngest path. Lets /observability render the live waterfall +
    terminal feed even when DISABLE_INNGEST_EMIT=1 routes to this legacy
    pipeline. Failures here never break the verdict — same swallow-and-log
    policy as agent_traces.
    """
    import inspect, time as _time
    from app.workflows.process_call import _persist_step_running, _persist_step_done

    started = _time.time()
    row_id = _persist_step_running(call_id, step_name, args, kwargs)
    try:
        raw = fn(*args, **kwargs)
        result = await raw if inspect.isawaitable(raw) else raw
        elapsed_ms = int((_time.time() - started) * 1000)
        _persist_step_done(row_id, step_name, "ok", result, None, elapsed_ms)
        return result
    except Exception as e:
        elapsed_ms = int((_time.time() - started) * 1000)
        _persist_step_done(row_id, step_name, "err", None, repr(e), elapsed_ms)
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
        analysis = await _trace_step(
            call_id, "analyze_checkpoints", _step_analyze_checkpoints, call_id, transcript_data, db
        )
        await _trace_step(call_id, "score", _step_score, call_id, analysis, db)
        # Sprint A5: auto-create a Rejection row when score < threshold.
        # Runs AFTER _step_score commits so the helper sees the finalized
        # call.score, and BEFORE _step_finalize so derive_compliance can
        # observe the new Rejection. Failures degrade silently — a bad
        # rejection insert must never block call finalisation.
        try:
            score_call = db.query(Call).filter_by(id=call_id).first()
            if score_call is not None:
                await _trace_step(call_id, "create_rejection", _maybe_create_rejection, score_call, db)
                db.commit()
        except Exception as rej_err:
            log.error(f"\U0001f6a9 REJECTION_CREATE_FAILED call_id={call_id} err={rej_err!r}")
            db.rollback()
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
        return await transcribe_audio_groq(audio_path) if "groq_whisper" in enabled else None

    async def _co():
        return await transcribe_audio_cohere(audio_path) if "cohere" in enabled else None

    aai_result, dg_result, gm_result, gq_result, co_result = await asyncio.gather(
        _aai(), _dg(), _gm(), _gq(), _co(),
    )

    call.groq_whisper_transcript = gq_result
    call.cohere_transcript = co_result

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


def _maybe_merge_into_existing_deal(call: Call, db: Session) -> None:
    """After detect_metadata writes detected_supplier + customer_name,
    look for an existing open Deal under the same customer with the same
    supplier. If found, re-attach the call and delete the stub Deal that
    upload-time auto-created (only if the stub has no other calls).

    Sprint v3-C1 — mirrors Watt's mental model where a single ``customer
    + supplier`` tuple maps to ONE open Deal regardless of how many calls
    land for it. The auto-detect upload path creates a fresh stub Deal
    for every upload; this helper collapses that stub back into the
    existing open Deal once detection has filled in the identifying
    fields.
    """
    from app.models import CustomerDeal
    detected_supplier = (call.detected_supplier or "").strip()
    detected_customer = (call.customer_name or "").strip()
    if not detected_supplier or not detected_customer:
        return
    if not call.deal_id:
        return
    stub = db.query(CustomerDeal).filter_by(id=call.deal_id).first()
    if stub is None:
        return
    # Find a different open Deal under the same customer + supplier.
    candidates = db.query(CustomerDeal).filter(
        CustomerDeal.id != stub.id,
        CustomerDeal.supplier == detected_supplier,
        CustomerDeal.customer_name == detected_customer,
        CustomerDeal.status.in_(("open", "in_progress")),
    ).order_by(CustomerDeal.created_at.desc()).all()
    if not candidates:
        return
    target = candidates[0]
    log.info(
        f"\U0001f517 DEAL MERGE call_id={call.id} stub={stub.id} → existing={target.id}"
    )
    call.deal_id = target.id
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
            business_name = await detect_business_name(transcript)
            # Last-resort fallback: when no business name surfaces, fall back to
            # the detected customer's name so we never leave the stub label.
            if not business_name and call.customer_name and call.customer_name.strip():
                business_name = call.customer_name.strip()

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


# ── Step 4: analyze_checkpoints ──────────────────────────────────────────
async def _step_analyze_checkpoints(
    call_id: str,
    transcript_data: dict,
    db: Session,
) -> dict:
    """Run analyze_all_checkpoints (or v1 fallback when no script matched).

    IDEMPOTENT: deletes existing CallCheckpoint rows for the call_id before
    inserting fresh ones, so a retried step never double-writes. Returns the
    raw analysis dict (script-path) or v1 result-shaped dict (v1 path),
    distinguished by the 'mode' key.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise RuntimeError(f"analyze_checkpoints: call {call_id} not found")

    transcript = transcript_data["transcript"]

    # Idempotency guard — wipe prior rows so a retry produces the same row
    # set as a fresh run.
    db.query(CallCheckpoint).filter_by(call_id=call_id).delete()
    db.flush()

    script = None
    checkpoints_def: list = []
    if call.script_id:
        script = db.query(Script).filter_by(id=call.script_id).first()
        if script:
            checkpoints_def = json.loads(script.checkpoints) or []
            log.info(
                f"\U0001f4cb SCRIPT matched call_id={call_id} → "
                f"\"{script.script_name}\" ({len(checkpoints_def)} checkpoints)"
            )
            if not checkpoints_def:
                # The script row is metadata-only (e.g. seeded before the
                # markdown extracts shipped). Don't return 0/0 — drop into
                # the V1 third-party-disclosure analyzer so the reviewer
                # still sees a real verdict.
                log.warning(
                    f"\U0001f4cb SCRIPT empty-checkpoints call_id={call_id} → "
                    "falling through to V1 third-party-disclosure analyzer"
                )
                script = None  # signals the fallback path below
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


# ── Step 5: score ────────────────────────────────────────────────────────
def _step_score(call_id: str, analysis: dict, db: Session) -> dict:
    """Compute call.score / compliant / status / reason from analysis.
    Sync — no I/O beyond the DB write. Returns the same fields it set.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise RuntimeError(f"score: call {call_id} not found")

    if analysis["mode"] == "script":
        verified = analysis["results"]
        summary = analysis["summary"]
        total = len(verified)
        errored = summary["error"]

        if errored > total / 2:
            call.score = summary["score"]
            call.compliant = False
            call.status = "needs_manual_review"
            call.reason = (
                f"{errored} of {total} checkpoints failed to analyze. Manual review required."
            )
        else:
            call.score = summary["score"]
            call.compliant = summary["compliant"]
            passed = summary["passed"]
            partial = summary["partial"]
            failed = summary["failed"]
            call.reason = f"Score: {call.score}. " + (
                "All checkpoints passed." if call.compliant
                else f"{failed} checkpoint(s) missed, {partial} partial."
            )
            if errored > 0:
                call.reason += f" {errored} checkpoint(s) had errors."
        call.excerpt = None

    else:  # v1 fallback
        v1 = analysis["v1"]
        if v1.checkpoints:
            passed = sum(1 for cp in v1.checkpoints if cp.passed)
            total = len(v1.checkpoints)
            call.score = f"{passed}/{total}"
            call.compliant = all(cp.passed for cp in v1.checkpoints)
            call.reason = v1.reason
            call.checkpoint_results = json.dumps([
                {
                    "section": i + 1,
                    "name": cp.rule,
                    "status": "pass" if cp.passed else "fail",
                    "evidence": cp.excerpt,
                    # Prefer the analyst's per-checkpoint reasoning when the
                    # LLM populated it; fall back to the call-level reason so
                    # the reviewer never sees an empty AI Verdict box.
                    "notes": (cp.notes or "").strip() or v1.reason,
                }
                for i, cp in enumerate(v1.checkpoints)
            ])
        else:
            call.compliant = v1.compliant
            call.reason = v1.reason

    if call.status != "needs_manual_review":
        call.status = "completed"
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

    call.completed_at = datetime.utcnow()
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
    """Idempotent extraction writer (L2). Deletes prior rows for this call_id
    then inserts fresh segments + flags + entities. Async LLM extraction is
    invoked synchronously via asyncio.run since this is called from a sync
    workflow step."""
    import asyncio
    import json as _json
    from app.extraction.segments import detect_segments
    from app.extraction.entities import extract_entities
    from app.extraction.flags import derive_flags
    from app.extraction.vulnerability import detect_vulnerability
    from app.models import CallSegment, Flag, ExtractedEntity, Script

    word_data = []
    if call.word_data:
        try:
            word_data = _json.loads(call.word_data)
        except Exception:
            word_data = []

    checkpoint_results = []
    if call.checkpoint_results:
        try:
            checkpoint_results = _json.loads(call.checkpoint_results)
        except Exception:
            checkpoint_results = []

    script = (
        db.query(Script).filter_by(id=call.script_id).first() if call.script_id else None
    )

    # Idempotent — delete prior rows; FK ON DELETE CASCADE clears segments first.
    db.query(Flag).filter_by(call_id=call.id).delete(synchronize_session=False)
    db.query(ExtractedEntity).filter_by(call_id=call.id).delete(synchronize_session=False)
    db.query(CallSegment).filter_by(call_id=call.id).delete(synchronize_session=False)
    db.flush()

    segments = detect_segments(call.id, call.transcript or "", word_data, script)
    for seg in segments:
        db.add(seg)
    db.flush()  # need PKs for flag.segment_id FK

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
