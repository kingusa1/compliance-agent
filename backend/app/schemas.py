from datetime import datetime, date
from decimal import Decimal
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ScriptCheckpoint(BaseModel):
    section: int
    name: str
    required: str
    key_phrases: list[str]
    customer_response_required: bool = False
    strictness: str = "mandatory"  # verbatim | mandatory | customer_yes
    # W1 (v3-watt-coverage): script-line number when known. Watt reviewers
    # name script lines explicitly ("amendment for line 11-14"); see XLSX
    # deep-dive §1.5, §8.
    line_number: int | None = None


class ScriptCreate(BaseModel):
    supplier_name: str
    script_name: str
    version: str | None = None
    mode: str = "meaning_for_meaning"
    checkpoints: list[ScriptCheckpoint]


class ScriptResponse(BaseModel):
    id: str
    supplier_name: str
    script_name: str
    version: str | None
    mode: str
    checkpoints: str  # JSON string
    active: bool
    created_at: datetime
    updated_at: datetime | None

    class Config:
        from_attributes = True

    # 2026-05-27 hot-fix — prod /api/scripts 500'd because a wave-24
    # raw-SQL INSERT bypassed the SQLAlchemy default and left mode=NULL.
    # Coerce None → default so a stray-null script row can NEVER take
    # down the whole list endpoint again. Companion data backfill ships
    # in alembic/versions/2026_05_27_backfill_script_mode.py.
    # security-reviewer LOW: log a warning when coerce fires so future
    # data-quality regressions surface in logs instead of being silent.
    @field_validator("mode", mode="before")
    @classmethod
    def _default_mode(cls, v: str | None) -> str:
        if not v:
            import logging
            logging.getLogger("app.schemas").warning(
                "ScriptResponse.mode coerced from NULL/empty to default — "
                "investigate the upstream INSERT that wrote it"
            )
            return "meaning_for_meaning"
        return v


class ScriptListResponse(BaseModel):
    scripts: list[ScriptResponse]
    total: int


class ScriptVersionResponse(BaseModel):
    id: str
    script_id: str
    version_number: int
    checkpoints_snapshot: str  # JSON string
    mode_snapshot: str
    created_at: datetime

    class Config:
        from_attributes = True

    # Same hot-fix as ScriptResponse.mode — a NULL mode_snapshot would
    # break GET /api/scripts/{id}/versions identically. Symmetry per
    # python-reviewer 2026-05-27 trio.
    @field_validator("mode_snapshot", mode="before")
    @classmethod
    def _default_mode_snapshot(cls, v: str | None) -> str:
        return v if v else "meaning_for_meaning"


class ScriptVersionListResponse(BaseModel):
    versions: list[ScriptVersionResponse]
    total: int


class CheckpointResult(BaseModel):
    section: int
    name: str
    status: str  # pass | partial | fail | unverified
    evidence: str
    notes: str | None = None
    start_ms: int | None = None  # milliseconds from call start, first word of evidence
    end_ms: int | None = None    # milliseconds from call start, last word of evidence


class CheckpointComplianceResult(BaseModel):
    detected_supplier: str
    agent_name: str
    customer_name: str
    mode: str
    checkpoints: list[CheckpointResult]
    summary: dict


class RuleCheckpoint(BaseModel):
    rule: str
    passed: bool
    excerpt: str
    # Optional analyst reasoning. The V1 prompt now asks for it explicitly so
    # the reviewer always has a one-line justification even when no script
    # was matched. Older payloads without notes still parse cleanly.
    notes: str | None = None


class CheckpointResponse(BaseModel):
    rule_text: str
    passed: bool
    excerpt: str | None

    class Config:
        from_attributes = True


class ComplianceResult(BaseModel):
    compliant: bool
    reason: str
    excerpt: str
    agent_name: str = "Unknown"
    customer_name: str = "Unknown"
    checkpoints: list[RuleCheckpoint] = []


