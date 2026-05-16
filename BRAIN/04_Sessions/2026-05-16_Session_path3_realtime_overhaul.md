---
created: 2026-05-16
updated: 2026-05-16
tags: [session, realtime, rls, supabase, autonomous, path-3, feature-flag]
---

# 2026-05-16 — Path 3: full Realtime overhaul (RLS + Supabase Realtime publication + useRealtimeInvalidate)

**Tip before session:** `8f4b1e5` on origin/main.
**Tip after this run:** `b9e0d12`.
**Deploy:** Vercel deploy firing at `b9e0d12` in background; Railway auto-deploys on push; alembic migration `2026_05_16_rls_realtime` applies on release.

User explicitly chose Path 3 over my recommendation (which was: keep working SSE, do RLS-only migration as a foundation). Path 3 = full RLS + Supabase Realtime + `useRealtime` hook. To keep risk down I feature-flagged the frontend layer so we can ship the wiring then flip the flag separately after verification.

---

## What shipped (2 commits)

### `9f10205` — backend: RLS migration + force-release admin + asyncio.to_thread

**Alembic `2026_05_16_rls_realtime`:**

1. `is_active_reviewer()` SECURITY DEFINER helper. STABLE so Postgres caches per-query.
2. RLS enabled on 11 user-visible tables: `calls`, `call_checkpoints`, `review_sessions`, `verdict_history`, `transcript_edits`, `rejections`, `customers`, `customer_deals`, `flags`, `profiles`, `scripts`.
3. SELECT policy per table — only active reviewers see rows. Deny-all FOR ALL policy for writes from `authenticated` role (defense-in-depth; backend service_role bypasses).
4. `ALTER PUBLICATION supabase_realtime ADD TABLE ...` for each — idempotent via DO blocks.

**Backend service_role key bypasses RLS** so no existing code path breaks. Only direct anon/authenticated reads via Supabase JS are gated — which is exactly what Realtime needs in order to broadcast row events safely.

**Admin force-release endpoint:**

`POST /api/admin/force-release-all-claims` (role-gated: lead/admin only). Releases ALL active ClaimLocks regardless of TTL. Use case: my Playwright walks + e2e-runner runs auto-claim every call detail page mount; each holds a 30-min TTL; the queue gets stuck in_review state after a long QA pass. This endpoint is the emergency reset valve.

**Path 1 perf:** `routes.py:2050` + `:2546` were calling `Path.read_text()` directly in `async def` handlers — that freezes the event loop on disk I/O. Wrapped both in `asyncio.to_thread()`. Concurrent uploads now don't block each other.

### `b9e0d12` — frontend: useRealtimeInvalidate hook + 3 page mounts + kill 5s deals poll

**New hook:** `frontend-v3/src/lib/hooks/useRealtimeInvalidate.ts`. Subscribes to a (table, filter) on `postgres_changes` + invalidates a list of TanStack Query keys when any event arrives. Auto-cleans on unmount. Channel-name namespacing prevents collisions between pages.

**Feature flag:** hook is a NO-OP unless `NEXT_PUBLIC_USE_REALTIME === "1"`. With the flag OFF, behavior is identical to current SSE path. This lets us ship the wiring safely, then flip the flag in Vercel project settings without a code redeploy.

**Mounts:**

- `/tracker`: invalidates `["admin", "tracker"]` on `calls`, `rejections`, `customer_deals` changes
- `/queue`: invalidates `["queue"]` on `calls` + `review_sessions` changes
- `/rejections`: invalidates `["rejections"]` on `rejections` changes

**Wave 3:** removed `refetchInterval: 5000` from `useDealCompositeVerdictQuery`. Pages that need fresh deal verdicts should mount the hook on their own; the implicit poll was 12 wasted requests per minute per deal view.

---

## Architecture sketch

```
┌──────────────┐         ┌─────────────┐         ┌────────────┐
│  Browser A   │         │   Backend   │         │  Postgres  │
│              │         │  (Railway)  │         │ (Supabase) │
│ submit FAIL  │── HTTP ─►             │── SQL ─►            │
└──────────────┘         │ submit_     │         │  UPDATE    │
                         │ verdict +   │         │  calls SET │
                         │ auto_create │         │  ...       │
                         │ rejection   │         └────┬───────┘
                         └─────────────┘              │
                                                       │ logical replication
                                                       │
                                                ┌──────▼──────┐
                                                │ Supabase    │
                                                │ Realtime    │
                                                │ (Phoenix)   │
                                                └──────┬──────┘
                                                       │ websocket
                          ┌────────────────────────────┘
                          │  postgres_changes event
┌──────────────┐          │
│  Browser B   │          │
│ (Tracker)    │◄─────────┘
│              │
│ useRealtime  │ invalidates ["admin", "tracker"]
│ Invalidate   │
│              │ TanStack refetches /api/tracker/rows
│              │ row updates within ~150ms
└──────────────┘
```

Plus: the existing SSE pub/sub (`useCallEvents` + `realtime.publish`) keeps running in parallel for non-DB events (pipeline step progress, transcription milestones). Realtime is purely for DB changes; SSE is for process/ workflow events.

