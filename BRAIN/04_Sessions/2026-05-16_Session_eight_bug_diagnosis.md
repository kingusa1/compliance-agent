---
created: 2026-05-16
updated: 2026-05-16
tags: [session, diagnosis, tracker, queue, upload, reject-flow, realtime-cross-sync, eight-bugs]
---

# 2026-05-16 — Eight-bug diagnosis (Tracker / Queue / Upload / Reject)

**Tip at session start:** `5bbec6e` on origin/main.
**Live URL:** `compliance-agent-mu.vercel.app` (dpl_356vjYNmTCXmja6itboSwi4aS2nv at sha 90c39f5).

User invoked `/gsd` with a diagnose-fix-verify brief covering 8 bugs across 3 surfaces. Per the system prompt's "parallel where independent" mandate, fanned out 4 diagnostic subagents in a single Task block:

1. **`debugger`** → Tracker bugs (1, 2, 3)
2. **`debugger`** → Human Review bug (4)
3. **`debugger`** → Upload bugs (5, 6)
4. **`tracer`** → Reject + cross-tab realtime wiring (7, 8)

All returned in ~9 minutes. Findings below.

---

## Bug-by-bug

### Bug 1 — Tracker "Awaiting Review · N" badge diverges from table row count

**File:** `frontend-v3/src/app/(admin)/tracker/page.tsx:77`

```ts
const awaitingQ = useTrackerRowsQuery({ tab: "awaiting_review" });
const awaitingCount = awaitingQ.data?.count ?? 0;
```

The badge runs a SEPARATE unfiltered query (`awaitingQ`). The table is driven by `filters` (category / search / advanced). When any filter is active, the badge keeps the unfiltered total while the table shows the filtered subset — count != rows visible.

**Secondary issue** (`backend/app/tracker_aggregator.py:583-586`): the category post-hoc filter is applied AFTER `LIMIT 500`. With >500 awaiting-review rows, the badge's `count` field is `len(rows_after_limit)` not a real `COUNT(*)`.

**Fix:** When the active tab IS `awaiting_review`, read the count from `rows.length` of the already-running main query — don't run a duplicate.

---

### Bug 2 — Tracker table flashes empty on filter change

**File:** `frontend-v3/src/lib/queries/tracker.ts:131`

```ts
queryKey: ["admin", "tracker", filters],
```

The whole `filters` object is the cache key. Every keystroke in the search box or pill toggle generates a brand-new query key → TanStack Query has no cached entry → `isLoading=true` → `rows = []` while the round-trip lands.

`staleTime: 15_000` does not help because staleTime only suppresses background refetches of an EXISTING key.

**Fix:** Add `placeholderData: keepPreviousData` so the prior key's data renders while the new key loads.

---

### Bug 3 — Saved Views do not work on Tracker

**Root cause:** `SavedViewsBar` is only mounted on `/queue` (`(reviewer)/queue/page.tsx:767`). It is literally absent from `(admin)/tracker/page.tsx`. The component itself only speaks the `QueueFilter` shape — it has no concept of `TrackerFilters` (category / advanced).

**Fix:** Mount on tracker with a `TrackerFilters ↔ QueueFilter`-style adapter OR extend `SavedViewsBar` to accept a generic shape. Minimum-viable: serialize `{tab, search, categories, advanced}` into the saved view's `filters` JSON column + a `tracker`-flavoured `onApply`.

---

### Bug 4 — Human Review Queue count vs list mismatch

**File:** `backend/app/hitl_routes.py:1344`

```python
Call.review_status != "reviewed",  # for backlog count
```

vs. the Pending tab list at the same file `:1408-1412` which filters `review_status == "unclaimed"`. Any call in `in_review` state counts in the badge but is hidden from the list.

**Frontend secondary** (`(reviewer)/queue/page.tsx:632-642`): `showProcessing=false` (default) hides rows where `score is null`. Backlog count is read from `metrics.backlog` (unfiltered server count). So even when calls exist in the list response, they can be filtered out client-side but still tallied in the badge.

**Fix:** Change backend `backlog` predicate to `Call.review_status == "unclaimed"` so count == list. The separate `in_review` metric at `:1350-1354` already tracks claimed-but-not-submitted if a separate badge is desired.

