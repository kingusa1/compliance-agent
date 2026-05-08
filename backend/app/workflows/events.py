"""Event schemas for Inngest workflows.

Names are constants so producers (upload route, webhook routes, HITL click
handlers) and consumers (durable functions in this package) can't drift.
Payload shapes are documented on the corresponding doc-classes — there's no
runtime validation yet, consumers defensively `.get()` the keys they need.
"""

from pydantic import BaseModel

CALL_UPLOADED = "call/uploaded"
CALL_HITL_APPROVED = "call/hitl-approved"  # reserved for next phase
CALL_ASSEMBLYAI_READY = "assemblyai/transcript-ready"  # reserved for next phase

# Tracker / verdict observability events. Emitted fire-and-forget from
# routes — no durable functions consume them yet, but they show up in the
# Inngest dashboard so the team can audit every state-change in the
# pipeline without grepping logs.
VERDICT_SUBMITTED = "call/verdict-submitted"           # reviewer commits PASS/REVIEW/FAIL
REJECTION_AUTO_CREATED = "rejection/auto-created"      # one fired per Rejection on FAIL
REJECTION_STATUS_CHANGED = "rejection/status-changed"  # NOT_STARTED → IN_PROGRESS → ...
DEAL_STATUS_CHANGED = "deal/status-changed"            # closed_lost / closed_done
CALL_METADATA_EDITED = "call/metadata-edited"          # reviewer override on /calls/[id]
TRACKER_ROWS_QUERIED = "tracker/rows-queried"          # GET /api/tracker/rows
TRACKER_XLSX_EXPORTED = "tracker/xlsx-exported"        # GET /api/tracker/export.xlsx
PORTAL_BATCH_SUBMITTED = "portal-batches/submitted"    # POST /api/portal-batches/submit


class VerdictSubmittedEvent:
    """Payload for call/verdict-submitted.
    Data: call_id, actor_id, verdict (PASS|REVIEW|FAIL),
    rejection_ids[] (one per failed checkpoint when verdict=FAIL/REVIEW),
    compliant (bool, only set on PASS)."""
    NAME = VERDICT_SUBMITTED


class RejectionAutoCreatedEvent:
    """Payload for rejection/auto-created.
    Data: rejection_id, call_id, deal_id, category, fix_required,
    confidence (AI), decision_path (AI_SUGGESTION|HEURISTIC_FALLBACK)."""
    NAME = REJECTION_AUTO_CREATED


class RejectionStatusChangedEvent:
    """Payload for rejection/status-changed.
    Data: rejection_id, from_status, to_status, actor_id, dead_reason?, outcome?"""
    NAME = REJECTION_STATUS_CHANGED


class DealStatusChangedEvent:
    """Payload for deal/status-changed.
    Data: deal_id, from_status, to_status, rejection_id?, actor_id."""
    NAME = DEAL_STATUS_CHANGED


class CallMetadataEditedEvent:
    """Payload for call/metadata-edited.
    Data: call_id, actor_id, fields_touched[]."""
    NAME = CALL_METADATA_EDITED


class TrackerRowsQueriedEvent:
    """Payload for tracker/rows-queried.
    Data: actor_id, tab, filter_keys[], row_count."""
    NAME = TRACKER_ROWS_QUERIED


class TrackerXlsxExportedEvent:
    """Payload for tracker/xlsx-exported.
    Data: actor_id, byte_count, sheet_counts{}."""
    NAME = TRACKER_XLSX_EXPORTED


class PortalBatchSubmittedEvent:
    """Payload for portal-batches/submitted.
    Data: supplier, rejection_ids[], submitted_count, actor_id."""
    NAME = PORTAL_BATCH_SUBMITTED


class CallUploadedEvent:
    """Payload for the call/uploaded event.

    Data keys:
        call_id: str — the Call row's id (varchar in DB)
        audio_path: str — storage key (Supabase) or absolute path to the
            stored audio file; the workflow resolves it the same way
            `process_call` does.
        customer_name: Optional[str]
        script_id: Optional[str]
    """

    NAME = CALL_UPLOADED


CALL_REANALYZE = "call/reanalyze"
"""Event emitted when a reviewer asks to re-derive a verdict from the
already-stored transcript. Cheap path — no transcription, no audio I/O.
Pipeline runs steps 4-5-6 only (analyze_checkpoints → score → finalize).
"""


class CallReanalyzePayload(BaseModel):
    """Payload for the `call/reanalyze` event.

    Mirrors `CallUploadedEvent` (the existing doc-only marker) minus
    audio-path fields, since this event never touches storage.
    """
    call_id: str
    actor: str | None = None  # reviewer who triggered the reanalyze