class DataQualityWarning(BaseModel):
    """Wave-50 — one non-compliance data-quality warning on a call (e.g.
    ``customer_name_mismatch``). Kept off the compliance ``flags`` channel
    so it never enters the compliance findings/report. ``extra="allow"``
    so future producers can attach context fields without a schema bump."""

    model_config = ConfigDict(extra="allow")

    code: str
    message: str


class CallResponse(BaseModel):
    id: str
    filename: str
    file_size: int | None
    duration_seconds: float | None
    status: str
    transcript: str | None
    assemblyai_transcript: str | None = None
    compliant: bool | None
    reason: str | None
    excerpt: str | None
    agent_name: str | None
    customer_name: str | None
    script_id: str | None
    checkpoint_results: str | None
    score: str | None
    detected_supplier: str | None
    rule_id: str
    created_at: datetime
    completed_at: datetime | None
    checkpoints: list[CheckpointResponse] = []
    compliance_status: str | None = None  # "pending" | "compliant" | "non_compliant"
    # HITL Task 21: draft autosave fields. Frontend hydrates the review form
    # from draft_snapshot iff review_status == "draft" on reopen.
    review_status: str | None = None
    draft_snapshot: str | None = None  # JSON blob (checkpoints + comment + notes)
    draft_saved_at: datetime | None = None
    # HITL Task 33: optimistic lock token. Frontend stashes the latest value
    # from GET and sends `If-Match: <revision>` on mutations so concurrent
    # reviewer/lead edits collide with a 409 instead of silently clobbering.
    revision: int = 1
    # Step 3/4: full provider-response JSONB. Frontend reads these in the
    # "Everything captured" summary panel on the call detail page. Null on
    # calls uploaded before 2026-04-19 — the panel self-hides in that case.
    deepgram_metadata: dict | None = None
    assemblyai_metadata: dict | None = None
    openai_whisper_metadata: dict | None = None
    # Human-friendly identifiers from migration f1a2b3c4d5e6.
    call_ref: str | None = None
    slug: str | None = None
    # V2 wiring (migration 4253da0ac3d9): deal linkage + call-type metadata.
    deal_id: Optional[UUID] = None
    call_type: Optional[str] = None
    supplier_variant: Optional[str] = None
    segments: List[Any] = Field(default_factory=list)
    flags: List[Any] = Field(default_factory=list)
    # W1 (v3-watt-coverage): per-call risk-tag chips
    # (Ombudsman/Mis-selling/Complaint/Cancellation/Vulnerable).
    # Optional[List[str]] coerced to list — DB column is nullable so older
    # rows return None; pydantic validator below normalises to [].
    risk_tags: Optional[List[str]] = Field(default_factory=list)

    # Wave-50 (2026-05-28): non-compliance data-quality warnings, e.g.
    # customer_name_mismatch (uploaded recording's detected business name
    # strongly diverges from the deal it was attached to). SEPARATE from
    # ``flags`` (compliance findings) so the call-detail banner never
    # mixes data-quality noise into the compliance report. Typed inner
    # model gives the frontend a stable {code, message} contract
    # (python-reviewer a4d7da00d77a76ce2). ``extra="allow"`` future-proofs
    # the row shape without breaking older callers.
    data_quality_warnings: List[DataQualityWarning] = Field(default_factory=list)

    # 2026-05-16 perf — pre-signed Supabase Storage URL for the call audio.
    # Populated by `get_call` so the call-detail page can start audio
    # playback without a second round-trip to /api/calls/{id}/audio-url.
    # Null when the call has no `audio_storage_key` (legacy on-disk uploads
    # or in-progress uploads) — frontend falls back to the dedicated
    # /audio-url endpoint in that case.
    audio_url: Optional[str] = None

    # ``meta`` is declared BEFORE the derived fields below so the
    # before-validators on ``transcript_agreement`` and ``diarization``
    # can read ``info.data["meta"]`` — Pydantic v2 only populates
    # ``info.data`` with fields validated in declaration order.
    meta: Optional[dict] = None

    # 2026-05-17 two-layer validation — Deepgram vs AssemblyAI
    # agreement report. Surfaced on the call-detail Transcript tab as
    # the divergence chip + side-by-side comparison drawer. Populated
    # by ``pipeline._step_transcribe`` and stored on Call.meta. Null
    # when only one engine returned a transcript or on legacy calls.
    transcript_agreement: Optional[dict] = None

    # 2026-05-17 — surface which engine's diarization the transcript
    # player is using and whether it had to fall back to single-speaker.
    diarization: Optional[dict] = None

    @model_validator(mode="after")
    def _hydrate_from_meta(self):
        # Pull ``transcript_agreement`` + ``diarization`` out of the
        # ``meta`` JSONB column when the caller hasn't already set them
        # explicitly. ``field_validator(mode="before")`` was unreliable
        # against ORM + ``from_attributes=True`` because ``info.data``
        # is not populated incrementally with non-validated attributes.
        # ``model_validator(mode="after")`` runs after every field is
        # built, so ``self.meta`` is guaranteed populated.
        if isinstance(self.meta, dict):
            if self.transcript_agreement is None:
                self.transcript_agreement = self.meta.get("transcript_agreement")
            if self.diarization is None:
                self.diarization = self.meta.get("diarization")
        return self

    @field_validator("risk_tags", mode="before")
    @classmethod
    def _risk_tags_default(cls, v):
        return v if v is not None else []

    @field_validator("segments", "flags", mode="before")
    @classmethod
    def _orm_to_dict(cls, v):
        # Call.segments / Call.flags are viewonly SQLAlchemy relationships.
        # Pydantic's from_attributes mode walks them and tries to serialize
        # the ORM objects as plain values — fails with PydanticSerializationError
        # because the analyzer-written rows have non-JSONable columns.
        # The dedicated /api/calls/{id}/segments endpoint owns the real shape;
        # here we just need a clean (and stable) summary tuple so the call
        # detail GET doesn't 500.
        if v is None:
            return []
        if not isinstance(v, list):
            try:
                v = list(v)
            except TypeError:
                return []
        out = []
        for item in v:
            if isinstance(item, dict):
                out.append(item)
                continue
            # ORM row — extract a small set of stable, JSON-safe fields.
            d: dict = {}
            for attr in (
                "id", "idx", "stage", "score", "bucket", "compliant",
                "compliance_status", "critical_breaches", "high_breaches",
                "medium_breaches", "reason",
                "severity", "rule_id", "evidence",  # Flag attrs
            ):
                if hasattr(item, attr):
                    val = getattr(item, attr)
                    if val is None or isinstance(val, (str, int, float, bool)):
                        d[attr] = val
                    else:
                        d[attr] = str(val)
            if d:
                out.append(d)
        return out

    class Config:
        from_attributes = True