---

### Bug 4b — "This page couldn't load" error page

**Diagnosis incomplete** — the agent could not fully reproduce without browser devtools. Best hypotheses:

1. Error originates on `/calls/[id]` page (the call detail "sub-tabs" Transcript / Script / Verdict / Flags), not the /queue page.
2. `SavedViewsBar` was added in `7b7e078` — its import chain pulls `useRouter` from `next/navigation` (safe) and Base UI's `DropdownMenu`/`Dialog`. No identified throw.
3. Could be a missing `saved_views` DB table in prod → fetch returns 500 → React Query catches → `placeholderData` shows previous data → no crash UI. **Probably not this.**

**Action:** Will deploy the bug-1/2/3 + bug-7/8 fixes first and re-run the Playwright smoke to confirm whether the error page persists. If yes, add a `console.log` instrumented build + re-trace.

---

### Bug 5 — Same deal does not group

**File:** `backend/app/pipeline.py:472`

```python
if not detected_supplier or not detected_customer:
    return
```

The merge function bails immediately when `detected_supplier` is empty. Lead-gen calls with no script match have empty `detected_supplier` → no merge ever fires for them. The per-candidate loop later has its own supplier-match check (and tolerates empty cand_supplier), so the early-return is over-broad.

The 2026-05-15 commit `52790a1` added a second-pass merge using `business_name`, but `52790a1` invokes `_maybe_merge_into_existing_deal(call, db, override_customer_name=business_name)` — and that function still bails on the supplier guard.

**Fix:** Change `:472` to `if not detected_customer: return`. Let the candidate loop's own supplier-match logic decide per candidate. This preserves correctness (the loop won't merge across suppliers) while unblocking the supplier-unknown case.

---

### Bug 6 — Upload jumps to Process page (NOT A BUG)

**Verdict: bug report is wrong.** The current behavior matches the implemented spec:

- Single file → `router.push("/calls/{id}")` (`UploadModal.tsx:57`)
- Multi-file → `router.push("/calls")` via `__BATCH_TO_CALLS_DASHBOARD__` sentinel (`UploadModal.tsx:53`)

There is no "review/grouping" pre-process step anywhere in the codebase or in `BRAIN/02_Domain/` workflow docs. The user is probably dropping one file at a time, which always triggers the single-file path.

**Action:** Confirm with the user whether the user wants a NEW pre-process review step added (would be a feature, not a fix). Skip in this commit.

---

### Bug 7 — Reject flow: conditional rejections invalidation

**File:** `frontend-v3/src/lib/mutations/reviewer.ts:254`

```ts
if (data?.auto_rejection_id) {
  qc.invalidateQueries({ queryKey: ["rejections"] });
}
```

The `["rejections"]` invalidation is gated on `auto_rejection_id` being non-null. If the FAIL verdict has no failing `CallCheckpoint` rows (empty rejection loop), `auto_rejection_id` returns null and `["rejections"]` is NEVER invalidated. The `/rejections` page stays stale even though the verdict shipped.

**Fix:** Unconditionally invalidate `["rejections"]` on any FAIL or REVIEW verdict (the rejection page may still need to refresh for confirmed_by changes on existing rows even if no new rejection was created).

---

### Bug 8 — Cross-tab realtime sync is broken (CRITICAL)

**File:** `backend/app/hitl_routes.py:654-659`

```python
emit_event(VERDICT_SUBMITTED, {
    "call_id": call_id,
    "actor_id": reviewer["id"],
    ...
})
```

`emit_event` writes to Postgres `pg_notify` channel. The SSE pub/sub at `backend/app/realtime.py` is an **in-memory asyncio.Queue** — `pg_notify` does NOT bridge to it. Searched `main.py` and confirmed: there is no `asyncpg.LISTEN` background task that translates pg_notify into `realtime.publish()`.

**Result:** Tab B's `useCallEvents("*")` global SSE subscriber never receives a "verdict_submitted" event. Tracker / Queue / Rejections in Tab B all stay stale until manual refresh OR until a different SSE event fires.

**Secondary key mismatch** (`frontend-v3/src/lib/hooks/useCallEvents.ts:68`):

