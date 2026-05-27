import asyncio
import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import ENCODERS_BY_TYPE
from fastapi.exceptions import ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic_core import PydanticSerializationError
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.exc import DBAPIError, DisconnectionError, OperationalError

# 2026-05-28 P0 — global Row→dict encoder.
#
# Production observed: ``PydanticSerializationError: Unable to serialize
# unknown type: <class 'sqlalchemy.engine.row.Row'>`` firing on a route
# whose handler accidentally bubbled a raw SQLAlchemy ``Row`` (typically
# from ``db.execute(text(...)).fetchone()`` or ``.fetchall()``) up through
# FastAPI's ``serialize_response``. The crash takes the route to a 500.
#
# Registering ``Row → dict(r._mapping)`` in FastAPI's
# ``ENCODERS_BY_TYPE`` makes ``jsonable_encoder`` (used for every route
# WITHOUT a ``response_model``) handle Row transparently. For routes WITH
# a ``response_model``, the dedicated exception handler below catches
# the PydanticSerializationError, logs the offending path, and falls
# back to ``jsonable_encoder`` so the response succeeds with a clean
# dict payload instead of cascading a 500. The handler logs enough
# diagnostic context (route path, error message, stack location) to
# pinpoint the offending route across the next release.
ENCODERS_BY_TYPE[Row] = lambda r: dict(r._mapping)

import inngest.fast_api

# Touch the metric registry at import time so /metrics surfaces our
# custom series even before the first pipeline run.
import app.observability_metrics  # noqa: F401
from app.agents_routes import agents_router
from app.webhook_routes import webhook_router
from app.config import settings
from app.customers_routes import customers_router
from app.database import create_tables, engine, get_db
from app.reviewers import current_reviewer
from app.agent_chat_routes import agent_chat_router
from app.deals_routes import deals_router
from app.directives_routes import directives_router
from app.email_routes import email_router
from app.flags_routes import flags_router
from app.hitl_routes import hitl_router
from app.import_xlsx_routes import import_xlsx_router
from app.inngest_client import inngest_client
from app.observability_routes import observability_router
from app.rag_admin_routes import rag_admin_router
from app.rag_routes import rag_router
from app.rejections_routes import rejections_router
from app.routes import router
from app.rules_routes import rules_router
from app.saved_views_routes import saved_views_router
from app.script_routes import script_router
from app.workflows.pg_dump_nightly import pg_dump_nightly as pg_dump_nightly_fn
from app.workflows.process_call import (
    process_call as process_call_fn,
    process_call_reanalyze as process_call_reanalyze_fn,
)
from app.workflows.rag_ingest import rag_ingest_call_fn, rag_ingest_script_fn
from app.workflows.redispatch_watchdog import redispatch_watchdog as redispatch_watchdog_fn

log = logging.getLogger(__name__)


async def _idle_release_loop(interval_seconds: int = 120):
    """Periodically release idle claim locks so calls don't stay claimed by
    offline reviewers.

    Runs forever until cancelled by the lifespan shutdown path. Each iteration
    opens its own Session (we can't share the request-scoped one because we're
    outside an HTTP request) and delegates to `_release_idle_claims_core`.

    2026-05-25 — wraps the iteration in `@db_retry_on_disconnect` so a
    transient Supavisor disconnect (which previously logged
    `idle_release loop iteration failed: SSL connection has been
    closed unexpectedly` and skipped the sweep entirely) gets one
    automatic retry with a fresh session. The Prometheus counter
    `db_retry_total{outcome=...}` records whether the retry recovered.
    """
    # 2026-05-26 — switched from `SessionLocal` (Supavisor pooled engine)
    # to `DirectSessionLocal` (direct :5432 engine). The Supavisor pooler
    # kills idle pool members; a periodic background loop that holds
    # `Session.query()` across a 2-min sleep is exactly the workload that
    # produces `SSL connection has been closed unexpectedly` every other
    # iteration. The direct engine has its own tiny pool (2+0) with TCP
    # keepalives. Falls back to the main pool when DIRECT_DATABASE_URL is
    # unset (dev/tests).
    from app.database import DirectSessionLocal
    from app.db_retry import db_retry_on_disconnect
    from app.hitl_routes import _release_idle_claims_core

    @db_retry_on_disconnect()
    def _iteration() -> int:
        db = DirectSessionLocal()
        try:
            return _release_idle_claims_core(db)
        finally:
            db.close()

    while True:
        try:
            count = _iteration()
            if count > 0:
                log.info(f"idle_release swept {count} expired claim(s)")
        except asyncio.CancelledError:
            # Propagate — the lifespan awaits this exception on shutdown.
            raise
        except Exception as e:
            # Retry already happened (and either recovered or surfaced
            # here). Log and continue — the sweeper must never die.
            log.warning(f"idle_release loop iteration failed: {e}")
        await asyncio.sleep(interval_seconds)


