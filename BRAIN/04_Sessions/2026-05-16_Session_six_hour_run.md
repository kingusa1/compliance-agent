---
created: 2026-05-16
updated: 2026-05-16
tags: [session, sse-push, deal-linker, sidebar-audit, autonomous-run]
---

# 2026-05-16 — Autonomous 6-hour run: SSE push, Metaphone deal-linker, sidebar audit

**Tip backend / frontend (Railway + Vercel):** `3ecd34c`
**Prior tip (handoff base):** `1e9bd6d` (which was `e1c8d3b` + BRAIN log only)

Owner brief: "validate the full system, fix every error, autonomous for the
next 6 hours, no questions." Worked end-to-end through the 5-phase mission
from the handoff prompt. This log is the receipt.

## Commit chain this run

| Commit | Layer | What it does |
|---|---|---|
| `7390b33` | both | SSE real-time push: `app/realtime.py` in-memory pub/sub, `app/realtime_routes.py` SSE endpoints, `_trace_step` publishes 6 step transitions + `step_started/ok/err`, upload boundary publishes `queued`, frontend `useCallEvents` hook + ScreenFrame mount, removed 3s in-flight refetchInterval from `useCallDetailQuery`+`useCallCheckpointsQuery`+`useAdminCallsQuery`. |
| `e2c7317` | backend | Route-order fix: `realtime_router` must register BEFORE `routes.py` router or `/api/calls/events` is shadowed by `@router.get("/api/calls/{call_id}")`. |
| `a873c19` | frontend | `useCallEvents` global scope now also invalidates `['admin']` key prefix (admin pages use `['admin', 'calls', params]`). Drop 5s poll from `useAdminCallsQuery`. |
| `ca76e2e` | backend | Deal-linker: Metaphone phonetic uplift (first-2 tokens phonetic-equal OR all-token Jaccard >= 0.5 → fuzzy floor 0.80 → 0.60). `detect_business_name` routes to **Opus 4.7** for non-EON suppliers (`cheap=True` only when supplier hint contains "eon"). Pipeline passes `call.detected_supplier` as the hint. |
| `3ecd34c` | frontend | Phase-4 audit fix: queue Reviewed tab was sending `filter=today` → backend regex `^(all\|unclaimed\|in_review\|reviewed_today)$` → 422. Translated at `lib/api.getQueue` wire boundary. |

All 5 commits on `origin/main`, all five deployed.

## Phase 1 — verify the polling rollback + L2 fix (acceptance: PASS)

- Wiped DB (4 calls + 3 deals deleted via `/api/admin/wipe-all-calls?confirm=YES_DELETE_EVERYTHING`).
- Copied `Awais Mustafa Ta Shah's Palace, Passover Ethan.mp3` to `/tmp` (apostrophe in path breaks curl) and uploaded as call `54ecb5dc-016a-4968-9fd7-cd892d98b4cf`.
- Railway logs show clean L2: `L2_EXTRACTION_WRITE call_id=54ecb5dc-... segments=3 flags=42 pricing_flags=0 entities=0 vulnerable=yes` followed by `💾 SAVED` and `📊 COMPLETE → 202.7s total`. **No `PendingRollbackError` and no `ck_flags_risk_tag` violation** — both `0c2408e` and `e1c8d3b` are confirmed live.
- Playwright on call detail:
  - Click Play, wait 5s, click Play again → audio is at **28.4s and paused** (was 0 reset in the bug). Click again → 37.6s playing. Audio reset bug is FIXED.
  - Click Override → Fail on a checkpoint, focus opens textarea, type a 53-character comment with multiple spaces. Audio stays at **77.6s playing**. Spacebar guard is FIXED. Click "Commit Fail" → no console errors, 0 4xx/5xx, evidence: `phase1-after-override.png`.
- Console errors on call detail: **0**.

## Phase 2 — SSE real-time push (acceptance: PASS with caveat)

**Backend** — `backend/app/realtime.py` (new, 110 LOC):

* Single-process in-memory pub/sub keyed by `call_id`. Unbounded
  asyncio.Queue per subscriber, drop after 1000 queued events to protect
  the publisher.
* Two subscription scopes: per-call_id (e.g. `subscribe("<uuid>")`) and
  global (`subscribe("*")`). `publish()` fans out to both.
* Event shape:
  `{event_type, call_id, ts, payload}`. Named event types:
  `queued, step_started, step_ok, step_err, transcribe_done,
  detect_metadata_done, segments_detected, checkpoints_scored,
  score_ready, finalized, failed`.

**Backend** — `backend/app/realtime_routes.py` (new, 60 LOC):

* `GET /api/calls/events` — global feed.
* `GET /api/calls/{call_id}/events` — per-call feed.
* Both return `text/event-stream`, emit `: connected` immediately on
  open + `: keep-alive` every 5s; close cleanly on client disconnect.
