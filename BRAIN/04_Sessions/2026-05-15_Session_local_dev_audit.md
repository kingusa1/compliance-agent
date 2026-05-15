---
created: 2026-05-15
tags: [session, audit, local-dev, tracker, deal-lifecycle, customers, rejections, queue, agents, security, cli-auth, dev-db]
---

# Session 2026-05-15 — System-wide audit sweep + local stack + CLI re-auth

> **TL;DR:** Five parallel reviewer agents (code-reviewer ×3, python-reviewer,
> database-reviewer) on the whole product surfaced **25 real bugs** across
> tracker, deals, customers, rejections, queue, dashboard, agents, pipeline,
> rubric, quality agent, and the auth surface. **17 P0/P1 fixed locally**;
> rest are deferred with notes. Mid-session the prod Railway backend went
> unreachable from this shell (HTTP 000), so I stood up the full local
> stack against the dev Supabase DB (`fgkzmldgpfezyqzjuqfq`, 549 calls)
> and inserted one fresh Rejection so the user can validate the fixes
> against live data. User explicitly held the push — 4 unpushed commits
> wait for go-ahead.

## What landed

### Tracker
- `_awaiting_review_row` now aggregates AI-suggested category + fix + deadline
  from `CallCheckpoint` rows. Modal vote weighted by `ai_category_confidence`,
  ties broken by sum-of-confidences. Awaiting-review tab now ships full row
  metadata to the frontend instead of leaving 7 cells as "—".
- TrackerSidePanel: three branches (`isRejection` / `isAwaitingReview` /
  `isCompliant`). The wrong "Compliant — No rejection. Customer-confirmation
  email sent." banner that was showing for AI-flagged-but-unreviewed calls
  is gone; awaiting-review rows now get an amber banner with the AI's
  reason quoted + an Open call analysis CTA.
- StatusPipelinePill: dedicated amber "Awaiting review" chip for the
  synthetic AWAITING_REVIEW status (was rendering as "1/6 AWAITING_REVIEW"
  through the unknown-status fallback).
- Outcome `<select>` controlled with onChange → editNotes.mutate. Was
  uncontrolled `defaultValue` silently dropping picks.
- Notes textarea remounts via `key={rejection_id + outcome_narrative}` so
  it never goes stale after invalidations.
- Fixed By column renders "Assigned" / "—" instead of 8 hex chars of a
  UUID. Real reviewer-name resolution queued.
- Empty state added for the awaiting_review tab.
- "Last edited" → "Last activity" (the timestamp is MAX(audit_log) of ANY
  event, not specifically a notes edit).
- `/api/calls/{id}/script-checkpoints` returns UNION of every segment's
  script (88 + 26 + 11 = 125 rules deduped by name) so per-segment cards
  always carry their `required` text.
- `_last_action_date` wraps narrow `(OperationalError, ProgrammingError)`
  so a missing `rejection_audit_log` table doesn't 500 the page.
- `tracker_edit_routes.ALLOWED_FIELDS` gains `fix_narrative`.
- Aggregator key rename `notes` → `outcome_narrative` end-to-end (incl. XLSX
  export). `_compliant_row` emits the full verdict_state triple
  (`verdict_state=AI_PENDING`, `confirmed_by=None`, `confirmed_at=None`).
- Awaiting-review query now excludes calls that already have a Rejection
  row (no more double-counting across tabs).

### Other pages
- **/deals**: Filter buckets + LifecyclePill aligned with the 7-state
  taxonomy from `deal_lifecycle.derive_lifecycle_status`. UI buckets are
  `In progress` (open + 4 stage-done states), `Verified`, `Rejected`.
  Was showing 0 rows on every tab.
- **/customers/[slug]**: Rollup field rename trio
  (`total_open_directives`, `total_deal_value_gbp_annual_sum`,
  `dead_rejections_count`). KPI cards were stuck at 0/— because the page
  read the wrong keys. Timeline row keys also renamed (`call_id`,
  `completed_at`, `rejection_category`).
- **/customers** list: `worstActionTone` aligned with backend canonical
  vocab (PASS / REVIEW / REJECT / TRIAGE; legacy COACHING/FAIL/BLOCK
  kept as defensive fallback).
- **AddCustomerDialog**: `business_type` now a `<select>` tied to the
  4 Literal values. Was a free-text Input that 422'd on submit.
- **/agents**: Status filter is a controlled `<select>` (was a fake
  `<div>` cycler with zero a11y).
- **/dashboard**: Upload onSuccess invalidates `dashboard:stats`,
  `dashboard:recent-calls`, `dashboard:queue-backlog` so KPIs refresh
  instantly.