async def _loop_lag_canary(
    *,
    target_sleep_s: float = 0.1,
    warn_threshold_s: float = 0.5,
    sample_interval_s: float = 5.0,
):
    """Background task that measures event-loop scheduling lag.

    Runs forever: sleep `target_sleep_s` (100 ms), measure the actual
    elapsed, log + increment a Prometheus counter when the elapsed
    exceeds `warn_threshold_s` (500 ms = "loop was starved").

    When a sync CPU block runs on the asyncio loop (the GIL-contention
    pattern that caused 2026-05-25's UI hang), the canary sleep takes
    several seconds and surfaces the starvation as a metric instead of
    a silent customer-visible hang.

    Reference: standard ops pattern documented at
    https://death.andgravity.com/limit-concurrency and adopted by
    aiohttp / Twisted production deployments.
    """
    import time as _time

    try:
        from app.observability_metrics import LOOP_LAG_WARN_TOTAL  # type: ignore
    except Exception:  # noqa: BLE001 — metric optional
        LOOP_LAG_WARN_TOTAL = None  # type: ignore

    # 2026-05-27 wave-15 — Sentry capture on loop_lag spike so ops gets
    # paged the first time the loop starves, not "whenever Mohamed reads
    # Railway logs". Uses `new_scope()` (sentry-sdk 2.x API — `push_scope`
    # was deprecated and silently no-ops scope-set calls in 2.x). Tags
    # group events by severity bucket; the message body itself stays the
    # same as the log line so log+Sentry events are 1:1 searchable.
    # Imported lazily so this background canary never fails on a Sentry
    # SDK import problem.
    try:
        import sentry_sdk as _sentry_sdk  # type: ignore
    except Exception:  # noqa: BLE001
        _sentry_sdk = None  # type: ignore

    # 2026-05-27 wave-15 hardening (python-reviewer HIGH) — explicit
    # rate limit. Without this, a perma-starved loop fires 12 events/min
    # = 17,280/day, burning ~3.4× the Sentry free-tier daily error
    # budget. Cap at 1 Sentry event per 60s; logs + Prometheus counter
    # still fire on every sample, so observability is intact.
    _SENTRY_RATE_LIMIT_S = 60.0
    _last_sentry_emit = 0.0  # time.monotonic() of last Sentry event

    while True:
        start = _time.monotonic()
        try:
            await asyncio.sleep(target_sleep_s)
        except asyncio.CancelledError:
            raise
        actual = _time.monotonic() - start
        lag = actual - target_sleep_s
        if lag > warn_threshold_s:
            log.warning(
                "loop_lag_canary target=%.0fms actual=%.0fms lag=%.0fms "
                "(asyncio loop is starved — likely sync CPU on the loop)",
                target_sleep_s * 1000, actual * 1000, lag * 1000,
            )
            if LOOP_LAG_WARN_TOTAL is not None:
                try:
                    LOOP_LAG_WARN_TOTAL.inc()
                except Exception:  # noqa: BLE001
                    pass
            # 2026-05-27 wave-15 — capture to Sentry, rate-limited to 1
            # event per 60s. Best-effort: never raise from the canary loop.
            now = _time.monotonic()
            if (
                _sentry_sdk is not None
                and (now - _last_sentry_emit) >= _SENTRY_RATE_LIMIT_S
            ):
                try:
                    # sentry-sdk 2.x: `new_scope()` is the supported
                    # context manager. `push_scope()` is deprecated; on
                    # 2.x it forwards to `new_scope()` but the API may
                    # change again. Use the current name explicitly.
                    with _sentry_sdk.new_scope() as _scope:
                        _scope.set_tag("perf.loop_lag", "warn")
                        _scope.set_tag(
                            "perf.lag_bucket",
                            ">2s" if lag > 2.0 else (">1s" if lag > 1.0 else ">500ms"),
                        )
                        _scope.set_extra("lag_ms", round(lag * 1000, 1))
                        _scope.set_extra("actual_ms", round(actual * 1000, 1))
                        _scope.set_extra("target_ms", round(target_sleep_s * 1000, 1))
                        _sentry_sdk.capture_message(
                            f"loop_lag {round(lag * 1000)}ms — asyncio loop starved",
                            level="warning",
                        )
                    _last_sentry_emit = now
                except Exception:  # noqa: BLE001 — canary must never crash
                    pass
        await asyncio.sleep(sample_interval_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.create_tables_on_startup:
        create_tables()
    os.makedirs(settings.upload_dir, exist_ok=True)

    from app.database import SessionLocal
    from app.logger import log as app_log

    # Production guard — refuse to start if dev_all_admin leaked into prod env.
    if settings.dev_all_admin and settings.sentry_environment.lower() == "production":
        raise RuntimeError(
            "DEV_ALL_ADMIN must be False in production. Refusing to start."
        )

    # Production CORS guard — block localhost/127.0.0.1 origins in prod.
    if settings.sentry_environment.lower() == "production":
        bad = [o for o in settings.allowed_origins.split(",")
               if "localhost" in o.lower() or "127.0.0.1" in o]
        if bad:
            raise RuntimeError(
                f"ALLOWED_ORIGINS contains dev origins in production: {bad}"
            )

    # 2026-05-26 Phase 2 boot guard — when USE_INNGEST_PIPELINE=true is
    # set on Railway but INNGEST_SIGNING_KEY/INNGEST_EVENT_KEY are missing,
    # Inngest events go nowhere (the upload handler emits CALL_UPLOADED
    # but no function ever receives it → pipeline silently never runs).
    # Refuse to boot in production with this misconfig.
    if settings.sentry_environment.lower() == "production" and settings.use_inngest_pipeline:
        if not os.environ.get("INNGEST_SIGNING_KEY", "").strip():
            raise RuntimeError(
                "USE_INNGEST_PIPELINE=true requires INNGEST_SIGNING_KEY. "
                "Set both Railway env vars from your Inngest Cloud Pro app. "
                "See BRAIN/04_Sessions/2026_05_26_*.md for the owner runbook."
            )
        if not os.environ.get("INNGEST_EVENT_KEY", "").strip():
            raise RuntimeError(
                "USE_INNGEST_PIPELINE=true requires INNGEST_EVENT_KEY. "
                "See BRAIN/04_Sessions/2026_05_26_*.md for the owner runbook."
            )

    # 2026-05-24 wiring audit C9 — production must use the Supabase
    # transaction-mode pooler at port :6543. Session-mode (:5432) pins a
    # server connection for the lifetime of the client connection; with
    # pool_size=25 + max_overflow=50 this can park 75 server-side
    # connections against a project that typically caps at 60–200.
    # A stale `supabase/.temp/pooler-url` stub ships :5432; this guard
    # makes the misconfiguration fail-fast instead of silently degrading.
    if settings.sentry_environment.lower() == "production":
        url = settings.database_url
        if "pooler.supabase.com" in url and ":5432/" in url:
            raise RuntimeError(
                "DATABASE_URL points at the Supabase session-mode pooler "
                "(:5432). Production must use the transaction-mode pooler "
                "(:6543/postgres). Update Railway env var DATABASE_URL."
            )
        if "localhost" in url or "127.0.0.1" in url:
            raise RuntimeError(
                "DATABASE_URL points at localhost in production env. "
                "Set it to the Supabase pooler URL on Railway."
            )

    # 2026-05-28 P0 owner-reported: every Railway redeploy was marking
    # in-flight user uploads as `failed` with reason "Processing was
    # interrupted by server restart". On a hot fix wave that meant
    # every push wiped any call that happened to be processing at the
    # moment. Users saw the same call repeatedly stuck in 'Pipeline
    # failed' on the call-detail page and had to manually click Retry.
    #
    # New behavior (wave 13): RESUME instead of FAIL. Stuck calls with
    # a stored audio path get their status reset to `pending` and the
    # background pipeline re-dispatched against the same audio file
    # AFTER FastAPI startup completes (we can't dispatch from inside
    # the lifespan setup — `asyncio.create_task` needs a running event
    # loop). Calls without a stored audio path (incomplete uploads
    # that never made it to storage) are legitimately broken and stay
    # marked `failed`.
    #
    # The redispatch is fire-and-forget — failure to re-enqueue any
    # single call must NOT block boot. The existing 3s safety-net poll
    # on the call detail page (useCallBundleQuery) means the reviewer
    # sees the call move from `pending` -> `processing` -> terminal as
    # the pipeline runs.
    _resume_candidates: list[tuple[str, str, str | None]] = []  # (id, file_path, script_id)
    try:
        from app.models import Call
        db = SessionLocal()
        try:
            stuck = db.query(Call).filter(
                Call.status.in_(["pending_stream", "pending", "processing"])
            ).all()
            resumed = 0
            killed = 0
            for call in stuck:
                fp = getattr(call, "file_path", None) or getattr(call, "audio_storage_key", None)
                if fp:
                    # Reset to pending; the lifespan post-startup hook
                    # below will create_task the redispatch once the
                    # event loop is running and the routes are mounted.
                    call.status = "pending"
                    call.reason = "Resuming after server restart"
                    _resume_candidates.append((str(call.id), str(fp), getattr(call, "script_id", None)))
                    resumed += 1
                else:
                    # No audio reference -> upload never completed; mark
                    # failed so the queue surfaces the broken row to a
                    # reviewer rather than leaving it Forever Pending.
                    call.status = "failed"
                    call.reason = "Upload incomplete (no audio file stored before restart)"
                    killed += 1
            db.commit()
            if stuck:
                app_log.info(
                    f"CLEANUP startup: {resumed} stuck calls reset to pending for redispatch, "
                    f"{killed} marked failed (no audio file)"
                )
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — DB may be down at boot; readyz will surface it
        app_log.warning(f"startup_stuck_cleanup_skipped: {type(e).__name__}: {e}")

    # Pre-warm customer cache so first call-ingest doesn't pay a full table scan.
    # Non-fatal: if the DB is unreachable at boot the cache populates on first miss.
    try:
        from app.business_detect import _refresh_customer_cache
        db = SessionLocal()
        try:
            _refresh_customer_cache(db)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — cache miss on first request is acceptable
        app_log.warning(f"customer_cache_warmup_skipped: {type(e).__name__}: {e}")

    # Pre-warm Supabase JWKS so first authenticated request doesn't pay the round-trip.
    # Skipped when SUPABASE_URL isn't set (e.g. tests) to keep startup fast.
    if settings.supabase_url:
        try:
            from app.auth import _get_jwks_client
            _get_jwks_client().get_signing_keys()
            app_log.info("JWKS pre-warmed from Supabase")
        except Exception as e:
            app_log.warning(f"JWKS pre-warm failed (will retry on first request): {e}")

    # Pre-load profile cache so first queue render doesn't pay a DB round-trip.
    # Non-blocking — log + continue on failure (DB may be unreachable at boot).
    try:
        from app.profile_cache import refresh_profile_cache
        _pc_db = SessionLocal()
        try:
            _pc_count = refresh_profile_cache(_pc_db)
            app_log.info(f"profile_cache: pre-loaded {_pc_count} profiles")
        finally:
            _pc_db.close()
    except Exception as e:  # noqa: BLE001
        app_log.warning(f"profile_cache: startup pre-load skipped: {type(e).__name__}: {e}")

    # 2026-05-25 — One-shot self-heal on every startup. Two passes, both
    # idempotent (second run finds zero work):
    #   1. Consolidate any deals that share a canonical MPAN/MPRN — folds
    #      duplicates onto the oldest survivor.
    #   2. Promote a real customer_name from Call.customer_name onto any
    #      deal still carrying a stub like "(pending audio upload)" or
    #      "(auto-detect pending {hash})". Without this the deal stays
    #      hidden from /customers because `_REAL_NAME_PREDICATE` filters
    #      it out — the 2026-05-25 user-reported bug.
    #
    # Wrapped in its own try/except + Session so a heal failure NEVER
    # blocks the app from accepting traffic — readyz still returns 200.
    #
    # 2026-05-25 — `AUTO_HEAL_ON_STARTUP` default flipped from ON to OFF
    # after a cross-supplier merge happened in prod (BG call's deal
    # absorbed into an E.ON deal sharing an MPRN). The merge function
    # itself now refuses cross-supplier matches (`_is_safe_to_auto_merge`
    # in `app.deal_meter_merge`), but batch heal at boot still feels
    # too aggressive for production — every restart re-evaluates every
    # deal. Per-call merge at finalize is enough for ongoing health.
    # Admin endpoints (/api/admin/consolidate-duplicate-deals and
    # /api/admin/backfill-placeholder-customer-names) remain available
    # for explicit reviewer-driven heal. Set the env var to `true` on
    # Railway only if a backfill window is needed.
    if os.environ.get("AUTO_HEAL_ON_STARTUP", "false").strip().lower() in ("true", "1", "yes"):
        try:
            from app.deal_meter_merge import (
                backfill_placeholder_customer_names,
                consolidate_all_duplicate_deals,
            )
            _heal_db = SessionLocal()
            try:
                consolidated = consolidate_all_duplicate_deals(_heal_db, dry_run=False)
                backfilled = backfill_placeholder_customer_names(_heal_db, dry_run=False)
                _heal_db.commit()
                app_log.info(
                    "AUTO_HEAL_ON_STARTUP done | "
                    "consolidate scanned=%d clusters=%d | "
                    "name_promote scanned=%d candidates=%d promoted=%d",
                    consolidated.get("deals_scanned", 0),
                    consolidated.get("clusters_found", 0),
                    backfilled.get("deals_scanned", 0),
                    backfilled.get("deals_with_placeholder", 0),
                    backfilled.get("promoted", 0),
                )
            finally:
                _heal_db.close()
        except Exception as e:  # noqa: BLE001 — heal must never break boot
            app_log.warning(f"AUTO_HEAL_ON_STARTUP skipped/failed: {type(e).__name__}: {e}")

    # 2026-05-26 — Raise AnyIO threadpool limiter from default 40 → 200.
    # Per AnyIO docs, `to_thread.run_sync()` and every sync FastAPI `def`
    # route share a global CapacityLimiter; with 5 concurrent uploads
    # firing parallel pipelines and the UI making concurrent reads, the
    # 40-token default queues threadpool requests behind pipeline work.
    # 200 tokens is well within Railway's per-replica thread budget and
    # matches FastAPI Discussion #12269's production guidance.
    try:
        import anyio.to_thread
        # 2026-05-27 — bumped 200 → 400 to match the Railway 24 vCPU /
        # 24 GB Pro replica (owner maxed the box). Off-loop file reads
        # for 5 transcribers × N concurrent pipelines now consume the
        # AnyIO limiter (since the same-day asyncio.to_thread →
        # anyio.to_thread.run_sync switch); 200 tokens was a bottleneck
        # under bulk concurrency with concurrent file I/O + sync DB
        # writes competing for the same pool.
        anyio.to_thread.current_default_thread_limiter().total_tokens = 400
        app_log.info("anyio threadpool limiter total_tokens=400")
    except Exception as e:  # noqa: BLE001
        app_log.warning(f"failed to raise anyio threadpool limiter: {e!r}")

    # Start the idle-claim sweeper. Runs every 120s on its own Session, so it
    # doesn't compete with request-scoped sessions for connection-pool slots.
    idle_task = asyncio.create_task(_idle_release_loop())

    # 2026-05-26 — asyncio loop-lag canary. Background task that sleeps
    # 0.1s and logs the ACTUAL elapsed. If a sync block holds the GIL,
    # the elapsed will exceed 100ms — surfacing event-loop starvation as
    # a metric instead of a silent UI hang. Standard ops pattern from
    # death.andgravity / Twisted / aiohttp production playbooks.
    loop_lag_task = asyncio.create_task(_loop_lag_canary())

    # 2026-05-28 (wave 13) — auto-resume stuck calls picked up by the
    # startup cleanup block at line ~241. The cleanup reset their status
    # to `pending` and recorded their file_path / script_id in the
    # module-local list `_resume_candidates`. Re-dispatch each via the
    # same `_process_in_background` coroutine the upload endpoint uses,
    # bounded by the existing pipeline semaphore so we don't overwhelm
    # the worker on boot. Fire-and-forget — a single bad redispatch
    # must NOT block startup. Skipped when Inngest pipeline is on
    # (durable workflow handles its own resume via the redispatch
    # watchdog cron — see workflows/redispatch_watchdog.py).
    resume_tasks: list = []
    if _resume_candidates and not settings.use_inngest_pipeline:
        try:
            from app.routes import _process_in_background

            async def _resume_one(cid: str, fp: str, sid: str | None) -> None:
                try:
                    app_log.info(f"AUTO_RESUME call_id={cid} file_path={fp!r}")
                    await _process_in_background(cid, fp, sid)
                except Exception as e:  # noqa: BLE001
                    app_log.warning(f"AUTO_RESUME_FAILED call_id={cid}: {type(e).__name__}: {e}")

            def _log_resume_task_exc(task: asyncio.Task) -> None:
                # `_resume_one` swallows every Exception internally and logs
                # AUTO_RESUME_FAILED, so this callback only catches the
                # extremely rare case where the coroutine raised something
                # outside that try/except (e.g. BaseException-subclass on
                # shutdown). Log at WARNING + consume.
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is not None:
                    app_log.warning(
                        "AUTO_RESUME_TASK_LEAK: %s: %s",
                        type(exc).__name__, exc,
                    )

            for cid, fp, sid in _resume_candidates:
                t = asyncio.create_task(_resume_one(cid, fp, sid))
                t.add_done_callback(_log_resume_task_exc)
                resume_tasks.append(t)
            app_log.info(f"AUTO_RESUME dispatched {len(resume_tasks)} task(s) after restart")
        except Exception as e:  # noqa: BLE001 — boot must never block on resume setup
            app_log.warning(f"AUTO_RESUME_SETUP_FAILED: {type(e).__name__}: {e}")

    # 2026-05-27 wave-21 — one-time speaker-label backfill of historical
    # `Call.transcript` blobs using the post-wave-16 `_detect_agent_speaker`
    # heuristic. Closes the wave-16 carry-forward where the persisted
    # transcript still carried the OLD diarized labels even after
    # /api/calls/{id}/words request-time re-derivation was fixed.
    #
    # Owner reported on 2026-05-27 that the Elzicle Ltd call STILL showed
    # swapped Agent/Customer labels in the UI even after clicking
    # Reanalyze. This boot-time backfill (paired with the new in-Reanalyze
    # rederive in app/replay.py) closes the gap for ALL existing calls.
    #
    # Off-loop via asyncio.to_thread per wave-18 pattern. Idempotent —
    # writes only when the new text differs from the stored transcript.
    # Batched + capped per boot so a 100K-call backlog doesn't OOM the
    # worker. Fire-and-forget after `yield` so it never blocks Railway's
    # readiness probe.
    backfill_task: asyncio.Task | None = None

    async def _speaker_label_backfill_on_boot() -> None:
        # Sleep briefly to let the readiness probe ack first, then do
        # one batch (capped). Re-runs idempotently on subsequent boots
        # until the unchanged count plateaus.
        await asyncio.sleep(30.0)
        try:
            from app.transcription import format_diarized_transcript
            import json as _json

            def _backfill_batch_in_thread() -> dict[str, int]:
                from app.database import SessionLocal as _SL
                _db = _SL()
                updated = 0
                unchanged = 0
                skipped = 0
                scanned = 0
                try:
                    # Cap to 2000/boot so a giant backlog doesn't tie up
                    # the worker. Subsequent boots clear more rows; once
                    # the dataset is fully migrated `updated` plateaus to 0.
                    rows = (
                        _db.query(Call)
                        .filter(Call.word_data.isnot(None))
                        .filter(Call.transcript.isnot(None))
                        .limit(2000)
                        .all()
                    )
                    for row in rows:
                        scanned += 1
                        try:
                            raw = row.word_data
                            words = (
                                _json.loads(raw)
                                if isinstance(raw, (str, bytes, bytearray))
                                else raw
                            )
                            if not isinstance(words, list) or not words:
                                skipped += 1
                                continue
                            new_text = format_diarized_transcript(words)
                            if new_text == (row.transcript or ""):
                                unchanged += 1
                                continue
                            row.transcript = new_text
                            updated += 1
                            # Batch-commit every 200 (per wave-17 pattern)
                            # to bound the loss-window on a transient
                            # Supavisor disconnect.
                            if updated % 200 == 0:
                                _db.commit()
                        except Exception:  # noqa: BLE001
                            skipped += 1
                            continue
                    _db.commit()
                finally:
                    try:
                        _db.close()
                    except Exception:  # noqa: BLE001
                        pass
                return {
                    "updated": updated,
                    "unchanged": unchanged,
                    "skipped": skipped,
                    "scanned": scanned,
                }

            result = await asyncio.to_thread(_backfill_batch_in_thread)
            app_log.info(
                "BOOT_SPEAKER_LABEL_BACKFILL updated=%d unchanged=%d "
                "skipped=%d scanned=%d (cap=2000)",
                result["updated"], result["unchanged"],
                result["skipped"], result["scanned"],
            )
        except Exception as e:  # noqa: BLE001 — boot backfill must not crash app
            app_log.warning(
                f"BOOT_SPEAKER_LABEL_BACKFILL_FAILED: "
                f"{type(e).__name__}: {e}"
            )

    if not settings.use_inngest_pipeline:
        # Skip on Inngest path — the durable workflow has its own data-
        # repair cron and we don't want duplicate writes.
        backfill_task = asyncio.create_task(_speaker_label_backfill_on_boot())

        def _log_backfill_task_exc(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                app_log.warning(
                    "BOOT_SPEAKER_LABEL_BACKFILL_TASK_LEAK: %s: %s",
                    type(exc).__name__, exc,
                )

        backfill_task.add_done_callback(_log_backfill_task_exc)

    try:
        yield
    finally:
        idle_task.cancel()
        loop_lag_task.cancel()
        # Bound the await so a wedged sweeper can't outlast Railway's 15s SIGTERM grace.
        try:
            await asyncio.wait_for(idle_task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        try:
            await asyncio.wait_for(loop_lag_task, timeout=2)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        # Wave-21 — cancel the boot-time speaker-label backfill on
        # shutdown. The task is idempotent so a mid-batch cancel just
        # means the next boot picks up where this one left off.
        if backfill_task is not None:
            backfill_task.cancel()
            try:
                await asyncio.wait_for(backfill_task, timeout=2)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        # 2026-05-26 — close the shared httpx.AsyncClient pool on shutdown.
        try:
            from app.http_clients import aclose_all_clients
            await aclose_all_clients()
        except Exception as e:  # noqa: BLE001 — best-effort shutdown
            app_log.warning(f"http_clients aclose failed: {e!r}")


def init_sentry() -> None:
    """Initialise Sentry SDK if DSN is configured. No-op otherwise.

    Sentry-API-compatible — points at self-hosted GlitchTip in prod.
    Errors here MUST NOT take the process down: GlitchTip availability
    is non-critical to request-path code.
    """
    dsn = settings.sentry_dsn.strip()
    if not dsn:
        return
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=settings.sentry_environment,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            send_default_pii=False,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                StarletteIntegration(),
            ],
        )
    except Exception:  # noqa: BLE001 — Sentry init must never break boot
        logging.getLogger(__name__).warning("sentry_init_failed", exc_info=True)


init_sentry()


app = FastAPI(
    title="Compliance Agent",
    description="Call recording compliance analysis platform",
    version="0.1.0",
    lifespan=lifespan,
)


# Single source of truth lives on the engine (database.py) so the listener
# and this handler can never drift apart. Re-exported under a clearer name
# for callsites that only see the handler.
from app.database import _DISCONNECT_SIGNATURES as _DB_DISCONNECT_SIGNATURES


def _is_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, DisconnectionError):
        return True
    msg = str(exc).lower()
    return any(sig in msg for sig in _DB_DISCONNECT_SIGNATURES)