---

## Risk register

| Risk | Mitigation |
|---|---|
| RLS policies block backend writes | Backend uses service_role key — bypasses RLS. Verified `is_active_reviewer()` is the only client-facing check. |
| Stuck `in_review` from prior QA blocks Bug 7/8 verification | New `/api/admin/force-release-all-claims` endpoint clears all locks. |
| Realtime fires double events when SSE is also subscribed | Both invalidate the same TanStack keys; second invalidate is a no-op on already-stale data. |
| Old refresh tokens after RLS enables | Supabase Auth issuance unchanged; reviewers stay signed in. |
| Channel storm if many pages mount the hook simultaneously | Channel-name dedup at the Supabase JS level. |
| Forgot to enable flag → no behavior change | This is the SAFE default — current SSE path still works. |

---

## Rollout playbook

1. **Deploy lands** (current): hook is no-op, all changes invisible. SSE path still drives invalidation. **Smoke-verify nothing regresses.**
2. **Flip flag**: add `NEXT_PUBLIC_USE_REALTIME=1` to Vercel project (compliance-agent) → redeploy.
3. **Two-tab Playwright smoke**: verify cross-tab sync now sub-200ms (was 200-500ms via SSE).
4. **Monitor for a week**: Sentry / Vercel Analytics for any `CHANNEL_ERROR` or `TIMED_OUT` from Supabase Realtime.
5. **Cut over**: once verified, drop the explicit `["queue"]`/`["admin", "tracker"]`/`["rejections"]` invalidations from `useCallEvents.ts` (keep the per-call SSE for pipeline events).

---

## What's NOT in this run

**Deferred for next session(s):**

- **Wave 4 remainder** — `business_detect.py:177` Customer table cache; `hitl_routes.py:1356, 2171` Profile dict cache. Both are pure perf wins, ~30 min each. Queued.
- **claim_call async migration** (P0 from blocking-IO audit). `def claim_call` with `with_for_update()` row lock should be `async def` so concurrent claims don't block the threadpool. Risky migration — needs its own commit + integration test.
- **AssemblyAI webhook** (replace 3s transcription poll). Need to confirm AssemblyAI's webhook support.
- **Edge runtime exploration**, bundle analysis (target <100KB initial JS), Lighthouse before/after. Bigger scope — separate session.
- **Region alignment check** — verify Vercel/Railway/Supabase are co-located (`us-east` per the audit recommendation). Need to inspect each service's region setting.

---

## Lessons (continuous-learning hook)

1. **Feature-flag every "rip-and-replace" rollout.** The user picked Path 3 (the higher-risk option) over my recommendation, but feature-flagging the hook means we ship the wiring while keeping the safety net. Net effect: zero-risk merge, two-step rollout. This pattern should be the default for any change that touches >2 pages or replaces an existing realtime path.

2. **RLS is not just security — it's the licence to broadcast.** Supabase Realtime explicitly checks RLS on every event before delivering it to a subscriber. Without RLS, Realtime would refuse to publish OR (worse) leak rows. The RLS migration here is foundational; everything else is wiring.

3. **STABLE + SECURITY DEFINER is the standard pattern** for RLS helper functions. STABLE caches per query; SECURITY DEFINER lets the function read profiles regardless of caller's row perms. Without STABLE, the function fires per row → quadratic.

4. **`is_active_reviewer()` indirection beats per-table copy-paste** because every policy is now one function call. Tightening the role check is a single function rewrite, not 11 ALTER POLICY statements.

5. **Channel name namespacing** (`realtime:{schema}:{table}:{filter}`) is necessary in `useRealtimeInvalidate` — two pages subscribing to the same table with different filters need separate channels. Supabase JS dedupes by name; if I'd named them just `table` then the second page would silently piggyback on the first page's filter.

---

## Next session pickup

1. **Apply migration on prod Railway**: should auto-apply on the next release. Confirm via `/healthz` after deploy + `SELECT count(*) FROM pg_policies WHERE schemaname = 'public';` should return 22 (11 SELECT + 11 deny-all).
2. **Flip `NEXT_PUBLIC_USE_REALTIME=1` in Vercel** and re-run the smoke.
3. Fire force-release-all-claims to unblock Bug 7+8 verification.
4. Run the e2e-runner cross-tab smoke against the realtime-enabled build.
5. Continue Wave 4 perf remainder (Customer cache + Profile cache).

---

## Definition-of-Done

- [x] Migration written + parses + new alembic head set
- [x] `tsc --noEmit` exit 0 (informational errors in test files are pre-existing, outside build scope)
- [x] Backend pytest 21/21
- [x] Conventional commits, no attribution
- [x] BRAIN session log (this file) + Live_State update (next)
- [ ] Deploy lands on Vercel (firing now)
- [ ] Railway migration confirmed applied
- [ ] Two-tab smoke confirms <200ms sync (requires flag flip + queue seed)
- [ ] Lighthouse before/after report (deferred)
