# Durability runbook (Wave 3)

This wave maps three durability concerns from the architecture spec to concrete code paths in the repo.

## 1. Pipeline durability

**Spec:** at-least-once delivery, acks-late semantics, checkpointable resumes.

**Implementation:** Inngest workflow engine. Each pipeline step is wrapped in `ctx.step.run("<name>", ...)`, which Inngest memoizes by `(step_name, input_hash)`. A redispatched event resumes from the first non-memoized step rather than re-running steps that already produced output. The `redispatch_watchdog` cron (Wave 1) detects calls whose `last_step_started_at` is older than 7 minutes and emits a fresh `call/uploaded` event; Inngest's memoization makes the redispatch idempotent.

Failed-job forensics: `failed_jobs` table (Wave 1) gets a row when an Inngest run exhausts its retry budget. Operators surface these in `/observability/stuck` and can replay via `POST /api/calls/{id}/reanalyze` (Wave 3).

## 2. Replay

**Spec:** re-derive a verdict from the stored transcript without re-transcription cost.

**Implementation:** `POST /api/calls/{id}/reanalyze` (`backend/app/replay.py`) emits an Inngest `call/reanalyze` event. The `process_call_reanalyze` workflow function (`backend/app/workflows/process_call.py`) runs only steps 4 (analyze_checkpoints) → 5 (score) → 6 (finalize). Existing CallCheckpoint idempotency replaces prior rows. An `audit_log` entry is written for every reanalyze.

Constraints: requires `Call.transcript`, `Call.word_data`, and `Call.script_id` to be non-null. Returns 422 otherwise. Not currently rate-limited; add 1/min/call when abuse is measured.

## 3. Database backup + restore drill

**Spec:** daily encrypted backup, 7-day retention, one restore drill per quarter.

**Implementation:**
- `backend/scripts/pg_dump_to_storage.py` runs `pg_dump --format=custom --compress=6`, optionally encrypts with `age` (recipient public key in `BACKUP_AGE_RECIPIENT`), uploads to `<backup_bucket>/YYYY/MM/DD/compliance-HHMMSS.sql.gz[.age]` via the active StorageBackend.
- `backend/app/workflows/pg_dump_nightly.py` is an Inngest scheduled function (`cron 0 2 * * *` UTC) invoking the script. Inngest retries up to 3× with exponential backoff; final failure produces a `failed_jobs` row.
- `scripts/restore_drill.sh` exercises the restore path: download → decrypt → pg_restore into a scratch DB → row-count sanity report. Run quarterly. Document in `claude-progress.txt`.

### Retention
Object-store retention is enforced by the storage provider (Supabase Storage policy or S3 lifecycle rule). Set retention=7d on `<backup_bucket>/*` in the prod console. Code does not delete; storage policy does.

### Encryption key management
`BACKUP_AGE_RECIPIENT` is the age public key (recipient). Keep the matching identity (private key) **off the VPS** — operators store it in a secrets vault. To restore, copy the identity file to the restore host and pass `BACKUP_AGE_IDENTITY` to `restore_drill.sh`.

## 4. Storage portability

**Spec:** swap object stores via env var with no code change.

**Implementation:** `app/storage/__init__.py` exposes `StorageBackend` ABC and `get_backend()` factory. Two impls: `SupabaseBackend` (default, `STORAGE_BACKEND=supabase`) and `S3Backend` (`STORAGE_BACKEND=s3`, supports MinIO/R2/AWS S3 via `S3_ENDPOINT`). Legacy module-level functions `upload_audio` / `download_audio` / `signed_url` delegate to the active backend so existing call sites are untouched.

Smoke procedure for swap: set `STORAGE_BACKEND=s3` + MinIO creds in `.env`, restart backend, upload a call audio, verify it lands in MinIO bucket and signed-URL playback works. Flip back to supabase, restart, verify same.

## Operational checklist

- [ ] First production backup completed (check `backups/<today>` exists in storage).
- [ ] First restore drill completed and documented (date + rough table row counts).
- [ ] `BACKUP_AGE_RECIPIENT` set in prod env; matching identity stored off-VPS.
- [ ] Storage retention=7d configured in provider console.
- [ ] Reanalyze endpoint tested in prod against a sample call.