def _row_safety_diag(request: Request) -> tuple[str, str]:
    """Extract route template + endpoint qualname from the request scope.

    Falls back to ``request.url.path`` + ``"unknown"`` when the scope is
    missing the typed route handle (e.g., the request hit a middleware
    short-circuit before routing resolved). Used by both Row-safety
    handlers below so they emit the same diagnostic shape.
    """
    try:
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        endpoint_name = getattr(getattr(route, "endpoint", None), "__qualname__", "unknown")
    except Exception:  # noqa: BLE001 — diagnostic helper, never raise
        route_path = request.url.path
        endpoint_name = "unknown"
    return route_path, endpoint_name


@app.exception_handler(ResponseValidationError)
async def _response_validation_row_handler(
    request: Request, exc: ResponseValidationError
) -> JSONResponse:
    """Recover from ResponseValidationError on a leaked SQLAlchemy Row.

    FastAPI runs ``response_field.validate()`` BEFORE ``serialize()`` when
    a route has a ``response_model``. A raw ``sqlalchemy.engine.row.Row``
    at the top level (e.g., a forgotten ``.fetchone()`` / ``.fetchall()``)
    typically fails ``validate()`` first and FastAPI raises
    ``ResponseValidationError`` — never reaching the ``serialize()``
    layer that would have raised ``PydanticSerializationError``. So this
    handler is the actual catcher for the top-level-Row failure mode.

    Returns 500 with ``Retry-After: 1`` so the frontend's TanStack Query
    retry layer absorbs the blip with bounded backoff while engineering
    pinpoints the offending route from the structured log line.
    """
    route_path, endpoint_name = _row_safety_diag(request)
    log.error(
        "response_validation_error path=%s method=%s route_template=%s "
        "endpoint=%s err=%s",
        request.url.path,
        request.method,
        route_path,
        endpoint_name,
        str(exc)[:500],
    )
    try:
        sentry_sdk.capture_exception(exc)
    except Exception as sentry_err:  # noqa: BLE001 — Sentry must never break a request
        log.debug("sentry_capture_failed: %r", sentry_err)
    return JSONResponse(
        {
            "detail": (
                "Response serialization failed. Engineering has been "
                "notified. Please retry."
            ),
        },
        status_code=500,
        headers={"Retry-After": "1"},
    )


