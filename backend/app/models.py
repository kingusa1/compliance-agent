import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base

# JSONB is Postgres-only. Tests run on SQLite where JSONB can't bind Python
# dicts. This TypeDecorator emits JSONB DDL on Postgres and TEXT on SQLite,
# and handles JSON serialization/deserialization on the SQLite path so
# `default=dict` works in both environments.
import json as _json

try:
    from sqlalchemy.dialects.postgresql import JSONB as _JSONB

    class _JSONBOrText(TypeDecorator):
        impl = sa.Text
        cache_ok = True

        def load_dialect_impl(self, dialect):
            if dialect.name == "postgresql":
                return dialect.type_descriptor(_JSONB())
            return dialect.type_descriptor(sa.Text())

        def process_bind_param(self, value, dialect):
            if dialect.name != "postgresql" and value is not None:
                return _json.dumps(value)
            return value

        def process_result_value(self, value, dialect):
            if dialect.name != "postgresql" and value is not None and isinstance(value, str):
                return _json.loads(value)
            return value

    JSONBCompat = _JSONBOrText
except ImportError:
    JSONBCompat = Text  # type: ignore[assignment,misc]

# TextArray — Postgres `TEXT[]`, SQLite `TEXT` (JSON-encoded list). Used
# for ``calls.risk_tags`` (W1 watt-coverage) which is a real PG array
# column, not JSONB. Round-trips Python ``list[str]`` either way.
try:
    from sqlalchemy.dialects.postgresql import ARRAY as _PGARRAY

    class _TextArrayOrJsonText(TypeDecorator):
        impl = sa.Text
        cache_ok = True

        def load_dialect_impl(self, dialect):
            if dialect.name == "postgresql":
                return dialect.type_descriptor(_PGARRAY(sa.Text()))
            return dialect.type_descriptor(sa.Text())

        def process_bind_param(self, value, dialect):
            if value is None:
                return None
            if dialect.name == "postgresql":
                # psycopg2 takes Python list directly for TEXT[].
                return list(value)
            return _json.dumps(list(value))

        def process_result_value(self, value, dialect):
            if value is None:
                return None
            if dialect.name == "postgresql":
                return list(value)
            if isinstance(value, str):
                try:
                    return _json.loads(value)
                except Exception:
                    return []
            return value

    TextArrayCompat = _TextArrayOrJsonText
except ImportError:
    TextArrayCompat = Text  # type: ignore[assignment,misc]