```ts
qc.invalidateQueries({ queryKey: ["tracker"] });
```

The actual tracker query key is `["admin", "tracker", filters]`. The prefix `["tracker"]` does NOT match `["admin", "tracker", ...]`. There is a separate `["admin"]` invalidation on line 72 that DOES match (and acts as a broad fallback) — but the explicit `["tracker"]` line is dead code.

**Fix (two parts):**

1. **Backend:** Add `realtime.publish(call_id, "score_ready", {...})` immediately after `db.commit()` in `submit_verdict`. `"score_ready"` is an existing named event in the frontend listener at `useCallEvents.ts:112-124` that triggers queue + tracker + admin invalidations on per-call subscribers. The global `*` subscriber will also see it.

2. **Frontend:** Change `["tracker"]` → `["admin", "tracker"]` in `useCallEvents.ts:68` to match the real query key. Don't depend on the `["admin"]` blanket invalidation surviving future tightening.

---

## Fix order (no file overlap, parallel-safe)

**Wave 1 — Backend (parallel within wave):**
- Bug 4: `hitl_routes.py:1344` backlog predicate
- Bug 5: `pipeline.py:472` supplier guard
- Bug 8a: `hitl_routes.py:~660` realtime.publish post-commit

**Wave 2 — Frontend (parallel within wave):**
- Bug 1: `tracker/page.tsx:77` awaitingCount logic
- Bug 2: `lib/queries/tracker.ts:131` placeholderData
- Bug 3: `tracker/page.tsx` mount SavedViewsBar
- Bug 7: `lib/mutations/reviewer.ts:254` unconditional rejections invalidate
- Bug 8b: `lib/hooks/useCallEvents.ts:68` key match

**Wave 3 — Tests + reviews + e2e.**

**Wave 4 — Push + deploy + smoke + BRAIN final state.**

Bug 6 deferred (not a bug; user clarification needed if they want a new pre-process feature).
Bug 4b deferred (needs browser-devtools repro; will revisit after the other fixes deploy).

---

## Lessons saved (continuous-learning hook)

1. **Type fiction with no runtime validation is the root of two P0 bugs this week.** `ClaimResponse.session_id` (yesterday's P0) and now Bug 8's `["tracker"]` vs `["admin", "tracker"]` key drift. Both are TypeScript types that don't match wire/runtime reality and never get validated. **Future rule:** every wire/contract boundary (HTTP response, query key shape, SSE event payload) needs Zod parse OR a vitest snapshot that asserts the runtime shape.

2. **TanStack Query queryKey design pattern:** if the key includes a mutable filter object, `placeholderData: keepPreviousData` is non-negotiable. Otherwise every keystroke = empty flash. Worth a sweep across other queries: `useQueueQuery`, `useCustomersQuery`, `useRejectionsQuery` (already has it per the 2026-05-16 audit fix), and any others.

3. **pg_notify and realtime.publish are different channels.** The codebase has two parallel notification systems (Postgres LISTEN/NOTIFY via `emit_event` + in-memory asyncio SSE pub/sub via `realtime.publish`) and they don't bridge. Every backend mutation that should propagate to UI MUST call `realtime.publish` (or both). Worth an audit sweep of `backend/app/**` for `emit_event` calls that have no corresponding `realtime.publish`.

4. **"Badge count vs list rows" disagreement is a structural anti-pattern.** When the badge query and the list query don't share the same predicate, they WILL drift. Either (a) the badge should be `rows.length` of the actual list query, or (b) both queries should explicitly share a backend endpoint that returns `{count, rows}` from the same predicate. Repeat offender: this is the 3rd time in this codebase (Bug 4 = backlog vs unclaimed; Bug 1 = awaiting badge vs filtered table; the 2026-05-13 session also caught a similar Rank Math-style count drift).

5. **Saved Views was wired on `/queue` but never extended to `/tracker`.** The pattern of "implement on one page, intend to copy to others, forget" is recurring. Future: when adding a new page-level UI primitive (saved views, filters, exports), put a TODO comment + grep target on every page that should have it. Or accept it as a one-page feature and stop telling users it works elsewhere.