@app.exception_handler(PydanticSerializationError)
async def _pydantic_row_serialization_handler(
    request: Request, exc: PydanticSerializationError
) -> JSONResponse:
    """Recover from PydanticSerializationError raised in serialize_response.

    Companion to ``_response_validation_row_handler``. ``Row`` objects
    nested DEEP inside a passing ``validate()`` (e.g., a Row buried in
    a JSON column that was loaded raw) survive validation but blow up
    in ``dump_python()``. This handler catches that narrower case.

    Same response contract as the validation handler: 500 + Retry-After
    so TanStack Query backs off bounded retries instead of looping.
    """
    route_path, endpoint_name = _row_safety_diag(request)
    log.error(
        "pydantic_serialization_error path=%s method=%s route_template=%s "
        "endpoint=%s err=%s",
        request.url.path,
        request.method,
        route_path,
        endpoint_name,
        str(exc)[:500],
    )
    try:
        sentry_sdk.capture_exception(exc)
    except Exception as sentry_err:  # noqa: BLE001 — Sentry must never break a request
        log.debug("sentry_capture_failed: %r", sentry_err)
    return JSONResponse(
        {
            "detail": (
                "Response serialization failed. Engineering has been "
                "notified. Please retry."
            ),
        },
        status_code=500,
        headers={"Retry-After": "1"},
    )