# UUIDCompat — Postgres ``UUID``, SQLite ``CHAR(36)`` storing the canonical
# hex string. Tolerates ``str``, ``uuid.UUID``, or ``None`` on bind so tests
# (which pass plain strings) and production code (which passes UUID objects)
# both work. Raw ``PGUUID(as_uuid=True)`` blows up on SQLite because the
# fallback path calls ``.hex`` on the bound value, which strings don't have.
class _UUIDOrChar(TypeDecorator):
    impl = sa.CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import UUID as _PGUUID

            return dialect.type_descriptor(_PGUUID(as_uuid=True))
        return dialect.type_descriptor(sa.CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        # SQLite — store as canonical hex string.
        return str(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


UUIDCompat = _UUIDOrChar


# pgvector is Postgres-only. Tests run on SQLite via tests/conftest.py's
# `Base.metadata.create_all`; SQLAlchemy happily ignores the `Vector`
# column type there (emits a no-op TypeDecorator column). Production writes
# go through the Postgres engine where the column is a real `vector(1536)`.
try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover — pgvector missing in minimal envs
    Vector = None  # type: ignore[assignment]


class Script(Base):
    __tablename__ = "scripts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    supplier_name = Column(String, nullable=False)
    script_name = Column(String, nullable=False)
    version = Column(String)
    mode = Column(String, default="meaning_for_meaning")
    checkpoints = Column(Text, nullable=False)  # JSON array
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime)
    organization_id = Column(String, nullable=True)  # Multi-tenancy (Phase 2)

    # L3: which lifecycle stage of the deal this script governs. Backfilled
    # by name pattern in migration c3d4e5f6a7b8. Allowed:
    # lead_gen|closer|amendment|c_call|standalone_loa|passover|full.
    lifecycle_phase = Column(String, nullable=True)

    versions = relationship("ScriptVersion", back_populates="script", order_by="ScriptVersion.version_number")


class ScriptVersion(Base):
    """Immutable snapshot of a script at a point in time."""
    __tablename__ = "script_versions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    script_id = Column(String, ForeignKey("scripts.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    checkpoints_snapshot = Column(Text, nullable=False)  # JSON array snapshot
    mode_snapshot = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    script = relationship("Script", back_populates="versions")


class ScriptLineMapping(Base):
    """W4.2 — supplier-script-line ↔ canonical checkpoint name.

    Seeded from XLSX deep-dive §8 (15 rows from real Watt rejection narratives).
    Joined to numbered script lines by ``GET /api/scripts/{id}/lines`` so the
    frontend can render "[L17] prices EXCLUDE VAT" badges that point back to a
    known checkpoint identifier the AI also references.
    """
    __tablename__ = "script_line_mappings"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier = Column(Text, nullable=False)              # e.g. 'E.ON'
    script_section = Column(Text, nullable=False)        # e.g. 'EON Verbal', 'LOA'
    line_number = Column(Integer, nullable=True)         # nullable — some checkpoints have no line ref
    checkpoint_name = Column(Text, nullable=False)       # human-readable label
    internal_key = Column(Text, nullable=False, unique=True)  # canonical id e.g. 'eon_verbal_l20_microbusiness_status'
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class Call(Base):
    __tablename__ = "calls"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    audio_storage_key = Column(String, nullable=True, index=True)
    file_size = Column(Integer)
    # SHA-256 of the uploaded audio bytes — used to dedup re-uploads of
    # the same recording (same content, possibly different filename).
    # Indexed because every upload does a hash lookup before persisting.
    file_hash = Column(String, nullable=True, index=True)
    duration_seconds = Column(Float)
    status = Column(String, default="processing")
    transcript = Column(Text)  # Deepgram transcript (fast, structured)
    gemini_transcript = Column(Text)  # Gemini transcript (accurate, primary for analysis)
    assemblyai_transcript = Column(Text)  # AssemblyAI Universal-3 Pro transcript (primary, most accurate)
    groq_whisper_transcript = Column(Text)  # Whisper Large v3 via Groq (86.7% agreement, fast + free)
    cohere_transcript = Column(Text)  # Cohere cohere-transcribe-03-2026 (87.4% agreement, second overall)
    word_data = Column(Text)  # JSON array of per-word data [{word, start, end, speaker, confidence, punctuated_word}]
    compliant = Column(Boolean)
    reason = Column(Text)
    excerpt = Column(Text)
    agent_name = Column(String)
    customer_name = Column(String)
    script_id = Column(String)
    script_version_id = Column(String, ForeignKey("script_versions.id"))
    checkpoint_results = Column(Text)  # JSON array
    score = Column(String)  # e.g. "5/7"
    detected_supplier = Column(String)
    rule_id = Column(String, default="THIRD_PARTY_DISCLOSURE")
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    organization_id = Column(String, nullable=True)  # Multi-tenancy (Phase 2)

    # L1 durability tracking (sprint v2-enterprise) — set by _logged_step before each step,
    # cleared on success, surfaced via /api/observability/stuck + StepDrawer + StuckBanner.
    last_step_started_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_step_name = Column(String, nullable=True)
    last_step_error = Column(Text, nullable=True)
    watchdog_redispatch_count = Column(Integer, nullable=False, server_default="0", default=0)

    # HITL additions
    review_status = Column(String, default="unclaimed", server_default="unclaimed", index=True, nullable=False)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String, nullable=True)
    compliance_status = Column(String, default="pending", server_default="pending", index=True, nullable=False)
    compliance_source = Column(String, nullable=True)
    compliance_decided_at = Column(DateTime, nullable=True)
    compliance_decided_by = Column(String, nullable=True)
    compliance_comment = Column(Text, nullable=True)

    # AI/HUMAN provenance gate. AI_PENDING after auto-categorization, HUMAN_CONFIRMED
    # after reviewer Confirms, HUMAN_OVERRIDDEN if reviewer edited any field then saved.
    # Compliant/non-compliant pages exclude AI_PENDING — only human-touched calls count.
    verdict_state = Column(String, default="AI_PENDING", server_default="AI_PENDING", nullable=False, index=True)

    # Draft autosave (Task 21): freeform JSON snapshot of in-progress review state.
    # Written every ~10s while a reviewer is working; hydrated on reopen so a
    # browser refresh / crash / accidental tab close doesn't lose their work.
    draft_snapshot = Column(Text, nullable=True)
    draft_saved_at = Column(DateTime, nullable=True)

    # Optimistic locking (Task 33): monotonically increasing counter bumped by
    # every mutating HITL endpoint (claim, release, verdict, edit-word,
    # compliance). Callers that cached the row can send `If-Match: <revision>`;
    # a mismatch returns 409 so the reviewer can refetch before their save
    # silently overwrites a concurrent edit (e.g. a lead reopen during submit).
    # Drafts intentionally skip the bump — see hitl_routes.save_draft.
    revision = Column(Integer, default=1, server_default="1", nullable=False)

    # Task 37: arbitrary key-value metadata for rich queue filtering.
    # Populated at end of pipeline; queried via GIN index on Postgres.
    # Falls back to Text on SQLite (tests).
    meta = Column(JSONBCompat, default=dict, server_default="{}", nullable=False)

    # Task 25: double-review / inter-annotator agreement.
    required_reviews = Column(Integer, default=1, server_default="1", nullable=False)
    completed_reviews = Column(Integer, default=0, server_default="0", nullable=False)

    # W1 (v3-watt-coverage): per-call risk-tag chips
    # (Ombudsman/Mis-selling/Complaint/Cancellation/Vulnerable). On Postgres
    # this is a real ``text[]`` column (per migration d10e5f3a8b91), so we
    # MUST use TextArrayCompat — JSONBCompat would serialise [] to JSON
    # ``'[]'`` and the array column would reject it (malformed array
    # literal). On SQLite (tests) TextArrayCompat falls back to JSON-text.
    # nullable=True on Postgres (Supabase pooler can't do full-table rewrite
    # to set NOT NULL DEFAULT in one DDL); app-side coerces NULL → [].
    risk_tags = Column(TextArrayCompat, nullable=True, default=list)

    # V2 Wiring Task 5: deal/call-type/supplier-variant linkage.
    deal_id = Column(PGUUID(as_uuid=True), ForeignKey("customer_deals.id", ondelete="SET NULL"), nullable=True, index=True)
    call_type = Column(Text, nullable=True)
    supplier_variant = Column(Text, nullable=True)

    # Step 1 (migration f1a2b3c4d5e6): human-friendly identifiers + full-fidelity
    # provider capture. call_ref is the citation code (CA-2026-0001) reviewers
    # use in emails and disputes; slug is the URL segment derived from filename.
    # The five *_metadata / log columns preserve everything the providers return
    # so the UI can count what was captured and the admin raw-JSON tab can audit
    # that nothing was silently dropped.
    call_ref = Column(String, nullable=True, unique=True, index=True)
    slug = Column(String, nullable=True, unique=True, index=True)
    deepgram_metadata = Column(JSONBCompat, nullable=True)
    assemblyai_metadata = Column(JSONBCompat, nullable=True)
    openai_whisper_metadata = Column(JSONBCompat, nullable=True)
    processing_log = Column(JSONBCompat, nullable=True)
    raw_llm_io = Column(JSONBCompat, nullable=True)

    checkpoints = relationship("CallCheckpoint", back_populates="call", order_by="CallCheckpoint.id")
    # Schema CallResponse expects ``segments`` and ``flags`` lists; without
    # the relationships pydantic.from_attributes raises AttributeError at
    # response-serialization time and the route returns 500 even though
    # the DB writes succeeded. Lazy=select keeps the query cheap when the
    # caller doesn't touch them.
    segments = relationship(
        "CallSegment",
        primaryjoin="Call.id==foreign(CallSegment.call_id)",
        order_by="CallSegment.idx",
        viewonly=True,
    )
    flags = relationship(
        "Flag",
        primaryjoin="Call.id==foreign(Flag.call_id)",
        viewonly=True,
    )