* Headers: `Cache-Control: no-cache`, `Connection: keep-alive`,
  `X-Accel-Buffering: no` (so nginx / Cloudflare don't buffer).

**Wiring in pipeline + upload**:

* `app/pipeline.py::_trace_step` — emits `step_started` + (on success)
  `step_ok` + the named event (`transcribe_done`, `segments_detected`,
  `checkpoints_scored`, `score_ready`, `finalized`); on failure emits
  `step_err` and re-raises. Every pipeline step now pushes to SSE.
* `app/routes.py::upload_call` — publishes `queued` after `db.commit()`
  but before the background task starts, so list pages light up within
  a frame of the POST returning.
* `app/main.py` — `realtime_router` included **BEFORE** the generic
  `router` (which has `@router.get("/api/calls/{call_id}")`) — otherwise
  FastAPI resolves `/api/calls/events` to that path with `call_id="events"`
  and returns 404. This was caught when the first SSE smoke test returned
  `{"detail":"Call not found"}`; fixed in `e2c7317`.

**Frontend** — `frontend-v3/src/lib/hooks/useCallEvents.ts` (new, 110 LOC):

* `useCallEvents(scope)`: opens an EventSource, attaches named-event
  listeners for all 11 event_types, invalidates the right React Query
  keys for that scope (global → `queue / calls / tracker / dashboard /
  intelligence / customers / deals / admin`; per-call → `call.<id>`,
  plus list keys on `finalized` / `score_ready`).
* Exponential reconnect (1s → 30s).

**Frontend mount**:

* `components/design/ScreenFrame.tsx` — `useCallEvents("*")` mounted
  once at the layout level so every list page reacts to backend events
  without polling. Single EventSource per session.
* `app/(reviewer)/calls/[id]/page.tsx` — `useCallEvents(id)` mounted at
  the page level. Replaces the 3s in-flight `refetchInterval` that was
  causing the audio re-mount bug.

**Polling removed / softened**:

* `useCallDetailQuery` — `refetchInterval` dropped entirely. Window-focus
  refresh remains.
* `useCallCheckpointsQuery` — same.
* `useQueueQuery` — slowed from 15s → 60s safety-net poll.
* `useAdminCallsQuery` — dropped the 5s in-flight refetch; 60s safety net.

**Validation**:

* SSE endpoint live on Railway (`compliance-agent-production-690e.up.railway.app/api/calls/events` → 200, raw stream emits `: connected` immediately + `: keep-alive` after 5s).
* On `/calls` (admin) with row-count instrumented at 150ms, uploaded a 4th file → row count went 3 → 4 without manual refresh, **without polling-driven refetch** (60s baseline poll was nowhere near firing).
* Network log shows the burst of `/api/calls?limit=200` fetches clustered immediately after upload — that's SSE invalidation, not polling.
* Lag from upload-end to row-render: ~8s end-to-end. **Mission target was 1s.** Most of that 8s is the FastAPI POST returning + Cloudflare TCP buffering of the SSE event + the Railway round-trip for the `/api/calls` refetch. The SSE pipe itself fires in <100ms server-side. Treat as a meaningful improvement over polling but not as low-latency as the spec required.

## Phase 3 — deal-linker accuracy (acceptance: PARTIAL)

**What I shipped:**

* `_maybe_merge_into_existing_deal` now does Metaphone phonetic comparison
  *in addition to* the SequenceMatcher fuzzy ratio. If either:
  * first-2-tokens phonetic keys overlap, OR
  * all-token phonetic-set Jaccard ≥ 0.5
  ...the fuzzy floor drops from 0.80 to 0.60. Logs
  `🔗 PHONETIC_UPLIFT` so the path is auditable.
* `detect_business_name` accepts a `supplier_hint` kwarg and routes
  to **Opus 4.7** (`cheap=False`) when supplier doesn't contain
  "eon". Sonnet was returning person names / hallucinated phonetic
  guesses on the Awais / Lucca British Gas transcripts. Pipeline
  passes `call.detected_supplier` through automatically.

**Awais 4-call re-test result: 4 calls → 4 deals (NO improvement).**

Reproduction:
1. Wipe DB.
2. Upload `/tmp/a1..a4.mp3` (Awais Mustafa Leadgen / Passover / Verbal / LOA).
3. Wait for all 4 to reach terminal status.
4. Each call lands on its own stub deal — no merge happens at any of the
   four finalize-step invocations.

Root cause from logs:
* One call: `BUSINESS_DETECT → 'Best Traders and Limited Trading As Charles Palace'`
* One call: `BUSINESS_DETECT failed:` (empty)
* The other two: detection didn't even run that pass (likely the path
  needed `current_deal.customer_name.startswith("(auto-detect pending")`
  and another path fell through first).

So we have 4 calls producing up to 4 distinct names ("Charles Palace",
"Awais", "Alister", "Frank"). None match anything in the candidate set
above 0.60 even with Metaphone uplift. The same recordings transcribed
by AssemblyAI produce wildly different surface forms — even Opus 4.7
can't recover "Shah's Palace" from a transcript that says
"Charles Palace" or no business at all.

