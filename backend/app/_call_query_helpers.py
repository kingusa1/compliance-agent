"""Per-callsite query helpers for the `calls` table.

2026-05-25 — Supabase slow-query dashboard surfaced multiple
`SELECT * FROM calls WHERE … ORDER BY created_at DESC` queries
collectively taking >29 seconds of database time:

    role     calls   mean_ms  total_ms  endpoint                          %_total
    postgres   446      42.9    19,137  /api/queue (HRQ inbox)             2.7%
    postgres   321      20.0     6,424  /api/calls/{id} (call detail)      0.9%
    postgres    44      54.7     2,408  /api/tracker/rows (awaiting tab)   0.3%
    postgres    16      65.7     1,052  /api/customers/{slug} deal-calls   0.15%

Every row pulled `transcript`, `gemini_transcript`, `assemblyai_transcript`,
`groq_whisper_transcript`, `cohere_transcript`, `word_data`, `meta`,
`deepgram_metadata`, `assemblyai_metadata`, `openai_whisper_metadata`,
`processing_log`, `raw_llm_io`, `draft_snapshot` — typically 50-100KB per
row of text/JSON the list view will never render. At 100-row pages that
is 5-10 MB of dead weight on each request.

The detail endpoint legitimately needs `transcript` and friends — so the
fix is callsite-local: list endpoints add `defer_heavy_call_columns(q)`
to their SQLAlchemy query, detail endpoints don't.

A deferred column is loaded lazily on attribute access, so the only
risk is N+1 if a list-shape consumer later touches one of the heavy
columns. The HRQ row builder, the tracker row builder, and the deal-
detail call list all hit ONLY: id, filename, customer_name, agent_name,
score, detected_supplier, duration_seconds, created_at, completed_at,
review_status, reviewed_at, reviewed_by, compliance_status, bucket,
checkpoint_results — none of which are in the deferred set.
"""
from __future__ import annotations

from sqlalchemy.orm import Query, defer

from app.models import Call


# Columns the LIST endpoints never read. Verified by grep across
# hitl_routes._row, tracker_aggregator._awaiting_review_row,
# tracker_aggregator._rejection_row, customers_routes.get_customer
# (deal-calls section), and deals_routes.list_deals.
#
# Update this set when adding a new column to `calls` that's >1KB of
# text/JSON and not surfaced on list views.
HEAVY_CALL_COLUMNS: tuple[str, ...] = (
    "transcript",
    "gemini_transcript",
    "assemblyai_transcript",
    "groq_whisper_transcript",
    "cohere_transcript",
    "word_data",
    "meta",
    "deepgram_metadata",
    "assemblyai_metadata",
    "openai_whisper_metadata",
    "processing_log",
    "raw_llm_io",
    "draft_snapshot",
)


def defer_heavy_call_columns(q: Query) -> Query:
    """Apply `defer()` for every column in `HEAVY_CALL_COLUMNS`.

    Use at the top of any list-shape query against `Call`. Safe to
    chain — the returned query still includes the lightweight columns
    that the row builder actually renders. Accessing a deferred column
    afterwards triggers a per-attribute SELECT, so do NOT use this
    helper in the detail-page endpoint (which iterates the transcript).
    """
    for col_name in HEAVY_CALL_COLUMNS:
        attr = getattr(Call, col_name, None)
        if attr is None:
            # Tolerate missing columns gracefully — the model may not
            # have all of these in test envs that use SQLite + a
            # minimal Base.metadata.create_all() set.
            continue
        q = q.options(defer(attr))
    return q
