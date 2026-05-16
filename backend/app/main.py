import asyncio
import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sqlalchemy import text

import inngest.fast_api

# Touch the metric registry at import time so /metrics surfaces our
# custom series even before the first pipeline run.
import app.observability_metrics  # noqa: F401
from app.agents_routes import agents_router
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
    Exceptions from a bad iteration are logged and swallowed — one malformed
    DB row shouldn't kill the sweep forever.
    """
    from app.database import SessionLocal
    from app.hitl_routes import _release_idle_claims_core

    while True:
        try:
            db = SessionLocal()
            try:
                count = _release_idle_claims_core(db)
                if count > 0:
                    log.info(f"idle_release swept {count} expired claim(s)")
            finally:
                db.close()
        except asyncio.CancelledError:
            # Propagate — the lifespan awaits this exception on shutdown.
            raise
        except Exception as e:
            log.warning(f"idle_release loop iteration failed: {e}")
        await asyncio.sleep(interval_seconds)


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

    # Clean up stuck calls from previous runs. Skip silently if DB unreachable
    # so the process can still start in degraded mode (readyz will report 503).
    try:
        from app.models import Call
        db = SessionLocal()
        try:
            stuck = db.query(Call).filter(
                Call.status.in_(["pending_stream", "pending", "processing"])
            ).all()
            for call in stuck:
                call.status = "failed"
                call.reason = "Processing was interrupted by server restart"
            db.commit()
            if stuck:
                app_log.info(f"CLEANUP {len(stuck)} stuck calls marked as failed on startup")
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

    # Start the idle-claim sweeper. Runs every 120s on its own Session, so it
    # doesn't compete with request-scoped sessions for connection-pool slots.
    idle_task = asyncio.create_task(_idle_release_loop())

    try:
        yield
    finally:
        idle_task.cancel()
        # Bound the await so a wedged sweeper can't outlast Railway's 15s SIGTERM grace.
        try:
            await asyncio.wait_for(idle_task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


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