@app.exception_handler(OperationalError)
@app.exception_handler(DBAPIError)
async def _db_operational_error_handler(request: Request, exc: DBAPIError):
    """Convert in-flight DB connection drops into a single-line warning + 503.

    Without this handler every disconnect dumps a 30-line traceback to stdout
    via Starlette's default ServerErrorMiddleware. Under a small burst that
    blows past Railway's 500-line/sec log ceiling and starts dropping ALL
    logs (including unrelated ones). The handler also returns Retry-After so
    well-behaved clients back off instead of hammering the just-recovered pool.
    """
    from app.logger import log as app_log

    if _is_disconnect(exc):
        app_log.warning(
            "db_disconnect_request_failed path=%s method=%s err=%s",
            request.url.path,
            request.method,
            type(exc.orig).__name__ if getattr(exc, "orig", None) else type(exc).__name__,
        )
        return JSONResponse(
            {"detail": "Database connection was reset. Please retry."},
            status_code=503,
            headers={"Retry-After": "1"},
        )
    # Non-disconnect DB errors (constraint violations, syntax, deadlocks) keep
    # full visibility — those are real bugs to investigate. FastAPI handlers
    # short-circuit Sentry's middleware, so we explicitly capture here.
    app_log.error(
        "db_error path=%s method=%s err_type=%s err=%s",
        request.url.path,
        request.method,
        type(exc).__name__,
        str(exc)[:500],
    )
    try:
        sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001 — Sentry must never break a request
        pass
    return JSONResponse({"detail": "Database error"}, status_code=500)


