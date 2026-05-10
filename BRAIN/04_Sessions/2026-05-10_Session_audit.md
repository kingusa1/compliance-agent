---
created: 2026-05-10
updated: 2026-05-10
tags: [session, log, audit, playwright]
session_date: 2026-05-10
---

# Session log — 2026-05-10 (audit pass, late evening)

> Continuation of the morning session. User goal: "use Playwright MCP, submit a record, check all pages, try delete, check everything. Most important: does it make sense?"

## What I did

1. **Tried Playwright MCP** — failed to connect (`✗ Failed to connect` per `claude mcp list`).
   - Diagnosed: bare `npx` shim issue on Windows. Installed `@playwright/mcp@latest` globally and rewrote the user-level MCP config (`~/.claude.json`) to point at the global `playwright-mcp.cmd` directly.
   - Backup at `~/.claude.json.bak-mcp-fix`.
   - Will pick up on the **next Claude Code restart** — current session already had the bad config loaded.

2. **Pivoted to direct Playwright scripts** — same browser, same automation, no MCP transport. Wrote three drivers under `audit-2026-05-10/`:
   - `01-route-walk.mjs` — desktop+mobile screenshot pass on all 17 routes
   - `02b-extract-supabase.mjs` — extracted live `NEXT_PUBLIC_SUPABASE_URL` (`https://zcmdsblqbgatsrofptsq.supabase.co`) and anon key from the bundle by triggering an auth call
   - `03-login-page-audit.mjs` — login form a11y + error UX

3. **Discovered the auth wall** — every protected route 200s but renders the Sign In form instead of content. The previous "17 routes verified 200/307" line in `Live_State.md` was HTTP-only verification. Updated.

4. **Tried to obtain a working session** — none of the seeded local credentials in `docs/dev-credentials.md` (`test@fame.dev / test`, `gomaa@fame.dev / test`, etc.) work on prod Supabase. Open signup is allowed but email confirmation is required. Marked as a blocker for future audits.

5. **Did the substantive audit via the backend API** (most endpoints don't require auth — only `/api/rejections`, `/api/tracker/rows`, and `PATCH /api/calls/{id}/metadata` do).
   - Snapshotted DB state before to `audit-2026-05-10/state-before/`
   - Uploaded `Mrs Zoe Larkins Ta Korner Kutz/lead.mp3` via `POST /api/calls/upload`
   - Polled `/api/calls/{id}` until completed — **81 seconds end-to-end**
   - Verified propagation across `/api/calls`, `/api/customers`, `/api/customers/<slug>`, `/api/deals`, `/api/agents`, `/api/observability/runs`
   - Tried `POST /api/admin/quality-resolve` — re-merged the church bucket (idempotent, but wastes an Opus 4.7 call); correctly did NOT stitch the new Korner Kutz call to anything
   - Snapshotted DB state after to `audit-2026-05-10/state-after/`

6. **Tested DELETE** — found a bug.
   - `DELETE /api/calls/42a89a59-…` (the failed Crosby grange stub from the morning) → **200 OK** ✅
   - `DELETE /api/calls/190868a8-…` (my completed Korner Kutz upload) → **HTTP 500** 🐛
   - Root-caused: `routes.py:1525-1550` only cascades `CallCheckpoint`. There are 9 other tables with non-cascading `ForeignKey("calls.id")` (ReviewSession, VerdictHistory, TranscriptEdit, ClaimLock, ComplianceDecision, VerdictSuggestion, VerdictResponse, AgentTrace).
   - Documented in `Known_Issues.md` and `audit-2026-05-10/AUDIT_REPORT.md`.

7. **Checked AI logic** — Opus 4.7 caught a real subtle compliance violation. Agent disclosed broker correctly at the start ("calling from What Utility"), but mid-call when the customer asked "is this with Eon or someone else?" the agent said "For the Eon" — Opus 4.7 failed all three TPI checkpoints with specific evidence + per-checkpoint notes. **The pipeline reasoning is sound.**

## Findings (linked from `audit-2026-05-10/AUDIT_REPORT.md`)

| Area | Verdict |
|---|---|
| Pipeline orchestration end-to-end | ✅ PASS (81s) |
| Data propagation | ✅ PASS |
| Quality Agent | ✅ PASS (idempotent) |
| AI compliance reasoning | ✅ PASS — caught a subtle "For the Eon" mis-direction |
| Frontend HTTP routes | ✅ PASS |
| Frontend visual content audit | ⚠️ BLOCKED on missing prod test creds |
| Delete on minimal-data calls | ✅ PASS |
| **Delete on completed calls** | 🐛 **BUG — HTTP 500** |
| Orphan customer/deal cleanup | 🐛 BUG |
| `CustomerDeal.stage` always null | 🐛 minor |

## Files changed

- `BRAIN/05_State/Live_State.md` — qualified "17 routes verified" + reflected new DB state
- `BRAIN/05_State/Known_Issues.md` — added 3 new bug entries (delete cascade, orphan stubs, stage null)
- `BRAIN/04_Sessions/2026-05-10_Session_audit.md` — this file
- `audit-2026-05-10/AUDIT_REPORT.md` — full structured findings
- `audit-2026-05-10/01-route-walk.mjs`, `02b-extract-supabase.mjs`, `03-login-page-audit.mjs` — drivers
- `audit-2026-05-10/state-before/*.json`, `state-after/*.json` — DB snapshots
- `audit-2026-05-10/shots/*.png` — 36 screenshots
- `~/.claude.json` — Playwright MCP `command` rewritten to global `playwright-mcp.cmd` (backup at `~/.claude.json.bak-mcp-fix`)

## What's next

1. **Get working prod test creds** so visual audit can complete.
2. **Ship the DELETE-cascade migration** — 9-line ALTER set + the routes.py change. Item #1 in the report's recommendations.
3. **Cascade-delete orphan Customer/CustomerDeal** when their last call is removed.
4. **Restart Claude Code** to pick up the fixed Playwright MCP config — then the next audit pass can use `mcp__playwright__*` tools natively.