class CallCheckpoint(Base):
    __tablename__ = "call_checkpoints"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    call_id = Column(String, ForeignKey("calls.id"), nullable=False)
    # 2026-05-12: nullable FK to the CallSegment that produced this row.
    # NULL for legacy rows graded under the old single-rubric pipeline.
    segment_id = Column(PGUUID(as_uuid=True), ForeignKey("call_segments.id", ondelete="SET NULL"), nullable=True, index=True)
    rule_text = Column(Text, nullable=False)
    passed = Column(Boolean, nullable=False)
    excerpt = Column(Text)
    confidence = Column(String, default="high")  # "high" or "low"
    needs_review = Column(Boolean, default=False)  # True when confidence is "low"
    reviewer_verdict = Column(String, nullable=True)  # "pass" / "fail" — set by human reviewer
    reviewer_notes = Column(Text, nullable=True)  # Human reviewer's notes
    organization_id = Column(String, nullable=True)  # Multi-tenancy (Phase 2)
    # W1 (v3-watt-coverage): script-line number associated with this checkpoint.
    # Watt's reviewers operate on "amendment for line 11-14" syntax (see XLSX
    # deep-dive §1.5, §8). Backfilled from script.checkpoints JSON when an
    # explicit line number is present; null otherwise.
    line_number = Column(Integer, nullable=True)
    # W4.7 (v3-watt-coverage): Claude's own bucket + remediation suggestion
    # for FAIL/PARTIAL checkpoints. Recorded on the call_checkpoints row at
    # analysis time; ``rejections_routes.auto_create_rejection_for_verdict``
    # prefers these over the keyword heuristic when ai_category_confidence
    # is ≥ 0.7. All three nullable — pre-W4.7 rows + analyzer errors leave
    # them empty, which routes us back to the legacy infer_category path.
    ai_category = Column(Text, nullable=True)
    ai_fix_required = Column(Text, nullable=True)
    ai_category_confidence = Column(Float, nullable=True)
    # Sprint A1 (v3-watt-coverage W5) — Claude's own rejection-tracker
    # narrative for FAIL/PARTIAL checkpoints. ai_rejection_reason is the
    # one-line headline (≤120 chars) that flows onto Rejection.rejection_reason
    # when auto_create_rejection_for_verdict fires; ai_narrative_notes is the
    # 2-4 sentence coaching text that flows onto Rejection.outcome_narrative.
    # Both nullable — pre-A1 rows leave them empty and the rejection auto-
    # create path falls back to the manual reviewer reason.
    ai_rejection_reason = Column(Text, nullable=True)
    ai_narrative_notes = Column(Text, nullable=True)

    call = relationship("Call", back_populates="checkpoints")


class AgentLearning(Base):
    """Permanent anonymized learnings from human review corrections.

    Deliberately contains NO PII — no call_id, no transcript excerpts, no
    names. Only abstracted patterns and lessons that persist across tenant
    data retention boundaries. Powers the get_similar_learnings agent tool.
    """
    __tablename__ = "agent_learnings"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    supplier = Column(String, nullable=False, index=True)
    checkpoint_name = Column(String, nullable=False, index=True)
    pattern = Column(Text, nullable=False)          # "agent used vague language like 'we work with suppliers'"
    agent_verdict = Column(String, nullable=False)  # "pass" | "partial" | "fail"
    human_verdict = Column(String, nullable=False)  # "pass" | "partial" | "fail"
    lesson = Column(Text, nullable=False)           # "require explicit broker terminology"
    created_at = Column(DateTime, default=datetime.utcnow)

    # Semantic search (Phase J Task 29) — OpenAI text-embedding-3-small returns
    # 1536-dim vectors. Populated on insert in abstract_and_store_review() and
    # queried via cosine distance (<=>) in get_similar_learnings().
    # Nullable so a failed embedding call doesn't block the lesson row.
    embedding = Column(Vector(1536), nullable=True) if Vector is not None else Column(Text, nullable=True)


def _utcnow():
    return datetime.utcnow()


class ReviewSession(Base):
    __tablename__ = "review_sessions"
    id = Column(String, primary_key=True)
    call_id = Column(String, ForeignKey("calls.id"), index=True, nullable=False)
    reviewer_id = Column(String, index=True, nullable=False)
    claimed_at = Column(DateTime, default=_utcnow, nullable=False)
    last_activity_at = Column(DateTime, default=_utcnow, index=True, nullable=False)
    released_at = Column(DateTime, nullable=True)
    release_reason = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, index=True, nullable=False)


class VerdictHistory(Base):
    __tablename__ = "verdict_history"
    id = Column(String, primary_key=True)
    call_id = Column(String, ForeignKey("calls.id"), index=True, nullable=False)
    checkpoint_id = Column(String, index=True, nullable=False)
    review_session_id = Column(String, ForeignKey("review_sessions.id"), nullable=True)
    actor_type = Column(String, nullable=False)   # ai | reviewer | lead
    actor_id = Column(String, nullable=False)
    verdict = Column(String, nullable=False)       # pass | fail | partial | flagged
    reasoning = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    evidence_text = Column(Text, nullable=True)
    # 12-char sha256 of the supplier-specific prompt that produced this verdict.
    # Stamped on insert by hitl_routes / the pipeline via
    # app.prompts.version_for_supplier. Nullable because pre-Task-32 rows
    # won't have one. Indexed so "override rate by prompt version" queries
    # don't scan the table.
    prompt_version = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True, nullable=False)
    is_current = Column(Boolean, default=True, index=True, nullable=False)


class TranscriptEdit(Base):
    __tablename__ = "transcript_edits"
    id = Column(String, primary_key=True)
    call_id = Column(String, ForeignKey("calls.id"), index=True, nullable=False)
    word_index = Column(Integer, nullable=False)
    word_start_ms = Column(Integer, nullable=False)
    old_text = Column(String, nullable=False)
    new_text = Column(String, nullable=False)
    edited_by = Column(String, nullable=False)
    review_session_id = Column(String, ForeignKey("review_sessions.id"), nullable=True)
    triggered_checkpoint_id = Column(String, nullable=True)
    triggered_reanalysis = Column(Boolean, default=False, nullable=False)
    reanalysis_changed_verdict = Column(Boolean, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True, nullable=False)