@app.get("/healthz", tags=["ops"])
def healthz():
    """Liveness — process is up."""
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
def readyz():
    """Readiness — process can serve traffic (DB reachable)."""
    checks = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001 — readiness must surface every failure mode
        checks["db"] = f"fail: {type(exc).__name__}"
    status_code = 200 if all(v == "ok" for v in checks.values()) else 503
    return JSONResponse({"status": "ready" if status_code == 200 else "degraded", "checks": checks}, status_code=status_code)


@app.post("/api/internal/refresh-customer-cache", tags=["ops"])
def refresh_customer_cache(
    reviewer: dict = Depends(current_reviewer),
    db=Depends(get_db),
):
    """Force-refresh the in-process customer name cache.

    Admin/lead only. Returns the number of customers now in cache.
    Useful after bulk customer imports or when matching feels stale.
    """
    from app.business_detect import _refresh_customer_cache, _CUSTOMER_CACHE

    if reviewer.get("role") not in ("lead", "admin"):
        raise HTTPException(status_code=403, detail="admin or lead role required")
    _refresh_customer_cache(db)
    return {"ok": True, "customer_count": len(_CUSTOMER_CACHE.customers)}


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2026-05-16 perf — gzip large JSON responses (>1KB). Call detail
# responses are 100-500KB with the inline transcript + checkpoint_results
# JSON; over the Railway→Vercel pipe gzip cuts that to ~30-80KB. Streaming
# responses (SSE) are exempt — Starlette's GZipMiddleware automatically
# skips text/event-stream responses.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

