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

    2026-05-14 audit fix: previously this returned silently when
    ``settings.admin_key`` was empty — turning every guarded endpoint into
    an open mutation surface in any environment that forgot to set the env
    var. Now hard-fails so a deploy misconfiguration is visible immediately
    rather than silently world-readable.
    """
    if not settings.admin_key:
        raise HTTPException(
            503,
            "Admin endpoints are unavailable — ADMIN_KEY env var is not set "
            "on this deployment. Configure it before exposing admin routes.",
        )
    if not secrets.compare_digest(
        x_admin_key.encode("utf-8"),
        settings.admin_key.encode("utf-8"),
    ):
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
    # 2026-05-12 taxonomy rebuild — reviewers no longer pick a call_type
    # at upload. Default is None; AI content_classifier auto-detects
    # segments in _step_classify_content. Accepts the 4 canonical values
    # when explicitly provided via the legacy form-field path.
    call_type: str | None = Form(default=None),
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

    # ── Dedup by SHA-256 content hash ───────────────────────────────────
    # If this exact audio (byte-for-byte) was uploaded before, skip
    # re-processing and return the existing call so the user/UI can
    # navigate to it. Suppliers occasionally re-send the same recording
    # with a different filename — this catches both "same name" and
    # "same content, different name" duplicates.
    import hashlib
    content_hash = hashlib.sha256(content).hexdigest()
    existing_by_hash = (
        db.query(Call)
        .filter_by(file_hash=content_hash)
        .first()
    ) if hasattr(Call, "file_hash") else None
    if existing_by_hash:
        log.info(
            f"\U0001f501 DEDUP upload sha256={content_hash[:12]} "
            f"existing call_id={existing_by_hash.id} filename={existing_by_hash.filename!r}"
        )
        # Return the existing call as a 200 with a `duplicate=true` flag.
        # The frontend can detect this and navigate the user to /calls/{id}
        # instead of the upload-success state.
        return existing_by_hash

    # Filename collision handling: when content is NOT a dup but the
    # filename collides, auto-suffix instead of rejecting (suppliers
    # ship distinct calls with overlapping filenames).
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
        from app.intake.matcher import (
            AUTO_MERGE_THRESHOLD,
            REVIEW_QUEUE_THRESHOLD,
            find_existing_deal,
        )
        from app.intake.upsert import upsert_customer, upsert_deal
        from app.models import Customer

        # 2026-05-15 deal-linker — try multi-tier match BEFORE creating a
        # fresh customer/deal. Hard-key hit (MPAN/MPRN/DocuSign/Companies
        # House/Charity) returns confidence=1.0 and ALWAYS overrides the
        # legacy slug-only upsert. Composite hit at >= AUTO_MERGE silently
        # attaches; [REVIEW_QUEUE, AUTO_MERGE) attaches AND marks for
        # reviewer confirmation in the candidate-merge queue. Below the
        # REVIEW threshold → fall through to the legacy upsert path.
        try:
            matcher_hit = find_existing_deal(
                intake_payload.customer, intake_payload.deal, db
            )
        except Exception as e:
            log.warning(f"matcher exception (ignored): {e}")
            matcher_hit = None

        if matcher_hit is not None and matcher_hit.confidence >= REVIEW_QUEUE_THRESHOLD:
            deal_row = (
                db.query(CustomerDeal)
                .filter(CustomerDeal.id == matcher_hit.deal_id)
                .first()
            )
            if deal_row is not None:
                if hasattr(deal_row, "match_method"):
                    deal_row.match_method = matcher_hit.method
                    deal_row.match_confidence = float(matcher_hit.confidence)
                db.flush()
                resolved_deal_id = deal_row.id
                # Pull canonical name from the linked Customer row when present
                # so legacy list views never disagree with /customers.
                customer_row = (
                    db.query(Customer)
                    .filter(Customer.id == matcher_hit.customer_id)
                    .first()
                    if matcher_hit.customer_id
                    else None
                )
                customer_name = (
                    customer_row.legal_name
                    if customer_row and customer_row.legal_name
                    else (deal_row.customer_name or intake_payload.customer.legal_name)
                )
                band = (
                    "MATCHED"
                    if matcher_hit.confidence >= AUTO_MERGE_THRESHOLD
                    else "REVIEW_QUEUE"
                )
                log.info(
                    f"\U0001f517 {band} deal_id={deal_row.id} "
                    f"method={matcher_hit.method} "
                    f"conf={matcher_hit.confidence:.3f} "
                    f"reason={matcher_hit.reason!r}"
                )

        if resolved_deal_id is None:
            # Legacy path — slug-based upsert. Stamp method=legacy so audit
            # can distinguish "created via matcher" vs "created via slug".
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
            if hasattr(deal_row, "match_method") and deal_row.match_method is None:
                deal_row.match_method = "legacy"
            resolved_deal_id = deal_row.id
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

    # 2026-05-12 taxonomy rebuild: call_type starts NULL and the new
    # content_classifier (pipeline._step_classify_content) emits 1-4
    # CallSegment rows per recording, each graded against its own rubric.
    # The Call.call_type column is back-compat only — segments are the
    # source of truth for grading.
    # Enforce the DB CHECK at the route boundary so a stale client sending
    # a legacy value (e.g. "full" / "closer") gets a clean 422.
    _ALLOWED_CALL_TYPES = {"lead_gen", "pre_sales", "verbal", "loa", None}
    if call_type not in _ALLOWED_CALL_TYPES:
        raise HTTPException(
            422,
            f"invalid call_type {call_type!r} (allowed: lead_gen, pre_sales, "
            "verbal, loa, or omit)",
        )

    call = Call(
        id=call_id,
        filename=file.filename,
        file_path=remote_key,
        audio_storage_key=remote_key,
        file_size=len(content),
        file_hash=content_hash,  # SHA-256 from earlier in this function
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

    # 2026-05-16 — push a "queued" SSE event the moment the row is committed
    # so list pages (queue / tracker / calls) light up within a frame of the
    # POST returning. The frontend useCallEvents("*") subscriber invalidates
    # the calls list query keys on this event.
    try:
        from app import realtime
        realtime.publish(
            call_id,
            "queued",
            {
                "filename": call.filename,
                "deal_id": str(call.deal_id) if call.deal_id else None,
                "customer_name": call.customer_name,
                "status": call.status,
            },
        )
    except Exception as e:  # noqa: BLE001 — realtime is best-effort
        log.warning(f"realtime publish(queued) failed call_id={call_id} err={e}")

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
                        # 2026-05-16 audit P2-8 — send the resolved UUID
                        # (string), not the raw form input which is None
                        # for auto-detect / L7-upsert uploads. Downstream
                        # Inngest steps relying on event.data["deal_id"]
                        # were silently getting None for all such uploads.
                        "deal_id": str(resolved_deal_id) if resolved_deal_id else None,
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
            # call_type was missing from the select so the UI showed every
            # call as "NULL stage" even after the AI classifier + backfill
            # had set it. Surface it here. deal_id is also useful for the
            # /calls list page to deep-link to /deals/{id}.
            Call.call_type, Call.deal_id,
        )
        .order_by(Call.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    calls = [dict(r._mapping) for r in rows]
    return CallListResponse(calls=calls, total=total)


@router.post("/api/calls/{call_id}/retry", response_model=CallResponse)
async def retry_call(
    call_id: str,
    db: Session = Depends(get_db),
    # 2026-05-14 audit fix: re-runs the full AI pipeline; was anonymous.
    user=Depends(current_reviewer),
):
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
async def retry_checkpoint(
    call_id: str,
    cp_index: int,
    db: Session = Depends(get_db),
    _reviewer=Depends(current_reviewer),
):
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

    # 2026-05-14 audit fix: malformed JSON in either column should 400,
    # not 500. Both columns are user-data-shaped JSON we don't fully control.
    try:
        script_checkpoints = json.loads(script.checkpoints or "[]")
    except json.JSONDecodeError:
        raise HTTPException(400, "Script.checkpoints is not valid JSON; cannot retry")
    if cp_index < 0 or cp_index >= len(script_checkpoints):
        raise HTTPException(400, f"Invalid checkpoint index {cp_index}")

    checkpoint_def = script_checkpoints[cp_index]
    log.info(f"🔄 RETRY checkpoint #{cp_index} \"{checkpoint_def.get('name', '')}\" for call_id={call_id}")

    # Re-analyze just this one checkpoint
    result = await analyze_single_checkpoint(call.transcript, checkpoint_def, script.mode)

    # Update the checkpoint_results JSON array
    try:
        results = json.loads(call.checkpoint_results or "[]")
    except json.JSONDecodeError:
        raise HTTPException(400, "Call.checkpoint_results is not valid JSON; cannot retry")
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
    _reviewer=Depends(current_reviewer),
):
    """Human reviewer confirms or overrides a checkpoint verdict.

    Query params: verdict=pass|fail, notes=optional
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(404, "Call not found")
    if not call.checkpoint_results:
        raise HTTPException(400, "Call has no checkpoint results")

    try:
        results = json.loads(call.checkpoint_results)
    except json.JSONDecodeError:
        raise HTTPException(400, "Call.checkpoint_results is not valid JSON")
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
def cleanup_stuck_calls(
    db: Session = Depends(get_db),
    # 2026-05-14 audit fix: bulk status mutation; was anonymous.
    user=Depends(current_reviewer),
):
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
    """Return per-word timestamp and confidence data for transcript player.

    Deepgram emits a numeric ``speaker`` id (0, 1, ...) per word. The
    transcript bubbles on the frontend need a stable AGENT/CUSTOMER label
    instead — we derive that here via the same heuristic
    ``format_diarized_transcript`` uses, then tag each word with a
    ``role`` field. Doing it server-side at read time means legacy calls
    (where ``word_data`` was written before this lived) light up correctly
    without a backfill migration.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(404, "Call not found")
    if not call.word_data:
        raise HTTPException(404, "No word data available for this call")

    try:
        words = json.loads(call.word_data)
    except json.JSONDecodeError:
        raise HTTPException(500, "Call.word_data is corrupt; reprocess required")

    # Tag each word with AGENT / CUSTOMER. Only do work if there's >1
    # speaker — otherwise everything is the agent by default.
    from app.transcription import _detect_agent_speaker

    agent_id: int | None = None
    speaker_ids: set[int] = set()
    for w in words:
        try:
            speaker_ids.add(int(w.get("speaker", 0) or 0))
        except (TypeError, ValueError):
            continue
    if len(speaker_ids) >= 2:
        try:
            agent_id = _detect_agent_speaker(words)
        except Exception:
            agent_id = None

    for w in words:
        try:
            spk = int(w.get("speaker", 0) or 0)
        except (TypeError, ValueError):
            spk = 0
        if agent_id is None:
            # Single-speaker recording (or detector failed) — call it
            # AGENT so the colour is consistent with the broker-side
            # treatment, and the reviewer isn't misled into thinking the
            # customer was the only voice on the line.
            w["role"] = "AGENT"
        else:
            w["role"] = "AGENT" if spk == agent_id else "CUSTOMER"

    return {
        "call_id": call_id,
        "word_count": len(words),
        "duration": words[-1]["end"] if words else 0,
        "words": words,
    }


# IMPORTANT: `name` MUST match exactly what the V1 analyzer persists in
# `Call.checkpoint_results[*].name` so the frontend can pair each script
# definition with its verdict by name. See backend/app/analysis.py:V1_PROMPT.
_V1_TPI_FALLBACK_CHECKPOINTS = [
    {
        "section": 1,
        "name": "The agent explicitly states the company is a third party",
        "required": "The agent explicitly states the company is a third party (e.g. \"I'm calling from <Broker>, which is a third-party intermediary\")",
        "key_phrases": ["third party", "third-party", "intermediary", "broker"],
        "customer_response_required": False,
        "strictness": "mandatory",
    },
    {
        "section": 2,
        "name": "The agent states the company is NOT an energy supplier",
        "required": "The agent explicitly states the company is NOT an energy supplier (e.g. \"We are not a supplier ourselves\")",
        "key_phrases": ["not a supplier", "not the supplier", "not an energy supplier"],
        "customer_response_required": False,
        "strictness": "mandatory",
    },
    {
        "section": 3,
        "name": "The agent identifies themselves/company as an independent broker or intermediary",
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

    2026-05-14 — returns the UNION across all CallSegment scripts so per-segment
    checkpoints from different rubrics (88-rule pre_sales pack + supplier
    verbal script + LOA script) all carry their ``required`` text. Without
    this, segments graded against a rubric different from ``call.script_id``
    rendered as "Script text unavailable" in the checkpoint cards because
    name-match against the single call-level script returned nothing.

    When a script has empty ``checkpoints`` (seed-only metadata), the
    pipeline falls through to the V1 third-party-disclosure analyzer — so
    those V1 rules are appended too. Duplicate ``name`` entries are
    dedupe'd (segment scripts win over the call-level script).
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(404, "Call not found")

    # Build the ordered set of script_ids to fetch: every segment's
    # script_id first (so per-segment rubrics win), then the call-level
    # script_id as a safety net. Falsy / duplicate ids are skipped.
    script_ids: list[str] = []
    seen_ids: set[str] = set()
    for seg in list(getattr(call, "segments", []) or []):
        sid = getattr(seg, "script_id", None)
        if sid and sid not in seen_ids:
            script_ids.append(str(sid))
            seen_ids.add(str(sid))
    if call.script_id and str(call.script_id) not in seen_ids:
        script_ids.append(str(call.script_id))
        seen_ids.add(str(call.script_id))

    if not script_ids:
        # No segments resolved + no call-level script → V1 fallback only.
        return {"call_id": call_id, "checkpoints": _V1_TPI_FALLBACK_CHECKPOINTS}

    rows = (
        db.query(Script).filter(Script.id.in_(script_ids)).all() if script_ids else []
    )
    scripts_by_id = {str(s.id): s for s in rows}

    merged: list = []
    merged_names: set[str] = set()
    primary: Script | None = None
    for sid in script_ids:
        s = scripts_by_id.get(sid)
        if not s:
            continue
        if primary is None:
            primary = s
        try:
            defs = json.loads(s.checkpoints or "[]") or []
        except Exception:
            defs = []
        for d in defs:
            if not isinstance(d, dict):
                continue
            name_key = (d.get("name") or "").strip().lower()
            if not name_key or name_key in merged_names:
                continue
            merged.append(d)
            merged_names.add(name_key)

    if not merged:
        # Every script had empty checkpoints → V1 fallback.
        return {
            "call_id": call_id,
            "script_name": (primary.script_name if primary else None),
            "supplier": (primary.supplier_name if primary else None),
            "mode": (primary.mode if primary else "v1_fallback"),
            "checkpoints": _V1_TPI_FALLBACK_CHECKPOINTS,
        }

    return {
        "call_id": call_id,
        "script_name": (primary.script_name if primary else None),
        "supplier": (primary.supplier_name if primary else None),
        "mode": (primary.mode if primary else None),
        "checkpoints": merged,
    }


def _resolve_segment_rubric(seg, script_obj) -> dict:
    """Compute rubric_kind + rubric_label for a CallSegment row.

    The reviewer sees one of FIVE rubric provenance badges per segment:
      - phrase_pack_lead_gen   (88-rule lead-gen phrase pack)
      - phrase_pack_pre_sales  (88-rule pre-sales phrase pack)
      - supplier_script_verbal (the supplier's verbal-contract script)
      - supplier_script_loa    (the supplier's LOA script — E.ON only)
      - v1_fallback            (V1 three-rule TPI fallback)

    Stage is the source of truth for the LABEL — `lead_gen` / `pre_sales`
    ALWAYS surface as the 88-rule pack, even when the pipeline happens to
    have routed them to a supplier script as a fallback (some
    deployments don't ship dedicated phrase-pack scripts yet). `verbal`
    and `loa` surface as the matched supplier script when there is one,
    else V1 fallback.
    """
    stage = (seg.stage or "").lower()
    has_script = seg.script_id is not None and script_obj is not None

    if stage == "lead_gen":
        return {
            "rubric_kind": "phrase_pack_lead_gen",
            "rubric_label": "88-rule Lead Gen phrase pack",
        }
    if stage == "pre_sales":
        return {
            "rubric_kind": "phrase_pack_pre_sales",
            "rubric_label": "88-rule Pre-Sales phrase pack",
        }
    if stage == "verbal":
        if has_script:
            supplier = script_obj.supplier_name or "Supplier"
            scrname = script_obj.script_name or "script"
            return {
                "rubric_kind": "supplier_script_verbal",
                "rubric_label": f"Verbal contract script · {supplier} — {scrname}",
            }
        return {
            "rubric_kind": "v1_fallback",
            "rubric_label": "V1 third-party-disclosure fallback (3 universal rules)",
        }
    if stage == "loa":
        if has_script:
            supplier = script_obj.supplier_name or "Supplier"
            scrname = script_obj.script_name or "script"
            return {
                "rubric_kind": "supplier_script_loa",
                "rubric_label": f"LOA script · {supplier} — {scrname}",
            }
        return {
            "rubric_kind": "v1_fallback",
            "rubric_label": "V1 third-party-disclosure fallback (3 universal rules)",
        }
    # Unknown stage — defensive fallback.
    return {
        "rubric_kind": "v1_fallback",
        "rubric_label": "V1 third-party-disclosure fallback (3 universal rules)",
    }


@router.get("/api/calls/{call_id}/segments")
def get_call_segments(call_id: str, db: Session = Depends(get_db)):
    """Return the per-segment verdicts the new pipeline writes.

    One row per CallSegment, with the stage / score / bucket / breach counts,
    a parsed `checkpoints` list, AND rubric provenance: which rubric was used
    to grade the segment (rubric_kind + rubric_label). Each individual
    checkpoint inherits its segment's rubric source so the UI can render a
    'where did this verdict come from?' badge per checkpoint.
    """
    from app.models import CallSegment as _CallSegment

    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(404, "Call not found")

    rows = (
        db.query(_CallSegment)
        .filter(_CallSegment.call_id == call_id)
        .order_by(_CallSegment.idx.asc())
        .all()
    )

    # Preload script names so we can stamp each segment's rubric_label
    # without N queries.
    script_ids = [s.script_id for s in rows if s.script_id]
    scripts_by_id: dict[str, Script] = {}
    if script_ids:
        for sc in db.query(Script).filter(Script.id.in_(script_ids)).all():
            scripts_by_id[str(sc.id)] = sc

    out = []
    for s in rows:
        try:
            cps = json.loads(s.checkpoint_results) if s.checkpoint_results else []
        except (TypeError, ValueError):
            cps = []
        rubric = _resolve_segment_rubric(s, scripts_by_id.get(str(s.script_id) if s.script_id else ""))
        # Stamp each inner checkpoint with the segment's source so the UI can
        # render a per-checkpoint badge.
        annotated_cps = []
        for cp in cps:
            if isinstance(cp, dict):
                annotated_cps.append(
                    {
                        **cp,
                        "rubric_kind": rubric["rubric_kind"],
                        "rubric_label": rubric["rubric_label"],
                    }
                )
            else:
                annotated_cps.append(cp)
        out.append(
            {
                "id": s.id,
                "idx": s.idx,
                "stage": s.stage,
                "confidence": float(s.confidence) if s.confidence is not None else None,
                "start_word_idx": s.start_word_idx,
                "end_word_idx": s.end_word_idx,
                "start_s": float(s.start_s) if s.start_s is not None else None,
                "end_s": float(s.end_s) if s.end_s is not None else None,
                "transcript_excerpt": s.transcript_excerpt,
                "classifier_reasoning": s.classifier_reasoning,
                "score": s.score,
                "bucket": s.bucket,
                "compliant": s.compliant,
                "compliance_status": s.compliance_status,
                "critical_breaches": s.critical_breaches or 0,
                "high_breaches": s.high_breaches or 0,
                "medium_breaches": s.medium_breaches or 0,
                "reason": s.reason,
                "script_id": s.script_id,
                "rubric_kind": rubric["rubric_kind"],
                "rubric_label": rubric["rubric_label"],
                "checkpoints": annotated_cps,
            }
        )
    return {"call_id": call_id, "segments": out}


@router.get("/api/calls/{call_id}", response_model=CallResponse)
def get_call(
    call_id: str,
    db: Session = Depends(get_db),
    _reviewer=Depends(current_reviewer),
):
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
    # 2026-05-16 perf — bake the signed audio URL into the detail response so
    # the call-detail page can start playback without a second round-trip to
    # /api/calls/{id}/audio-url. Saves ~150-250ms RTT on every mount.
    #
    # NOTE: build the response model explicitly via `model_validate` and
    # set `audio_url` on the Pydantic object, NOT on the ORM instance.
    # Setting attributes on the SQLAlchemy row works only until the next
    # session expiry / commit — Pydantic's from_attributes serialisation
    # may run after that point and reset to the column default (None).
    response = CallResponse.model_validate(call, from_attributes=True)
    if call.audio_storage_key:
        try:
            url = signed_url(call.audio_storage_key, expires_in=3600)
            if url:
                response.audio_url = url
        except Exception:
            # Never let signed-URL failure 500 the detail GET — the legacy
            # /audio-url path is still wired as a fallback.
            log.warning(
                "signed_url_failed call_id=%s", call_id, exc_info=True,
            )
    return response


@router.post("/api/calls/{call_id}/reanalyze", status_code=202)
async def reanalyze_call(
    call_id: str,
    db: Session = Depends(get_db),
    reviewer=Depends(current_reviewer),
):
    """Replay the analyze->score->finalize sub-pipeline against the stored
    transcript. Returns 202 with a fresh run_id; client polls the call to
    see the new verdict.

    2026-05-16 audit fix — actor_id now derived from the authenticated
    reviewer instead of being a client-controllable query param. Previously
    any unauthenticated request could stamp arbitrary actor_ids on the
    audit trail.
    """
    actor_id = (
        reviewer.get("id") if isinstance(reviewer, dict) else getattr(reviewer, "id", None)
    )
    return await _reanalyze_call(call_id, db, actor_id=actor_id)


@router.post("/api/admin/wipe-all-calls", status_code=200)
async def admin_wipe_all_calls(
    confirm: str = "",
    db: Session = Depends(get_db),
    _auth: dict = Depends(_require_admin),
):
    """DESTRUCTIVE — hard-deletes every Call + cascade-bound rows.

    Required for the 2026-05-12 taxonomy rebuild: user wants the prod DB
    cleared of legacy 37 calls graded under the old single-rubric model
    before the new content-classifier + per-segment pipeline goes live.

    Cascade-delete tables (FK has ON DELETE CASCADE):
        call_checkpoints · agent_traces · call_segments · flags ·
        extracted_entities · pipeline_step_log · failed_jobs ·
        transcript_chunks · fix_directives

    SET NULL tables (we explicitly delete the now-orphaned rows so they
    don't accumulate):
        rejections · verdict_history · transcript_edits ·
        verdict_suggestions · verdict_responses · review_sessions

    customer_deals + customers with no remaining calls are deleted too.

    Hard requires ``?confirm=YES_DELETE_EVERYTHING`` — any other value
    returns 400 to avoid accidental fires.
    """
    if confirm != "YES_DELETE_EVERYTHING":
        raise HTTPException(
            400,
            "Missing or wrong confirm. Pass ?confirm=YES_DELETE_EVERYTHING to proceed.",
        )

    from sqlalchemy import text

    counts: dict[str, int] = {}

    # 1) Tables that have call_id with SET NULL — delete the orphans
    # explicitly so we don't leave dangling rows.
    for table in (
        "rejections",
        "verdict_history",
        "transcript_edits",
        "verdict_suggestions",
        "verdict_responses",
        "review_sessions",
    ):
        try:
            result = db.execute(text(f"DELETE FROM {table}"))
            counts[table] = result.rowcount or 0
        except Exception as e:
            log.warning(f"wipe: {table} skipped ({e})")
            counts[table] = -1

    # 2) The big one — DELETE FROM calls cascades to ~9 child tables.
    result = db.execute(text("DELETE FROM calls"))
    counts["calls"] = result.rowcount or 0

    # 3) Drop deals + customers that no longer have any calls.
    result = db.execute(
        text(
            "DELETE FROM customer_deals WHERE id NOT IN "
            "(SELECT DISTINCT deal_id FROM calls WHERE deal_id IS NOT NULL)"
        )
    )
    counts["customer_deals"] = result.rowcount or 0

    try:
        result = db.execute(
            text(
                "DELETE FROM customers WHERE id NOT IN "
                "(SELECT DISTINCT customer_id FROM customer_deals "
                "WHERE customer_id IS NOT NULL)"
            )
        )
        counts["customers"] = result.rowcount or 0
    except Exception as e:
        log.warning(f"wipe: customers skipped ({e})")
        counts["customers"] = -1

    db.commit()
    log.warning(f"\U0001f4a3 WIPE-ALL-CALLS executed → {counts}")
    return {"wiped": True, "row_counts": counts}


@router.post("/api/admin/backfill-tracker", status_code=200)
async def admin_backfill_tracker(db: Session = Depends(get_db)):
    """Backfill tracker columns on legacy calls + rejections.

    Walks every completed call missing one of:
      - CustomerDeal.expected_live_date  (DateExtractorAgent)
      - Rejection.category / fix_required (RejectionAdvisorAgent)
      - Rejection.deadline (DeadlineComputerAgent)
    and runs the agent to fill it. Idempotent — already-filled rows are
    skipped. Safe to run multiple times.
    """
    from app.agents.date_extractor import DateExtractorAgent as _Date
    from app.agents.rejection_advisor import (
        RejectionAdvisorAgent as _Adv,
        advise_rejection,
    )
    from app.agents.deadline_computer import DeadlineComputerAgent as _Deadline
    from app.models import Rejection as _Rej, CustomerDeal as _CDeal

    completed = (
        db.query(Call)
        .filter(Call.status == "completed", Call.transcript.isnot(None))
        .order_by(Call.created_at.asc())
        .all()
    )

    dates_filled = 0
    advisor_filled = 0
    deadlines_filled = 0

    for c in completed:
        # 1. expected_live_date
        if c.deal_id:
            deal = db.query(_CDeal).filter_by(id=c.deal_id).first()
            if deal and not deal.expected_live_date:
                v = await _Date(c.id, db)
                if v.get("expected_live_date"):
                    dates_filled += 1

        # 2 & 3. category / fix_required / deadline
        rejs = db.query(_Rej).filter_by(call_id=c.id).all()
        if not rejs:
            continue
        # Run advisor once per call (cheaper than per-rejection)
        advisor_verdict = {}
        if any(not (r.category and r.fix_required) for r in rejs):
            advisor_verdict = await advise_rejection(c) or {}

        for rej in rejs:
            changed = False
            if advisor_verdict and not (rej.category and rej.fix_required):
                rej.category = advisor_verdict.get("category", rej.category)
                rej.fix_required = advisor_verdict.get(
                    "fix_required", rej.fix_required
                )
                changed = True
                advisor_filled += 1

            if not rej.deadline and rej.rejected_at:
                sev = advisor_verdict.get("severity") or "MEDIUM"
                parent_deal = (
                    db.query(_CDeal).filter_by(id=c.deal_id).first()
                    if c.deal_id
                    else None
                )
                rej.deadline = _Deadline(
                    rejected_at=rej.rejected_at,
                    severity=sev,
                    expected_live_date=(
                        parent_deal.expected_live_date if parent_deal else None
                    ),
                )
                changed = True
                deadlines_filled += 1
            if changed:
                db.flush()

    db.commit()
    return {
        "scanned_calls": len(completed),
        "dates_filled": dates_filled,
        "advisor_filled": advisor_filled,
        "deadlines_filled": deadlines_filled,
    }


@router.post("/api/admin/ingest-phrase-packs", status_code=200)
async def admin_ingest_phrase_packs(
    apply: bool = False,
    only_phase: str | None = None,
    db: Session = Depends(get_db),
):
    """Convert the Watt phrase-detection dataset into per-call_type
    phrase packs stored as synthetic `Script` rows with
    supplier_name='PHRASE_PACK'. The rubric router picks one of these
    when a call's call_type is not 'closer/verbal/full' and there's no
    supplier-specific LOA script.

    Pack rows shipped:
      - PHRASE_PACK / lead_gen           (88 Lead Generation rules)
      - PHRASE_PACK / passover           (Lead Gen subset)
      - PHRASE_PACK / verbal_confirmation (32 Verbal Confirmation rules)
      - PHRASE_PACK / c_call              (Verbal Confirmation subset)
      - PHRASE_PACK / amendment           (Verbal Confirmation subset)

    apply=true     → persist (upsert by supplier+phase).
    only_phase=X   → restrict to one phase, useful for re-ingesting one
                      pack after a prompt tweak.
    """
    from pathlib import Path
    from app.agents.phrase_pack_extractor import extract_phrase_pack, pack_definitions
    from app.watt_compliance.supplier_seed import docs_dir
    from app.agents.rubric_router import PHRASE_PACK_SUPPLIER

    src = docs_dir() / "compliance_xai__watt_ai_phrase_detection_dataset_1.md"
    if not src.exists():
        raise HTTPException(500, f"phrase dataset not found at {src}")
    md = src.read_text(encoding="utf-8", errors="ignore")

    defs = pack_definitions()
    if only_phase:
        defs = [d for d in defs if d["phase"] == only_phase]
        if not defs:
            raise HTTPException(400, f"unknown phase {only_phase!r}")

    results: list[dict] = []
    total_rules = 0
    for d in defs:
        cps = await extract_phrase_pack(
            markdown=md,
            stage_label=d["stage_label"],
            call_types=d["call_types"],
            stage_filter=d["stage_filter"],
        )
        if apply and cps:
            existing = (
                db.query(Script)
                .filter(Script.supplier_name == PHRASE_PACK_SUPPLIER)
                .filter(Script.lifecycle_phase == d["phase"])
                .first()
            )
            if existing:
                existing.checkpoints = json.dumps(cps)
                existing.active = True
                existing.script_name = f"Watt Phrase Pack · {d['stage_label']}"
                existing.version = "phrase-dataset-v1"
                existing.mode = "phrase_pack"
            else:
                db.add(
                    Script(
                        supplier_name=PHRASE_PACK_SUPPLIER,
                        script_name=f"Watt Phrase Pack · {d['stage_label']}",
                        version="phrase-dataset-v1",
                        mode="phrase_pack",
                        lifecycle_phase=d["phase"],
                        checkpoints=json.dumps(cps),
                        active=True,
                    )
                )
        total_rules += len(cps)
        results.append(
            {
                "phase": d["phase"],
                "stage": d["stage_label"],
                "rule_count": len(cps),
                "sample_names": [c["name"] for c in cps[:3]],
            }
        )
    if apply:
        db.commit()
    else:
        db.rollback()
    return {
        "applied": apply,
        "packs": len(defs),
        "total_rules": total_rules,
        "results": results,
    }


@router.post("/api/admin/reanalyze-all", status_code=200)
async def admin_reanalyze_all(
    apply: bool = False,
    only_script_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Re-run the analyzer + score + finalize pipeline steps SYNCHRONOUSLY
    against every completed call with a transcript + word_data + script.

    Needed because /api/calls/{id}/reanalyze emits an Inngest event and
    prod runs USE_INNGEST_PIPELINE=false — those events go nowhere.

    Use after `POST /api/admin/ingest-script-checkpoints?apply=true` so
    the freshly-extracted checkpoint rules actually grade existing calls.

    apply=false   → dry-run; only reports how many calls would be processed.
    apply=true    → actually runs. Skips calls already 'reviewed' to keep
                     reviewer-signed-off verdicts intact.
    only_script_id → restrict to calls with this script_id (debugging).
    """
    from app.pipeline import _step_analyze_checkpoints, _step_score, _step_finalize

    q = db.query(Call).filter(
        Call.transcript.isnot(None),
        Call.word_data.isnot(None),
        Call.script_id.isnot(None),
    )
    if only_script_id:
        q = q.filter(Call.script_id == only_script_id)
    calls = q.order_by(Call.created_at.desc()).all()

    if not apply:
        return {
            "would_process": len(calls),
            "applied": False,
            "sample_ids": [str(c.id)[:8] for c in calls[:10]],
        }

    results: list[dict] = []
    successes = 0
    for c in calls:
        if (c.review_status or "") == "reviewed":
            results.append({"call_id": str(c.id)[:8], "status": "skipped_reviewed"})
            continue
        transcript_data = {"transcript": c.transcript or ""}
        try:
            analysis = await _step_analyze_checkpoints(str(c.id), transcript_data, db)
            _step_score(str(c.id), analysis, db)
            _step_finalize(str(c.id), db)
            db.commit()
            # Re-read the fresh score.
            db.refresh(c)
            results.append(
                {
                    "call_id": str(c.id)[:8],
                    "status": "ok",
                    "new_score": c.score,
                    "new_compliance_status": c.compliance_status,
                }
            )
            successes += 1
        except Exception as e:
            db.rollback()
            results.append(
                {"call_id": str(c.id)[:8], "status": f"error:{type(e).__name__}", "msg": str(e)[:120]}
            )
    return {
        "processed": len(calls),
        "successes": successes,
        "applied": True,
        "results": results,
    }


@router.post("/api/admin/backfill-agent-names", status_code=200)
def admin_backfill_agent_names(
    apply: bool = False,
    only_missing: bool = True,
    db: Session = Depends(get_db),
):
    """Repair Call.agent_name for completed calls whose name extraction
    failed at first-pass time.

    Uses the new deterministic regex extractor in
    ``app.analysis._extract_agent_name_regex`` against ``Call.transcript``
    — no LLM call, so it runs in seconds across the whole catalogue.

    Query params:
      apply=false        — dry run; reports proposed names without writing.
      apply=true         — commits the changes.
      only_missing=true  — only touch rows where agent_name is NULL/empty
                           (default). Set to false to also overwrite
                           existing names (useful after the prompt update
                           if the previous LLM made bad calls).
    """
    from app.analysis import _extract_agent_name_regex

    q = db.query(Call).filter(Call.transcript.isnot(None))
    if only_missing:
        q = q.filter((Call.agent_name.is_(None)) | (Call.agent_name == ""))
    calls = q.order_by(Call.created_at.desc()).all()

    proposals: list[dict] = []
    updated = 0
    for c in calls:
        if not c.transcript:
            continue
        new_name = _extract_agent_name_regex(c.transcript)
        if not new_name:
            continue
        existing = (c.agent_name or "").strip()
        if existing and existing.lower() == new_name.lower():
            continue
        # Safety: never overwrite a non-empty existing name with a SHORTER
        # one (e.g. existing "Dominic Gratte" vs regex-only "Dominic") and
        # never replace an existing name with a regex result that just
        # happens to share the same first token (e.g. "Parat" → "Paris We").
        # only_missing=True (default) already filters these out; this is
        # defence-in-depth for the only_missing=False overwrite mode.
        if existing:
            ex_lower = existing.lower()
            new_lower = new_name.lower()
            ex_first = ex_lower.split()[0] if ex_lower else ""
            new_first = new_lower.split()[0] if new_lower else ""
            # Existing already richer than regex → keep existing.
            if ex_first == new_first and len(existing) > len(new_name):
                continue
            # Regex changed the first name entirely → likely a regex false
            # positive (regex catches the first self-intro phrase but the
            # LLM had access to more context). Don't touch.
            if ex_first and new_first and ex_first != new_first:
                continue
        proposals.append(
            {
                "call_id": str(c.id)[:8],
                "was": existing or None,
                "now": new_name,
                "filename": (c.filename or "")[:80],
            }
        )
        if apply:
            c.agent_name = new_name
            updated += 1
    if apply:
        db.commit()
    return {
        "scanned": len(calls),
        "candidates": len(proposals),
        "updated": updated if apply else 0,
        "applied": apply,
        "proposals": proposals[:50],
    }


@router.post("/api/admin/normalize-checkpoint-results", status_code=200)
def admin_normalize_checkpoint_results(
    call_id: str | None = None,
    apply: bool = False,
    db: Session = Depends(get_db),
    _auth=Depends(_require_admin),
):
    """Reconcile existing ``Call.checkpoint_results`` against the script
    templates used by the call's segments, without re-running the LLM.

    Useful for calls analyzed BEFORE the pipeline's per-CP coverage
    guarantee landed (commit 0f56394) — those calls have:
      * silent duplicate result entries for rules covered by two segments
      * silent gaps for rules whose anchor phrases didn't appear in any
        single segment slice

    Both cases get fixed in-place by replaying ``_normalize_checkpoint_results``
    against the live data. Synthetic ``status="not_scored"`` rows fill
    the gaps so the UI renders every template CP with a clear muted
    "Not Scored" label instead of falling through to the placeholder
    "Not yet scored" hard-coded in CheckpointCard.

    ``call_id`` — optional. When omitted, iterates every completed call.
    ``apply`` — when False, returns the diff in dry-run mode.
    """
    from app.models import CallSegment, Script
    from app.pipeline import _normalize_checkpoint_results

    q = db.query(Call).filter(Call.status == "completed")
    if call_id:
        q = q.filter(Call.id == call_id)
    calls = q.all()

    diffs: list[dict] = []
    updated = 0
    for c in calls:
        if not c.checkpoint_results:
            continue
        try:
            existing = json.loads(c.checkpoint_results) or []
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        # Build template_index from every script referenced by this call's
        # segments. Falls back to dataset.script_id when segments aren't
        # populated (legacy single-rubric calls).
        segments = (
            db.query(CallSegment)
            .filter(CallSegment.call_id == c.id)
            .all()
        )
        script_ids = {s.script_id for s in segments if s.script_id}
        if not script_ids and c.script_id:
            script_ids = {c.script_id}
        template_index: dict[tuple, dict] = {}
        for sid in script_ids:
            script = db.query(Script).filter(Script.id == sid).first()
            if not script:
                continue
            try:
                tcps = json.loads(script.checkpoints or "[]") or []
            except (TypeError, ValueError, json.JSONDecodeError):
                tcps = []
            for tcp in tcps:
                sec = tcp.get("section") or tcp.get("line_number") or 0
                name = (tcp.get("name") or "").strip()
                if name:
                    template_index[(sec, name)] = tcp

        if not template_index:
            # Nothing to reconcile against — leave the row alone.
            continue

        normalized = _normalize_checkpoint_results(existing, template_index)
        before = len(existing)
        after = len(normalized)
        added = max(0, after - before)
        # Count net new "not_scored" rows for clearer reporting.
        existing_names = {((r.get("name") or "").strip().lower()) for r in existing}
        filled = sum(
            1
            for r in normalized
            if (r.get("name") or "").strip().lower() not in existing_names
        )
        diff = {"call_id": c.id, "before": before, "after": after, "filled_not_scored": filled, "duplicates_collapsed": max(0, before - (after - added))}

        # 2026-05-15: also re-derive each CallSegment's score / bucket /
        # compliant from the normalized flat list, restricted to that
        # segment's own template. Fixes Andrew's LOA segment which had
        # score="0/11", compliant=True, bucket="coaching" while its own
        # checkpoint_results was empty `[]` — the segment row never got
        # the analyzer's verified list persisted.
        seg_fixes: list[dict] = []
        norm = lambda s: (s or "").strip().lower()
        flat_by_name = {norm(r.get("name")): r for r in normalized}
        for seg in segments:
            seg_tmpl: list[str] = []
            if seg.script_id:
                script = db.query(Script).filter(Script.id == seg.script_id).first()
                if script:
                    try:
                        tcps = json.loads(script.checkpoints or "[]") or []
                    except (TypeError, ValueError, json.JSONDecodeError):
                        tcps = []
                    seg_tmpl = [(t.get("name") or "").strip() for t in tcps if t.get("name")]
            if not seg_tmpl:
                continue
            # Collect this segment's CPs from the normalized flat list.
            seg_cps = [flat_by_name.get(norm(n)) for n in seg_tmpl]
            seg_cps = [c2 for c2 in seg_cps if c2]
            if not seg_cps:
                continue
            total_cps = len(seg_cps)
            passed_cps = sum(1 for c2 in seg_cps if c2.get("status") == "pass")
            # Severity-aware bucket — mirrors checkpoint_analyzer logic
            # for the post-2026-05-15 contract.
            critical = sum(1 for c2 in seg_cps if (c2.get("severity") or "").lower() == "critical" and c2.get("status") in ("fail", "partial", "unverified"))
            high = sum(1 for c2 in seg_cps if (c2.get("severity") or "").lower() == "high" and c2.get("status") in ("fail", "partial", "unverified"))
            medium = sum(1 for c2 in seg_cps if (c2.get("severity") or "medium").lower() in ("medium", "low", "info") and c2.get("status") in ("fail", "partial", "unverified", "not_scored"))
            if critical:
                bucket = "blocked"; compliant = False
            elif high:
                bucket = "review"; compliant = False
            elif medium:
                if total_cps > 0 and (passed_cps / total_cps) < 0.5:
                    bucket = "review"; compliant = False
                else:
                    bucket = "coaching"; compliant = True
            else:
                bucket = "pass"; compliant = total_cps > 0
            new_score = f"{passed_cps}/{total_cps}"
            new_compliance_status = (
                "compliant" if bucket == "pass" else
                ("compliant" if bucket == "coaching" else
                 ("pending" if bucket == "review" else "non_compliant"))
            )
            changed = (
                seg.score != new_score or
                seg.bucket != bucket or
                bool(seg.compliant) != compliant or
                (seg.critical_breaches or 0) != critical or
                (seg.high_breaches or 0) != high or
                (seg.medium_breaches or 0) != medium
            )
            if changed:
                seg_fixes.append({
                    "segment_id": str(seg.id),
                    "stage": seg.stage,
                    "old_score": seg.score,
                    "new_score": new_score,
                    "old_bucket": seg.bucket,
                    "new_bucket": bucket,
                    "old_compliant": bool(seg.compliant),
                    "new_compliant": compliant,
                })
                if apply:
                    seg.score = new_score
                    seg.bucket = bucket
                    seg.compliant = compliant
                    seg.compliance_status = new_compliance_status
                    seg.critical_breaches = critical
                    seg.high_breaches = high
                    seg.medium_breaches = medium
                    # Also persist the normalized CPs back to the segment
                    # row so /segments shows the same data the call-level
                    # JSON has.
                    seg.checkpoint_results = json.dumps([c2 for c2 in seg_cps])
        if seg_fixes:
            diff["segment_fixes"] = seg_fixes

        diffs.append(diff)
        if apply and (filled or after != before or seg_fixes):
            c.checkpoint_results = json.dumps(normalized)
            updated += 1
    if apply:
        db.commit()
    return {
        "scanned": len(calls),
        "candidates": len([d for d in diffs if d["filled_not_scored"] or d["before"] != d["after"]]),
        "updated": updated if apply else 0,
        "applied": apply,
        "diffs": diffs[:50],
    }


@router.post("/api/admin/ingest-script-checkpoints", status_code=200)
async def admin_ingest_script_checkpoints(
    apply: bool = False,
    only_empty: bool = True,
    db: Session = Depends(get_db),
):
    """Walk every `Script` row, locate its source markdown in
    `.planning/phase2-docs/`, ask Opus 4.7 to extract the canonical
    per-rule checkpoint list, and write it to `Script.checkpoints`.

    Fixes the long-standing bug where every call fell through to the
    V1 third-party-disclosure analyzer (3 universal rules) instead of
    being graded against the 20-30 supplier-specific rules in the
    actual script.

    Query params:
      apply=true       — persist the new checkpoints to DB
      only_empty=true  — default; only touch rows whose `checkpoints`
                          column is empty/`[]`. Pass `only_empty=false`
                          to re-extract every row (expensive — one Opus
                          call per script).
    """
    from pathlib import Path
    from app.agents.script_checkpoint_extractor import extract_checkpoints_from_markdown
    from app.watt_compliance.supplier_seed import CATALOGUE, docs_dir

    src_dir = docs_dir()
    if not src_dir.exists():
        raise HTTPException(500, f"phase2-docs/ not found at {src_dir}")

    # Index catalogue by canonical name parts so we can match scripts in
    # DB (which use freeform names) to the right markdown filename.
    def _canon(s: str) -> str:
        return "".join(ch for ch in (s or "").lower() if ch.isalnum())

    catalogue_by_filename_stem: dict[str, Path] = {}
    for meta in CATALOGUE:
        p = src_dir / meta.filename
        if p.exists():
            catalogue_by_filename_stem[_canon(meta.filename)] = p

    all_md_files = list(src_dir.glob("supplier_scripts__*.md"))

    scripts = db.query(Script).all()
    results: list[dict] = []
    extracted = 0
    skipped_filled = 0
    skipped_no_md = 0
    total_checkpoints = 0
    for s in scripts:
        existing = (s.checkpoints or "").strip()
        existing_count = 0
        try:
            existing_count = len(json.loads(existing) or []) if existing else 0
        except Exception:
            existing_count = 0
        if only_empty and existing_count > 0:
            skipped_filled += 1
            continue

        # Best-effort match: scan the markdown corpus and pick the file
        # whose canonical-name shares the most substrings with the
        # script_name. Tolerates slug-vs-display name drift.
        name_canon = _canon(s.script_name)
        best_path: Path | None = None
        best_score = 0
        for p in all_md_files:
            stem_canon = _canon(p.stem)
            # very simple longest-common-substring proxy: count shared
            # 6-char windows
            score = 0
            for i in range(0, len(name_canon) - 5):
                if name_canon[i : i + 6] in stem_canon:
                    score += 1
            if score > best_score:
                best_score = score
                best_path = p

        if best_path is None or best_score < 5:
            skipped_no_md += 1
            results.append(
                {
                    "script_id": str(s.id)[:8],
                    "name": s.script_name,
                    "status": "no-markdown-match",
                    "existing_checkpoints": existing_count,
                }
            )
            continue

        md = best_path.read_text(encoding="utf-8", errors="ignore")
        try:
            cps = await extract_checkpoints_from_markdown(
                script_md=md,
                supplier=s.supplier_name,
                script_name=s.script_name,
                script_type=(getattr(s, "lifecycle_phase", None) or "acquisition"),
            )
        except Exception as e:
            results.append(
                {
                    "script_id": str(s.id)[:8],
                    "name": s.script_name,
                    "status": f"extract-error:{type(e).__name__}",
                    "existing_checkpoints": existing_count,
                }
            )
            continue

        extracted += 1
        total_checkpoints += len(cps)
        results.append(
            {
                "script_id": str(s.id)[:8],
                "name": s.script_name,
                "matched_md": best_path.name,
                "match_score": best_score,
                "checkpoint_count": len(cps),
                "existing_checkpoints": existing_count,
                "status": "extracted" if cps else "empty",
                "sample_names": [c["name"] for c in cps[:3]],
            }
        )
        if apply and cps:
            s.checkpoints = json.dumps(cps)
            # Commit per-script so Railway's 5-min proxy timeout doesn't
            # lose all progress on the long-running prose-heavy scripts.
            # Each LLM call can take 30-90s; bundling 5+ extractions into
            # a single transaction was timing out at the gateway before
            # any rows landed.
            try:
                db.commit()
            except Exception as commit_err:
                log.warning(
                    f"ingest commit failed for script "
                    f"{s.id}: {commit_err}"
                )
                db.rollback()

    if not apply:
        db.rollback()

    return {
        "scripts_total": len(scripts),
        "extracted": extracted,
        "skipped_already_filled": skipped_filled,
        "skipped_no_markdown": skipped_no_md,
        "applied": apply,
        "only_empty": only_empty,
        "total_checkpoints_extracted": total_checkpoints,
        "results": results,
    }


@router.post("/api/admin/backfill-call-types", status_code=200)
async def admin_backfill_call_types(
    apply: bool = False,
    only_full: bool = True,
    db: Session = Depends(get_db),
):
    """Re-classify Call.call_type via AI for every call with a transcript.

    Replaces the old filename pre-pass results. Reviewer-signed-off calls
    are skipped. By default ``only_full=True`` so we only touch rows whose
    call_type is unset or 'full'; pass ``only_full=False`` to re-classify
    every call (slower, costs Opus 4.7 calls per recording).

    Pass ``?apply=true`` to persist; default is a dry-run that just logs
    the proposed changes + returns the diff.
    """
    from app.analysis import detect_call_type as _detect_call_type
    from app.deal_lifecycle import derive_lifecycle_status as _derive
    from app.models import CustomerDeal as _CDeal

    _CANON = {"lead_gen", "passover", "closer", "standalone_loa", "c_call", "amendment"}

    q = db.query(Call).filter(Call.transcript.isnot(None))
    if only_full:
        q = q.filter((Call.call_type.is_(None)) | (Call.call_type == "full"))
    calls = q.order_by(Call.created_at.desc()).all()

    changes: list[dict] = []
    unresolved = 0
    skipped_reviewed = 0
    for c in calls:
        existing = (c.call_type or "").strip().lower()
        if (c.review_status or "") == "reviewed" and existing in _CANON:
            skipped_reviewed += 1
            continue
        new_ct = await _detect_call_type(c.transcript or "")
        if new_ct is None:
            unresolved += 1
            continue
        if new_ct == existing:
            continue
        changes.append(
            {
                "call_id": str(c.id),
                "filename": c.filename,
                "from": existing or None,
                "to": new_ct,
            }
        )
        if apply:
            c.call_type = new_ct

    if apply:
        # Re-derive lifecycle on every affected deal.
        deal_ids = {c.deal_id for c in calls if c.deal_id}
        relifed = 0
        for did in deal_ids:
            deal = db.query(_CDeal).filter_by(id=did).first()
            if not deal:
                continue
            deal_calls = [c for c in calls if c.deal_id == did]
            new_status = _derive(deal, deal_calls)
            if new_status and new_status != deal.lifecycle_status:
                deal.lifecycle_status = new_status
                relifed += 1
        db.commit()
    else:
        relifed = 0
        db.rollback()

    return {
        "scanned": len(calls),
        "applied": apply,
        "only_full": only_full,
        "changes": changes,
        "unresolved": unresolved,
        "skipped_reviewed": skipped_reviewed,
        "deals_relifed": relifed,
    }


@router.post("/api/admin/quality-resolve", status_code=200)
async def admin_quality_resolve(
    db: Session = Depends(get_db),
    # 2026-05-14 audit fix: cross-call DB mutation (merges deals, renames
    # customers, fills suppliers); admin-only.
    _admin=Depends(_require_admin),
):
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
def delete_call(
    call_id: str,
    db: Session = Depends(get_db),
    # 2026-05-14 audit fix: this hard-deletes a call (cascades to 9 child
    # tables) — must require auth. Previously anonymous.
    user=Depends(current_reviewer),
):
    """Delete a call and clean up orphan parents.

    After the 2026-05-10 migration adds ON DELETE CASCADE to the 9 child
    tables (CallCheckpoint, ReviewSession, VerdictHistory, TranscriptEdit,
    ClaimLock, ComplianceDecision, VerdictSuggestion, VerdictResponse,
    AgentTrace), `db.delete(call)` cascades through them automatically \u2014
    no manual child cleanup needed.

    Additionally: if removing this call leaves its parent CustomerDeal
    with zero remaining calls, delete the deal too. If THAT in turn
    leaves the parent Customer with zero remaining deals, delete the
    Customer. This stops the "(auto-detect pending \u2026)" / "(pending audio
    upload)" stub rows that used to accumulate forever.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(404, "Call not found")

    filename = call.filename
    file_path = call.file_path
    deal_id = call.deal_id

    # `Call.checkpoints` is a passive relationship without `passive_deletes`;
    # ORM otherwise tries to UPDATE call_checkpoints.call_id=NULL (NOT-NULL
    # violation). Drop the children explicitly; the DB-level CASCADE on the
    # other 8 child tables (added in 2026_05_10 migration) handles the rest.
    db.query(CallCheckpoint).filter_by(call_id=call_id).delete()
    db.delete(call)
    db.flush()  # so the count() below sees the deletion

    # \u2500\u2500 parent cleanup \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    deal_deleted = False
    customer_deleted = False
    if deal_id:
        from app.models import CustomerDeal, Customer
        deal = db.query(CustomerDeal).filter_by(id=deal_id).first()
        if deal:
            remaining_calls = (
                db.query(Call).filter_by(deal_id=deal_id).count()
            )
            if remaining_calls == 0:
                customer_id = getattr(deal, "customer_id", None)
                db.delete(deal)
                db.flush()
                deal_deleted = True

                # If the deal's parent Customer now has no deals at all,
                # delete the Customer row too. CustomerDeal.customer_id
                # FK has ondelete=CASCADE going Customer\u2192Deal but not
                # the other way, so we must clean up explicitly.
                if customer_id is not None:
                    try:
                        remaining_deals = (
                            db.query(CustomerDeal)
                            .filter_by(customer_id=customer_id)
                            .count()
                        )
                        if remaining_deals == 0:
                            cust = (
                                db.query(Customer)
                                .filter_by(id=customer_id)
                                .first()
                            )
                            if cust:
                                db.delete(cust)
                                customer_deleted = True
                    except Exception as e:  # noqa: BLE001
                        log.warning(
                            f"\U0001f5d1\ufe0f DELETE customer cleanup skipped: {e}"
                        )

    db.commit()

    # Best-effort remove the audio file on disk
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError as e:
            log.warning(f"\U0001f5d1\ufe0f DELETE audio file removal failed call_id={call_id}: {e}")

    log.info(
        f"\U0001f5d1\ufe0f DELETE call_id={call_id} filename=\"{filename}\" "
        f"deal_deleted={deal_deleted} customer_deleted={customer_deleted}"
    )
    return {
        "status": "ok",
        "deleted": call_id,
        "deal_deleted": deal_deleted,
        "customer_deleted": customer_deleted,
    }


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
def patch_call_risk_tags(
    call_id: str,
    body: dict,
    db: Session = Depends(get_db),
    _reviewer=Depends(current_reviewer),
):
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
