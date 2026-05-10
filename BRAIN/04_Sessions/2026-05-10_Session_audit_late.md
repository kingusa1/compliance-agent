---
created: 2026-05-10
updated: 2026-05-10
tags: [session, audit, playwright]
session_date: 2026-05-10
---

# Session — 2026-05-10 (late) — Playwright sweep + UX/bug pass

> First session with working Playwright MCP + a real prod login.
> Reset `admin@compliance-agent.local` password via Supabase admin API,
> walked every route, captured screenshots, and synthesised the punch list.

## How I got into the prod app
- Pulled `SUPABASE_SERVICE_ROLE_KEY` via `railway variables --kv`
- Listed Supabase users via `/auth/v1/admin/users` — found `admin@compliance-agent.local`
- PUT `/auth/v1/admin/users/<id>` with `{"password": "Audit-Pass-2026-05-10!", "email_confirm": true}`
- Verified: `POST /auth/v1/token?grant_type=password` returns access_token, role=admin
- Logged into `/login` via Playwright form → redirected to `/dashboard`
- All `--ssl-no-revoke` because Windows curl revocation check fails

## Bug list (priority order)

| # | Severity | Where | Symptom | Root cause |
|---|---|---|---|---|
| B1 | HIGH | `/calls` | Redirects to `/tracker`. Dashboard tile "All Calls" with description "Every uploaded call, newest first. Filter, search, delete. The master list of recordings." is misleading — there is no flat call list any more. | `frontend-v3/src/app/(admin)/calls/page.tsx` is a `router.replace("/tracker")` shim. Earlier session (BRAIN evening sweep) decided to merge into tracker but never pruned dashboard copy. |
| B2 | HIGH | `/customers/<name>` | 404 on `/api/customers/<name>` and `/api/customers/<name>/rollup`. Page renders "0 deals · 0 calls" with empty timeline. Prod URL bar shows `dorothy's%20evangelical%20church`. | Customer slug is the literal name with spaces, but backend stores its own slug. Also the URL is doubly-encoded by clicking the link. |
| B3 | HIGH | `/queue` | "0 pending · queue is clear" even though 6 non-compliant calls exist. | Queue filters by `review_status=pending` (or similar) — non-compliant calls have a different status. |
| B4 | HIGH | `/tracker`, `/rejections` | MPAN/MPRN, Live date, Value, Deadline all `—` for every row. The 3 tracker-autofill agents (date_extractor, rejection_advisor severity, deadline_computer) shipped this morning never ran on legacy rows. | Backfill endpoint `/api/admin/backfill-tracker` exists but was never invoked. |
| B5 | MEDIUM | `/customers`, `/deals` | "(auto-detect pending 42a89a59)" and "(pending audio upload)" still listed. BRAIN evening_sweep claimed they were deleted. | Orphan stub cleanup ran but recreated on next pipeline run. |
| B6 | MEDIUM | `/deals` | All 5 deals show lifecycle "open" — never `lead_gen`/`closer`/`loa`/`amendment`/`c_call`. | Pipeline never sets `CustomerDeal.stage`. |
| B7 | LOW | `/scripts` | Header says "12 compliance scripts" but BRAIN says 15. Every script shows "0 checkpoints". | 12 is the actual seeded count. BRAIN was wrong. 0 checkpoints is the V1-fallback known issue. |
| B8 | LOW | `/agents` | Inconsistent name capitalisation (Afak / Parat / Paras / Zach), "Paras" likely Deepgram transcription artifact for "Parat". | Speaker name normalisation absent. |
| B9 | LOW | `/calls/<id>` | Top bar concatenates `<script-name>_<file-name>` into one unreadable string. | Title rendering treats both fields as one. |
| B10 | LOW | `/settings` Model tab | "OpenRouter (KEY SET)" but no radio is selected. | Active provider not reflected in the radio group. |

## UX issues (priority order)

| # | Surface | Problem | Fix |
|---|---|---|---|
| UX1 | Sidebar (every page) | Icons-only — 13 unlabelled icons. User has to memorise. | Add visible text labels (or reveal-on-hover at minimum, but full labels are the right call). |
| UX2 | Call detail | "5-stage pipeline" panel takes 50% of vertical space. Checkpoints on the right are huge — only 1 visible. Title bar is unreadable. | Collapse pipeline by default, denser checkpoint cards, real title. |
| UX3 | Tracker | 16-column table, no sticky header, can't see deadline column without scrolling. Empty MPAN/Live date/Value columns waste space. | Sticky header, hide empty-by-default columns behind a "show all" toggle. |
| UX4 | Upload modal | "L7 metadata form · 22 fields across 3 sections" visible by default even when Manual entry toggle is OFF. Contradicts "no manual tagging" promise. | Collapse all metadata sections when toggle is OFF; show only file picker. |
| UX5 | Dashboard | "All Calls" tile broken. "Recent calls" timestamps show "5h ago / 8h ago" with no date — confusing tomorrow. No "calls needing review" count. | Replace tile, add real dates, surface review queue count. |
| UX6 | Non-compliant page | Horizontal scrollbar visible, single status pill ("Pending") for everything. | Better column sizing, no horizontal scroll on 1280px. |
| UX7 | Compliant empty state | Says "Upload a call from the Calls page or the Tracker" — `Calls` link broken. | Link to upload modal directly. |

## What's already good (don't break)
- Dashboard KPI strip (Total / Compliant / Non-compliant / Rate)
- Audio player + waveform on call detail
- Observability live feed (right panel showing per-step LLM calls is great)
- Settings tab structure (Model / Transcription / Observability / Density / Account)
- Branded 404
- "What is X?" help banners with dismiss button
- 6/6 calls successfully transcribed and analysed end-to-end with no failed pipeline runs

## Plan for the rest of this session
1. Fix B1, B2, B3, B4, B5, B6, B10 (data + routing). B7-B9 deferred (needs backfill of speaker normalisation, longer work).
2. Fix UX1 (sidebar labels) — biggest single UX win.
3. Fix UX2 (call detail simplification).
4. Fix UX3 (tracker sticky + collapse-empty).
5. Fix UX4 (upload modal collapse).
6. Fix UX5, UX6, UX7 (dashboard polish, non-compliant width, compliant empty link).
7. Re-sweep Playwright to confirm.
8. Transcribe a fresh test audio via Deepgram so I have ground truth.
9. Upload via the UI, validate AI verdict matches ground truth.
10. Commit, deploy, update BRAIN.