if settings.prometheus_enabled:
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/healthz", "/readyz"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# 2026-05-16: realtime_router MUST register before the generic call detail
# router below; otherwise /api/calls/events matches @router.get("/api/calls/{call_id}")
# in routes.py and FastAPI returns a 404 "Call not found".
from app.realtime_routes import realtime_router as _realtime_router_early
app.include_router(_realtime_router_early)
app.include_router(router)
app.include_router(script_router)
app.include_router(hitl_router)
app.include_router(deals_router)
app.include_router(customers_router)
app.include_router(directives_router)
app.include_router(agents_router)
app.include_router(observability_router)
app.include_router(rules_router)
# L8: glue mounts for L4 + L6 routers shipped by sub-agents.
app.include_router(flags_router)
app.include_router(saved_views_router)
app.include_router(rag_router)
app.include_router(rag_admin_router)
# W2 (v3-watt-coverage): rejection workflow (Stage 4 of Watt's 41-step flow).
app.include_router(rejections_router)
# L10 chat UI revival: agent_chat_router for /api/agent/chat SSE endpoint.
app.include_router(agent_chat_router)
# W3.B (v3-watt-coverage): customer confirmation email endpoint (compliance §8).
app.include_router(email_router)
# v3-tracker: master XLSX-mirror page rows endpoint.
from app.tracker_routes import tracker_router
app.include_router(tracker_router)
# v3-tracker Phase C2: PATCH /api/tracker/rows/{id} inline-edit endpoint.
from app.tracker_edit_routes import tracker_edit_router
app.include_router(tracker_edit_router)
# v3-tracker: admin XLSX-import endpoint (POST /api/admin/import-tracker-xlsx).
app.include_router(import_xlsx_router)
# Plan §5f: dashboard intelligence panel (read-only aggregations).
from app.intelligence_routes import intelligence_router
app.include_router(intelligence_router)
# Webhook callbacks from external providers (AssemblyAI job completion).
app.include_router(webhook_router)
# 2026-05-16: SSE pub/sub fan-out for live call events is registered at the
# top of the include_router block above so `/api/calls/events` resolves to
# the realtime endpoint instead of being shadowed by the generic call
# detail route `/api/calls/{call_id}`.

# L6: rag_ingest fires on `call/finalized` and `script/changed`. Adding the
# two new functions next to the L1 watchdog so Inngest discovers them.
# Skip Inngest registration when no signing key is configured — the SDK
# raises SigningKeyMissingError at import time otherwise, taking the whole
# process down even when USE_INNGEST_PIPELINE=false.
if os.environ.get("INNGEST_SIGNING_KEY", "").strip():
    inngest.fast_api.serve(
        app,
        inngest_client,
        [
            process_call_fn,
            process_call_reanalyze_fn,
            redispatch_watchdog_fn,
            rag_ingest_call_fn,
            rag_ingest_script_fn,
            pg_dump_nightly_fn,
        ],
    )
else:
    log.info("Inngest serve() skipped — INNGEST_SIGNING_KEY not set (degraded mode)")