class CallSummary(BaseModel):
    """Lightweight row for list views — omits transcript/word_data/snapshot to
    keep the payload small and the SQL under Supabase's statement timeout."""
    id: str
    filename: str
    file_size: int | None
    duration_seconds: float | None
    status: str
    compliant: bool | None
    agent_name: str | None
    customer_name: str | None
    script_id: str | None
    score: str | None
    detected_supplier: str | None
    rule_id: str
    created_at: datetime
    completed_at: datetime | None
    compliance_status: str | None = None
    review_status: str | None = None
    reason: str | None = None
    # 2026-05-16 audit P2-7 fix — `list_calls` selects both columns from
    # the DB, but the response model was silently dropping them, so the
    # /calls list page rendered every row with "NULL stage" and no deal
    # linkage. Surface them here so the table can show stage + deal
    # without a second fetch.
    call_type: str | None = None
    deal_id: UUID | None = None
    # Wave-26 (2026-05-27) — per-call segment chips. Bulk-loaded in the
    # list_calls handler via app.segment_chips.fetch_segments_by_call_ids
    # so a multi-segment call (lead_gen + pre_sales + verbal + loa) is
    # surfaced on /calls instead of a single call_type pill.
    # Field(default_factory=list) for consistency with DealCallSlot —
    # avoids any chance of a shared mutable default across instances
    # (python-reviewer 2026-05-27 trio nit).
    segments: list[dict] = Field(default_factory=list)

    class Config:
        from_attributes = True


class CallListResponse(BaseModel):
    calls: list[CallSummary]
    total: int