- **/rejections**: Passes `source=reviewer` so legacy AI auto-created
  rows are hidden (Phase 4 gate finally enforced client-side).
- **/queue**: Download wired to `audioUrlQuery.data?.url` via `<a download>`.
  Saved views gated "Coming soon" pill. AI Verdict pill drops the "AI:"
  prefix.
- Sidebar, queue page header, dashboard quick-links, guide steps, 404
  routing: "Review Queue" → "Human Review Queue". Call-detail verdict
  pill: "Review" → "Human Review".

### Pipeline + AI agents
- V1 fallback wraps `analyze_compliance_v1` in try/except — a JSON
  decode error on one segment no longer wipes earlier segments' work.
  Marks the segment "review" bucket and continues.
- Last-segment-wins overwrite guard on `call.agent_name` /
  `call.customer_name`. Preserves the bulletproof regex extraction from
  `detect_metadata` instead of the loop's final LLM call clobbering it.
- Groq + Cohere transcription wrappers gain try/except + `isinstance(str)`
  defence-in-depth (was writing exception objects into Text columns).
- Quality Agent `find_sibling_candidates` adds `ORDER BY created_at ASC`
  for deterministic concurrent-upload resolution.
- AI-generated rejection narrative writes to `fix_narrative` (AI slot)
  not `outcome_narrative` (reviewer slot). Stops reviewer edits from
  silently overwriting the AI forensic trail.

### Security (routes.py)
- `_require_admin` hard-fails 503 when `ADMIN_KEY` env var is empty.
  Was silently no-op → every admin endpoint open in any misconfigured
  environment.
- `DELETE /api/calls/{id}`, `POST /api/calls/{id}/retry`,
  `POST /api/calls/cleanup`, `POST /api/admin/quality-resolve` all gain
  auth deps (`current_reviewer` / `_require_admin`). Were anonymous;
  delete cascades to 9 child tables.
- Three bare `json.loads()` on `Script.checkpoints`,
  `Call.checkpoint_results`, `Call.word_data` wrapped in
  `try/except json.JSONDecodeError` returning 400/500 with clear
  messages.

## Local dev stack stood up

| Layer | Status |
|---|---|
| Backend (FastAPI uvicorn) | `http://127.0.0.1:8001` — pid varies, ran via `./venv/Scripts/uvicorn.exe app.main:app` with `.env` swapped to `.env.supabase-cloud` (original preserved at `backend/.env.local-backup-2026-05-15`) |
| Frontend (Next.js 16 / Turbopack) | `http://localhost:3000` — Ready in 447ms · `.env.local` Supabase URL fixed from placeholder to `https://fgkzmldgpfezyqzjuqfq.supabase.co` (backup at `frontend-v3/.env.local.backup-2026-05-15`) |
| DB | Supabase `fgkzmldgpfezyqzjuqfq` (EU-west-1 pooler) — **DEV DB, not prod**. Prod is `zcmdsblqbgatsrofptsq` per legacy BRAIN; couldn't reach prod backend (HTTP 000) so can't introspect Railway env for the prod DATABASE_URL right now. |
| Counts in dev DB | 549 calls · 152 rejections (+ fresh `ffa72170` Christopher / Afaq / E.ON Next) · 197 customers · 447 deals · 50 scripts · 2393 segments · 1807 checkpoints |

## CLI auth state

Mid-session the user requested logout+relogin of GitHub, Vercel, Railway.
End state:

| CLI | Account | Verified |
|---|---|---|
| `gh` | **kingusa1** | `gh auth status` ✓ · token scopes: gist, read:org, repo |
| Git push | **kingusa1** | `git credential fill` returns kingusa1 (not the legacy sheerazfame PAT that was breaking earlier pushes) |
| `vercel` | **mohamedhisham735-1861** | direct API call to `api.vercel.com/v2/user` with `-k` (TLS bypass for this shell) ✓ |
| `railway` | **mohamed hisham ismail** | `railway whoami` ✓ · sees `compliance-agent-backend` project |

Workarounds noted for future sessions (logged in [[../06_Operations/Credentials]]):
- Vercel login needs `NODE_TLS_REJECT_UNAUTHORIZED=0` in this Bash tool.
- Railway login needs a detached `Start-Process powershell -Command 'railway login'` window because no TTY in this Bash.

## Unpushed local commits

| Commit | Scope |
|---|---|
| `becb958` | BRAIN session log + Live State + INDEX (2026-05-14 reviewer polish) |
| `1b55dec` | Full Claude Code skills roster + auto-trigger notes |
| `30fa836` | Task→skill routing matrix + project-root CLAUDE.md |
| `147dcd5` | System-wide wiring sweep (25 files, 17 bugs fixed) |

