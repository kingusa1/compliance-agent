from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    deepgram_api_key: str = ""  # Optional — empty disables Deepgram; CI tests mock providers
    # EU endpoint pins audio processing to EU region (UK call PII residency).
    # Override with DEEPGRAM_BASE_URL env if a different region is required.
    deepgram_base_url: str = "https://api.eu.deepgram.com"
    # UK English locale for British/Scottish/Welsh accents — Nova-3 supports en-GB.
    deepgram_language: str = "en-GB"
    # 2026-05-24 — owner explicitly opted into raw PII in stored
    # transcripts so MPAN / MPRN / £ amounts survive for the entity
    # extractor + the AI name detector. When False (default), Deepgram's
    # `redact` option is empty + AAI's redact_pii flags are not set + the
    # post-process UK National Insurance scrub is skipped. Set to True
    # only if legal / customer policy ever requires re-redaction.
    transcript_redact_pii: bool = False
    # ─── LLM provider keys + model defaults ──────────────────────────
    # Production runs Opus 4.7 via OpenRouter. Opus is the right tier
    # for compliance audit accuracy (27 rejection reasons, 8 standards,
    # tone-sensitive tracker fix_required text). Override per-env.
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-opus-4.7"
    # ``cheap=True`` model route — 2026-05-16 Mohamed mandate: keep this on
    # Opus 4.7 too. Sonnet was returning unreliable transcripts on
    # supplier / name / business / call-type detection and that's the
    # highest-cost failure mode (cascades through deal-linker + reviewer
    # queue). Defense-in-depth: every callsite has also been switched to
    # `cheap=False`, but if any future caller forgets, this default still
    # routes to Opus.
    openrouter_cheap_model: str = "anthropic/claude-opus-4.7"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    anthropic_api_key: str = ""
    # Direct Anthropic API path (used when ACTIVE_PROVIDER=anthropic).
    # Same Opus 4.7 model id as OpenRouter, in Anthropic's id format.
    anthropic_model: str = "claude-opus-4-7"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    # Active provider: openrouter | gemini | anthropic | openai
    # OpenRouter is the production default per user's setup.
    active_provider: str = "openrouter"
    # Comma-separated list of CORS-allowed origins. In production set this to
    # ONLY the production frontend domain(s); the lifespan guard blocks
    # localhost entries when environment == "production".
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    admin_key: str = ""
    speechmatics_api_key: str = ""
    assemblyai_api_key: str = ""
    groq_api_key: str = ""
    cohere_api_key: str = ""
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/compliance_dev"
    migration_database_url: str = ""  # Session-mode pooler (port 5432) for Alembic; transaction pooler lacks advisory locks.
    create_tables_on_startup: bool = False  # Alembic owns schema; tests can override via env (e.g. in conftest.py).
    upload_dir: str = "./uploads"
    max_file_size: int = 25 * 1024 * 1024  # 25MB — within Vercel/Railway proxy limits

    # Supabase (Postgres DB + Auth + Storage + Realtime)
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_storage_bucket: str = "call-audio"

    # ─── Smart Agent Layer ────────────────────────────────────────────
    use_agent_analyzer: bool = False  # Feature flag: False = old batched analyzer, True = new agent
    gemini_flash_model: str = "google/gemini-2.5-flash"  # First-pass agent model (cheap)
    agent_escalation_model: str = "anthropic/claude-opus-4.7"  # Opus 4.7 — Watt compliance audit accuracy
    agent_escalation_threshold: Literal["low", "medium", "high"] = "low"  # Escalate when first-pass confidence == this value
    agent_max_turns: int = Field(default=8, ge=1, le=50)  # Max tool-use turns per batch before forcing a verdict

    # ─── Durable Pipeline (Inngest) ───────────────────────────────────
    # When True, the upload handler additionally emits a `call/uploaded`
    # event so the Inngest `process_call` function runs the pipeline for
    # durability/observability. Default False keeps the legacy in-process
    # background task as the sole path. See `app/workflows/process_call.py`.
    use_inngest_pipeline: bool = False

    # ─── W3.A — Pricing mismatch flag ─────────────────────────────────
    # When True, the L2 extraction writer runs the pricing-rate regex
    # extractor and emits PRICING_MISMATCH flags whenever an agent-stated
    # rate differs from the script reference rate by > 0.1p. Disable if
    # the regex turns out to be too noisy on real call transcripts.
    pricing_mismatch_enabled: bool = True

    # ─── W3.C Vulnerable-customer detection ───────────────────────────
    # Two-stage detector (regex + optional LLM) that emits a
    # VULNERABLE_CUSTOMER flag from the extraction pipeline. Per harness
    # §"Failure-mode plan" this is gated by a feature flag so we can
    # disable banner emission if false-positive rate spikes; the 5th
    # risk pill is rendered unconditionally on the frontend.
    vulnerable_detection_enabled: bool = True

    # ─── Wave 2 — observability ───────────────────────────────────────
    sentry_dsn: str = ""  # GlitchTip-compatible DSN; empty → SDK no-ops
    sentry_environment: str = "development"
    sentry_traces_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    prometheus_enabled: bool = True

    # ─── Wave 3 — durability + portability ────────────────────────────
    storage_backend: Literal["supabase", "s3"] = "supabase"
    s3_endpoint: str = ""           # MinIO/custom endpoint; empty = AWS default
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "call-audio"
    s3_region: str = "us-east-1"
    backup_bucket: str = "backups"  # Bucket inside the active backend
    backup_age_recipient: str = ""  # `age` recipient public key; empty = no encryption (dev only)

    # ─── Wave 4 — cost optimizers ─────────────────────────────────────
    embedding_prefilter_enabled: bool = False  # Off by default — A/B-gated
    embedding_prefilter_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    # Note: use_agent_analyzer default flipped True in T7 after A/B passes.

    # 2026-05-16 — Anthropic prompt-cache adoption on the grader +
    # OpenRouter cache-header forwarding. When True, _analyze_batch splits
    # the static rubric+transcript prefix into a `system=` block so the
    # 6-21 batches per upload share Anthropic's 5-min ephemeral cache
    # (cached reads bill at ~10% of input rate). The OpenRouter transport
    # in analysis._call_openrouter is upgraded in parallel to pass the
    # `cache_control: ephemeral` flag through to Anthropic when the model
    # is a Claude model — without that, OpenRouter would silently drop
    # the cache marker. Default OFF; operator runs the A/B harness at
    # backend/scripts/cache_ab_harness.py and flips this to True on
    # Railway only after a clean diff against the baseline path.
    grader_prompt_caching_enabled: bool = False

    # ─── Dev convenience ──────────────────────────────────────────────
    # When True, every authenticated user is treated as `admin` so engineers
    # can exercise admin/lead/reviewer-gated UI without seeding multiple
    # accounts. NEVER true in production. Read in app/auth.py:current_user.
    dev_all_admin: bool = False

    # ─── Phase 2 — Watt-grounded compliance prompt ────────────────────
    # When True, app/checkpoint_analyzer.py routes through
    # app/analysis.py:analyze_compliance_watt which uses the Watt-canonical
    # 8 Standards + 27 rejection reasons + supplier detection + regex
    # pre-pass. When False, the legacy V1/V2 paths run unchanged. Default
    # False so the flag can be turned on per-environment after the user
    # supplies API keys and confirms the new prompt against real audio.
    use_watt_prompt: bool = False

    # ─── Two-layer transcript validation ──────────────────────────────
    # AssemblyAI is the primary transcript for downstream scoring;
    # Deepgram runs in parallel as an independent second opinion.
    # ``app/transcript_cross_validation.py`` compares both transcripts
    # and writes an agreement report onto ``Call.meta["transcript_agreement"]``.
    # When agreement < floor, the call is routed to human review
    # (not auto-passed) so a reviewer can listen to the disagreement
    # windows. 0.85 is the empirical floor from the L9 benchmark
    # briefing — tune per environment without code changes.
    transcript_agreement_floor: float = Field(default=0.85, ge=0.0, le=1.0)
    # Force human review when agreement is below floor. Default ON for
    # enterprise safety; flip OFF to surface a UI chip only without
    # changing the verdict gate.
    transcript_divergence_forces_review: bool = True


settings = Settings()