class StatsResponse(BaseModel):
    total_calls: int
    compliant_count: int
    non_compliant_count: int
    compliance_rate: float
    processing_count: int
    needs_review_count: int = 0
    reviewed_count: int = 0
    automated_rate: float = 0.0  # % of checkpoints resolved without human review


# ---------------------------------------------------------------------------
# V2 wiring: CustomerDeal schemas (migration 4253da0ac3d9).
# Mirrors app.models.CustomerDeal. assigned_agent_id is a str because
# profiles.id is varchar (not UUID).
# ---------------------------------------------------------------------------
class DealMeter(BaseModel):
    """W1 (v3-watt-coverage): one MPAN or MPRN attached to a deal.

    A dual-fuel deal carries 2 entries (one for elec MPAN + one for gas MPRN).
    Either field may be null but at least one must be present per row.
    """
    mpan: Optional[str] = None
    mprn: Optional[str] = None


class CustomerDealBase(BaseModel):
    customer_name: str
    supplier: Optional[str] = None
    status: str = "in_progress"
    deal_value_gbp: Optional[Decimal] = None
    mpan_or_mprn: Optional[str] = None
    expected_live_date: Optional[date] = None
    risk_tags: List[str] = Field(default_factory=list)
    rejection_category: Optional[str] = None
    assigned_agent_id: Optional[str] = None  # profiles.id is varchar, not UUID
    # W1 (v3-watt-coverage): Watt portal deep-link for this deal (X1).
    external_watt_site_id: Optional[int] = None
    # W1 (v3-watt-coverage): meter array — replaces single mpan_or_mprn for
    # dual-fuel deals (X2). Backfilled from mpan_or_mprn during migration.
    meters: List[DealMeter] = Field(default_factory=list)


class CustomerDealCreate(CustomerDealBase):
    pass


class CustomerDealOut(CustomerDealBase):
    id: UUID
    created_at: datetime
    final_score: Optional[Decimal] = None
    final_action: Optional[str] = None
    pipeline_workflow_id: Optional[str] = None

    class Config:
        from_attributes = True


class EditCallMetadataRequest(BaseModel):
    """Reviewer override of auto-detected metadata on a single Call.

    Every field is optional — only the fields the reviewer touched come
    back from the dialog. Empty-string values count as "clear" and write
    None into the underlying Call/Deal/Customer rows.
    """
    customer_name: Optional[str] = None
    agent_name: Optional[str] = None
    mpan_or_mprn: Optional[str] = None
    expected_live_date: Optional[str] = None  # ISO yyyy-mm-dd
    deal_value_gbp: Optional[float] = None
    supplier: Optional[str] = None
    contract_length_months: Optional[int] = None
    notes: Optional[str] = None

    # Length caps protect the DB columns + stop "200KB of nonsense pasted
    # into the textarea" footguns. customer_name + agent_name also get
    # whitespace-collapsed so "  Awais  " can't bypass the shrink guard
    # that the route layer performs after this validator runs.
    @field_validator("customer_name", "agent_name", mode="before")
    @classmethod
    def _normalise_short_text(cls, v):
        if v is None:
            return v
        if not isinstance(v, str):
            return v
        # Collapse internal whitespace + strip — defends against
        # accidental "tab-tab Save" pre-fills with hidden whitespace.
        cleaned = " ".join(v.split())
        if len(cleaned) > 200:
            raise ValueError("name fields are capped at 200 characters")
        return cleaned

    @field_validator("mpan_or_mprn", "supplier", mode="before")
    @classmethod
    def _normalise_identifier(cls, v):
        if v is None or not isinstance(v, str):
            return v
        cleaned = v.strip()
        if len(cleaned) > 120:
            raise ValueError("identifier fields are capped at 120 characters")
        return cleaned

    @field_validator("notes", mode="before")
    @classmethod
    def _cap_notes(cls, v):
        if v is None or not isinstance(v, str):
            return v
        cleaned = v.strip()
        if len(cleaned) > 4000:
            raise ValueError("notes are capped at 4000 characters")
        return cleaned
