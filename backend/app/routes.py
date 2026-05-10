import asyncio
import json
import os
import secrets
import tempfile
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.agent.feedback import abstract_and_store_review
from app.analysis import _call_llm, detect_supplier, V1_PROMPT
from app.audit import record_audit
from app.replay import reanalyze as _reanalyze_call
from app.checkpoint_analyzer import analyze_all_checkpoints
from app.config import settings
from app.database import get_db, SessionLocal
from app.logger import log
from app.models import Call, CallCheckpoint, CustomerDeal, Profile, Script
from app.reviewers import current_reviewer
from app.pipeline import process_call
from app.schemas import CallListResponse, CallResponse, StatsResponse
from app.storage import download_audio, signed_url, upload_audio
from app.transcription import transcribe_audio
from app.verification import fuzzy_match, _escape_ilike

router = APIRouter()


# Magic bytes for supported audio formats
_AUDIO_SIGNATURES = {
    b"\xff\xfb": "mp3",
    b"\xff\xf3": "mp3",
    b"\xff\xf2": "mp3",
    b"ID3": "mp3",
    b"RIFF": "wav",
    b"fLaC": "flac",
    b"OggS": "ogg",
}


# Whitelist of accepted upload extensions + their Content-Type headers.
# Kept strict: unknown extensions are rejected at the upload route so we
# never silently mislabel formats we can't actually play back.
SUPPORTED_AUDIO_EXTENSIONS = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


def _validate_audio_content(content: bytes, ext: str) -> bool:
    """Check that the file content matches a known audio format signature."""
    for sig in _AUDIO_SIGNATURES:
        if content[:len(sig)] == sig:
            return True
    # M4A/MP4 container: check for 'ftyp' at offset 4
    if len(content) >= 8 and content[4:8] == b"ftyp":
        return True
    return False


def _require_admin(x_admin_key: str = Header(default="")):
    """Simple shared-secret auth for admin endpoints.

    Constant-time comparison via secrets.compare_digest defends against
    timing-side-channel attacks on the admin key.
    """
    if not settings.admin_key:
        return
    if not secrets.compare_digest(x_admin_key.encode("utf-8"),
                                  settings.admin_key.encode("utf-8")):
        raise HTTPException(403, "Invalid or missing X-Admin-Key header")


