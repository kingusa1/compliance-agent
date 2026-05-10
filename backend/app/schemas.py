from datetime import datetime, date
from decimal import Decimal
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("risk_tags", mode="before")
    @classmethod
    def _risk_tags_default(cls, v):
        return v if v is not None else []

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