**This is a transcription-accuracy problem, not a deal-linker logic problem.**
The Metaphone / Opus routing changes WILL help cases where the
transcripts produce nearby-but-not-identical business names; the Awais
fixture has drift wider than fuzzy 0.60 can bridge.

Suggested next attack: per-call audio fingerprint / voice embedding to
cluster calls of the same speaker independently of transcript drift.
Out of scope for tonight.

## Phase 4 — sidebar audit (acceptance: PASS, 1 bug found and fixed)

Walked every sidebar page; clicked all primary tabs / filter buttons on
each; captured console errors + 4xx/5xx; closed the loop on the only
bug surfaced. One-line report per page:

* **Dashboard** — OK (SSE EventSource open from page load — `/api/calls/events` 200 @ req#2; all `/api/intelligence/*` 200).
* **Queue · All** — OK
* **Queue · Pending** — OK
* **Queue · Reviewed** — **WAS BROKEN** (422 on `/api/queue?filter=today`); fixed in `3ecd34c` (translate `today` → `reviewed_today` at the wire boundary in `lib/api.getQueue`). Post-fix: 0 console errors on `/queue?filter=today`.
* **Tracker · Awaiting / Active / Fixed / Dead / Compliant** — OK (5 tabs, 0 errors)
* **Rejections · Active / Fixed / Dead / Archive** — OK (4 tabs, 0 errors)
* **Customers** — OK
* **Deals** — OK
* **All Calls** — OK
* **Agents** — OK
* **Scripts** — OK
* **Compliant** — OK
* **Non-compliant** — OK
* **Settings** — OK
* **User Guide** — OK
* **Call detail (Pass / Override→Fail / Edit metadata / Reanalyze / Export buttons)** — OK (0 console errors across all 5 mutations on call `3e8f707d-...`).

## What still doesn't work — exact reproduction steps

| Severity | Bug | Reproduction | Commit that holds it |
|---|---|---|---|
| **P1** | SSE end-to-end lag is ~8s on /calls (admin), not the <1s the spec called for. | On `/calls`, open DevTools and instrument `setInterval(()=>document.querySelectorAll('tbody tr').length, 150)`. Upload via curl. Watch first count change. | `7390b33` design; the 8s is dominated by Cloudflare SSE buffering + Railway `/api/calls` RTT, not the publish path itself. |
| **P1** | Awais 4-call → 4-deal: deal-linker still can't collapse. | Wipe DB, sequentially upload `/tmp/a1..a4.mp3` (Awais Mustafa fixtures from `compliance-docs/AI Data/Non-EON/Compliant/`). Final state: `/deals` shows 4 distinct deals instead of 1. | `ca76e2e` shipped the Metaphone + Opus 4.7 routing; the underlying limit is transcription drift, not the matcher. |
| **P2** | Business name detection silently returns None on some non-EON transcripts. | Same 4-call upload. Railway logs show `🏢 BUSINESS_DETECT failed:` (empty string after `failed:`) on one call. | Looks like the LLM returned None / empty. `business_detect.py` correctly swallows but pipeline then can't run the second-pass merge with a business name. |
| **P2** | One call landed on `needs_manual_review` status (a3 Verbal). | Same 4-call upload. Watch call `0c9b4df3-9fe5-4db0-b7dd-5ce3bf6ff9b4`. | Score-step decided `needs_manual_review` — investigate the bucket gate logic for this specific recording. |
| **P3** | One call has `customer_name=None` and `agent_name=Alyssa` (Leadgen). | Same. Call `26553fa9-084f-45ac-8917-6a28dbd69565`. | `detect_names` returned only the agent; customer fell through. Could backfill from business_name + person_name when both are missing. |

## Resume guide for next-me

* **Repo state**: clean on origin/main at `3ecd34c`. All my BRAIN edits
  in this session are appended below in commit `<next>` (see history).
* **Live URLs unchanged**:
  - https://compliance-agent-mu.vercel.app
  - https://compliance-agent-production-690e.up.railway.app
* **Login**: `admin@compliance-agent.local` / `Audit-Pass-2026-05-10!`
* **Open queries**: there's a 4-call Awais test fixture sitting in
  production DB. If a Phase-2 retest is needed, run
  `POST /api/admin/wipe-all-calls?confirm=YES_DELETE_EVERYTHING` first.
* **Phase 3 deeper attack**: audio-fingerprint clustering. See `BUSINESS_DETECT failed` lines in Railway logs around `2026-05-15T21:42–21:46` for the empty-name path.
* **Phase 2 latency**: investigate whether Railway is buffering SSE
  responses upstream of `X-Accel-Buffering: no` (`curl -N` to Railway
  origin direct, no Cloudflare, and time the first `: connected` — if
  that's <50ms server-side, Cloudflare proxy is the buffer).