class ClaimLock(Base):
    __tablename__ = "claim_locks"
    call_id = Column(String, ForeignKey("calls.id"), primary_key=True)
    reviewer_id = Column(String, nullable=False)
    review_session_id = Column(String, ForeignKey("review_sessions.id"), nullable=False)
    claimed_at = Column(DateTime, default=_utcnow, nullable=False)
    expires_at = Column(DateTime, index=True, nullable=False)


class ComplianceDecision(Base):
    __tablename__ = "compliance_decisions"
    id = Column(String, primary_key=True)
    call_id = Column(String, ForeignKey("calls.id"), index=True, nullable=False)
    status = Column(String, nullable=False)        # compliant | non_compliant
    actor_type = Column(String, nullable=False)    # system | reviewer | lead
    actor_id = Column(String, nullable=False)
    comment = Column(Text, nullable=True)
    failing_checkpoints = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True, nullable=False)
    is_current = Column(Boolean, default=True, index=True, nullable=False)
    # L4: 5-state action vocabulary (PASS|REVIEW|COACHING|FAIL|BLOCK) used
    # by ComplianceDecisionPanel. Constraint enforced in d4e5f6a7b8c9.
    action = Column(String, nullable=True)


class VerdictSuggestion(Base):
    """AI-generated verdict for a checkpoint (Task 28)."""
    __tablename__ = "verdict_suggestions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    call_id = Column(String, ForeignKey("calls.id"), index=True, nullable=False)
    checkpoint_id = Column(String, index=True, nullable=False)
    verdict = Column(String, nullable=False)
    confidence = Column(Float, nullable=True)
    reasoning = Column(Text, nullable=True)
    prompt_version = Column(String, nullable=True, index=True)
    model = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    superseded_by = Column(String, nullable=True)


class VerdictResponse(Base):
    """Human response to an AI suggestion (Task 28)."""
    __tablename__ = "verdict_responses"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    suggestion_id = Column(String, ForeignKey("verdict_suggestions.id"), index=True, nullable=False)
    call_id = Column(String, ForeignKey("calls.id"), index=True, nullable=False)
    checkpoint_id = Column(String, index=True, nullable=False)
    actor_id = Column(String, nullable=False)
    actor_role = Column(String, nullable=False)  # reviewer | lead
    verdict = Column(String, nullable=False)
    agreed_with_ai = Column(Boolean, nullable=False)
    reasoning = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    is_current = Column(Boolean, default=True, index=True, nullable=False)


class Profile(Base):
    """Mirrors auth.users.id one-to-one. Holds role and display name for reviewers.

    Note: Postgres-level `server_default` is owned by Alembic migrations
    (see 72f574ad4871 + a1b3c5e7f9d0). We deliberately don't re-declare those
    here because `Base.metadata.create_all` is the tests' path — and SQLite
    can't parse `now()` or unquoted `true`. Python-side `default=` keeps ORM
    inserts ergonomic in both environments.
    """
    __tablename__ = "profiles"
    id = Column(String, primary_key=True)  # matches auth.users.id (UUID string)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    role = Column(String, default="reviewer", nullable=False)  # reviewer | lead | admin
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    # L4: retraining flag toggled by lead via PATCH /api/agents/{name}.
    # Surfaces on the agent drilldown header + leaderboard.
    retraining_assigned = Column(Boolean, default=False, nullable=False)
    retraining_reason = Column(Text, nullable=True)


class AgentTrace(Base):
    """Persisted reasoning trace for one agent run on one batch of checkpoints.

    Each row is a turn in the agent's tool-use conversation: the initial
    user prompt, each assistant response (with or without tool calls), and
    each tool's output. Grouped by `run_id` so the HITL UI can show the
    complete chain-of-thought for a checkpoint alongside its verdict.

    checkpoint_id is nullable because the agent processes BATCHES of
    checkpoints — a single run produces verdicts for up to 6 at once, and
    most turns are shared across them. The call-level filter (`call_id`)
    is the primary access path; the checkpoint filter narrows to a single
    run when the UI wants to scope to one cp.
    """
    __tablename__ = "agent_traces"
    id = Column(String, primary_key=True)
    call_id = Column(String, ForeignKey("calls.id"), index=True, nullable=False)
    checkpoint_id = Column(String, index=True, nullable=True)
    run_id = Column(String, index=True, nullable=False)
    turn = Column(Integer, nullable=False)
    role = Column(String, nullable=False)        # "user" | "assistant" | "tool"
    tool_name = Column(String, nullable=True)
    tool_input = Column(Text, nullable=True)     # JSON
    tool_output = Column(Text, nullable=True)    # JSON
    content = Column(Text, nullable=True)        # LLM message text
    model = Column(String, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True, nullable=False)


class TraceAnnotation(Base):
    """Reviewer feedback on individual agent reasoning steps (Task 35)."""
    __tablename__ = "trace_annotations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    trace_id = Column(String, ForeignKey("agent_traces.id"), index=True, nullable=False)
    actor_id = Column(String, nullable=False)
    score = Column(Integer, nullable=False)  # -1 bad, 0 neutral, +1 good
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class SavedView(Base):
    """Persisted queue filter preset (Task 26)."""
    __tablename__ = "saved_views"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False)
    filters = Column(Text, nullable=False)  # JSON: {supplier, agent, status, ...}
    is_shared = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    # L8: bumped on every PATCH for ordering / freshness.
    updated_at = Column(DateTime, default=_utcnow, nullable=False)