@router.get("/api/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@router.get("/api/reviewers")
def list_reviewers(user=Depends(current_reviewer), db: Session = Depends(get_db)):
    """List all active reviewer profiles. Authenticated endpoint."""
    rows = db.query(Profile).filter_by(active=True).order_by(Profile.name).all()
    return {
        "reviewers": [
            {"id": p.id, "name": p.name, "email": p.email, "role": p.role}
            for p in rows
        ]
    }


@router.get("/api/me")
def get_me(user=Depends(current_reviewer)):
    """Return the authenticated user's profile (id, email, name, role).

    Used by the frontend to route users by role after login (reviewers land
    on /queue, everyone else on /calls).
    """
    return user


@router.post("/api/log")
async def browser_log(payload: dict):
    """Client-side console bridge. Browser `console.*` calls POST here so devs
    can see browser logs in the same terminal as backend logs."""
    level = str(payload.get("level", "info")).lower()
    message = str(payload.get("message", ""))[:2000]
    source = str(payload.get("source", ""))[:120]
    prefix = f"\U0001f310 BROWSER [{source}]" if source else "\U0001f310 BROWSER"
    if level in ("error", "exception"):
        log.error(f"{prefix} {message}")
    elif level in ("warn", "warning"):
        log.warning(f"{prefix} {message}")
    else:
        log.info(f"{prefix} {message}")
    return {"ok": True}


@router.post("/api/calls/upload", response_model=CallResponse)
async def upload_call(
    request: Request,
    file: UploadFile = File(...),
    script_id: str | None = None,
    stream: bool = False,
    deal_id: str | None = Form(default=None),
    call_type: str | None = Form(default="full"),
    customer_name: str | None = Form(default=None),
    # L7 — structured intake envelope. When the frontend sends the new
    # form, ``metadata`` is a JSON string matching ``IntakePayload``;
    # legacy clients omit it and we fall back to the form-encoded
    # customer_name + call_type + deal_id below.
    metadata: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    # ── L7: parse structured-intake envelope (when present) ───────────
    intake_payload = None
    intake_warnings: list[dict] = []
    if metadata:
        try:
            from app.intake import IntakePayload, validate_payload
            from app.intake.validators import ValidationGateError

            intake_payload = IntakePayload.model_validate_json(metadata)
        except ValidationGateError:
            raise
        except Exception as e:
            raise HTTPException(400, f"invalid intake metadata: {e}")
        # Run validation gates; the blocking gate raises ValidationGateError
        # which we surface as 422. Warnings are collected for the response.
        try:
            warnings = validate_payload(intake_payload)
            intake_warnings = [
                {"code": w.code, "message": w.message, "field": w.field}
                for w in warnings
            ]
        except ValidationGateError as e:
            raise HTTPException(422, {"code": e.code, "message": e.message})
        # Override the legacy form params from the structured envelope so
        # the rest of the route keeps working without rewriting the deal-
        # resolution / pipeline-dispatch logic. Manual fields land here
        # as ground truth; auto-detect runs in shadow downstream.
        if intake_payload.customer.legal_name:
            customer_name = intake_payload.customer.legal_name
        call_type = intake_payload.call.call_type
        if intake_payload.deal.existing_deal_id:
            deal_id = str(intake_payload.deal.existing_deal_id)
        log.info(
            f"\U0001f4cb L7 INTAKE call_type={call_type!r} supplier="
            f"{intake_payload.deal.supplier!r} dev_auto_detect="
            f"{intake_payload.dev_auto_detect} warnings={len(intake_warnings)}"
        )
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in SUPPORTED_AUDIO_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported audio format: {ext or 'no extension'}. "
            f"Supported: {', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS.keys()))}",
        )
    content_type = SUPPORTED_AUDIO_EXTENSIONS[ext]

    content = await file.read()
    if len(content) > settings.max_file_size:
        raise HTTPException(400, f"File too large. Maximum: {settings.max_file_size // (1024*1024)}MB")

    if not _validate_audio_content(content, ext):
        raise HTTPException(400, "File content does not match a known audio format. Ensure the file is a valid audio recording.")

    # Filename collision handling: auto-suffix instead of rejecting. The old
    # 409 forced reviewers to rename the file on disk before re-uploading a
    # previously-processed call, which was the wrong tradeoff — suppliers
    # frequently ship calls with overlapping filenames. Append ` (2)`,
    # ` (3)`, … until we find a free slot.
    if db.query(Call).filter_by(filename=file.filename).first():
        stem, dot, ext_only = file.filename.rpartition(".")
        base = stem if dot else file.filename
        tail = f".{ext_only}" if dot else ""
        n = 2
        while True:
            candidate = f"{base} ({n}){tail}"
            if not db.query(Call).filter_by(filename=candidate).first():
                file.filename = candidate
                break
            n += 1

    call_id = str(uuid.uuid4())
    log.info(f"\U0001f4e4 UPLOAD {file.filename} ({len(content)/1024/1024:.1f}MB) \u2192 call_id={call_id}")

    # Resolve deal linkage. Three priority levels:
    #   1. L7 envelope with customer.legal_name → upsert Customer + Deal so
    #      supplier / meter ids / commission / customer_id are persisted at
    #      intake time (B-1 fix). This overrides the legacy auto-create
    #      path that only wrote customer_name.
    #   2. explicit deal_id form param → existing-deal lookup (legacy).
    #   3. customer_name form param only → legacy auto-create by name.
    resolved_deal_id = None
    if intake_payload is not None and intake_payload.customer_id:
        # B-3: customer-page upload — Customer row already exists; attach
        # this call's deal to it directly without re-running the slug
        # upsert. customer.legal_name still needs to be present so the
        # Call.customer_name backfill below can stay in sync.
        from app.intake.upsert import upsert_deal
        from app.models import Customer

        customer_row = (
            db.query(Customer)
            .filter(Customer.id == intake_payload.customer_id)
            .first()
        )
        if customer_row is None:
            raise HTTPException(
                400, f"customer_id {intake_payload.customer_id} not found"
            )
        try:
            deal_row = upsert_deal(
                intake_payload.deal,
                customer_id=customer_row.id,
                customer_name=customer_row.legal_name,
                db=db,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        resolved_deal_id = deal_row.id
        customer_name = customer_row.legal_name
        log.info(
            f"\U0001f4c4 INTAKE_PREFILL customer_id={customer_row.id} "
            f"deal_id={deal_row.id} supplier={deal_row.supplier!r} "
            f"slug={customer_row.slug!r}"
        )
    elif intake_payload is not None and intake_payload.customer.legal_name:
        from app.intake.upsert import upsert_customer, upsert_deal

        try:
            customer_row = upsert_customer(intake_payload.customer, db)
            deal_row = upsert_deal(
                intake_payload.deal,
                customer_id=customer_row.id,
                customer_name=customer_row.legal_name,
                db=db,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        resolved_deal_id = deal_row.id
        # Keep customer_name in sync with the canonical Customer row so the
        # Call.customer_name column (still queried by legacy list views)
        # never disagrees with the new customers table.
        customer_name = customer_row.legal_name
        log.info(
            f"\U0001f4c4 INTAKE_UPSERT customer_id={customer_row.id} "
            f"deal_id={deal_row.id} supplier={deal_row.supplier!r} "
            f"slug={customer_row.slug!r}"
        )
    elif deal_id:
        try:
            resolved_deal_id = uuid.UUID(deal_id)
        except ValueError:
            raise HTTPException(400, "invalid deal_id")
        existing_deal = db.query(CustomerDeal).filter(CustomerDeal.id == resolved_deal_id).first()
        if not existing_deal:
            raise HTTPException(400, f"deal {deal_id} not found")
    elif customer_name:
        existing = db.query(CustomerDeal).filter(CustomerDeal.customer_name == customer_name).first()
        if existing:
            resolved_deal_id = existing.id
        else:
            new_deal = CustomerDeal(customer_name=customer_name, status="in_progress")
            db.add(new_deal)
            db.flush()
            resolved_deal_id = new_deal.id
            log.info(f"\U0001f4c4 DEAL auto-created id={new_deal.id} customer_name={customer_name!r}")
    else:
        # Auto-detect upload path: no customer_name + no deal_id + no L7
        # envelope. Create a STUB Deal so the pipeline's detect_metadata
        # step has somewhere to write the detected supplier/customer, and
        # so C1's _maybe_merge_into_existing_deal has a stub to collapse.
        # customer_name column is NOT NULL on cloud DB — pre-fill with a
        # placeholder the pipeline backfill will overwrite once detection
        # lands real names.
        stub_name = f"(auto-detect pending {call_id[:8]})"
        new_deal = CustomerDeal(customer_name=stub_name, status="in_progress")
        db.add(new_deal)
        db.flush()
        resolved_deal_id = new_deal.id
        log.info(f"\U0001f4c4 DEAL stub created id={new_deal.id} (auto-detect)")

    # Buffer to a local temp file so the Supabase client can stream it as a
    # file-like object, then remove it. We dual-populate `file_path` with the
    # storage key for back-compat; `file_path` will be dropped in a later task.
    remote_key = f"{call_id}/{file.filename}"

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        upload_audio(tmp_path, remote_key, content_type=content_type)
        log.info(f"\u2601\ufe0f  STORAGE uploaded key={remote_key}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    call = Call(
        id=call_id,
        filename=file.filename,
        file_path=remote_key,
        audio_storage_key=remote_key,
        file_size=len(content),
        script_id=script_id,
        status="pending_stream" if stream else "processing",
        deal_id=resolved_deal_id,
        call_type=call_type,
        customer_name=customer_name,
    )
    db.add(call)
    # Audit row written inside the same transaction so the business write +
    # tamper-evident chain extension are atomic. Frontend sends `x-user-id`
    # on authenticated uploads; absent for anonymous/legacy clients.
    record_audit(
        db,
        action="call.upload",
        entity_type="call",
        entity_id=str(call.id),
        payload={
            "filename": file.filename,
            "size": len(content),
            "call_type": call_type,
            "deal_id": str(resolved_deal_id) if resolved_deal_id else None,
        },
        actor_id=request.headers.get("x-user-id"),
    )
    db.commit()

    # Re-query with joinedload so checkpoints relationship is loaded
    call = db.query(Call).options(joinedload(Call.checkpoints)).filter_by(id=call_id).first()

    # When the Inngest pipeline is on, the durable workflow is the SOLE
    # writer for this call — skip the legacy asyncio task to avoid double
    # writes (D02 idempotency contract). When the flag is off, fall through
    # to the legacy task as before.
    if not stream and not settings.use_inngest_pipeline:
        asyncio.create_task(_process_in_background(call_id, remote_key, script_id))

    if settings.use_inngest_pipeline:
        try:
            import inngest as _inngest
            from app.inngest_client import inngest_client
            from app.workflows.events import CALL_UPLOADED

            await inngest_client.send(
                _inngest.Event(
                    name=CALL_UPLOADED,
                    data={
                        "call_id": str(call.id),
                        "audio_path": call.file_path,
                        "customer_name": call.customer_name,
                        "deal_id": deal_id,
                        "call_type": call_type,
                        "script_id": call.script_id,
                    },
                )
            )
            log.info(f"INNGEST_EVENT_SENT name={CALL_UPLOADED} call_id={call.id}")
        except Exception as e:
            log.warning(f"INNGEST_EVENT_FAILED call_id={call.id} err={e}")

    return call


async def _process_in_background(call_id: str, file_path: str, script_id: str | None = None):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        await process_call(call_id, file_path, db, script_id)
        # B-2: emit call/finalized so L6 RAG ingest fires on the non-Inngest
        # path too. The Inngest workflow has its own emit at the end of
        # process_call_fn; this catches the legacy direct-pipeline path
        # (settings.use_inngest_pipeline=False).
        try:
            import inngest as _inngest
            from app.inngest_client import inngest_client
            await inngest_client.send(
                _inngest.Event(name="call/finalized", data={"call_id": call_id})
            )
            log.info(f"INNGEST_EVENT_SENT name=call/finalized call_id={call_id} (non-Inngest path)")
        except Exception as e:
            log.warning(f"INNGEST_EVENT_FAILED name=call/finalized call_id={call_id} err={e!r}")
    finally:
        db.close()


# Same-deal upload mode helper. UI calls this once before firing N parallel
# /api/calls/upload requests with the returned deal_id, so all N audio files
# attach to one deal record. Pipeline _step_detect_metadata is race-safe
# (only-fill-if-blank) so the first call to finish wins.
#
# Note: status="pending_audio" is intentional and distinct from the
# "(auto-detect pending …)" stub created in /api/calls/upload — that path
# is per-call auto-detect; this one is the "I'm about to upload N files,
# group them" handshake. CustomerDeal.status has no CHECK constraint
# (only loa_status / lifecycle_status do, per migration c3d4e5f6a7b8).
@router.post("/api/deals/stub")
async def post_deal_stub(request: Request, db: Session = Depends(get_db)):
    deal = CustomerDeal(
        customer_name="(pending audio upload)",
        status="pending_audio",
    )
    db.add(deal)
    # Flush so the server-generated UUID is available for the audit row.
    # The stub create + audit row commit atomically to keep the chain
    # consistent with the explicit POST /api/deals path.
    db.flush()
    record_audit(
        db,
        action="deal.create",
        entity_type="deal",
        entity_id=str(deal.id),
        payload={"status": deal.status, "stub": True},
        actor_id=request.headers.get("x-user-id"),
    )
    db.commit()
    log.info(f"\U0001f4c4 DEAL stub created id={deal.id} (same-deal upload mode)")
    return {"deal_id": str(deal.id)}


@router.get("/api/calls/export.csv")
def export_calls_csv(
    status: str | None = None,
    compliance_status: str | None = None,
    review_status: str | None = None,
    db: Session = Depends(get_db),
):
    """Stream every call matching the filters as CSV.

    Called from the /calls toolbar "Export CSV" button. Filters are optional
    and unset → whole dataset. Columns match what ops wants to pivot on; we
    skip the transcript/word_data/raw metadata to keep the file spreadsheet-
    friendly.
    """
    import csv
    import io

    q = db.query(
        Call.call_ref, Call.filename, Call.detected_supplier,
        Call.agent_name, Call.customer_name, Call.score,
        Call.compliance_status, Call.review_status, Call.status,
        Call.reviewed_by, Call.reviewed_at, Call.created_at, Call.completed_at,
    )
    if status:
        q = q.filter(Call.status == status)
    if compliance_status:
        q = q.filter(Call.compliance_status == compliance_status)
    if review_status:
        q = q.filter(Call.review_status == review_status)
    rows = q.order_by(Call.created_at.desc()).all()

    def _iter():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "call_ref", "filename", "supplier", "agent", "customer",
            "score", "compliance_status", "review_status", "processing_status",
            "reviewer", "reviewed_at", "created_at", "completed_at",
        ])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()
        for r in rows:
            writer.writerow([
                r.call_ref or "",
                r.filename,
                r.detected_supplier or "",
                r.agent_name or "",
                r.customer_name or "",
                r.score or "",
                r.compliance_status or "",
                r.review_status or "",
                r.status or "",
                r.reviewed_by or "",
                r.reviewed_at.isoformat() if r.reviewed_at else "",
                r.created_at.isoformat() if r.created_at else "",
                r.completed_at.isoformat() if r.completed_at else "",
            ])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()

    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        _iter(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="calls-{stamp}.csv"'},
    )


@router.get("/api/calls", response_model=CallListResponse)
def list_calls(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    # Summary query: skip large TEXT/JSONB columns (transcript, word_data,
    # checkpoint_results, draft_snapshot, etc.) so the response stays small
    # enough to finish inside Supabase's statement_timeout.
    total = db.query(func.count(Call.id)).scalar()
    rows = (
        db.query(
            Call.id, Call.filename, Call.file_size, Call.duration_seconds,
            Call.status, Call.compliant, Call.agent_name, Call.customer_name,
            Call.script_id, Call.score, Call.detected_supplier, Call.rule_id,
            Call.created_at, Call.completed_at, Call.compliance_status,
            Call.review_status, Call.reason,
        )
        .order_by(Call.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    calls = [dict(r._mapping) for r in rows]
    return CallListResponse(calls=calls, total=total)


@router.post("/api/calls/{call_id}/retry", response_model=CallResponse)
async def retry_call(call_id: str, db: Session = Depends(get_db)):
    call = (
        db.query(Call)
        .options(joinedload(Call.checkpoints))
        .filter_by(id=call_id)
        .first()
    )
    if not call:
        raise HTTPException(404, "Call not found")
    # Historically this 400'd any call with status="processing". That
    # blocked the obvious recovery when a backend restart (deploy, crash)
    # killed the in-flight asyncio.create_task pipeline — the call would
    # sit in "processing" forever with no way to kick it. Allow retry if
    # the row is older than 5 minutes since created_at; anything fresher
    # is likely a real concurrent run.
    from datetime import datetime, timedelta
    if call.status == "processing" and call.created_at and (datetime.utcnow() - call.created_at) < timedelta(minutes=5):
        raise HTTPException(400, "Call is already processing (try again in a few minutes)")

    # Delete existing checkpoint records
    db.query(CallCheckpoint).filter_by(call_id=call_id).delete()

    # Reset call state, preserving transcript and file info
    call.status = "processing"
    call.compliant = None
    call.reason = None
    call.checkpoint_results = None
    call.score = None
    call.completed_at = None
    db.commit()

    # Kick off processing. Mirrors the upload route's dispatch logic so the
    # durable Inngest workflow gets the retry event when the flag is on,
    # otherwise fall through to the legacy asyncio task path.
    if settings.use_inngest_pipeline:
        try:
            import inngest as _inngest
            from app.inngest_client import inngest_client
            from app.workflows.events import CALL_UPLOADED

            await inngest_client.send(
                _inngest.Event(
                    name=CALL_UPLOADED,
                    data={
                        "call_id": str(call.id),
                        "audio_path": call.file_path,
                        "customer_name": call.customer_name,
                        "deal_id": str(call.deal_id) if call.deal_id else None,
                        "call_type": call.call_type,
                        "script_id": call.script_id,
                    },
                )
            )
            log.info(f"INNGEST_EVENT_SENT name={CALL_UPLOADED} call_id={call.id} (retry)")
        except Exception as e:
            log.warning(f"INNGEST_EVENT_FAILED call_id={call.id} (retry) err={e}")
    else:
        asyncio.create_task(_process_in_background(call_id, call.file_path, call.script_id))

    # Re-query so checkpoints relationship reflects the reset state
    call = db.query(Call).options(joinedload(Call.checkpoints)).filter_by(id=call_id).first()
    return call


@router.post("/api/calls/{call_id}/checkpoint/{cp_index}/retry")
async def retry_checkpoint(call_id: str, cp_index: int, db: Session = Depends(get_db)):
    """Re-analyze a single checkpoint for a call."""
    from app.checkpoint_analyzer import analyze_single_checkpoint

    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(404, "Call not found")
    if not call.transcript:
        raise HTTPException(400, "Call has no transcript")
    if not call.checkpoint_results:
        raise HTTPException(400, "Call has no checkpoint results")

    # Get the script's checkpoint definition
    script = db.query(Script).filter_by(id=call.script_id).first()
    if not script:
        raise HTTPException(400, "No script associated with this call")

    script_checkpoints = json.loads(script.checkpoints)
    if cp_index < 0 or cp_index >= len(script_checkpoints):
        raise HTTPException(400, f"Invalid checkpoint index {cp_index}")

    checkpoint_def = script_checkpoints[cp_index]
    log.info(f"🔄 RETRY checkpoint #{cp_index} \"{checkpoint_def.get('name', '')}\" for call_id={call_id}")

    # Re-analyze just this one checkpoint
    result = await analyze_single_checkpoint(call.transcript, checkpoint_def, script.mode)

    # Update the checkpoint_results JSON array
    results = json.loads(call.checkpoint_results)
    if cp_index < len(results):
        results[cp_index] = result
    call.checkpoint_results = json.dumps(results)

    # Recalculate score — exclude error checkpoints from denominator (matches analyze_all_checkpoints)
    non_error = [r for r in results if r["status"] != "error"]
    passed = sum(1 for r in non_error if r["status"] == "pass")
    failed = sum(1 for r in non_error if r["status"] in ("fail", "unverified"))
    partial = sum(1 for r in non_error if r["status"] == "partial")
    total = len(non_error)
    call.score = f"{passed}/{total}" if total > 0 else "0/0"
    call.compliant = total > 0 and failed == 0 and partial == 0
    call.reason = f"Score: {call.score}. " + (
        "All checkpoints passed." if call.compliant
        else f"{failed} checkpoint(s) missed, {partial} partial."
    )

    log.info(f"✅ RETRY checkpoint done → {result['status']}, new score={call.score}")
    db.commit()

    return {"status": "ok", "checkpoint": result, "score": call.score, "compliant": call.compliant}


@router.put("/api/calls/{call_id}/checkpoint/{cp_index}/review")
async def review_checkpoint_verdict(
    call_id: str,
    cp_index: int,
    verdict: str,
    notes: str = "",
    db: Session = Depends(get_db),
):
    """Human reviewer confirms or overrides a checkpoint verdict.

    Query params: verdict=pass|fail, notes=optional
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(404, "Call not found")
    if not call.checkpoint_results:
        raise HTTPException(400, "Call has no checkpoint results")

    results = json.loads(call.checkpoint_results)
    if cp_index < 0 or cp_index >= len(results):
        raise HTTPException(400, f"Invalid checkpoint index {cp_index}")

    if verdict not in ("pass", "fail"):
        raise HTTPException(400, "verdict must be 'pass' or 'fail'")

    # Update the checkpoint result
    results[cp_index]["reviewer_verdict"] = verdict
    results[cp_index]["reviewer_notes"] = notes
    results[cp_index]["needs_review"] = False
    call.checkpoint_results = json.dumps(results)

    # Update the CallCheckpoint row if it exists
    checkpoints = db.query(CallCheckpoint).filter_by(call_id=call_id).all()
    if cp_index < len(checkpoints):
        cp_row = checkpoints[cp_index]
        cp_row.reviewer_verdict = verdict
        cp_row.reviewer_notes = notes
        cp_row.needs_review = False

    # Recalculate score using reviewer verdicts where available
    non_error = [r for r in results if r["status"] != "error"]
    passed = 0
    failed = 0
    partial = 0
    for r in non_error:
        effective_status = r.get("reviewer_verdict") or r["status"]
        if effective_status == "pass":
            passed += 1
        elif effective_status in ("fail", "unverified"):
            failed += 1
        elif effective_status == "partial":
            partial += 1
    total = len(non_error)
    needs_review_remaining = sum(1 for r in results if r.get("needs_review"))

    call.score = f"{passed}/{total}" if total > 0 else "0/0"
    call.compliant = total > 0 and failed == 0 and partial == 0
    call.reason = f"Score: {call.score}. " + (
        "All checkpoints passed." if call.compliant
        else f"{failed} checkpoint(s) missed, {partial} partial."
    )
    if needs_review_remaining > 0:
        call.reason += f" {needs_review_remaining} pending review."

    log.info(f"📝 REVIEW checkpoint #{cp_index} → verdict={verdict}, new score={call.score}")
    db.commit()

    # Trigger anonymized feedback logging — agent's verdict was whatever it had
    # before the reviewer changed it. If human == agent, feedback.py no-ops.
    try:
        cp = results[cp_index]
        agent_status = cp.get("status", "fail")
        agent_simple = "pass" if agent_status == "pass" else "fail"
        if agent_simple != verdict:
            await abstract_and_store_review(
                db=db,
                supplier=call.detected_supplier or "Unknown",
                checkpoint_name=cp.get("name", f"Checkpoint {cp_index}"),
                transcript_excerpt=cp.get("evidence", "")[:2000],
                agent_verdict=agent_simple,
                human_verdict=verdict,
                reviewer_notes=notes,
            )
    except Exception as e:
        log.warning(f"📚 feedback processing failed (non-fatal): {e}")

    return {
        "status": "ok",
        "checkpoint": results[cp_index],
        "score": call.score,
        "compliant": call.compliant,
        "needs_review_remaining": needs_review_remaining,
    }


# --- Model / Provider Settings ---

PROVIDERS = {
    "openrouter": {"model_attr": "openrouter_model", "key_attr": "openrouter_api_key", "label": "OpenRouter"},
    "gemini": {"model_attr": "gemini_model", "key_attr": "gemini_api_key", "label": "Google Gemini"},
    "anthropic": {"model_attr": "anthropic_model", "key_attr": "anthropic_api_key", "label": "Anthropic"},
    "openai": {"model_attr": "openai_model", "key_attr": "openai_api_key", "label": "OpenAI"},
}


@router.get("/api/settings/model")
def get_model_settings():
    providers = {}
    for pid, meta in PROVIDERS.items():
        key_val = getattr(settings, meta["key_attr"], "") if meta["key_attr"] else ""
        providers[pid] = {
            "label": meta["label"],
            "model": getattr(settings, meta["model_attr"]),
            "api_key_masked": _mask_key(key_val) if meta["key_attr"] else "not needed",
            "api_key_set": True if not meta["key_attr"] else bool(key_val),
            "no_key_required": meta["key_attr"] is None,
        }
    return {
        "active_provider": settings.active_provider,
        "providers": providers,
    }


_RUNTIME_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime_settings.json")


def _save_runtime_settings(updates: dict):
    """Persist runtime setting overrides so they survive server restarts."""
    current: dict = {}
    if os.path.exists(_RUNTIME_SETTINGS_FILE):
        try:
            with open(_RUNTIME_SETTINGS_FILE) as f:
                current = json.load(f)
        except Exception:
            current = {}
    current.update(updates)
    with open(_RUNTIME_SETTINGS_FILE, "w") as f:
        json.dump(current, f, indent=2)


def _load_runtime_settings():
    """Apply persisted overrides to settings on startup."""
    if not os.path.exists(_RUNTIME_SETTINGS_FILE):
        return
    try:
        with open(_RUNTIME_SETTINGS_FILE) as f:
            data = json.load(f)
        for k, v in data.items():
            if hasattr(settings, k):
                setattr(settings, k, v)
        log.info(f"\u2699\ufe0f SETTINGS loaded from runtime_settings.json \u2192 provider={settings.active_provider}")
    except Exception as e:
        log.warning(f"\u26a0\ufe0f SETTINGS failed to load runtime overrides: {e}")


# Apply any persisted overrides at module import time
_load_runtime_settings()


@router.put("/api/settings/model")
def update_model_settings(body: dict, _=Depends(_require_admin)):
    updates: dict = {}

    if "active_provider" in body:
        if body["active_provider"] not in PROVIDERS:
            raise HTTPException(400, f"Provider must be one of: {', '.join(PROVIDERS.keys())}")
        settings.active_provider = body["active_provider"]
        updates["active_provider"] = body["active_provider"]

    # Update any provider's model or key
    for pid, meta in PROVIDERS.items():
        model_field = f"{pid}_model"
        key_field = f"{pid}_api_key"
        if model_field in body and body[model_field]:
            setattr(settings, meta["model_attr"], body[model_field])
            updates[meta["model_attr"]] = body[model_field]
        if meta["key_attr"] and key_field in body and body[key_field]:
            setattr(settings, meta["key_attr"], body[key_field])
            updates[meta["key_attr"]] = body[key_field]

    if updates:
        _save_runtime_settings(updates)

    active = settings.active_provider
    active_model = getattr(settings, PROVIDERS[active]["model_attr"])
    log.info(f"⚙️ SETTINGS updated → provider={active}, model={active_model} (persisted: {list(updates.keys())})")
    return {"status": "ok", "active_provider": active, "model": active_model}


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return ""
    return "****" + key[-4:]


# ── Transcription provider toggles ─────────────────────────────────
TRANSCRIPTION_PROVIDERS = {
    "assemblyai":   {"label": "AssemblyAI Universal-3 Pro", "agreement": 87.8, "role": "primary"},
    "groq_whisper": {"label": "Whisper LV3 (Groq)",         "agreement": 86.7, "role": "alternate"},
    "cohere":       {"label": "Cohere Transcribe",          "agreement": 87.4, "role": "alternate"},
    "deepgram":     {"label": "Deepgram Nova-3",            "agreement": 84.3, "role": "alternate"},
    "gemini":       {"label": "Gemini 2.5 Flash",           "agreement": 82.7, "role": "fallback"},
}
DEFAULT_TRANSCRIPTION_ENABLED = ["assemblyai", "groq_whisper", "cohere", "deepgram", "gemini"]


def get_enabled_transcription_providers() -> list[str]:
    """Read current enabled transcription providers from runtime settings,
    falling back to all-on if not yet configured."""
    if not os.path.exists(_RUNTIME_SETTINGS_FILE):
        return DEFAULT_TRANSCRIPTION_ENABLED
    try:
        with open(_RUNTIME_SETTINGS_FILE) as f:
            data = json.load(f)
        enabled = data.get("transcription_enabled")
        if isinstance(enabled, list) and enabled:
            return enabled
    except Exception:
        pass
    return DEFAULT_TRANSCRIPTION_ENABLED


@router.get("/api/settings/transcription")
def get_transcription_settings():
    enabled = set(get_enabled_transcription_providers())
    return {
        "providers": [
            {**info, "id": pid, "enabled": pid in enabled}
            for pid, info in TRANSCRIPTION_PROVIDERS.items()
        ],
    }


@router.put("/api/settings/transcription")
def update_transcription_settings(body: dict, _=Depends(_require_admin)):
    enabled = body.get("enabled")
    if not isinstance(enabled, list):
        raise HTTPException(400, "Body must include 'enabled': list[str]")
    valid = [p for p in enabled if p in TRANSCRIPTION_PROVIDERS]
    # AssemblyAI is required for the karaoke player to work; force-include it.
    if "assemblyai" not in valid:
        valid.insert(0, "assemblyai")
    _save_runtime_settings({"transcription_enabled": valid})
    log.info(f"⚙️ TRANSCRIPTION providers updated → {valid}")
    return {"status": "ok", "enabled": valid}


@router.get("/api/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)):
    total = db.query(func.count(Call.id)).scalar()
    compliant = db.query(func.count(Call.id)).filter(Call.compliant == True).scalar()
    non_compliant = db.query(func.count(Call.id)).filter(Call.compliant == False).scalar()
    processing = db.query(func.count(Call.id)).filter(Call.status == "processing").scalar()

    # Review analytics
    total_checkpoints = db.query(func.count(CallCheckpoint.id)).scalar() or 0
    needs_review = db.query(func.count(CallCheckpoint.id)).filter(
        CallCheckpoint.needs_review == True
    ).scalar() or 0
    reviewed = db.query(func.count(CallCheckpoint.id)).filter(
        CallCheckpoint.reviewer_verdict.isnot(None)
    ).scalar() or 0
    automated = total_checkpoints - needs_review - reviewed
    automated_rate = (automated / total_checkpoints * 100) if total_checkpoints > 0 else 0.0

    rate = (compliant / total * 100) if total > 0 else 0.0

    return StatsResponse(
        total_calls=total,
        compliant_count=compliant,
        non_compliant_count=non_compliant,
        compliance_rate=round(rate, 1),
        processing_count=processing,
        needs_review_count=needs_review,
        reviewed_count=reviewed,
        automated_rate=round(automated_rate, 1),
    )


# --- SSE Streaming Endpoint ---

@router.get("/api/calls/{call_id}/stream")
async def stream_call_processing(call_id: str):
    """SSE endpoint for real-time call processing with per-checkpoint streaming."""

    async def event_generator():
        db = SessionLocal()
        local_audio: str | None = None
        try:
            call = db.query(Call).filter_by(id=call_id).first()
            if not call or not (call.audio_storage_key or call.file_path):
                yield _sse("error", {"message": "Call not found or no audio file"})
                return

            # Step 1: Transcribe — download from Supabase Storage first if this
            # is a Storage-backed call; fall back to on-disk path for pre-Storage
            # uploads. Cleanup happens in the outer finally (also covers SSE
            # client disconnect, which cancels this generator).
            if call.audio_storage_key:
                ext = os.path.splitext(call.filename or "")[1] or ".mp3"
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                    local_audio = tmp.name
                download_audio(call.audio_storage_key, local_audio)
                audio_path = local_audio
                log.info(f"\u2601\ufe0f  STORAGE download key={call.audio_storage_key} \u2192 {local_audio}")
            else:
                audio_path = call.file_path

            stream_start = time.time()
            log.info(f"\U0001f399\ufe0f TRANSCRIBE start call_id={call_id}")
            yield _sse("status", {"step": "transcribing", "message": "Transcribing with AssemblyAI Universal-3..."})

            # AssemblyAI (primary — accurate word timings + diarization).
            # Falls back to Deepgram if AAI errors (rare).
            from app.assemblyai_transcription import transcribe_audio_assemblyai
            try:
                aai = await transcribe_audio_assemblyai(audio_path)
            except Exception as e:
                log.warning(f"\u26a0\ufe0f ASSEMBLYAI failed, falling back to Deepgram: {e}")
                aai = None

            if aai:
                transcript = aai["transcript"]
                call.transcript = transcript
                call.assemblyai_transcript = transcript
                call.word_data = json.dumps(aai["words"])
                source = "assemblyai"
            else:
                transcript = await transcribe_audio(audio_path)
                call.transcript = transcript
                source = "deepgram"

            line_count = transcript.count("\n") + 1
            log.info(f"\U0001f399\ufe0f TRANSCRIBE done call_id={call_id} \u2192 {source}, {line_count} lines, {time.time()-stream_start:.1f}s")
            yield _sse("transcription_done", {"lines": line_count, "preview": transcript[:300]})

            db.commit()

            # Step 2: Detect supplier and load script
            log.info(f"\U0001f50d DETECT start call_id={call_id}")
            yield _sse("status", {"step": "detecting", "message": "Detecting supplier..."})

            script = None
            if call.script_id:
                script = db.query(Script).filter_by(id=call.script_id, active=True).first()

            if not script:
                detected = await detect_supplier(transcript)
                log.info(f"\U0001f50d DETECT done call_id={call_id} \u2192 supplier=\"{detected}\", {time.time()-stream_start:.1f}s")
                call.detected_supplier = detected
                db.commit()

                # Exact ilike match
                safe_detected = _escape_ilike(detected)
                script = db.query(Script).filter(
                    Script.supplier_name.ilike(f"%{safe_detected}%", escape="\\"),
                    Script.active == True,
                ).first()

                # Fuzzy keyword fallback
                if not script:
                    keywords = [w for w in detected.lower().split() if len(w) > 2]
                    all_scripts = db.query(Script).filter_by(active=True).all()
                    for s in all_scripts:
                        name = s.supplier_name.lower()
                        if any(kw in name for kw in keywords):
                            script = s
                            break

            if not script:
                # V1 fallback — stream 3 built-in checkpoints
                log.info(f"\U0001f4cb SCRIPT no match call_id={call_id} \u2192 falling back to V1")
                yield _sse("status", {"step": "v1_fallback", "message": "No script found. Running third-party disclosure check..."})

                v1_checkpoints = [
                    {"section": 1, "name": "Agent states company is a third party", "strictness": "mandatory"},
                    {"section": 2, "name": "Agent states company is NOT an energy supplier", "strictness": "mandatory"},
                    {"section": 3, "name": "Agent identifies as independent broker/intermediary", "strictness": "mandatory"},
                ]

                yield _sse("supplier_detected", {
                    "supplier": "Unknown",
                    "script_name": "Third-Party Disclosure (default)",
                    "checkpoint_count": len(v1_checkpoints),
                    "checkpoints": v1_checkpoints,
                })

                prompt = V1_PROMPT.replace("{transcript}", transcript)
                content = await _call_llm(prompt)
                parsed = json.loads(content)
                v1_cps = parsed.get("checkpoints", [])

                checkpoint_results = []
                for i, cp in enumerate(v1_cps):
                    yield _sse("checkpoint_start", {"section": i + 1, "name": cp["rule"], "index": i})

                    match = fuzzy_match(transcript, cp.get("excerpt", ""))
                    status = "pass" if cp["passed"] else "fail"
                    if cp["passed"] and not match["verified"]:
                        status = "unverified"

                    result = {
                        "section": i + 1,
                        "name": cp["rule"],
                        "status": status,
                        "evidence": cp.get("excerpt", ""),
                        "notes": None if cp["passed"] else "Checkpoint not met",
                        "verified": match["verified"],
                        "similarity": match["similarity"],
                    }
                    checkpoint_results.append(result)

                    status_emoji = {"pass": "\u2705", "fail": "\u274c", "partial": "\u26a0\ufe0f", "unverified": "\u2753"}.get(status, "\u2753")
                    log.info(f"{status_emoji} CHECKPOINT {i+1}/{len(v1_cps)} \"{cp['rule']}\" \u2192 {status}")
                    yield _sse("checkpoint_done", result)

                    db.add(CallCheckpoint(
                        call_id=call_id,
                        rule_text=cp["rule"],
                        passed=status == "pass",
                        excerpt=cp.get("excerpt"),
                    ))

                passed = sum(1 for r in checkpoint_results if r["status"] == "pass")
                failed = sum(1 for r in checkpoint_results if r["status"] in ("fail", "unverified"))
                total = len(checkpoint_results)
                score = f"{passed}/{total}" if total > 0 else None
                compliant = total > 0 and failed == 0

                call.compliant = compliant
                call.reason = f"Score: {score}. {'All checkpoints passed.' if compliant else f'{failed} missed.'}" if score else parsed.get("reason", "")
                call.excerpt = parsed.get("excerpt")
                call.agent_name = parsed.get("agent_name", "Unknown")
                call.customer_name = parsed.get("customer_name", "Unknown")
                call.checkpoint_results = json.dumps(checkpoint_results) if checkpoint_results else None
                call.score = score
                call.status = "completed"
                call.completed_at = datetime.utcnow()
                db.commit()

                log.info(f"\U0001f4ca COMPLETE call_id={call_id} \u2192 score={score}, compliant={compliant}, {time.time()-stream_start:.1f}s total")
                log.info(f"\U0001f4be SAVED call_id={call_id} \u2192 {total} checkpoint rows written")

                yield _sse("complete", {
                    "score": score, "compliant": compliant, "passed": passed,
                    "partial": 0, "failed": failed, "total": total,
                    "agent_name": call.agent_name, "customer_name": call.customer_name,
                })
                return

            # Script found — V2 per-checkpoint streaming
            checkpoints = json.loads(script.checkpoints) if isinstance(script.checkpoints, str) else script.checkpoints
            log.info(f"\U0001f4cb SCRIPT matched call_id={call_id} \u2192 \"{script.script_name}\" ({len(checkpoints)} checkpoints)")

            yield _sse("supplier_detected", {
                "supplier": script.supplier_name,
                "script_name": script.script_name,
                "checkpoint_count": len(checkpoints),
                "checkpoints": [{"section": cp["section"], "name": cp["name"], "strictness": cp.get("strictness", "mandatory")} for cp in checkpoints],
            })

            call.script_id = script.id
            call.detected_supplier = script.supplier_name
            db.commit()

            # Step 3: Emit all checkpoint_start events, then run in parallel
            for i, cp in enumerate(checkpoints):
                yield _sse("checkpoint_start", {"section": cp["section"], "name": cp["name"], "index": i})

            # Run all checkpoints in parallel via analyze_all_checkpoints
            analysis = await analyze_all_checkpoints(transcript, checkpoints, script.mode, supplier=script.supplier_name)
            results = analysis["results"]
            agent_name = analysis["agent_name"]
            customer_name = analysis["customer_name"]
            summary = analysis["summary"]

            # Emit checkpoint_done for each result
            for idx, result in enumerate(results):
                status_emoji = {
                    "pass": "\u2705", "fail": "\u274c", "partial": "\u26a0\ufe0f",
                    "error": "\U0001f4a5", "unverified": "\u2753",
                }.get(result["status"], "\u2753")
                log.info(f"{status_emoji} CHECKPOINT {idx+1}/{len(results)} \"{result['name']}\" \u2192 {result['status']}")
                yield _sse("checkpoint_done", result)
                # W4.4 + W4.7 — also persist the AI-suggested category /
                # remediation / line citation so the rejections auto-create
                # path can prefer the AI's bucket over the keyword heuristic.
                db.add(CallCheckpoint(
                    call_id=call_id,
                    rule_text=result["name"],
                    passed=result["status"] == "pass",
                    excerpt=result.get("evidence"),
                    line_number=result.get("script_line_number"),
                    ai_category=result.get("suggested_category"),
                    ai_fix_required=result.get("suggested_fix_required"),
                    ai_category_confidence=result.get("category_confidence"),
                    # Sprint A1 — AI-populated rejection narrative.
                    ai_rejection_reason=result.get("ai_rejection_reason"),
                    ai_narrative_notes=result.get("ai_narrative_notes"),
                ))

            # Step 4: Save results
            score = summary["score"]
            compliant = summary["compliant"]
            passed = summary["passed"]
            partial = summary["partial"]
            failed = summary["failed"]
            error_count = summary["error"]

            call.agent_name = agent_name
            call.customer_name = customer_name
            call.checkpoint_results = json.dumps(results)
            call.score = score
            call.compliant = compliant
            reason = f"Score: {score}. {'All checkpoints passed.' if compliant else f'{failed} missed, {partial} partial.'}"
            if error_count > 0:
                reason += f" {error_count} checkpoint(s) had errors."
            call.reason = reason
            call.status = "completed"
            call.completed_at = datetime.utcnow()
            db.commit()

            log.info(f"\U0001f4ca COMPLETE call_id={call_id} \u2192 score={score}, compliant={compliant}, {time.time()-stream_start:.1f}s total")
            log.info(f"\U0001f4be SAVED call_id={call_id} \u2192 {summary['total']} checkpoint rows written")

            yield _sse("complete", {
                "score": score, "compliant": compliant, "passed": passed,
                "partial": partial, "failed": failed, "total": summary["total"],
                "agent_name": agent_name, "customer_name": customer_name,
            })

        except Exception as err:
            log.error(f"\U0001f4a5 ERROR call_id={call_id} \u2192 {str(err)}")
            yield _sse("error", {"message": str(err)})
            call = db.query(Call).filter_by(id=call_id).first()
            if call:
                call.status = "failed"
                call.reason = f"Processing error: {str(err)}"
                db.commit()
        finally:
            # Clean up the temp-downloaded audio (if any). Runs on success,
            # exception, AND SSE client disconnect (FastAPI cancels the
            # generator which still executes finally).
            if local_audio and os.path.exists(local_audio):
                try:
                    os.unlink(local_audio)
                except OSError:
                    pass
            db.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _sse(event: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# --- Cleanup stuck calls ---

@router.post("/api/calls/cleanup")
def cleanup_stuck_calls(db: Session = Depends(get_db)):
    """Mark stuck pending_stream / pending / processing calls as failed."""
    stuck = db.query(Call).filter(Call.status.in_(["pending_stream", "pending", "processing"])).all()
    count = 0
    for call in stuck:
        call.status = "failed"
        call.reason = "Processing was interrupted — call was stuck in pending state"
        count += 1
    db.commit()
    return {"cleaned": count}


# --- Serve Call Audio File ---

@router.get("/api/calls/{call_id}/audio-url")
def get_audio_url(call_id: str, db: Session = Depends(get_db)):
    """Return a short-lived signed URL for the call's audio in Supabase Storage.

    Frontend should call this right before playback; URL TTL is 1 hour.
    404 if the call doesn't exist or has no storage key (pre-Storage uploads).
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call or not call.audio_storage_key:
        raise HTTPException(404, "Audio not found in storage")
    return {"url": signed_url(call.audio_storage_key, expires_in=3600)}


@router.get("/api/calls/{call_id}/audio")
async def get_call_audio(call_id: str, db: Session = Depends(get_db)):
    """Serve audio for a call — redirect to Supabase signed URL if the file
    lives in Storage, else fall back to the on-disk path for legacy calls."""
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(404, "Call not found")

    # Storage-backed: redirect to a short-lived signed URL so the browser
    # streams bytes directly from Supabase.
    if call.audio_storage_key:
        from fastapi.responses import RedirectResponse
        from app.storage import signed_url
        url = signed_url(call.audio_storage_key, expires_in=3600)
        if url:
            return RedirectResponse(url=url, status_code=302)

    if not call.file_path:
        raise HTTPException(404, "Audio file not found")

    file_path = call.file_path
    if not os.path.isabs(file_path):
        file_path = os.path.join(os.getcwd(), file_path)

    if not os.path.exists(file_path):
        raise HTTPException(404, "Audio file not found on disk")

    ext = os.path.splitext(file_path)[1].lower()
    media_types = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav",
        ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".flac": "audio/flac",
    }

    return FileResponse(
        file_path,
        media_type=media_types.get(ext, "audio/mpeg"),
        headers={"Accept-Ranges": "bytes", "Content-Disposition": "inline"},
    )


# --- Single Call Detail (MUST be after /stream, /retry, and /audio to avoid path conflict) ---

@router.get("/api/calls/{call_id}/words")
def get_call_words(call_id: str, db: Session = Depends(get_db)):
    """Return per-word timestamp and confidence data for transcript player."""
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(404, "Call not found")
    if not call.word_data:
        raise HTTPException(404, "No word data available for this call")

    words = json.loads(call.word_data)
    return {
        "call_id": call_id,
        "word_count": len(words),
        "duration": words[-1]["end"] if words else 0,
        "words": words,
    }


_V1_TPI_FALLBACK_CHECKPOINTS = [
    {
        "section": 1,
        "name": "Third-party disclosure",
        "required": "The agent explicitly states the company is a third party (e.g. \"I'm calling from <Broker>, which is a third-party intermediary\")",
        "key_phrases": ["third party", "third-party", "intermediary", "broker"],
        "customer_response_required": False,
        "strictness": "mandatory",
    },
    {
        "section": 2,
        "name": "Not the energy supplier",
        "required": "The agent explicitly states the company is NOT an energy supplier (e.g. \"We are not a supplier ourselves\")",
        "key_phrases": ["not a supplier", "not the supplier", "not an energy supplier"],
        "customer_response_required": False,
        "strictness": "mandatory",
    },
    {
        "section": 3,
        "name": "Independent broker / intermediary",
        "required": "The agent identifies themselves/their company as an independent broker or intermediary acting on behalf of the customer",
        "key_phrases": ["independent broker", "on your behalf", "act for you"],
        "customer_response_required": False,
        "strictness": "mandatory",
    },
]


@router.get("/api/calls/{call_id}/script-checkpoints")
def get_call_script_checkpoints(call_id: str, db: Session = Depends(get_db)):
    """Return the script's checkpoint definitions matched to this call so the
    UI can show Expected vs Actual ('what the agent should have said').

    When the matched script has empty `checkpoints` (seed-only metadata), the
    pipeline falls through to the V1 third-party-disclosure analyzer — so we
    return those V1 rules here too. This stops the UI from showing
    "Script text unavailable" when the AI did, in fact, evaluate the call
    against a known rule set.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(404, "Call not found")
    if not call.script_id:
        return {"call_id": call_id, "checkpoints": _V1_TPI_FALLBACK_CHECKPOINTS}

    script = db.query(Script).filter_by(id=call.script_id).first()

    defs: list = []
    if script and script.checkpoints:
        try:
            defs = json.loads(script.checkpoints) or []
        except Exception:
            defs = []

    if not defs:
        # Match what the pipeline actually did at line 793-802 of pipeline.py:
        # empty script.checkpoints → V1 TPI analyzer → 3 universal rules.
        return {
            "call_id": call_id,
            "script_name": (script.script_name if script else None),
            "supplier": (script.supplier_name if script else None),
            "mode": (script.mode if script else "v1_fallback"),
            "checkpoints": _V1_TPI_FALLBACK_CHECKPOINTS,
        }

    return {
        "call_id": call_id,
        "script_name": script.script_name,
        "supplier": script.supplier_name,
        "mode": script.mode,
        "checkpoints": defs,
    }


@router.get("/api/calls/{call_id}", response_model=CallResponse)
def get_call(call_id: str, db: Session = Depends(get_db)):
    # selectinload, not joinedload: Call rows are huge (transcripts + word_data
    # ~200KB each), and joinedload's cartesian join duplicates the Call row once
    # per checkpoint — 24× blowup, ~5MB over the Supabase pooler, ~100s per request.
    # selectinload issues one query for Call and one for its checkpoints → ~2s total.
    call = (
        db.query(Call)
        .options(selectinload(Call.checkpoints))
        .filter_by(id=call_id)
        .first()
    )
    if not call:
        raise HTTPException(404, "Call not found")
    return call


@router.post("/api/calls/{call_id}/reanalyze", status_code=202)
async def reanalyze_call(
    call_id: str,
    db: Session = Depends(get_db),
    actor_id: str | None = None,
):
    """Replay the analyze->score->finalize sub-pipeline against the stored
    transcript. Returns 202 with a fresh run_id; client polls the call to
    see the new verdict."""
    return await _reanalyze_call(call_id, db, actor_id=actor_id)


@router.post("/api/admin/quality-resolve", status_code=200)
async def admin_quality_resolve(db: Session = Depends(get_db)):
    """Run the Quality AI Agent across all completed calls and apply its
    canonical-identity verdict — merges duplicate Church/X customers,
    fixes agent==customer mix-ups, fills missing suppliers via cross-call
    inference. Idempotent: safe to run multiple times.

    Returns a list of changes applied so the operator can audit.
    """
    from app.quality_agent import resolve_identity
    from app.intake.upsert import _slugify as slugify
    from app.models import Customer, CustomerDeal as _Deal

    completed = (
        db.query(Call)
        .filter(Call.status == "completed", Call.transcript.isnot(None))
        .order_by(Call.created_at.asc())
        .all()
    )
    if not completed:
        return {"resolved": 0, "changes": []}

    # Bucket calls by overlapping human customer name OR business name.
    # Each bucket gets ONE Quality Agent call; the agent then tells us
    # whether the bucket should merge (yes for sibling calls of the
    # same customer; no for accidental name collisions).
    buckets: list[list[Call]] = []
    for c in completed:
        placed = False
        h = (c.customer_name or "").strip().lower()
        for b in buckets:
            for other in b:
                oh = (other.customer_name or "").strip().lower()
                if h and oh and (h in oh or oh in h or _bucket_token_overlap(h, oh)):
                    b.append(c)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            buckets.append([c])

    changes: list[dict] = []
    for bucket in buckets:
        if len(bucket) < 2:
            continue  # singleton — nothing to resolve, current state is fine
        payload = [
            {
                "id": str(c.id),
                "filename": c.filename,
                "detected_supplier": c.detected_supplier,
                "agent_name": c.agent_name,
                "customer_name": c.customer_name,
                "score": c.score,
                "transcript": c.transcript,
            }
            for c in bucket
        ]
        verdict = await resolve_identity(payload)
        if not verdict or verdict.get("confidence", 0) < 0.7:
            continue
        if verdict.get("stitch") != "merge_all":
            continue

        # Pick the most-recent deal as the survivor and re-point sibling
        # calls to it. Apply the canonical customer name + slug.
        survivor_call = max(bucket, key=lambda c: c.created_at or 0)
        survivor_deal = (
            db.query(_Deal).filter_by(id=survivor_call.deal_id).first()
            if survivor_call.deal_id
            else None
        )
        if not survivor_deal:
            continue
        canonical = verdict["canonical_customer_name"] or survivor_deal.customer_name

        for c in bucket:
            if c.id == survivor_call.id:
                continue
            old_deal_id = c.deal_id
            c.deal_id = survivor_deal.id
            other = (
                db.query(Call)
                .filter(Call.deal_id == old_deal_id, Call.id != c.id)
                .count()
            )
            if other == 0 and old_deal_id != survivor_deal.id:
                old_deal = db.query(_Deal).filter_by(id=old_deal_id).first()
                if old_deal:
                    db.delete(old_deal)
            # Quality Agent's call_type wins
            ct = (verdict.get("call_classifications") or {}).get(str(c.id))
            if ct:
                c.call_type = ct
            # Fix agent name when the per-call detect_names was confused
            an = verdict.get("agent_name")
            if an and (not c.agent_name or c.agent_name == c.customer_name):
                c.agent_name = an
        # Survivor too gets the agent + call_type fix
        sct = (verdict.get("call_classifications") or {}).get(str(survivor_call.id))
        if sct:
            survivor_call.call_type = sct
        san = verdict.get("agent_name")
        if san and (not survivor_call.agent_name or survivor_call.agent_name == survivor_call.customer_name):
            survivor_call.agent_name = san

        # Apply canonical business name + supplier on the survivor deal
        if canonical:
            survivor_deal.customer_name = canonical
        sup = verdict.get("supplier")
        if sup and sup != "Unknown" and not survivor_deal.supplier:
            survivor_deal.supplier = sup

        # Re-slug + rename Customer if survivor has one
        if survivor_deal.customer_id and canonical:
            cust = db.query(Customer).filter_by(id=survivor_deal.customer_id).first()
            if cust:
                cust.legal_name = canonical
                base_slug = slugify(canonical) or f"customer-{cust.id[:8]}"
                slug = base_slug
                n = 2
                while db.query(Customer).filter(
                    Customer.slug == slug, Customer.id != cust.id
                ).first():
                    slug = f"{base_slug}-{n}"
                    n += 1
                cust.slug = slug

        changes.append(
            {
                "bucket_size": len(bucket),
                "survivor_call": str(survivor_call.id),
                "survivor_deal": str(survivor_deal.id),
                "canonical_name": canonical,
                "supplier": sup,
                "confidence": verdict.get("confidence"),
                "stitch_reason": verdict.get("stitch_reason"),
            }
        )

    db.commit()
    return {"resolved": len(changes), "changes": changes}


def _bucket_token_overlap(a: str, b: str) -> bool:
    """Same as pipeline._names_overlap but inlined here to avoid an import
    cycle. Returns True when two names share ≥2 tokens of length ≥3."""
    if not a or not b:
        return False
    a_t = [t for t in a.replace(".", " ").split() if len(t) >= 3]
    b_t = [t for t in b.replace(".", " ").split() if len(t) >= 3]
    return len(set(a_t) & set(b_t)) >= 2


@router.delete("/api/calls/{call_id}")
def delete_call(call_id: str, db: Session = Depends(get_db)):
    """Delete a call + its checkpoint rows + its audio file.
    Required so users can re-upload after a failed run (duplicate-filename
    check otherwise blocks re-upload)."""
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(404, "Call not found")

    filename = call.filename
    file_path = call.file_path

    # Drop any CallCheckpoint rows that reference this call
    db.query(CallCheckpoint).filter_by(call_id=call_id).delete()
    db.delete(call)
    db.commit()

    # Best-effort remove the audio file on disk
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError as e:
            log.warning(f"\U0001f5d1\ufe0f DELETE audio file removal failed call_id={call_id}: {e}")

    log.info(f"\U0001f5d1\ufe0f DELETE call_id={call_id} filename=\"{filename}\"")
    return {"status": "ok", "deleted": call_id}


# \u2500\u2500 W1 (v3-watt-coverage): risk_tags toggle \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# Allowed enum (frontend passes one of these per chip click).
_RISK_TAGS_ALLOWED = frozenset({
    "Ombudsman",
    "Mis-selling",
    "Complaint",
    "Cancellation",
    "Vulnerable",
})


@router.patch("/api/calls/{call_id}/risk-tags")
def patch_call_risk_tags(call_id: str, body: dict, db: Session = Depends(get_db)):
    """Update the per-call risk-tag chip set.

    Body shape: ``{"tags": [...]}``. Tags must come from the closed enum
    {Ombudsman, Mis-selling, Complaint, Cancellation, Vulnerable}; unknown
    values trigger a 400. Idempotent \u2014 clients can send any superset/subset
    and the call row mirrors it.
    """
    raw = body.get("tags")
    if not isinstance(raw, list):
        raise HTTPException(400, "tags must be an array")
    cleaned: list[str] = []
    for t in raw:
        if not isinstance(t, str):
            raise HTTPException(400, "tags must be strings")
        if t not in _RISK_TAGS_ALLOWED:
            raise HTTPException(400, f"unknown risk tag: {t!r}")
        if t not in cleaned:
            cleaned.append(t)

    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(404, "Call not found")
    call.risk_tags = cleaned
    db.commit()
    return {"call_id": call_id, "risk_tags": cleaned}


# ── Tracker Task 13: Edit Customer Metadata (reviewer override) ────────────
from app.auth import current_user as _metadata_current_user
from app.schemas import EditCallMetadataRequest
from datetime import date as _date


@router.patch("/api/calls/{call_id}/metadata")
def patch_call_metadata(
    call_id: str,
    payload: EditCallMetadataRequest,
    db: Session = Depends(get_db),
    user=Depends(_metadata_current_user),
):
    """Reviewer override for auto-detected metadata. Updates Call +
    parent CustomerDeal + parent Customer rows in one transaction so
    the tracker row stays consistent across cols A (Customer), B (MPAN),
    C (Live Date), D (Value), E (Supplier), G (Agent)."""
    from app.models import Customer

    call = db.query(Call).filter_by(id=call_id).first()
    if call is None:
        raise HTTPException(404, "Call not found")

    deal = db.query(CustomerDeal).filter_by(id=call.deal_id).first() if call.deal_id else None
    customer = db.query(Customer).filter_by(id=deal.customer_id).first() if deal and deal.customer_id else None

    # Update Call (cols A overlay + G)
    if payload.customer_name is not None:
        call.customer_name = payload.customer_name or None
    if payload.agent_name is not None:
        call.agent_name = payload.agent_name or None

    # Update Deal (cols B, C, D, E)
    if deal is not None:
        if payload.customer_name is not None:
            deal.customer_name = payload.customer_name or None
        if payload.mpan_or_mprn is not None:
            deal.mpan_or_mprn = payload.mpan_or_mprn or None
        if payload.expected_live_date is not None:
            try:
                deal.expected_live_date = _date.fromisoformat(payload.expected_live_date) if payload.expected_live_date else None
            except ValueError:
                raise HTTPException(422, "expected_live_date must be ISO yyyy-mm-dd")
        if payload.deal_value_gbp is not None:
            deal.deal_value_gbp = payload.deal_value_gbp
        if payload.supplier is not None:
            deal.supplier = payload.supplier or None
        if payload.contract_length_months is not None:
            deal.term_months = payload.contract_length_months
        if payload.notes is not None:
            deal.notes = payload.notes or None

    # Update Customer (col A canonical)
    if customer is not None and payload.customer_name is not None:
        customer.legal_name = payload.customer_name or "Unknown"

    # Audit row inside the same transaction — captures which fields the
    # reviewer touched (no values, no PII) so the chain stays minimal but
    # the timeline is reconstructible.
    fields_touched = [
        k for k in (
            "customer_name", "agent_name", "mpan_or_mprn",
            "expected_live_date", "deal_value_gbp", "supplier",
            "contract_length_months", "notes",
        ) if getattr(payload, k, None) is not None
    ]
    record_audit(
        db,
        action="call.edit_metadata",
        entity_type="call",
        entity_id=str(call.id),
        payload={"fields_touched": fields_touched},
        actor_id=user["id"],
    )

    db.commit()
    log.info(f"\U0001f4dd METADATA_EDIT call_id={call_id} actor={user['id']}")

    # Inngest observability — surface reviewer overrides so the dashboard
    # tracks how often auto-detect needs human correction. Fields_touched
    # lets us see which auto-detect paths fail most.
    try:
        from app.workflows.events import CALL_METADATA_EDITED
        from app.workflows.observability import emit_event
        fields_touched = [
            k for k in (
                "customer_name", "agent_name", "mpan_or_mprn",
                "expected_live_date", "deal_value_gbp", "supplier",
                "contract_length_months", "notes",
            ) if getattr(payload, k, None) is not None
        ]
        emit_event(CALL_METADATA_EDITED, {
            "call_id": call_id,
            "actor_id": user["id"],
            "fields_touched": fields_touched,
        })
    except Exception:
        pass

    return {
        "call": {
            "id": call.id,
            "customer_name": call.customer_name,
            "agent_name": call.agent_name,
            "deal_id": str(call.deal_id) if call.deal_id else None,
        },
        "deal": {
            "id": str(deal.id) if deal else None,
            "supplier": deal.supplier if deal else None,
            "mpan_or_mprn": deal.mpan_or_mprn if deal else None,
            "expected_live_date": deal.expected_live_date.isoformat() if deal and deal.expected_live_date else None,
            "deal_value_gbp": float(deal.deal_value_gbp) if deal and deal.deal_value_gbp is not None else None,
        } if deal else None,
        "customer": {
            "id": str(customer.id) if customer else None,
            "legal_name": customer.legal_name if customer else None,
        } if customer else None,
    }
