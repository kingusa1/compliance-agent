"""HITL (Human-in-the-Loop) reviewer endpoints.

This module owns the reviewer-workflow API surface. It is the boundary between
an authenticated Supabase user (via `app.auth.current_user`, re-exported as
`current_reviewer` from `app.reviewers`) and the HITL tables:
`review_sessions`, `claim_locks`, `verdict_history`, `transcript_edits`,
`compliance_decisions`.

Endpoints (this task adds only the first; others land in later HITL tasks):
- POST   /api/calls/{id}/claim             — acquire a 30-min lock on a call
- POST   /api/review-sessions/{id}/release — release a claim
- POST   /api/calls/{id}/verdict           — submit/override a checkpoint verdict
- GET    /api/calls/{id}/history           — verdict + compliance history
- POST   /api/calls/{id}/edit-word         — correct a transcript word
- POST   /api/calls/{id}/compliance        — final pass/fail decision
- GET    /api/queue                        — reviewer inbox
- GET    /api/compliant, /api/non-compliant — completed lists

Every endpoint uses `Depends(current_reviewer)`; the JWT comes through
`app.auth.current_user` via the `app.reviewers` compatibility shim.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from app._clock import utcnow

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.feedback import abstract_and_store_review
from app.audit import record_audit
from app.checkpoint_analyzer import analyze_all_checkpoints
from app.events import emit
from app.database import get_db
from app.prompts import version_for_supplier
from app.models import (
    AgentLearning,
    AgentTrace,
    Call,
    ClaimLock,
    ComplianceDecision,
    Profile,
    ReviewSession,
    SavedView,
    Script,
    TraceAnnotation,
    TranscriptEdit,
    VerdictHistory,
    VerdictResponse,
    VerdictSuggestion,
)
from app.reviewers import current_reviewer, require_lead

logger = logging.getLogger(__name__)


# Locks idle out after 30 minutes. Picked to outlast a distracted reviewer
# stepping away for coffee but still recover within a reasonable window if
# someone walks away mid-review.
CLAIM_TTL_MIN = 30

hitl_router = APIRouter()


def _reviewer_name(db: Session, reviewer_id: str) -> str:
    """Resolve a reviewer id to a display name via the profiles table.

    Falls back to the raw id if no profile exists (shouldn't happen in
    production because `current_user` requires an active profile, but keeps
    the 409 path defensive).
    """
    profile = db.query(Profile).filter_by(id=reviewer_id).first()
    return profile.name if profile else reviewer_id


def _check_if_match(request: Request, call: Call) -> None:
    """Optimistic-locking precondition (Task 33).

    Pure-functional guard: reads the `If-Match` header off the request, and:
      - header absent / empty         → no-op (backwards compat + older frontends).
      - header not a valid int        → 400.
      - header != call.revision       → 409 with the current revision in detail
                                         so the caller can refetch + merge.
      - header == call.revision       → no-op (caller has the up-to-date state).

    Never mutates state. Call BEFORE making changes and bump `call.revision`
    after the change within the same commit.
    """
    raw = request.headers.get("If-Match") if request else None
    if raw is None or raw.strip() == "":
        return
    try:
        expected = int(raw.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid If-Match")
    current = call.revision or 1
    if expected != current:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "revision_mismatch",
                "current_revision": current,
                "provided": expected,
            },
        )


@hitl_router.post("/api/calls/{call_id}/claim")
def claim_call(
    call_id: str,
    request: Request,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    """Acquire a 30-min exclusive lock on a call for the current reviewer.

    Semantics:
    - First-time claim → create ReviewSession + ClaimLock, set call.review_status = "in_review", return 200.
    - Same reviewer re-claims → refresh expires_at, touch last_activity_at, return 200 with the SAME session id (idempotent).
    - Another reviewer holds a live lock → 409 with the holder's display name.
    - The lock is past its TTL → auto-release (mark session inactive with reason "idle_timeout"), then fall through to the first-time claim path.
    - Call id unknown → 404.
    """
    # TOCTOU fix (audit 2026-05-16 P1-4): take a row-level lock on the
    # target Call BEFORE checking the existing ClaimLock + writing a new
    # one. Without this lock, two concurrent claim requests from different
    # reviewers can both read existing=None (no live lock), both proceed
    # to step 3, and both create a ClaimLock — leaving the call held by
    # whichever request commits second while the audit row claims it for
    # the first. SELECT ... FOR UPDATE serializes the critical section.
    #
    # On Postgres this acquires a row-level lock that releases on commit.
    # On SQLite (tests) with_for_update is silently no-op; the test suite
    # uses a single connection so the race is moot there.
    call = (
        db.query(Call)
        .filter_by(id=call_id)
        .with_for_update()
        .first()
    )
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    # Optimistic lock gate (Task 33) — raises 409 if caller's If-Match is stale.
    _check_if_match(request, call)

    now = utcnow()

    # Step 1 — sweep an expired lock if any. Treat TTL reached as idle_timeout
    # so the audit trail on ReviewSession explains why the reviewer lost it.
    existing = db.query(ClaimLock).filter_by(call_id=call_id).first()
    if existing and existing.expires_at <= now:
        stale = db.query(ReviewSession).filter_by(id=existing.review_session_id).first()
        if stale is not None:
            stale.is_active = False
            stale.released_at = now
            stale.release_reason = "idle_timeout"
        db.delete(existing)
        db.flush()
        existing = None

    # Step 2 — live lock held. Either refresh (same reviewer) or 409 (different).
    if existing:
        if existing.reviewer_id == reviewer["id"]:
            existing.expires_at = now + timedelta(minutes=CLAIM_TTL_MIN)
            session = db.query(ReviewSession).filter_by(id=existing.review_session_id).first()
            if session is not None:
                session.last_activity_at = now
            # Same-reviewer re-claim is idempotent from the caller's POV but it
            # does refresh server-side state — bump so any other client holding
            # a stale `revision` refetches.
            call.revision = (call.revision or 1) + 1
            # Audit row in same transaction — refresh path still mutates (TTL,
            # last_activity_at, revision) so the chain captures it. Payload
            # only carries structural fields; no PII.
            record_audit(
                db,
                action="hitl.claim",
                entity_type="call",
                entity_id=call_id,
                payload={
                    "review_session_id": existing.review_session_id,
                    "mode": "refresh",
                },
                actor_id=reviewer["id"],
            )
            db.commit()
            return {"review_session_id": existing.review_session_id, "call_id": call_id}
        holder = _reviewer_name(db, existing.reviewer_id)
        raise HTTPException(
            status_code=409,
            detail=f"Call already claimed by {holder}",
        )

    # Step 3 — no lock (fresh call, or we just swept an expired one). Create.
    session_id = str(uuid.uuid4())
    session = ReviewSession(
        id=session_id,
        call_id=call_id,
        reviewer_id=reviewer["id"],
        claimed_at=now,
        last_activity_at=now,
        is_active=True,
    )
    lock = ClaimLock(
        call_id=call_id,
        reviewer_id=reviewer["id"],
        review_session_id=session_id,
        claimed_at=now,
        expires_at=now + timedelta(minutes=CLAIM_TTL_MIN),
    )
    call.review_status = "in_review"
    # Task 33: bump revision — a fresh claim flipped review_status so any
    # other client caching this call is now stale.
    call.revision = (call.revision or 1) + 1
    # Insert ReviewSession before ClaimLock — the claim_locks.review_session_id
    # FK requires the session row to exist. SQLAlchemy's unit-of-work doesn't
    # topologically sort these two inserts reliably (no ORM relationship()),
    # so we flush the session explicitly before adding the lock.
    db.add(session)
    db.flush()
    db.add(lock)
    # Audit row inside the same transaction so a successful claim implies a
    # chain extension; an IntegrityError rollback below also rolls this back.
    record_audit(
        db,
        action="hitl.claim",
        entity_type="call",
        entity_id=call_id,
        payload={
            "review_session_id": session_id,
            "mode": "fresh",
        },
        actor_id=reviewer["id"],
    )
    try:
        db.commit()
    except IntegrityError:
        # Concurrent first-claim race: two workers both saw `existing is None`
        # and both tried to insert into claim_locks (PK = call_id). The loser
        # lands here. Re-read the winner and surface a clean 409.
        db.rollback()
        winner = db.query(ClaimLock).filter_by(call_id=call_id).first()
        holder = _reviewer_name(db, winner.reviewer_id) if winner else "another reviewer"
        raise HTTPException(status_code=409, detail=f"Call already claimed by {holder}")
    return {"review_session_id": session_id, "call_id": call_id}


@hitl_router.post("/api/review-sessions/{session_id}/release")
def release_session(
    session_id: str,
    request: Request,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    """Release a review session's claim before its 30-min TTL runs out.

    Authorization: the claiming reviewer (release reason `"abandoned"`) or a
    lead/admin releasing someone else's session (reason `"lead_reopen"`).
    Anyone else → 403.

    Idempotent: if the session is already inactive, return 200 with
    `{"status": "already_released"}` and touch nothing.

    Side effects on a fresh release:
    - ReviewSession: is_active=False, released_at=now, release_reason set.
    - ClaimLock row for this session is deleted.
    - Owning Call's review_status flips back to "unclaimed" iff it was
      "in_review" (don't trample terminal states like "completed").
    """
    rs = db.query(ReviewSession).filter_by(id=session_id).first()
    if not rs:
        raise HTTPException(status_code=404, detail="Session not found")

    is_owner = rs.reviewer_id == reviewer["id"]
    is_lead = reviewer["role"] in ("lead", "admin")
    if not is_owner and not is_lead:
        raise HTTPException(
            status_code=403,
            detail="Only the claiming reviewer or a lead can release",
        )

    if not rs.is_active:
        return {"status": "already_released"}

    # Task 33: If-Match targets the OWNING Call (not the session row itself —
    # release is a Call-mutating operation from the reviewer's POV because it
    # flips review_status). Bumping only when the call still exists keeps the
    # check a no-op for orphaned sessions.
    call = db.query(Call).filter_by(id=rs.call_id).first()
    if call is not None:
        _check_if_match(request, call)

    rs.is_active = False
    rs.released_at = utcnow()
    rs.release_reason = "abandoned" if is_owner else "lead_reopen"

    lock = db.query(ClaimLock).filter_by(review_session_id=session_id).first()
    if lock is not None:
        db.delete(lock)

    if call and call.review_status == "in_review":
        call.review_status = "unclaimed"
    # Bump revision even if the call state didn't flip (e.g. session was
    # "abandoned" but the call was already unclaimed) — the session status
    # itself is visible on GET /queue so the cached row is stale.
    if call is not None:
        call.revision = (call.revision or 1) + 1

    # Audit row inside the same transaction — capture release reason so the
    # tamper-evident chain shows whether the owning reviewer abandoned the
    # session or a lead reopened it. No PII; structural fields only.
    record_audit(
        db,
        action="hitl.release",
        entity_type="call",
        entity_id=rs.call_id,
        payload={
            "review_session_id": session_id,
            "reason": rs.release_reason,
        },
        actor_id=reviewer["id"],
    )

    db.commit()
    return {"status": "released", "reason": rs.release_reason}


# ─── POST /api/calls/{id}/verdict ──────────────────────────────────────────
#
# Writes an immutable VerdictHistory row every time a human touches a
# checkpoint. On the FIRST human touch we also bootstrap an AI row (mirroring
# whatever the agent originally decided) so the history is self-contained:
# anyone reading verdict_history later can reconstruct the full chain without
# having to also read `calls.checkpoint_results`. The current row is pointed
# at by `is_current=True`; prior current rows get flipped to False.
#
# Learning extraction (`abstract_and_store_review`) ONLY fires when the human
# disagrees with the AI — otherwise we'd pollute agent_learnings with
# "reviewer confirmed the AI was right" no-ops. feedback.py short-circuits on
# agreement too, but we guard at the call site so the async task isn't even
# scheduled when there's nothing to learn.


class VerdictPayload(BaseModel):
    checkpoint_id: str
    verdict: str  # pass | fail | partial | flagged
    reasoning: str | None = None


def _find_checkpoint(call: Call, checkpoint_id: str):
    """Locate a checkpoint in call.checkpoint_results.

    The real pipeline emits checkpoints without an explicit `id` field, so we
    support two addressing modes:
      1. Explicit: `cp["id"] == checkpoint_id`.
      2. Synthetic: `checkpoint_id == "cp_{index}"` (0-indexed into the array).

    Returns (index, dict) or (None, None) if not found. Survives NULL /
    malformed checkpoint_results (old rows, fresh inserts) by returning None
    rather than raising.
    """
    try:
        cps = json.loads(call.checkpoint_results or "[]")
    except (TypeError, ValueError):
        return None, None
    for i, cp in enumerate(cps):
        if cp.get("id") == checkpoint_id:
            return i, cp
    if checkpoint_id.startswith("cp_"):
        try:
            i = int(checkpoint_id[3:])
            if 0 <= i < len(cps):
                return i, cps[i]
        except ValueError:
            pass
    return None, None


def _ai_verdict_of(cp: dict) -> str:
    """Read the AI's verdict from a checkpoint dict.

    The pipeline uses `status` on real runs and `verdict` in tests / newer
    checkpoint shapes. We accept both rather than forcing one convention.
    Fallback `"flagged"` keeps us safe if neither key is present.
    """
    return cp.get("verdict") or cp.get("status") or "flagged"


_CONFIDENCE_LABELS = {"high": 0.95, "medium": 0.75, "low": 0.4}


def _normalize_confidence(raw) -> float | None:
    """The pipeline stores confidence as a string label ("high"/"medium"/"low");
    tests and the reviewer API pass a float. VerdictHistory.confidence is a
    Float column, so coerce here before insert. None stays None."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        return _CONFIDENCE_LABELS.get(raw.lower())
    return None


def _bootstrap_ai_history_if_missing(
    db: Session, call: Call, checkpoint_id: str, cp: dict
) -> None:
    """Insert an AI row into VerdictHistory the first time a human touches a
    checkpoint. Idempotent: subsequent reviewer edits won't duplicate it."""
    if (
        db.query(VerdictHistory)
        .filter_by(call_id=call.id, checkpoint_id=checkpoint_id)
        .count()
        > 0
    ):
        return
    db.add(VerdictHistory(
        id=str(uuid.uuid4()),
        call_id=call.id,
        checkpoint_id=checkpoint_id,
        actor_type="ai",
        actor_id="agent",
        verdict=_ai_verdict_of(cp),
        reasoning=cp.get("reasoning"),
        confidence=_normalize_confidence(cp.get("confidence")),
        evidence_text=cp.get("evidence"),
        # Task 32: tag with the prompt version active for this call's supplier.
        # This is the AI's *original* verdict being retroactively mirrored into
        # history, so we stamp with the current supplier prompt — the best
        # approximation given we don't persist the version at pipeline time.
        prompt_version=version_for_supplier(call.detected_supplier),
        is_current=False,
    ))


@hitl_router.post("/api/calls/{call_id}/verdict")
async def submit_verdict(
    call_id: str,
    payload: VerdictPayload,
    request: Request,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    """Submit (or override) a reviewer's verdict on a single checkpoint.

    Flow:
      1. 404 if the call doesn't exist.
      2. 400 if the checkpoint_id doesn't resolve inside checkpoint_results.
      3. Find the reviewer's active ReviewSession (optional — the UI enforces
         claim-before-verdict; we allow it here so tests / scripts can post
         without claiming first).
      4. Bootstrap an AI history row if this is the first touch.
      5. Flip the prior `is_current=True` row (if any) to False; insert the
         new reviewer row with `is_current=True`.
      6. Mirror the reviewer's verdict into call.checkpoint_results so the
         `/api/queue` endpoint shows the new state without joining against
         verdict_history.
      7. Fire `abstract_and_store_review` if AI disagreed — failures here log
         and swallow so the endpoint never fails because the learning LLM
         timed out.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    # Task 33: gate on If-Match BEFORE any state changes.
    _check_if_match(request, call)

    idx, cp = _find_checkpoint(call, payload.checkpoint_id)
    if cp is None:
        raise HTTPException(
            status_code=400,
            detail=f"Checkpoint {payload.checkpoint_id} not found",
        )

    # Optional active session. Not required: tests and direct-API callers can
    # skip the /claim step. Pick the freshest active one if multiple exist.
    rs = (
        db.query(ReviewSession)
        .filter_by(call_id=call_id, reviewer_id=reviewer["id"], is_active=True)
        .order_by(ReviewSession.claimed_at.desc())
        .first()
    )

    _bootstrap_ai_history_if_missing(db, call, payload.checkpoint_id, cp)

    prior = (
        db.query(VerdictHistory)
        .filter_by(
            call_id=call_id,
            checkpoint_id=payload.checkpoint_id,
            is_current=True,
        )
        .first()
    )
    prior_verdict = prior.verdict if prior else None
    if prior:
        prior.is_current = False

    # Task 32: a reviewer override inherits the prompt_version from the most
    # recent AI row for this checkpoint. This lets ops ask "what's the
    # override rate for verdicts produced under prompt v X?" — the interesting
    # dimension is the AI prompt the reviewer *disagreed with*, not the
    # reviewer themselves. Fall back to the current supplier version if no
    # AI row has been persisted yet (rare — bootstrap above ensures one).
    ai_row = (
        db.query(VerdictHistory)
        .filter_by(
            call_id=call_id,
            checkpoint_id=payload.checkpoint_id,
            actor_type="ai",
        )
        .order_by(VerdictHistory.created_at.desc())
        .first()
    )
    inherited_version = (
        ai_row.prompt_version if ai_row and ai_row.prompt_version
        else version_for_supplier(call.detected_supplier)
    )

    new_row = VerdictHistory(
        id=str(uuid.uuid4()),
        call_id=call_id,
        checkpoint_id=payload.checkpoint_id,
        review_session_id=rs.id if rs else None,
        actor_type=reviewer["role"],  # "reviewer" or "lead"
        actor_id=reviewer["id"],
        verdict=payload.verdict,
        reasoning=payload.reasoning,
        prompt_version=inherited_version,
        is_current=True,
    )
    db.add(new_row)

    # Mirror reviewer fields into the JSON so `/queue` + `/calls/{id}` don't
    # need to join verdict_history. Keep AI-era keys intact so we have a
    # "last known AI verdict" after the override.
    cps = json.loads(call.checkpoint_results or "[]")
    if 0 <= idx < len(cps):
        cps[idx]["verdict"] = payload.verdict
        cps[idx]["reviewer_verdict"] = payload.verdict
        cps[idx]["reviewer_reasoning"] = payload.reasoning
        cps[idx]["reviewer_id"] = reviewer["id"]
        call.checkpoint_results = json.dumps(cps)

    if rs:
        rs.last_activity_at = utcnow()

    # Task 33: bump revision since checkpoint_results mutated.
    call.revision = (call.revision or 1) + 1

    # Sprint TR-3 — multi-rejection FAIL: loop ALL FAIL/PARTIAL CallCheckpoint
    # rows for this call and create one Rejection per failure. Mirrors the
    # Watt XLSX where one bad call produces N tracker rows (one per failed
    # script line). Replaces the legacy single-rejection auto-create which
    # only emitted one row keyed off the verdict's checkpoint_id.
    #
    # Per-row failures inside the loop are logged and skipped so a single
    # bad checkpoint can't block the rest. The outer try/except guards the
    # branch entirely so the verdict commit itself is never blocked.
    auto_rejection_ids: list[str] = []
    # 2026-05-15: case-insensitive verdict check. Frontend sends lowercase
    # ("fail" / "review") while this branch was matching UPPERCASE only,
    # so the entire rejection-create side effect was being silently
    # skipped — a Playwright contract test caught it. Verdict normalisation
    # also lets the downstream ``auto_create_rejection_for_verdict`` see
    # the canonical uppercase form it expects internally.
    verdict_action_norm = (payload.verdict or "").strip().upper()
    if verdict_action_norm in ("FAIL", "REVIEW"):
        try:
            from app.rejections_routes import auto_create_rejection_for_verdict
            from app.models import CallCheckpoint

            failed_cps = (
                db.query(CallCheckpoint)
                .filter(CallCheckpoint.call_id == call.id)
                .filter(CallCheckpoint.passed.is_(False))
                .all()
            )
            if not failed_cps:
                logger.info(
                    "FAIL verdict but no failed checkpoints for call_id=%s",
                    call.id,
                )
            for fcp in failed_cps:
                try:
                    rej = auto_create_rejection_for_verdict(
                        db,
                        call=call,
                        actor_id=reviewer["id"],
                        verdict_action=verdict_action_norm,
                        reason=fcp.ai_rejection_reason or payload.reasoning,
                        rule_id=(
                            fcp.rule_text.upper().replace(" ", "_")
                            if fcp.rule_text else None
                        ),
                        checkpoint=fcp,  # W4.7 — AI category/fix off ORM row
                    )
                    if rej is not None:
                        auto_rejection_ids.append(str(rej.id))
                except Exception as e:
                    logger.warning(
                        "auto-create rejection failed for cp %s: %s",
                        fcp.id, e,
                    )
        except Exception as e:  # pragma: no cover — best-effort side effect
            logger.warning("FAIL-multi-rejection branch failed: %s", e)

    # Sprint A2 — fire the customer-confirmation email on PASS verdict
    # (compliance manual §8 mandate: every accepted verbal contract is
    # followed by a customer-facing email). Best-effort — never fails the
    # verdict commit. The helper itself catches and returns sent=False on
    # error; we still wrap in try/except as belt-and-braces.
    #
    # Sprint TR-3 — also flip ``call.compliant=True`` on PASS so the
    # tracker's "Compliant" tab can read the boolean directly without
    # joining the verdict_history audit table.
    elif verdict_action_norm == "PASS":
        # 2026-05-16 audit P2-6 fix — frontend sends lowercase "pass" too.
        # The previous case-sensitive `payload.verdict == "PASS"` silently
        # skipped this branch, leaving call.compliant unset and never
        # firing the customer confirmation email.
        call.compliant = True
        try:
            from app.email_routes import send_customer_email_for_call

            send_customer_email_for_call(
                db=db,
                call_id=call.id,
                sender={"email": reviewer.get("email"), "id": reviewer["id"]},
            )
        except Exception as e:  # pragma: no cover — best-effort side effect
            logger.warning(
                "Customer email on PASS verdict failed call_id=%s: %s",
                call.id, e,
            )

    # Downstream response field: surface the first auto-created rejection id
    # (preserves existing API shape; multi-id callers can re-query by call_id).
    auto_rejection_id: str | None = (
        auto_rejection_ids[0] if auto_rejection_ids else None
    )

    db.commit()

    # 2026-05-16 audit Bug 8 fix: push the verdict change onto the in-memory
    # SSE pub/sub so OTHER open tabs (Tracker / Queue / Rejections in Tab B)
    # invalidate their TanStack Query caches within 200ms. Previously only
    # the `emit()` pg_notify path fired — pg_notify is consumed by nothing
    # in this codebase (no asyncpg LISTEN bridge), so Tab B never saw the
    # change. "score_ready" is a named event in the frontend's
    # useCallEvents listener (frontend-v3/src/lib/hooks/useCallEvents.ts)
    # that triggers queue + tracker + admin + intelligence invalidations.
    try:
        from app import realtime
        realtime.publish(
            call_id,
            "score_ready",
            {
                "actor": "reviewer",
                "verdict": verdict_action_norm,
                "auto_rejection_id": auto_rejection_id,
            },
        )
    except Exception:
        logger.warning("realtime.publish on verdict failed", exc_info=True)

    # Task 30: emit event for downstream listeners (analytics, Slack, etc.)
    emit(db, "verdict.submitted", {
        "call_id": call_id,
        "checkpoint_id": payload.checkpoint_id,
        "verdict": payload.verdict,
        "actor_id": reviewer["id"],
    })

    # Inngest tracker observability — surface verdict outcome + the
    # rejection ids spawned (FAIL path) or compliant flip (PASS path).
    try:
        from app.workflows.events import VERDICT_SUBMITTED
        from app.workflows.observability import emit_event
        emit_event(VERDICT_SUBMITTED, {
            "call_id": call_id,
            "actor_id": reviewer["id"],
            "verdict": verdict_action_norm,
            "rejection_ids": auto_rejection_ids,
            "compliant": verdict_action_norm == "PASS",
        })
    except Exception:
        logger.warning("VERDICT_SUBMITTED emit_event failed", exc_info=True)

    ai_verdict = ai_row.verdict if ai_row else _ai_verdict_of(cp)
    learning_triggered = False
    if ai_verdict != payload.verdict:
        try:
            await abstract_and_store_review(
                db=db,
                supplier=call.detected_supplier or "Unknown",
                checkpoint_name=cp.get("name") or payload.checkpoint_id,
                transcript_excerpt=(cp.get("evidence") or call.transcript or "")[:2000],
                agent_verdict=ai_verdict,
                human_verdict=payload.verdict,
                reviewer_notes=payload.reasoning,
            )
            learning_triggered = True
        except Exception as e:
            # feedback.py already swallows most failures, but belt-and-braces:
            # a bad network or a malformed LLM response should never fail the
            # reviewer's save.
            logger.warning("Learning extraction failed: %s", e)

    return {
        "saved": True,
        "verdict_history_id": new_row.id,
        "prior_verdict": prior_verdict,
        "learning_triggered": learning_triggered,
        "auto_rejection_id": auto_rejection_id,
    }


# ─── GET /api/calls/{id}/history ───────────────────────────────────────────
#
# Read-only view over the three HITL audit tables (verdict_history,
# transcript_edits, compliance_decisions) for a single call. Every authenticated
# reviewer can read — lead-only gating is enforced at higher layers (e.g. the
# compliance endpoint). Each array is returned in ascending `created_at` order
# so a UI timeline can render the conversation without re-sorting.


@hitl_router.get("/api/calls/{call_id}/history")
def get_history(
    call_id: str,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    """Return the full verdict / edit / compliance audit trail for a call.

    - 404 if the call doesn't exist (mirrors /claim + /verdict for consistency).
    - Otherwise returns `{"verdicts": [...], "edits": [...], "compliance": [...]}`,
      each ordered ASC by `created_at`.
    """
    if not db.query(Call).filter_by(id=call_id).first():
        raise HTTPException(status_code=404, detail="Call not found")

    verdicts = (
        db.query(VerdictHistory)
        .filter_by(call_id=call_id)
        .order_by(VerdictHistory.created_at.asc())
        .all()
    )
    edits = (
        db.query(TranscriptEdit)
        .filter_by(call_id=call_id)
        .order_by(TranscriptEdit.created_at.asc())
        .all()
    )
    compliance = (
        db.query(ComplianceDecision)
        .filter_by(call_id=call_id)
        .order_by(ComplianceDecision.created_at.asc())
        .all()
    )

    def _v(v: VerdictHistory) -> dict:
        return {
            "id": v.id,
            "checkpoint_id": v.checkpoint_id,
            "actor_type": v.actor_type,
            "actor_id": v.actor_id,
            "verdict": v.verdict,
            "reasoning": v.reasoning,
            "confidence": v.confidence,
            "is_current": v.is_current,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }

    def _e(e: TranscriptEdit) -> dict:
        return {
            "id": e.id,
            "word_index": e.word_index,
            "old_text": e.old_text,
            "new_text": e.new_text,
            "edited_by": e.edited_by,
            "triggered_checkpoint_id": e.triggered_checkpoint_id,
            "reanalysis_changed_verdict": e.reanalysis_changed_verdict,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }

    def _c(c: ComplianceDecision) -> dict:
        return {
            "id": c.id,
            "status": c.status,
            "actor_type": c.actor_type,
            "actor_id": c.actor_id,
            "comment": c.comment,
            "is_current": c.is_current,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }

    return {
        "verdicts": [_v(v) for v in verdicts],
        "edits": [_e(e) for e in edits],
        "compliance": [_c(c) for c in compliance],
    }


# ─── POST /api/calls/{id}/edit-word ────────────────────────────────────────
#
# A reviewer clicks on a misheard word in the transcript and types the correct
# spelling. We:
#   1. Patch `call.word_data[index]["word"]` with the new text.
#   2. Write a `TranscriptEdit` audit row.
#   3. If a `checkpoint_id` is attached, re-run JUST that one checkpoint
#      against the corrected transcript (loaded from the script, not from
#      checkpoint_results — results store OUTPUT, scripts hold INPUT).
#   4. If the rerun flipped the verdict, mirror the new fields into
#      `call.checkpoint_results` AND write a new `actor_type="ai"` row in
#      `verdict_history` (is_current=True, prior is_current demoted first).
#
# Reanalysis is best-effort: if the script is missing, the checkpoint can't be
# resolved, or the LLM call blows up, we save the edit and carry on. The
# reviewer already typed the correction — losing the edit because the LLM
# timed out is worse than skipping reanalysis.


class WordEditPayload(BaseModel):
    word_index: int
    old_text: str
    new_text: str
    checkpoint_id: str | None = None


def _rebuild_transcript_from_words(words: list[dict]) -> str:
    """Join the edited word array back into a transcript string for reanalysis.

    Reanalysis must run against the EDITED text, not `call.transcript`, because
    the original string was frozen at upload time.
    """
    return " ".join(w.get("word", "") for w in words)


def _find_script_checkpoint(script_checkpoints: list[dict], checkpoint_id: str):
    """Locate a checkpoint in a script's checkpoint list.

    Mirrors the addressing in `_find_checkpoint` (for call.checkpoint_results):
    explicit id first, then the `cp_{i}` synthetic form that indexes 0-based
    into the list. Returns None if neither matches.
    """
    for cp in script_checkpoints:
        if cp.get("id") == checkpoint_id:
            return cp
    if checkpoint_id.startswith("cp_"):
        try:
            i = int(checkpoint_id[3:])
            if 0 <= i < len(script_checkpoints):
                return script_checkpoints[i]
        except ValueError:
            pass
    return None


@hitl_router.post("/api/calls/{call_id}/edit-word")
async def edit_word(
    call_id: str,
    payload: WordEditPayload,
    request: Request,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    """Correct a single word in a call's transcript and (optionally) re-score
    one checkpoint against the edited text.

    Flow:
      1. 404 if call is unknown.
      2. 400 if `word_index` falls outside `word_data`.
      3. Patch the word in place and persist a `TranscriptEdit` audit row.
      4. If `checkpoint_id` is present AND a Script + checkpoint can be located,
         call `analyze_all_checkpoints` with that single checkpoint and the
         rebuilt transcript.
      5. If the rerun produced a verdict different from the stored one, mirror
         it into `call.checkpoint_results` and insert a new AI `VerdictHistory`
         row (demoting any prior `is_current=True` row first).

    Response: `{saved, edit_id, verdict_changed, new_verdict, checkpoint}`.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    # Task 33: gate on If-Match BEFORE any state changes.
    _check_if_match(request, call)

    words = json.loads(call.word_data or "[]")
    if payload.word_index < 0 or payload.word_index >= len(words):
        raise HTTPException(status_code=400, detail="word_index out of range")

    # Patch the word in-place. `payload.old_text` is informational for the audit
    # row; don't block if it mismatches the stored token — the reviewer is the
    # source of truth and stale old_text would just be noise.
    target_word = words[payload.word_index]
    target_word["word"] = payload.new_text
    call.word_data = json.dumps(words)

    # Optional active session. Same rationale as /verdict: tests and scripts
    # can skip /claim, but if a session exists, link the edit to it.
    rs = (
        db.query(ReviewSession)
        .filter_by(call_id=call_id, reviewer_id=reviewer["id"], is_active=True)
        .order_by(ReviewSession.claimed_at.desc())
        .first()
    )

    edit = TranscriptEdit(
        id=str(uuid.uuid4()),
        call_id=call_id,
        word_index=payload.word_index,
        word_start_ms=int((target_word.get("start") or 0) * 1000),
        old_text=payload.old_text,
        new_text=payload.new_text,
        edited_by=reviewer["id"],
        review_session_id=rs.id if rs else None,
        triggered_checkpoint_id=payload.checkpoint_id,
        # Flipped to True below only if reanalysis actually runs.
        triggered_reanalysis=False,
    )
    db.add(edit)

    verdict_changed = False
    new_verdict = None
    updated_checkpoint = None

    if payload.checkpoint_id:
        script = (
            db.query(Script).filter_by(id=call.script_id).first()
            if call.script_id
            else None
        )
        if script:
            try:
                script_checkpoints = json.loads(script.checkpoints or "[]")
            except (TypeError, ValueError):
                script_checkpoints = []
            target = _find_script_checkpoint(script_checkpoints, payload.checkpoint_id)
            if target:
                try:
                    result = await analyze_all_checkpoints(
                        transcript=_rebuild_transcript_from_words(words),
                        checkpoints=[target],
                        script_mode=script.mode or "meaning_for_meaning",
                        supplier=call.detected_supplier or "Unknown",
                        word_data=words,
                    )
                    rerun = result.get("results", []) if isinstance(result, dict) else []
                    # If the LLM succeeded and returned at least one row, we
                    # "actually ran" reanalysis — flip the audit flag.
                    if rerun:
                        edit.triggered_reanalysis = True
                except Exception as err:
                    logger.warning("Re-analysis after word edit failed: %s", err)
                    rerun = []

                if rerun:
                    new_cp = rerun[0]
                    # Read prior verdict from call.checkpoint_results so we can
                    # both detect the flip AND mirror new fields into the row.
                    try:
                        results = json.loads(call.checkpoint_results or "[]")
                    except (TypeError, ValueError):
                        results = []
                    prior_idx = None
                    for i, r in enumerate(results):
                        if r.get("id") == payload.checkpoint_id:
                            prior_idx = i
                            break
                    if prior_idx is None and payload.checkpoint_id.startswith("cp_"):
                        try:
                            i = int(payload.checkpoint_id[3:])
                            if 0 <= i < len(results):
                                prior_idx = i
                        except ValueError:
                            pass

                    prior_verdict = None
                    if prior_idx is not None:
                        prior_cp = results[prior_idx]
                        prior_verdict = prior_cp.get("verdict") or prior_cp.get("status")
                        for k in ("verdict", "status", "confidence", "reasoning", "evidence"):
                            if k in new_cp:
                                prior_cp[k] = new_cp[k]
                        # Normalize: if the rerun only set `status`, propagate
                        # it into `verdict` so downstream readers don't need
                        # both code paths.
                        if "verdict" not in new_cp and "status" in new_cp:
                            prior_cp["verdict"] = new_cp["status"]
                        updated_checkpoint = prior_cp
                        call.checkpoint_results = json.dumps(results)

                    new_verdict = new_cp.get("verdict") or new_cp.get("status")
                    verdict_changed = (
                        new_verdict is not None and new_verdict != prior_verdict
                    )
                    edit.reanalysis_changed_verdict = verdict_changed

                    if verdict_changed:
                        # Demote the prior is_current row FIRST to avoid a
                        # transient (new_row, prior_row) both being
                        # is_current=True during flush. Mirrors the pattern in
                        # submit_verdict.
                        prior_row = (
                            db.query(VerdictHistory)
                            .filter_by(
                                call_id=call_id,
                                checkpoint_id=payload.checkpoint_id,
                                is_current=True,
                            )
                            .first()
                        )
                        if prior_row:
                            prior_row.is_current = False
                        db.add(VerdictHistory(
                            id=str(uuid.uuid4()),
                            call_id=call_id,
                            checkpoint_id=payload.checkpoint_id,
                            review_session_id=rs.id if rs else None,
                            actor_type="ai",
                            actor_id="agent",
                            verdict=new_verdict,
                            reasoning=(
                                f"Re-analysis after word edit: "
                                f"{payload.old_text}\u2192{payload.new_text}"
                            ),
                            confidence=_normalize_confidence(new_cp.get("confidence")),
                            # Task 32: this is a fresh AI call against the live
                            # prompt, so stamp with the current supplier version
                            # (not whatever the prior row had — the prompt may
                            # have changed since the original analysis).
                            prompt_version=version_for_supplier(call.detected_supplier),
                            is_current=True,
                        ))

    if rs:
        rs.last_activity_at = utcnow()

    # Task 33: bump revision — word_data (and possibly checkpoint_results)
    # mutated, so cached callers must refetch.
    call.revision = (call.revision or 1) + 1

    db.commit()
    return {
        "saved": True,
        "edit_id": edit.id,
        "verdict_changed": verdict_changed,
        "new_verdict": new_verdict,
        "checkpoint": updated_checkpoint,
    }


# ─── POST /api/calls/{id}/compliance ───────────────────────────────────────
#
# Reviewer (or lead) makes the final pass/fail call on compliance. This is
# the authoritative override path: the pipeline populates
# `call.compliance_status` automatically after checkpoint analysis (Task 9),
# but a human can confirm or flip that verdict here.
#
# A comment is required on disagreement ("I'm overriding the system, here's
# why") but NOT on agreement ("I reviewed and the system was right") — the
# confirmation row is still written either way so the audit trail captures
# that a human looked at it.
#
# Side effects beyond the decision row itself:
#   - Call mirror fields (compliance_status/source/decided_at/decided_by/
#     comment) are updated for fast queries.
#   - review_status flips to "reviewed" (terminal); reviewed_at/reviewed_by
#     set.
#   - Any ClaimLock on the call is released, its ReviewSession marked
#     inactive with release_reason="submitted" — unlike "abandoned" or
#     "idle_timeout", this records WHY the claim ended (the reviewer
#     finished).
# Leads can submit without first claiming — they have override power.


class CompliancePayload(BaseModel):
    status: str  # "compliant" | "non_compliant"
    comment: str | None = None


@hitl_router.post("/api/calls/{call_id}/compliance")
def submit_compliance(
    call_id: str,
    payload: CompliancePayload,
    request: Request,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    """Record a reviewer's final compliance decision for a call.

    Flow:
      1. 400 if `status` isn't one of the two allowed values.
      2. 404 if the call doesn't exist.
      3. 422 if the reviewer is flipping the verdict without a comment.
      4. Demote any prior is_current ComplianceDecision row → insert a new
         is_current row with actor_type=reviewer["role"].
      5. Mirror the reviewer's choice into Call's compliance_* + review_*
         fields.
      6. If a ClaimLock exists, delete it and mark its ReviewSession
         released with reason="submitted".
    """
    if payload.status not in ("compliant", "non_compliant"):
        raise HTTPException(
            status_code=400,
            detail="status must be 'compliant' or 'non_compliant'",
        )

    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    # Task 33: gate on If-Match BEFORE any state changes.
    _check_if_match(request, call)

    prior_status = call.compliance_status
    has_comment = bool(payload.comment and payload.comment.strip())
    if payload.status != prior_status and not has_comment:
        raise HTTPException(
            status_code=422,
            detail=(
                "A comment is required when overriding the existing "
                "compliance verdict"
            ),
        )

    now = utcnow()

    # Demote prior is_current row (flip first so we never transiently have
    # two is_current=True rows for the same call).
    prior_cd = (
        db.query(ComplianceDecision)
        .filter_by(call_id=call_id, is_current=True)
        .first()
    )
    if prior_cd is not None:
        prior_cd.is_current = False

    new_cd = ComplianceDecision(
        id=str(uuid.uuid4()),
        call_id=call_id,
        status=payload.status,
        actor_type=reviewer["role"],  # "reviewer" or "lead"
        actor_id=reviewer["id"],
        comment=payload.comment,
        is_current=True,
    )
    db.add(new_cd)

    # Mirror into the Call for fast queries (the audit trail is the
    # ComplianceDecision rows; the Call fields are a denormalized cache).
    call.compliance_status = payload.status
    call.compliance_source = reviewer["role"]
    call.compliance_decided_at = now
    call.compliance_decided_by = reviewer["id"]
    call.compliance_comment = payload.comment
    call.review_status = "reviewed"
    call.reviewed_at = now
    call.reviewed_by = reviewer["id"]
    # AI/HUMAN provenance gate. The compliance verdict is the canonical
    # human review touchpoint at the Call level, so flipping the call out
    # of AI_PENDING here keeps Call.verdict_state in sync with whether a
    # reviewer has actually weighed in. Use HUMAN_CONFIRMED for "auto"
    # source agreements (matches AI), HUMAN_OVERRIDDEN otherwise.
    call.verdict_state = (
        "HUMAN_CONFIRMED" if payload.status == prior_status else "HUMAN_OVERRIDDEN"
    )

    # Release any claim on this call. Leads who submit without claiming
    # won't have a lock to release — short-circuit on None.
    lock = db.query(ClaimLock).filter_by(call_id=call_id).first()
    if lock is not None:
        rs = db.query(ReviewSession).filter_by(id=lock.review_session_id).first()
        if rs is not None:
            rs.is_active = False
            rs.released_at = now
            rs.release_reason = "submitted"
        db.delete(lock)

    # Task 33: bump revision — compliance_* + review_status flipped.
    call.revision = (call.revision or 1) + 1

    db.commit()

    # Task 30: emit compliance event for downstream listeners.
    emit(db, "compliance.decided", {
        "call_id": call_id,
        "status": payload.status,
        "actor_id": reviewer["id"],
    })

    return {"saved": True, "compliance_status": payload.status}


# ─── GET /api/compliant and GET /api/non-compliant ─────────────────────────
#
# Two twin list endpoints that power the reviewer "completed" tabs. Both are
# thin paginated views over `calls` filtered by `compliance_status`. Optional
# supplier + agent filters are exact-match (good enough until we need fuzzy
# search) and results are always ordered newest-first by created_at so the UI
# shows the most recent work at the top.


def _list_calls_by_status(
    status: str,
    supplier: str | None,
    agent: str | None,
    limit: int,
    offset: int,
    db: Session,
) -> dict:
    """Shared query path for /api/compliant and /api/non-compliant.

    Returns `{total, calls}` where `total` is the filter-aware count (for the
    paginator) and `calls` is the requested page of rows as plain dicts.

    Projection is important here: the Call row includes transcript (~12 KB),
    word_data (~200 KB on a 10-min call), checkpoint_results JSON, and — since
    migration f1a2b3c4d5e6 — five large provider-metadata JSONB columns. A full
    `db.query(Call).all()` on three rows moved ~2 MB over the Supabase pooler
    and pushed the endpoint to ~76 s end-to-end, which rendered as an infinite
    "Loading…" in the UI. Select only the summary fields the list needs.
    """
    base_filter = [Call.compliance_status == status]
    # AI/HUMAN provenance gate: only show calls a human has touched. Calls
    # still in AI_PENDING are reviewer queue inventory, not part of the
    # confirmed compliant/non-compliant tally.
    base_filter.append(Call.verdict_state.in_(["HUMAN_CONFIRMED", "HUMAN_OVERRIDDEN"]))
    if supplier:
        base_filter.append(Call.detected_supplier == supplier)
    if agent:
        base_filter.append(Call.agent_name == agent)

    total = db.query(func.count(Call.id)).filter(*base_filter).scalar() or 0

    rows = (
        db.query(
            Call.id,
            Call.filename,
            Call.detected_supplier,
            Call.agent_name,
            Call.duration_seconds,
            Call.created_at,
            Call.reviewed_by,
            Call.reviewed_at,
            Call.compliance_source,
            Call.compliance_comment,
            Call.call_ref,
            Call.slug,
            Call.score,
        )
        .filter(*base_filter)
        .order_by(Call.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "calls": [
            {
                "id": r.id,
                "filename": r.filename,
                "supplier": r.detected_supplier,
                "agent_name": r.agent_name,
                "duration": r.duration_seconds,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "reviewed_by": r.reviewed_by,
                "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                "source": r.compliance_source,
                "comment": r.compliance_comment,
                "call_ref": r.call_ref,
                "slug": r.slug,
                "score": r.score,
            }
            for r in rows
        ],
    }


@hitl_router.get("/api/compliant")
def list_compliant(
    supplier: str | None = None,
    agent: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    """Paginated list of calls with compliance_status == "compliant"."""
    return _list_calls_by_status("compliant", supplier, agent, limit, offset, db)


@hitl_router.get("/api/non-compliant")
def list_non_compliant(
    supplier: str | None = None,
    agent: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    """Paginated list of calls with compliance_status == "non_compliant"."""
    return _list_calls_by_status("non_compliant", supplier, agent, limit, offset, db)


# ─── GET /api/queue ────────────────────────────────────────────────────────
#
# The reviewer "inbox". Returns two things: (1) a metrics strip showing
# backlog size, today's throughput, avg turnaround, and a top-5 leaderboard
# so leads can see who's doing what; (2) up to 100 calls matching the
# selected filter. Name resolution for claimed_by / reviewed_by goes through
# the `profiles` table — we never surface raw UUIDs to the UI.
#
# The default filter ("all") intentionally includes calls across three
# states (pending, in_review, reviewed_today) because that's what a reviewer
# opening the app first wants to see: "what's waiting, what's being worked,
# and what I finished today." Dedicated filters give each state in isolation.


@hitl_router.get("/api/queue")
def get_queue(
    filter: str = Query("all", pattern="^(all|unclaimed|in_review|reviewed_today)$"),
    meta_filter: str | None = Query(None),
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    """Reviewer inbox: metrics strip + up to 100 calls, filtered by state.

    Filters:
      - `all` (default): pending OR in_review OR reviewed-today.
      - `unclaimed`: unclaimed + pending.
      - `in_review`: mid-review.
      - `reviewed_today`: completed since midnight.

    Metrics are computed over the whole table regardless of the filter, so
    the header doesn't lie when you narrow the view.
    """
    now = utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_ago = now - timedelta(hours=24)

    # Resolve id→name once. Profile count stays small (one row per reviewer),
    # so loading the whole table is cheaper than a per-row join.
    name_map = {p.id: p.name for p in db.query(Profile).all()}

    # ─── Metrics ─────────────────────────────────────────────────────────
    # Backlog = anything the reviewer still needs to sign off. That includes
    # calls the AI flagged non-compliant ("agent failed X") and calls still
    # mid-pipeline. A reviewer-signed-off call leaves the queue when
    # review_status flips to "reviewed".
    #
    # 2026-05-16 audit Bug 4 fix: backlog must match the Pending tab's actual
    # list filter (review_status == "unclaimed"). The prior `!= "reviewed"`
    # predicate counted in-review (claimed) calls too, so the badge showed
    # N while the list showed N-claimed. Separate in_review metric below
    # tracks claimed-but-not-submitted.
    backlog = (
        db.query(Call)
        .filter(
            Call.review_status == "unclaimed",
            Call.compliance_status.in_(("pending", "non_compliant")),
        )
        .count()
    )

    in_review = (
        db.query(Call)
        .filter(Call.review_status == "in_review")
        .count()
    )

    # Mean (reviewed_at - created_at) in whole minutes, last 24h. Python-side
    # computation keeps SQLite + Postgres identical (neither has a clean
    # cross-dialect "minutes between" aggregate).
    tt_rows = (
        db.query(Call.created_at, Call.reviewed_at)
        .filter(
            Call.review_status == "reviewed",
            Call.reviewed_at >= day_ago,
            Call.created_at.isnot(None),
        )
        .all()
    )
    avg_min = (
        int(sum((r[1] - r[0]).total_seconds() for r in tt_rows) / len(tt_rows) / 60)
        if tt_rows else 0
    )

    reviewed_today = (
        db.query(VerdictHistory)
        .filter(
            VerdictHistory.actor_type.in_(("reviewer", "lead", "admin")),
            VerdictHistory.is_current.is_(True),
            VerdictHistory.created_at >= today,
        )
        .count()
    )

    leaderboard_rows = (
        db.query(VerdictHistory.actor_id, func.count(VerdictHistory.id).label("n"))
        .filter(
            VerdictHistory.actor_type.in_(("reviewer", "lead", "admin")),
            VerdictHistory.is_current.is_(True),
            VerdictHistory.created_at >= day_ago,
        )
        .group_by(VerdictHistory.actor_id)
        .order_by(func.count(VerdictHistory.id).desc())
        .limit(5)
        .all()
    )
    leaderboard = [
        {"reviewer_id": rid, "name": name_map.get(rid, rid), "count": int(n)}
        for rid, n in leaderboard_rows
    ]

    # ─── Call list ───────────────────────────────────────────────────────
    # "Pending" surface = anything not signed off by a reviewer that the AI
    # already produced a verdict for OR is still finishing. Non-compliant
    # AI verdicts are explicitly part of this set — they are the reviewer's
    # core workload. (Pre-2026-05-10 the queue only included
    # compliance_status == 'pending', so a non-compliant call dropped out
    # before a human could see it. Bug B3 in audit-late.)
    q = db.query(Call)
    if filter == "unclaimed":
        q = q.filter(
            Call.review_status == "unclaimed",
            Call.compliance_status.in_(("pending", "non_compliant")),
        )
    elif filter == "in_review":
        q = q.filter(Call.review_status == "in_review")
    elif filter == "reviewed_today":
        q = q.filter(
            Call.review_status == "reviewed",
            Call.reviewed_at >= today,
        )
    else:  # "all"
        q = q.filter(
            (
                (Call.review_status != "reviewed")
                & Call.compliance_status.in_(("pending", "non_compliant"))
            )
            | (Call.review_status == "in_review")
            | ((Call.review_status == "reviewed") & (Call.reviewed_at >= today))
        )

    # Task 37: arbitrary JSONB meta filters. Format: "key:val,key2:val2".
    if meta_filter:
        for kv in meta_filter.split(","):
            parts = kv.strip().split(":", 1)
            if len(parts) == 2:
                k, v = parts
                q = q.filter(Call.meta[k].astext == v)

    rows = q.order_by(Call.created_at.desc()).limit(100).all()
    # Preload claim locks in one query so _row is O(1) per call.
    claims = {cl.call_id: cl for cl in db.query(ClaimLock).all()}

    # Preload per-segment summaries for the visible rows so the queue table
    # can show "Lead Gen · Verbal · LOA" without N+1 queries (Plan §5a).
    from app.models import CallSegment as _CallSegment
    row_ids = [c.id for c in rows]
    seg_rows = (
        db.query(_CallSegment)
        .filter(_CallSegment.call_id.in_(row_ids))
        .order_by(_CallSegment.call_id, _CallSegment.idx)
        .all()
        if row_ids
        else []
    )
    seg_by_call: dict[str, list[dict]] = {}
    for s in seg_rows:
        seg_by_call.setdefault(s.call_id, []).append(
            {
                "stage": s.stage,
                "score": s.score,
                "bucket": s.bucket,
            }
        )

    def _row(c: Call) -> dict:
        # flagged_count counts BOTH legacy `needs_review=True` (low-confidence
        # from Task 9 auto-compliance) AND explicit `verdict|status=flagged`
        # from newer analyses. Either signal means "a human should look".
        try:
            cps = json.loads(c.checkpoint_results or "[]")
        except (TypeError, ValueError):
            cps = []
        flagged = sum(
            1
            for cp in cps
            if cp.get("needs_review")
            or (cp.get("verdict") or cp.get("status")) == "flagged"
        )
        cl = claims.get(c.id)
        return {
            "id": c.id,
            "filename": c.filename,
            # audit-late: surface customer + agent on the queue row so the
            # /queue master table doesn't have to fall back to filenames
            # (which the upload pipeline prefixes with the supplier script).
            "customer_name": c.customer_name,
            "agent_name": c.agent_name,
            "score": c.score,
            "supplier": c.detected_supplier,
            "duration": c.duration_seconds,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "review_status": c.review_status,
            "compliance_status": c.compliance_status,
            "bucket": getattr(c, "bucket", None),
            "flagged_count": flagged,
            "claimed_by": name_map.get(cl.reviewer_id) if cl else None,
            "reviewed_by": name_map.get(c.reviewed_by) if c.reviewed_by else None,
            "reviewed_at": c.reviewed_at.isoformat() if c.reviewed_at else None,
            # Plan §5a: drive the "Lead Gen · Verbal · LOA" segments column
            # off the new CallSegment rows the per-segment pipeline writes.
            "segments": seg_by_call.get(c.id, []),
        }

    return {
        "metrics": {
            "backlog": backlog,
            "in_review": in_review,
            "avg_turnaround_min": avg_min,
            "reviewed_today": reviewed_today,
            "leaderboard": leaderboard,
        },
        "calls": [_row(c) for c in rows],
    }


# ─── POST /api/calls/{id}/draft ────────────────────────────────────────────
#
# Autosave for in-progress reviews. The frontend debounces 10s on checkpoint
# / comment state changes and hits this endpoint so a crash, refresh, or
# accidental tab close doesn't lose the reviewer's work.
#
# The endpoint is deliberately dumb: it takes an opaque JSON blob (checkpoints
# + comment + arbitrary notes dict), serializes it, and stores it on
# Call.draft_snapshot. We do NOT merge into any other table — VerdictHistory
# is the audit log, draft_snapshot is scratchpad. On reopen the frontend
# hydrates the review form from this blob iff review_status == "draft".
#
# review_status handling: flip "in_review" → "draft" so the queue can show
# distinct "someone started + walked away" versus "actively held" states. We
# do NOT trample terminal states like "reviewed" — autosave after final
# submission is a no-op on the status field (but we still persist the snapshot
# so nothing is lost).


class DraftPayload(BaseModel):
    # A permissive blob — the pydantic model only exists so we can call
    # `model_dump()` for a clean JSON serialization. Structure matches what
    # the review UI holds client-side.
    checkpoints: list[dict] = []
    comment: str | None = None
    notes: dict | None = None


@hitl_router.post("/api/calls/{call_id}/draft")
def save_draft(
    call_id: str,
    payload: DraftPayload,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    """Persist the reviewer's in-progress draft so they can resume on reopen.

    Flow:
      1. 404 if the call doesn't exist.
      2. Serialize the payload to JSON and store on Call.draft_snapshot.
      3. Stamp Call.draft_saved_at = now.
      4. Flip Call.review_status "in_review" → "draft" (keep draft state
         distinct from actively-held so queue filters can separate them).
         Do NOT overwrite terminal states.
      5. Bump the reviewer's active ReviewSession.last_activity_at so the
         idle-timeout sweep doesn't reclaim the call mid-autosave.

    Task 33 note: drafts deliberately skip BOTH the If-Match check AND the
    revision bump. The frontend autosaves every 10s while a reviewer types,
    so enforcing the lock here would produce a 409 storm the moment any
    other endpoint (claim/release/verdict) bumps revision behind the scenes.
    Bumping would be equally bad — every keystroke-debounce would force every
    other open client to refetch. Drafts are reviewer-local scratchpad; the
    audit log lives in verdict_history, not here.
    """
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    # Ownership check: caller must hold the active ClaimLock or be lead/admin.
    is_privileged = reviewer["role"] in ("lead", "admin")
    if not is_privileged:
        lock = db.query(ClaimLock).filter_by(call_id=call_id).first()
        if lock and lock.reviewer_id != reviewer["id"]:
            raise HTTPException(
                status_code=403,
                detail="Another reviewer holds this call",
            )

    now = utcnow()
    call.draft_snapshot = json.dumps(payload.model_dump())
    call.draft_saved_at = now

    # Only flip from the "actively held" state. Terminal states (reviewed,
    # draft already, etc.) stay put — we still persist the snapshot so a
    # reviewer who reopened a reviewed call and typed notes doesn't lose
    # them, but we won't downgrade the queue-visible status.
    if call.review_status == "in_review":
        call.review_status = "draft"

    rs = (
        db.query(ReviewSession)
        .filter_by(call_id=call_id, reviewer_id=reviewer["id"], is_active=True)
        .order_by(ReviewSession.claimed_at.desc())
        .first()
    )
    if rs:
        rs.last_activity_at = now

    db.commit()
    return {"saved_at": now.isoformat()}


# ─── POST /api/internal/release-idle-claims ────────────────────────────────
#
# Background-task target + manual "sweep" endpoint. The core is a pure
# function over a Session so the lifespan-owned periodic loop in main.py can
# call it directly without going through the HTTP stack. The endpoint wraps
# the helper behind the standard reviewer auth so any authenticated user can
# force a sweep (useful when debugging or when someone wants to re-queue a
# stuck call fast).


def _release_idle_claims_core(db: Session) -> int:
    """Sweep expired ClaimLock rows. Returns count of locks released.

    For each expired lock:
      - Mark its ReviewSession inactive with release_reason="idle_timeout"
        (only if it's still active — don't overwrite a different release
        reason set elsewhere).
      - Flip the owning Call back to "unclaimed" IFF it was "in_review".
        Terminal states like "reviewed" are preserved — a stale lock on a
        completed call is a data anomaly, not a reason to un-complete it.
      - Delete the lock row.

    Idempotent + re-entrant: safe to call from both the cron task and the
    HTTP handler. Commits once at the end so a DB error leaves everything
    untouched.
    """
    now = utcnow()
    expired = db.query(ClaimLock).filter(ClaimLock.expires_at <= now).all()
    released = 0
    for lock in expired:
        rs = db.query(ReviewSession).filter_by(id=lock.review_session_id).first()
        if rs is not None and rs.is_active:
            rs.is_active = False
            rs.released_at = now
            rs.release_reason = "idle_timeout"
        call = db.query(Call).filter_by(id=lock.call_id).first()
        if call is not None and call.review_status == "in_review":
            call.review_status = "unclaimed"
            call.revision = (call.revision or 1) + 1
        db.delete(lock)
        released += 1
    db.commit()
    return released


@hitl_router.post("/api/internal/release-idle-claims")
def release_idle_claims(
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    """Manually trigger the idle-claim sweep. Any authenticated reviewer/lead
    can call this; the background task in main.py runs it every 120s."""
    count = _release_idle_claims_core(db)
    return {"released": count}


# ─── GET /api/calls/{id}/agent-trace ───────────────────────────────────────
#
# Expose the persisted chain-of-thought for an agent run. The agent loop
# writes one AgentTrace row per turn (user prompt, assistant message, tool
# call, tool result) — this endpoint hands the reviewer UI the ordered list
# so they can expand "Show AI reasoning" on a call or a single checkpoint.
#
# Ordering is (run_id, turn) asc: each run stays grouped, turns inside a
# run stay in execution order. That matches how a human reads a conversation
# transcript.


@hitl_router.get("/api/calls/{call_id}/agent-trace")
def get_agent_trace(
    call_id: str,
    checkpoint_id: str | None = None,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    """Return the agent's persisted reasoning trace for a call.

    - 404 if the call doesn't exist.
    - Optional `checkpoint_id` filter narrows to rows recorded for that
      specific checkpoint (useful when the UI expands trace per-cp).
    - Response: `{"trace": [dict per row]}` ordered by (run_id, turn) asc.
    """
    if not db.query(Call).filter_by(id=call_id).first():
        raise HTTPException(status_code=404, detail="Call not found")

    q = db.query(AgentTrace).filter(AgentTrace.call_id == call_id)
    if checkpoint_id:
        q = q.filter(AgentTrace.checkpoint_id == checkpoint_id)
    rows = q.order_by(AgentTrace.run_id.asc(), AgentTrace.turn.asc()).all()

    def _row(t: AgentTrace) -> dict:
        return {
            "id": t.id,
            "run_id": t.run_id,
            "turn": t.turn,
            "role": t.role,
            "tool_name": t.tool_name,
            "tool_input": t.tool_input,
            "tool_output": t.tool_output,
            "content": t.content,
            "model": t.model,
            "latency_ms": t.latency_ms,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }

    return {"trace": [_row(t) for t in rows]}


# ── Task 23: Inline guidelines per checkpoint ─────────────────────
#
# Returns the script excerpt + strictness + past reviewer learnings for a
# checkpoint so the UI can show "How to judge this" inline. Learnings come
# from agent_learnings (populated by verdict feedback in Task 6).


@hitl_router.get("/api/calls/{call_id}/checkpoint-guidelines")
def checkpoint_guidelines(
    call_id: str,
    checkpoint_name: str = Query(...),
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    supplier = call.detected_supplier or ""
    script_excerpt = ""
    strictness = "meaning_for_meaning"

    if call.script_id:
        script = db.query(Script).filter_by(id=call.script_id).first()
        if script:
            strictness = script.mode or "meaning_for_meaning"
            try:
                cps = json.loads(script.checkpoints or "[]")
                for cp in cps:
                    if cp.get("name") == checkpoint_name:
                        script_excerpt = cp.get("required", "")
                        if cp.get("strictness"):
                            strictness = cp["strictness"]
                        break
            except (json.JSONDecodeError, TypeError):
                pass

    learnings = (
        db.query(AgentLearning)
        .filter(
            AgentLearning.checkpoint_name == checkpoint_name,
            AgentLearning.supplier == supplier,
        )
        .order_by(AgentLearning.created_at.desc())
        .limit(5)
        .all()
    )

    logger.info(
        "checkpoint_guidelines call_id=%s checkpoint=%s learnings=%d",
        call_id,
        checkpoint_name,
        len(learnings),
    )

    return {
        "checkpoint_name": checkpoint_name,
        "script_excerpt": script_excerpt,
        "strictness": strictness,
        "examples": [
            {
                "pattern": l.pattern,
                "agent_verdict": l.agent_verdict,
                "human_verdict": l.human_verdict,
                "lesson": l.lesson,
            }
            for l in learnings
        ],
    }


# ── Task 34: Generic /api/predict endpoint ────────────────────────
#
# Re-analysis primitive for bulk re-runs, prompt A/B tests, evaluations.
# Lead-only. Optionally persists results into checkpoint_results or returns
# dry-run output (persist=false).


class PredictPayload(BaseModel):
    call_id: str
    checkpoint_ids: list[str] | None = None
    persist: bool = True


@hitl_router.post("/api/predict")
async def predict(
    payload: PredictPayload,
    lead: dict = Depends(require_lead),
    db: Session = Depends(get_db),
):
    call = db.query(Call).filter_by(id=payload.call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    script = (
        db.query(Script).filter_by(id=call.script_id).first()
        if call.script_id
        else None
    )
    if not script:
        raise HTTPException(status_code=400, detail="Call has no linked script")

    try:
        script_checkpoints = json.loads(script.checkpoints or "[]")
    except (TypeError, ValueError):
        script_checkpoints = []

    if payload.checkpoint_ids:
        targets = [
            cp
            for cp in script_checkpoints
            if cp.get("name") in payload.checkpoint_ids
        ]
        if not targets:
            raise HTTPException(status_code=400, detail="No matching checkpoints found")
    else:
        targets = script_checkpoints

    transcript = call.assemblyai_transcript or call.transcript or ""
    words = json.loads(call.word_data or "[]") if call.word_data else None

    result = await analyze_all_checkpoints(
        transcript=transcript,
        checkpoints=targets,
        script_mode=script.mode or "meaning_for_meaning",
        supplier=call.detected_supplier or "Unknown",
        word_data=words,
    )
    results_list = result.get("results", []) if isinstance(result, dict) else []

    if payload.persist and results_list:
        try:
            existing = json.loads(call.checkpoint_results or "[]")
        except (TypeError, ValueError):
            existing = []

        result_names = {r.get("name") for r in results_list}
        merged = [cp for cp in existing if cp.get("name") not in result_names]
        merged.extend(results_list)
        call.checkpoint_results = json.dumps(merged)
        db.commit()

    logger.info(
        "predict call_id=%s checkpoints=%d persist=%s actor=%s",
        payload.call_id,
        len(results_list),
        payload.persist,
        lead["id"],
    )

    return {
        "call_id": payload.call_id,
        "persisted": payload.persist and bool(results_list),
        "results": results_list,
    }


# ── Task 36: Structured fine-tuning export ────────────────────────
#
# JSONL export of human-overridden verdicts paired with the prompt the AI
# originally saw. Each line is a {prompt, completion, meta} object ready
# for fine-tuning or evaluation datasets.


@hitl_router.get("/api/exports/verdict-overrides")
def export_verdict_overrides(
    lead: dict = Depends(require_lead),
    db: Session = Depends(get_db),
):
    from fastapi.responses import StreamingResponse

    def _stream():
        rows = (
            db.query(VerdictHistory)
            .filter(
                VerdictHistory.actor_type == "reviewer",
                VerdictHistory.is_current.is_(True),
            )
            .order_by(VerdictHistory.created_at.asc())
            .all()
        )
        for row in rows:
            call = db.query(Call).filter_by(id=row.call_id).first()
            if not call:
                continue

            transcript_excerpt = ""
            if call.checkpoint_results:
                try:
                    cps = json.loads(call.checkpoint_results)
                    match = next(
                        (c for c in cps if c.get("name") == row.checkpoint_id),
                        None,
                    )
                    if match:
                        transcript_excerpt = match.get("evidence", "")
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass

            entry = {
                "prompt": (
                    f"Supplier: {call.detected_supplier or 'Unknown'}\n"
                    f"Checkpoint: {row.checkpoint_id}\n"
                    f"Transcript excerpt: {transcript_excerpt}"
                ),
                "completion": (
                    f"{row.verdict}\n{row.reasoning or ''}"
                ).strip(),
                "meta": {
                    "call_id": row.call_id,
                    "supplier": call.detected_supplier,
                    "checkpoint_id": row.checkpoint_id,
                    "created_at": (
                        row.created_at.isoformat() if row.created_at else None
                    ),
                    "reviewer_id": row.actor_id,
                    "prompt_version": row.prompt_version,
                },
            }
            yield json.dumps(entry) + "\n"

    logger.info("export_verdict_overrides actor=%s", lead["id"])

    return StreamingResponse(
        _stream(),
        media_type="application/jsonl",
        headers={
            "Content-Disposition": "attachment; filename=verdict-overrides.jsonl"
        },
    )


# ── Task 26: Saved view CRUD ─────────────────────────────────────
#
# Reviewers save their frequent filter combos (supplier, status, meta keys)
# and restore them with one click. Shared views are visible to everyone.


class SavedViewPayload(BaseModel):
    name: str
    filters: dict
    is_shared: bool = False


@hitl_router.post("/api/views")
def create_view(
    payload: SavedViewPayload,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    view = SavedView(
        id=str(uuid.uuid4()),
        owner_id=reviewer["id"],
        name=payload.name,
        filters=json.dumps(payload.filters),
        is_shared=payload.is_shared,
    )
    db.add(view)
    db.commit()

    logger.info("create_view id=%s name=%s actor=%s", view.id, payload.name, reviewer["id"])

    return {
        "id": view.id,
        "name": view.name,
        "filters": payload.filters,
        "is_shared": view.is_shared,
        "created_at": view.created_at.isoformat() if view.created_at else None,
    }


@hitl_router.get("/api/views")
def list_views(
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(SavedView)
        .filter(
            (SavedView.owner_id == reviewer["id"]) | (SavedView.is_shared.is_(True))
        )
        .order_by(SavedView.created_at.desc())
        .all()
    )

    def _row(v: SavedView) -> dict:
        try:
            filters = json.loads(v.filters)
        except (TypeError, ValueError):
            filters = {}
        return {
            "id": v.id,
            "name": v.name,
            "filters": filters,
            "is_shared": v.is_shared,
            "owner_id": v.owner_id,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }

    return {"views": [_row(v) for v in rows]}


@hitl_router.delete("/api/views/{view_id}")
def delete_view(
    view_id: str,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    view = db.query(SavedView).filter_by(id=view_id).first()
    if not view:
        raise HTTPException(status_code=404, detail="View not found")
    if view.owner_id != reviewer["id"]:
        raise HTTPException(status_code=403, detail="Not your view")
    db.delete(view)
    db.commit()

    logger.info("delete_view id=%s actor=%s", view_id, reviewer["id"])

    return {"deleted": True}


# ── Task 35: Trace annotations (score + comment per step) ────────


class TraceAnnotationPayload(BaseModel):
    trace_id: str
    score: int  # -1, 0, +1
    comment: str | None = None


@hitl_router.post("/api/trace-annotations")
def create_trace_annotation(
    payload: TraceAnnotationPayload,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    trace = db.query(AgentTrace).filter_by(id=payload.trace_id).first()
    if not trace:
        raise HTTPException(status_code=404, detail="Trace step not found")
    if payload.score not in (-1, 0, 1):
        raise HTTPException(status_code=400, detail="score must be -1, 0, or 1")

    existing = (
        db.query(TraceAnnotation)
        .filter_by(trace_id=payload.trace_id, actor_id=reviewer["id"])
        .first()
    )
    if existing:
        existing.score = payload.score
        existing.comment = payload.comment
        db.commit()
        logger.info("trace_annotation updated id=%s", existing.id)
        return {"id": existing.id, "updated": True}

    ann = TraceAnnotation(
        trace_id=payload.trace_id,
        actor_id=reviewer["id"],
        score=payload.score,
        comment=payload.comment,
    )
    db.add(ann)
    db.commit()

    logger.info("trace_annotation created id=%s trace=%s", ann.id, payload.trace_id)

    return {"id": ann.id, "updated": False}


@hitl_router.get("/api/calls/{call_id}/trace-annotations")
def list_trace_annotations(
    call_id: str,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    trace_ids = [
        t.id
        for t in db.query(AgentTrace.id).filter(AgentTrace.call_id == call_id).all()
    ]
    if not trace_ids:
        return {"annotations": []}

    rows = (
        db.query(TraceAnnotation)
        .filter(TraceAnnotation.trace_id.in_(trace_ids))
        .order_by(TraceAnnotation.created_at.asc())
        .all()
    )
    return {
        "annotations": [
            {
                "id": a.id,
                "trace_id": a.trace_id,
                "actor_id": a.actor_id,
                "score": a.score,
                "comment": a.comment,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ]
    }


# ── Task 25: Double-review / inter-annotator agreement ───────────


@hitl_router.get("/api/calls/{call_id}/agreement")
def get_agreement(
    call_id: str,
    lead: dict = Depends(require_lead),
    db: Session = Depends(get_db),
):
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    rows = (
        db.query(VerdictHistory)
        .filter_by(call_id=call_id, is_current=True)
        .filter(VerdictHistory.actor_type == "reviewer")
        .all()
    )
    by_cp: dict[str, list] = {}
    for r in rows:
        by_cp.setdefault(r.checkpoint_id, []).append(
            {"reviewer": r.actor_id, "verdict": r.verdict}
        )

    name_map = {p.id: p.name for p in db.query(Profile).all()}

    return {
        "call_id": call_id,
        "required_reviews": call.required_reviews,
        "completed_reviews": call.completed_reviews,
        "checkpoints": [
            {
                "checkpoint_id": k,
                "verdicts": [
                    {**v, "reviewer_name": name_map.get(v["reviewer"], v["reviewer"])}
                    for v in vlist
                ],
                "agreed": len({x["verdict"] for x in vlist}) == 1,
            }
            for k, vlist in by_cp.items()
        ],
    }


@hitl_router.post("/api/calls/{call_id}/require-double-review")
def set_double_review(
    call_id: str,
    lead: dict = Depends(require_lead),
    db: Session = Depends(get_db),
):
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    call.required_reviews = 2
    db.commit()

    logger.info("double_review enabled call_id=%s actor=%s", call_id, lead["id"])

    return {"call_id": call_id, "required_reviews": 2}


# ── Task 27: Prompt playground (lead-only, non-persistent) ───────


class PlaygroundPayload(BaseModel):
    call_id: str
    checkpoint_ids: list[str] | None = None
    prompt_override: str | None = None


@hitl_router.post("/api/playground/run")
async def playground_run(
    payload: PlaygroundPayload,
    lead: dict = Depends(require_lead),
    db: Session = Depends(get_db),
):
    call = db.query(Call).filter_by(id=payload.call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    script = (
        db.query(Script).filter_by(id=call.script_id).first()
        if call.script_id
        else None
    )
    if not script:
        raise HTTPException(status_code=400, detail="Call has no linked script")

    try:
        script_checkpoints = json.loads(script.checkpoints or "[]")
    except (TypeError, ValueError):
        script_checkpoints = []

    if payload.checkpoint_ids:
        targets = [
            cp for cp in script_checkpoints
            if cp.get("name") in payload.checkpoint_ids
        ]
        if not targets:
            raise HTTPException(status_code=400, detail="No matching checkpoints")
    else:
        targets = script_checkpoints

    transcript = call.assemblyai_transcript or call.transcript or ""
    words = json.loads(call.word_data or "[]") if call.word_data else None

    result = await analyze_all_checkpoints(
        transcript=transcript,
        checkpoints=targets,
        script_mode=script.mode or "meaning_for_meaning",
        supplier=call.detected_supplier or "Unknown",
        word_data=words,
    )
    results_list = result.get("results", []) if isinstance(result, dict) else []

    logger.info(
        "playground_run call_id=%s checkpoints=%d actor=%s",
        payload.call_id,
        len(results_list),
        lead["id"],
    )

    return {
        "call_id": payload.call_id,
        "persisted": False,
        "prompt_override_used": payload.prompt_override is not None,
        "results": results_list,
    }


# ── Task 28: Suggestion vs response split — query endpoints ──────
#
# Read-only endpoints over the new verdict_suggestions / verdict_responses
# tables. The existing VerdictHistory write path is untouched — the pipeline
# and /verdict endpoint continue writing VerdictHistory. These tables are
# populated in parallel and provide the clean "AI said X, human said Y,
# did they agree?" queryability that VerdictHistory's mutable-blob design
# can't deliver.


@hitl_router.get("/api/calls/{call_id}/suggestions")
def list_suggestions(
    call_id: str,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    if not db.query(Call).filter_by(id=call_id).first():
        raise HTTPException(status_code=404, detail="Call not found")

    rows = (
        db.query(VerdictSuggestion)
        .filter_by(call_id=call_id)
        .order_by(VerdictSuggestion.created_at.asc())
        .all()
    )
    return {
        "suggestions": [
            {
                "id": s.id,
                "checkpoint_id": s.checkpoint_id,
                "verdict": s.verdict,
                "confidence": s.confidence,
                "reasoning": s.reasoning,
                "prompt_version": s.prompt_version,
                "model": s.model,
                "superseded_by": s.superseded_by,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in rows
        ]
    }


@hitl_router.get("/api/calls/{call_id}/responses")
def list_responses(
    call_id: str,
    reviewer: dict = Depends(current_reviewer),
    db: Session = Depends(get_db),
):
    if not db.query(Call).filter_by(id=call_id).first():
        raise HTTPException(status_code=404, detail="Call not found")

    rows = (
        db.query(VerdictResponse)
        .filter_by(call_id=call_id)
        .order_by(VerdictResponse.created_at.asc())
        .all()
    )
    return {
        "responses": [
            {
                "id": r.id,
                "suggestion_id": r.suggestion_id,
                "checkpoint_id": r.checkpoint_id,
                "actor_id": r.actor_id,
                "actor_role": r.actor_role,
                "verdict": r.verdict,
                "agreed_with_ai": r.agreed_with_ai,
                "reasoning": r.reasoning,
                "is_current": r.is_current,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


# ── Task 31: Time-travel read (event-sourced verdicts) ───────────
#
# Returns the checkpoint state as it existed at a given point in time.
# Reads from VerdictHistory (which has full actor_type + timestamp chain)
# rather than requiring a separate event table — simpler and uses the
# existing audit trail.


@hitl_router.get("/api/calls/{call_id}/at")
def verdicts_at_time(
    call_id: str,
    t: str = Query(..., description="ISO 8601 timestamp"),
    lead: dict = Depends(require_lead),
    db: Session = Depends(get_db),
):
    call = db.query(Call).filter_by(id=call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    try:
        cutoff = datetime.fromisoformat(t.replace("Z", "+00:00").replace("+00:00", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp format")

    rows = (
        db.query(VerdictHistory)
        .filter(
            VerdictHistory.call_id == call_id,
            VerdictHistory.created_at <= cutoff,
        )
        .order_by(VerdictHistory.created_at.asc())
        .all()
    )

    # Build the latest-as-of-cutoff state per checkpoint.
    by_cp: dict[str, dict] = {}
    for r in rows:
        by_cp[r.checkpoint_id] = {
            "checkpoint_id": r.checkpoint_id,
            "verdict": r.verdict,
            "actor_type": r.actor_type,
            "actor_id": r.actor_id,
            "reasoning": r.reasoning,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }

    return {
        "call_id": call_id,
        "as_of": t,
        "checkpoints": list(by_cp.values()),
    }