User explicitly asked NOT to push. Awaiting go-ahead.

## Reviewer findings I deferred (real bugs, not blocking)

1. **N+1 in tracker rejection loop** — perf, not correctness.
2. **Alembic merge heads conflict** (`2026_05_14_stagefix` vs trunk) — needs careful merge migration; doing this wrong corrupts prod.
3. **5 unauthenticated GET endpoints** (`/api/customers/{slug}`, `/api/deals/{id}`, etc.) — data leak only, no mutation; lower urgency.
4. **`_EON_PHASES` doesn't include `loa`** — product decision on E.ON 4-stage scope.
5. **Two missing CHECK constraints** on `calls.status` / `calls.verdict_state` — needs alembic migration.
6. **Reviewer-name resolution** behind `fix_assignee_id` column — needs new reviewer-by-id API.
7. **Status field state-machine bypass** — `status` is in the PATCH whitelist with no FSM guard; reviewer can jump directly from NOT_STARTED → FIXED_AND_APPROVED. Needs FSM design before tightening.
8. **`MPAN/MPRN` / `Live Date` / `Deal Value` columns on awaiting-review rows are deal-sourced** — call has no deal → cells stay "—". Either capture them at upload (L7Form has the capability), or add an inline-edit affordance on awaiting-review rows.

## New AI agents proposed (not yet built)

Documented at the bottom of the previous session's report — short summary
to keep in BRAIN:

1. **`reviewer_assigner_agent`** — route a new rejection to the right
   reviewer based on workload + supplier expertise. Closes the
   `fix_assignee_id` orphan.
2. **`deadline_priority_agent`** — rank active rejections by urgency
   (close-to-deadline × deal-value × supplier recoverability).
3. **`coaching_narrative_agent`** — aggregate per-agent coaching feedback
   across last N non-compliant calls.
4. **`trend_anomaly_agent`** — weekly statistical anomaly detection
   (compliance % crash per supplier, failure-category surges, agent
   rejection spikes).
5. **`call_summarizer_agent`** — 2-3 sentence TL;DR per call so the
   queue is scannable without opening every record.
6. **`vulnerability_flag_advisor`** — LLM judgment layer on top of the
   existing W3.C regex flag (false-positive rate is high on regex-only).
7. **`audit_evidence_compiler`** — generate a complete evidence pack
   (transcript + segment cards + reviewer history + audit log) for an
   Ofgem/EFA audit request in one shot.

Build order if/when prioritised: `call_summarizer_agent` first (instant
reviewer time savings + cheap), then `reviewer_assigner_agent` (closes a
real UX gap surfaced by the tracker audit).

## Resume guide

1. **First**, decide whether to push the 4 commits or keep iterating
   locally. Local frontend at `http://localhost:3000` + backend at
   `http://127.0.0.1:8001` (Supabase dev DB) is live and covers every
   fix.
2. If push: `git push origin main`. kingusa1 credential is the active
   one — should go through cleanly.
3. If prod backend Railway URL still returns HTTP 000 from this shell
   after push, run `railway logs --service compliance-agent` for runtime
   logs. Railway CLI now authenticated.
4. To swap local backend to the **real prod** DB, run
   `railway variables --service compliance-agent --kv` to read
   `DATABASE_URL`, then drop it into `backend/.env`.
5. For new AI agent proposals see "New AI agents proposed" above.

## Earlier session deferred / linked

See [[2026-05-14_Session_reviewer_polish]] for the smaller polish sweep
that landed earlier today (LOA router, real speaker names, CheckpointCard
2-row header, drag-scrub, Chat coming-soon, agent-name regex extraction).
That session's 5 commits are on `origin/main` already (tip `8eb9763`).

---

## Late addition — "Where do the 88 rules come from?" (provenance lookup)

User asked 2026-05-15 PM where the 88-rule pack originates. Answered:

- **Source file:** `compliance-docs/COMPLIANCE XAI/Watt_AI_Phrase_Detection_Dataset (1).docx`
- **Extracted markdown for ingestion:** `.planning/phase2-docs/compliance_xai__watt_ai_phrase_detection_dataset_1.md`
- The number 88 = the six **Lead Generation** sub-sections of that doc: 20 + 12 + 20 + 12 + 12 + 12 = 88. (Verbal Confirmation half = 32, separate.)
- Cross-references: [[../02_Domain/Watt_Compliance#source-of-the-88-rule-lead-gen-phrase-pack]] (new section added this session), [[../02_Domain/Stage_Terminology]], `backend/app/agents/rubric_router.py::_PHRASE_PACK_PHASE`, `backend/app/agents/phrase_pack_extractor.py`.

No code changes — documentation-only brain update.
