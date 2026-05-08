"""Singleton Inngest client.

Imported by:
    - app.main             — `inngest.fast_api.serve(app, inngest_client, [...])`
    - app.workflows.*      — `@inngest_client.create_function(...)`
    - app.routes           — `await inngest_client.send(_inngest.Event(...))`

The dev server picks this up by hitting `/api/inngest` on the FastAPI app, which
the `inngest.fast_api.serve` call exposes (see app/main.py).
"""
from __future__ import annotations

import os

import inngest

inngest_client = inngest.Inngest(
    app_id="compliance-agent",
    is_production=os.getenv("INNGEST_ENV", "dev").lower() == "production",
)