class CustomerDeal(Base):
    """V2 Wiring Task 5: customer deal record aggregating calls, evidence, and pipeline state.

    `assigned_agent_id` is typed as String (not UUID) because `profiles.id` is varchar.
    See migration 4253da0ac3d9.

    Note: same pattern as `Profile` — Postgres-specific `server_default` clauses
    (`gen_random_uuid()`, `NOW()`, `'[]'::jsonb`) live in the Alembic migration,
    NOT here, because `Base.metadata.create_all` on SQLite (tests) can't parse
    them. Python-side `default=` keeps ORM inserts ergonomic in both envs.
    JSONBCompat emits JSONB on Postgres and TEXT on SQLite.
    """
    __tablename__ = "customer_deals"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_name = Column(Text, nullable=False, index=True)
    supplier = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    status = Column(Text, nullable=False, default="in_progress", index=True)
    deal_value_gbp = Column(Numeric, nullable=True)
    mpan_or_mprn = Column(Text, nullable=True)
    expected_live_date = Column(Date, nullable=True)
    final_score = Column(Numeric, nullable=True)
    final_action = Column(Text, nullable=True)
    risk_tags = Column(JSONBCompat, nullable=False, default=list)
    rejection_category = Column(Text, nullable=True)
    assigned_agent_id = Column(String, ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    pipeline_workflow_id = Column(Text, nullable=True)

    # L3 lifecycle: derive_lifecycle_status writes here. CHECK constraint
    # in c3d4e5f6a7b8 enforces vocabulary. Defaults to 'open' so partial
    # rows match the derive function's open-state.
    lifecycle_status = Column(String, nullable=False, default="open", server_default="open")
    # L3 LOA evidence: bundled (within main call) | standalone_call |
    # document_attached | missing.
    loa_status = Column(String, nullable=True, default="missing")
    loa_document_url = Column(Text, nullable=True)
    # L7 intake: meter identifiers split. customer_deals.mpan_or_mprn is
    # retained read-only for legacy.
    mpan_electricity = Column(Text, nullable=True)
    mprn_gas = Column(Text, nullable=True)
    # Commission shape (pct|gbp).
    commission_value = Column(Numeric, nullable=True)
    commission_unit = Column(String, nullable=True)
    # Term length in months — constrained to 12/24/36/48/60.
    term_months = Column(Integer, nullable=True)
    # Optional DocuSign envelope reference for offline evidence.
    docusign_reference = Column(Text, nullable=True)
    # L7: link to the new Customer table. Nullable so legacy rows without
    # a customer row keep working until the backfill completes.
    customer_id = Column(PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=True, index=True)
    # W1 (v3-watt-coverage): Watt portal deep-link. Every rejection-tracker
    # row in the XLSX has a hyperlink to api.wattutilities.co.uk:4433/sites/{N}.
    # See `.planning/v3-rebuild/2026-05-03-watt-xlsx-deep-dive.md` §1.3, X1.
    external_watt_site_id = Column(Integer, nullable=True, index=True)
    # W1 (v3-watt-coverage): meter array — Watt deals can carry multiple
    # MPAN/MPRN (dual-fuel = 2 meters). Legacy ``mpan_or_mprn`` is retained
    # for backwards compatibility; new inserts populate both fields.
    # Shape: [{mpan?: str, mprn?: str}].
    meters = Column(JSONBCompat, nullable=False, default=list, server_default="[]")
    # Sprint C2 (v3-watt-coverage W5) — back-link to the Rejection that ended
    # this deal (NULL while the deal is open or won). ``rejections_routes.
    # auto_create_rejection_for_verdict`` populates this + flips ``status``
    # to ``closed_lost`` whenever a reviewer marks a checkpoint FAIL. Lets
    # /customers/[slug] + /deals/[id] surface lost deals with a deep-link to
    # the rejection-tracker row that killed them.
    rejection_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("rejections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Sprint B (tracker XLSX parity): per-field provenance map. Keys are
    # column names (e.g. "customer_name", "supplier"), values are source
    # tags ("ai" | "xlsx_import" | "reviewer_edit" | "placeholder"). Migration
    # ``c0d3a1b2c3d4`` adds the DB column with NOT NULL + server_default '{}'.
    # JSONBCompat keeps SQLite tests working.
    field_sources = Column(JSONBCompat, nullable=False, server_default="{}", default=dict)
    # 2026-05-15 deal-linker provenance — set when intake.matcher attaches
    # a new call to an existing deal. ``match_method`` is one of:
    #   hard_key:mpan | hard_key:mprn | hard_key:docusign |
    #   hard_key:company_number | hard_key:charity_number |
    #   composite_auto | composite_review | reviewer_picked | legacy
    # ``match_confidence`` is 0.0-1.0 (1.0 for hard keys, calibrated
    # posterior for composite). NULL on rows created before this column
    # landed or via the legacy upsert path.
    match_method = Column(String, nullable=True)
    match_confidence = Column(Numeric, nullable=True)


class FixDirective(Base):
    """Reviewer-raised follow-up on a call. State machine:
    pending -> in_progress -> submitted -> fixed (or dead)."""
    __tablename__ = "fix_directives"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id = Column(String, ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_id = Column(String, ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="pending")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)
    fixed_at = Column(DateTime, nullable=True)
    # FK enforced at DB layer (migration 497bd38e5551). Skipping ORM
    # ForeignKey() to avoid needing an Organization model class here.
    organization_id = Column(PGUUID(as_uuid=True), nullable=True)
    # L4: free-text reason if the directive is killed without a fix.
    dead_reason = Column(Text, nullable=True)
    # L4: who closed it.
    fixed_by_id = Column(String, ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)


# ── L2 enterprise sprint: extraction tables ──────────────────────────────
# Watt-aligned 6-stage taxonomy + flags with severity/risk_tag + entities.
# Writer module in `app.extraction.*` produces these rows; finalize step
# does idempotent delete-then-insert per call_id.

class CallSegment(Base):
    """One row per AI-classified segment of a call.

    Stage vocabulary — 2026-05-12 taxonomy rebuild — locked to the 4
    canonical compliance segments: ``lead_gen | pre_sales | verbal | loa``.
    (The old 6-stage ``intro|qualification|pitch|transfer|verbal|close``
    taxonomy is gone — those were sub-segments of single recordings
    written by the legacy ``extraction/segments.py`` anchor matcher.)

    Each row carries its own verdict so the call detail UI can render
    a separate verdict card per segment and the call-level score is an
    aggregate (worst-bucket wins) across the rows here.
    """
    __tablename__ = "call_segments"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id = Column(String, ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    idx = Column(Integer, nullable=False)
    stage = Column(String, nullable=False)            # lead_gen | pre_sales | verbal | loa
    transcript_excerpt = Column(Text, nullable=True)
    speaker = Column(String, nullable=True)
    start_s = Column(Numeric, nullable=True)
    end_s = Column(Numeric, nullable=True)
    # Per-segment classifier output (2026-05-12)
    start_word_idx = Column(Integer, nullable=True)   # inclusive
    end_word_idx = Column(Integer, nullable=True)     # inclusive
    confidence = Column(Numeric, nullable=True)       # classifier 0-1
    classifier_reasoning = Column(Text, nullable=True)
    # Per-segment verdict
    script_id = Column(String, ForeignKey("scripts.id", ondelete="SET NULL"), nullable=True)
    score = Column(String, nullable=True)             # "21/26"
    compliant = Column(Boolean, nullable=True)
    compliance_status = Column(String, nullable=True) # compliant | pending | non_compliant
    bucket = Column(String, nullable=True)            # pass | coaching | review | blocked
    critical_breaches = Column(Integer, default=0)
    high_breaches = Column(Integer, default=0)
    medium_breaches = Column(Integer, default=0)
    reason = Column(Text, nullable=True)
    checkpoint_results = Column(Text, nullable=True)  # JSON array per segment
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class Flag(Base):
    """One row per failed/needs-review checkpoint, mapped to segment via
    word-index proximity, with Watt severity tier (critical|high|medium)
    and risk_tag (ombudsman|mis-selling|complaint|cancellation)."""
    __tablename__ = "flags"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id = Column(String, ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    segment_id = Column(PGUUID(as_uuid=True), ForeignKey("call_segments.id", ondelete="SET NULL"), nullable=True, index=True)
    rule_id = Column(String, nullable=False)
    severity = Column(String, nullable=False)  # critical|high|medium
    reason = Column(Text, nullable=True)
    evidence = Column(Text, nullable=True)
    word_start = Column(Integer, nullable=True)
    word_end = Column(Integer, nullable=True)
    risk_tag = Column(String, nullable=True)  # ombudsman|mis-selling|complaint|cancellation|None
    source = Column(String, nullable=False, server_default="auto")  # auto|reviewer
    created_by_id = Column(String, ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    # L4 reviewer UX extensions (migration a7b8c9d0e1f2).
    # ``family`` = Compliance/Conduct/Disclosure/etc. — used for the
    # findings page family-filter chips.
    family = Column(String, nullable=True)
    # ``detection_type`` describes how the rule fired so reviewers know
    # whether to expect verbatim phrase matches or fuzzier semantic ones.
    detection_type = Column(String, nullable=True)
    # ``approved_alternative`` is the canonical script-correct utterance
    # the agent should have said instead. Surfaced in finding-detail.
    approved_alternative = Column(Text, nullable=True)


class ExtractedEntity(Base):
    """One row per (call_id, key) pair. Keys: mpan|mprn|deal_value_gbp|
    expected_live_date|commission|annual_cost|other. Unique per (call_id, key)."""
    __tablename__ = "extracted_entities"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id = Column(String, ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    key = Column(String, nullable=False)
    value = Column(Text, nullable=False)
    confidence = Column(Numeric, nullable=True)
    source = Column(String, nullable=False)  # regex|llm|word_match
    created_at = Column(DateTime(timezone=True), default=_utcnow)


# ── L7 enterprise sprint: Customer first-class entity ────────────────────
# Promotes the implicit customer-by-name pattern to a proper row.
# customer_deals.customer_id FK + backfill from migration f6a7b8c9d0e1.

class Customer(Base):
    """First-class customer record. ``slug`` is unique and indexed; the
    customers page reads by slug, not by id."""
    __tablename__ = "customers"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    legal_name = Column(Text, nullable=False)
    trading_as = Column(Text, nullable=True)
    dob = Column(Date, nullable=True)
    company_number = Column(Text, nullable=True)
    charity_number = Column(Text, nullable=True)
    address_postcode = Column(Text, nullable=True)
    business_type = Column(String, nullable=True)  # sole_trader|limited|partnership|charity
    vulnerable_customer_flag = Column(Boolean, nullable=False, default=False)
    slug = Column(Text, nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    # W1 (v3-watt-coverage): Watt portal deep-link. The same site_id may
    # apply to a customer (typical single-site SME) or differ per deal (a
    # multi-site customer has one Customer row but multiple Deals each with
    # their own site_id). Both fields are populated where known.
    external_watt_site_id = Column(Integer, nullable=True, index=True)


# ── L6 enterprise sprint: RAG chunk tables ──────────────────────────────
# Both tables behind the same Vector-import guard as AgentLearning. On
# SQLite (tests) the Vector type is unavailable so the column is Text;
# Postgres production gets the real vector(1536). embedding nullable so
# a failed embed call doesn't block the row.

class TranscriptChunk(Base):
    """One chunk of a finalized call's transcript. ingest_call() in
    app.rag.ingest is the writer; rag.search reads via cosine distance."""
    __tablename__ = "transcript_chunks"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id = Column(String, ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_idx = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    speaker = Column(String, nullable=True)
    start_s = Column(Numeric, nullable=True)
    end_s = Column(Numeric, nullable=True)
    embedding = (
        Column(Vector(1536), nullable=True) if Vector is not None else Column(Text, nullable=True)
    )
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class ScriptChunk(Base):
    """One checkpoint of a script (or its versioned snapshot) chunked +
    embedded. Idempotent on (script_version_id, checkpoint_idx)."""
    __tablename__ = "script_chunks"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    script_id = Column(String, ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True)
    script_version_id = Column(
        String, ForeignKey("script_versions.id", ondelete="SET NULL"), nullable=True
    )
    checkpoint_idx = Column(Integer, nullable=True)
    text = Column(Text, nullable=False)
    embedding = (
        Column(Vector(1536), nullable=True) if Vector is not None else Column(Text, nullable=True)
    )
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


# ── L10 enterprise sprint: 5 new RAG namespaces ────────────────────────
# loa_templates / supplier_docs / gates / rule_catalog / rejections.
# Same Vector-import guard as TranscriptChunk/ScriptChunk so SQLite test
# environments work. Lane D ships the ingest pipelines that activate
# once these classes register.

class LoaChunk(Base):
    """One chunk of a Letter of Authority template per supplier.
    Admin uploads a PDF/markdown; ingest_loa() chunks + embeds + writes."""
    __tablename__ = "loa_chunks"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier = Column(String, nullable=False, index=True)
    chunk_idx = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    section = Column(String, nullable=True)
    embedding = (
        Column(Vector(1536), nullable=True) if Vector is not None else Column(Text, nullable=True)
    )
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class SupplierDocChunk(Base):
    """One chunk of a supplier policy / contract terms document.
    Keyed on (supplier, doc_type) for namespace-aware retrieval."""
    __tablename__ = "supplier_doc_chunks"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier = Column(String, nullable=False, index=True)
    doc_type = Column(String, nullable=False)
    chunk_idx = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    section = Column(String, nullable=True)
    embedding = (
        Column(Vector(1536), nullable=True) if Vector is not None else Column(Text, nullable=True)
    )
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class GateChunk(Base):
    """One chunk per compliance-gate step parsed from
    docs/research/2026-04-25-v2-step-by-step-with-gates.md. Build-time
    ingest, idempotent on (step_number, chunk_idx)."""
    __tablename__ = "gate_chunks"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    step_number = Column(Integer, nullable=False, index=True)
    title = Column(String, nullable=False)
    # chunk_idx defaults to 0 — Lane D's ingest_gates writes one chunk per
    # step (no sliding-window subdivision), so the column is redundant but
    # NOT NULL in spec. server_default keeps spec satisfied without
    # forcing the ingester to supply a value.
    chunk_idx = Column(Integer, nullable=False, server_default="0", default=0)
    text = Column(Text, nullable=False)
    embedding = (
        Column(Vector(1536), nullable=True) if Vector is not None else Column(Text, nullable=True)
    )
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class RuleChunk(Base):
    """One chunk per rule in rules_catalog.json — name + phrases +
    description embedded together. Build-time ingest."""
    __tablename__ = "rule_chunks"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=True)
    severity = Column(String, nullable=True)
    text = Column(Text, nullable=False)
    embedding = (
        Column(Vector(1536), nullable=True) if Vector is not None else Column(Text, nullable=True)
    )
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class SalesAgentAlias(Base):
    """W1 (v3-watt-coverage): canonical sales-agent name aliases.

    Watt's tracker XLSX has 25 distinct strings for ~22 humans (typo +
    casing collisions like ``Bradley Clayton`` vs ``Bradley Claytob``,
    ``Jack Shaw`` vs ``jack shaw``). This table maps dirty inputs to a
    canonical display name so /api/agents groups consistently.

    Seed empty — admin populates via the Settings tab in W4 (not yet
    surfaced in W1). The ``/api/agents`` endpoint best-efforts the lookup;
    agents not yet aliased show up as separate entries.
    """
    __tablename__ = "sales_agent_aliases"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_name = Column(Text, nullable=False, index=True)
    alias = Column(Text, nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class RejectionChunk(Base):
    """One chunk per rejection-tracker row. Anonymized through L9 PII
    redaction primitives BEFORE chunking — no raw customer names land
    in the vector store."""
    __tablename__ = "rejection_chunks"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category = Column(String, nullable=True, index=True)
    agent_name = Column(String, nullable=True)
    supplier = Column(String, nullable=True, index=True)
    fix = Column(String, nullable=True)
    chunk_idx = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    embedding = (
        Column(Vector(1536), nullable=True) if Vector is not None else Column(Text, nullable=True)
    )
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


# ── W2 (v3-watt-coverage): rejection workflow ───────────────────────────
# Stage 4 of Watt's 41-step flow lives here: a supplier-rejected deal +
# the audit trail of how the team fixed it.
#
# Enums (Postgres) / CHECK constraints (SQLite) live in the alembic
# migration `b1d4f7e2c903_w2_rejections.py`. Vocabularies match the XLSX
# deep-dive §2.4-2.7:
#   rejection_category (8): ADMIN_ERROR | PROCESS_FAILURE | VERBAL_SALES_ERROR
#                           | COMPLIANCE_ISSUE | COMPLIANCE_ERROR | PRICING_ISSUE
#                           | DOCUSIGN_ERROR | FAILED_CREDIT_CHECK
#   rejection_status   (7): NOT_STARTED | IN_PROGRESS | FIXED | BATCHED_TO_PORTAL
#                           | SUBMITTED_TO_PORTAL | FIXED_AND_APPROVED | DEAD
#   rejection_outcome  (5): FIXED_AND_SUBMITTED | CUSTOMER_LOST | CANCELLED
#                           | NOT_RECOVERABLE | RESIGNED_TO_OTHER_SUPPLIER
#   remediation_action(10): AMENDMENT_CALL | CONFIRMATION_CALL | NEW_LOA
#                           | NEW_DOCUSIGN | DD_MANDATE | RESELL_TO_OTHER_SUPPLIER
#                           | PRICE_RECHECK | COT_CHANGE_OF_TENANCY
#                           | CONTRACT_LENGTH_LIMIT | MANUAL_ADMIN_SUBMISSION
#
# `deadline` is a Postgres GENERATED ALWAYS AS column (rejected_at + 2 days)
# in production; on SQLite (tests) the migration emits a plain TIMESTAMP and
# we compute the value app-side on insert (see rejections_routes._compute_deadline).


class Rejection(Base):
    __tablename__ = "rejections"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id = Column(String, ForeignKey("calls.id", ondelete="SET NULL"), nullable=True, index=True)
    customer_slug = Column(Text, nullable=True, index=True)
    external_watt_site_id = Column(Integer, nullable=True, index=True)
    supplier = Column(Text, nullable=True)
    sales_agent = Column(Text, nullable=True)
    category = Column(String, nullable=False, index=True)
    rejection_reason = Column(Text, nullable=False)
    fix_required = Column(String, nullable=True)
    # Free-text 1-sentence corrective-action narrative. XLSX ops use phrases
    # like "amendment + confirmation call" or "chase up with dom" that don't
    # fit the fix_required enum. Populated by rejection_factory's
    # _propose_narrative LLM helper. Distinct from outcome_narrative which
    # is reserved for terminal-status (DEAD / FIXED) close-out text.
    fix_narrative = Column(Text, nullable=True)
    fix_assignee_id = Column(String, ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default="NOT_STARTED", server_default="NOT_STARTED", index=True)
    outcome = Column(String, nullable=True)
    outcome_narrative = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    rejected_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    # On Postgres this is a GENERATED ALWAYS AS column; on SQLite it's a
    # plain timestamp the route layer fills in. Either way reads work the same.
    deadline = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    # W4.6 (v3-watt-coverage): dead-reason classification when status=DEAD.
    # Allowed vocab lives in ``rejections_routes.DEAD_REASONS``. Migration
    # ``c4g7i8m9n0o1_w4_dead_reasons.py`` adds the column + an index so the
    # Dead-tab filter chips can do cheap exact-match filtering.
    dead_reason = Column(Text, nullable=True, index=True)
    # Sprint B (tracker XLSX parity): per-field provenance map. Keys are
    # column names (e.g. "supplier", "rejection_reason"), values are source
    # tags ("ai" | "xlsx_import" | "reviewer_edit" | "placeholder"). Migration
    # ``c0d3a1b2c3d4`` adds the DB column with NOT NULL + server_default '{}'.
    # JSONBCompat keeps SQLite tests working.
    field_sources = Column(JSONBCompat, nullable=False, server_default="{}", default=dict)

    # AI/HUMAN provenance gate (mirrors Call.verdict_state).
    # AI_PENDING (factory output) → HUMAN_CONFIRMED (Confirm button)
    #                            → HUMAN_OVERRIDDEN (Save changes after edit)
    verdict_state = Column(String, default="AI_PENDING", server_default="AI_PENDING", nullable=False, index=True)
    confirmed_by = Column(String, nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)


class RejectionAuditLog(Base):
    __tablename__ = "rejection_audit_log"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rejection_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("rejections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id = Column(String, ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    action = Column(Text, nullable=True)
    from_status = Column(String, nullable=True)
    to_status = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class ReviewerEdit(Base):
    """Audit log row for inline edits on /tracker.

    Captures who changed which field, from what to what, when. Used by the
    Phase C UI to show the "Last edited by" line and the AI-badge tooltip's
    "Previously AI: X" text.

    Note: Postgres-side ``server_default`` (``gen_random_uuid()`` for ``id``,
    ``NOW()`` for ``at``) is owned by the Alembic migration ``d4e5a6b7c8d9``.
    The Python-side ``default=`` here keeps SQLite tests working.
    """

    __tablename__ = "reviewer_edits"

    id = Column(UUIDCompat, primary_key=True, default=uuid.uuid4)
    rejection_id = Column(UUIDCompat, nullable=False, index=True)
    field = Column(String(64), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    reviewer_id = Column(String(64), nullable=True)
    at = Column(DateTime, nullable=False, default=_utcnow)


class PipelineStepLog(Base):
    """One row per pipeline step per call — the n8n-equivalent flow log.

    Captures input + output JSON for every step in the workflow (download,
    transcribe, deepgram, rag, classify, narrative, etc) so reviewers can
    drill into the live flow on /observability and see exactly what each
    step received and produced. Distinct from agent_traces (which is
    LLM-turn-grain) — this is step-grain across the whole pipeline.

    payload_in / payload_out are truncated to 64KB to bound row size.
    """
    __tablename__ = "pipeline_step_log"
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id = Column(String, ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    step_name = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, index=True)  # running | ok | err
    payload_in = Column(JSONBCompat, nullable=True)
    payload_out = Column(JSONBCompat, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)


class FailedJob(Base):
    """One row per call whose pipeline exhausted all retries (Wave 1 — T5).

    Written by ``app.workflows.redispatch_watchdog.record_failed_job`` when
    the watchdog gives up on a stuck call after the last redispatch attempt
    fails. Provides a queryable, audit-friendly record of permanently broken
    jobs that operators escalate manually. Idempotent on (call_id, attempts)
    so repeated watchdog ticks for the same exhausted job don't duplicate.

    Note: Postgres-side ``server_default`` (``gen_random_uuid()`` for ``id``,
    ``NOW()`` for ``exhausted_at`` / ``created_at``) is owned by the Alembic
    migration ``6c863e1ce3b1``. The Python-side ``default=`` here keeps
    SQLite tests working.
    """

    __tablename__ = "failed_jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    call_id = Column(
        String,
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    last_step = Column(String(64), nullable=False)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    last_error = Column(Text, nullable=True)
    exhausted_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "call_id", "attempts", name="ix_failed_jobs_call_attempt"
        ),
    )


class AuditLog(Base):
    """Tamper-evident audit log. Owned by Alembic migration 497bd38e5551.

    The actual writer is ``app.audit.record_audit()`` which uses raw SQL to
    extend the hash chain (prev_hash + canonical(payload) -> this_hash).
    This ORM class exists ONLY so that ``Base.metadata.create_all()``
    (used by SQLite-backed test fixtures) materializes the table —
    production reads still go through raw SELECTs (see
    ``app.observability_routes.list_audit``).

    Do NOT use this class for writes — bypassing ``record_audit()`` breaks
    the hash chain. Read-only.

    Column shapes mirror migration 497bd38e5551 exactly. ``payload`` uses
    the cross-platform ``JSONBCompat`` decorator (real JSONB on Postgres,
    JSON-encoded TEXT on SQLite). ``id`` / ``organization_id`` use
    ``UUIDCompat`` (real UUID on Postgres, CHAR(36) on SQLite).
    Postgres-side ``server_default`` for ``id`` (``gen_random_uuid()``)
    and ``occurred_at`` (``NOW()``) is owned by the Alembic migration;
    Python-side ``default=`` keeps SQLite tests ergonomic.
    """

    __tablename__ = "audit_log"

    id = Column(
        UUIDCompat,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    occurred_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    )
    organization_id = Column(UUIDCompat, nullable=True)
    actor_id = Column(
        String,
        ForeignKey("profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    action = Column(Text, nullable=False)
    entity_type = Column(Text, nullable=False)
    entity_id = Column(Text, nullable=True)
    payload = Column(JSONBCompat, nullable=False, default=dict)
    prev_hash = Column(Text, nullable=True)
    this_hash = Column(Text, nullable=False)
