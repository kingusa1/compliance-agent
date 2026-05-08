"""Inngest scheduled backup function — runs `pg_dump_to_storage` once a day.

Cron `0 2 * * *` UTC = 02:00 UTC nightly. Off-peak for European review
hours and the EU-region pgvector instance. Inngest retries up to 3 times
with exponential backoff; final failure produces a `failed_jobs` row via
the existing exhaustion handler.
"""
from __future__ import annotations

import inngest

from app.inngest_client import inngest_client
from app.logger import log
# Import via the package path that the test patches.
from scripts.pg_dump_to_storage import run as run_pg_dump


async def _run_backup() -> dict:
    """Wrapper that's easy to mock in tests."""
    key = run_pg_dump()
    return {"remote_key": key}


@inngest_client.create_function(
    fn_id="pg_dump_nightly",
    trigger=inngest.TriggerCron(cron="0 2 * * *"),
    retries=3,
)
async def pg_dump_nightly(ctx: inngest.Context) -> dict:
    log.info("pg_dump_nightly_start", extra={"run_id": getattr(ctx, "run_id", None)})
    result = await ctx.step.run("pg_dump_to_storage", _run_backup)
    log.info("pg_dump_nightly_ok", extra={"remote_key": result["remote_key"]})
    return result
