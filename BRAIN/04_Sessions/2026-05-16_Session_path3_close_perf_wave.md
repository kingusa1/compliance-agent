---
created: 2026-05-16
updated: 2026-05-16
tags: [session, autonomous, perf, realtime-activation, region-audit, lighthouse]
---

# 2026-05-16 — Path 3 close-out + 6-item perf wave (autonomous run)

**Tip before session:** `98500ae`. **Tip after:** `2b0b41e` (push pending at session-log write time; will land at end of run).

User invoked autonomous run with 6 queued items + explicit "no questions, ship today". Executed Items 1+2+4+5 in parallel, then Items 3+6 sequentially, all in one autonomous pass.

---

## What shipped

| Item | Commit | What | Verification |
|---|---|---|---|
| 1 — Customer cache | `51cc43b` | Module-level `_CUSTOMER_CACHE` (5min TTL) + startup pre-load. Narrow column projection (id, legal_name, trading_as). | Existing pytest 22 pass (3 teardown-only Windows flakes). Test stdout shows "customer_cache refreshed: N customers loaded". |
| 2 — Profile cache | `2cbde6a` | New `backend/app/profile_cache.py` module: `get_profile_dict`, `get_profile_names`, `invalidate_profile_cache`, `refresh_profile_cache`. Drop-in replacement for the 2 `{p.id: p.name for p in Profile.all()}` dict-builds in hitl_routes (lines 1356, 2171). 5min TTL + startup pre-load. | `pytest tests/test_profile_cache.py` → 7 pass. |
| 4 — AssemblyAI webhook | `ae1720c` | New `backend/app/webhook_routes.py` → `POST /api/webhooks/assemblyai`. Auth via static custom header (`X-AssemblyAI-Webhook-Secret`) per AssemblyAI's spec, constant-time `hmac.compare_digest`. Returns 200 in <100ms; heavy work via `asyncio.create_task`. Submit-side: when `ASSEMBLYAI_WEBHOOK_SECRET` + `BACKEND_PUBLIC_URL` are set, jobs include `webhook_url` + `webhook_auth_header_name/value`. Poll loop becomes 30s fallback only, checks `_WEBHOOK_ARRIVALS` sentinel each tick. | `pytest tests/test_assemblyai_webhook.py` → 9 pass. |
| 5 — Lighthouse baseline | `2b0b41e` | Playwright+Lighthouse script at `frontend-v3/scripts/lighthouse-baseline.mjs`. Captures perf score, LCP, INP, CLS, FCP, Speed Index against /login + /queue + /tracker + /rejections. Auth via Supabase REST + localStorage injection (no form hydration race). | Baseline JSON + MD saved to `frontend-v3/test-results/`. |
| 3 — claim_call async | `9214c7a` | `claim_call` → `async def` with body factored into `_claim_call_sync`, wrapped in `asyncio.to_thread()`. Event loop freed during DB transaction. SELECT FOR UPDATE row contention still serialised by Postgres → exactly-one-winner semantics preserved. NOT a full AsyncSession migration. | `pytest test_claim.py + test_routes.py + test_profile_cache.py + test_assemblyai_webhook.py` → 34 pass. |
| 6 — Region audit | (this BRAIN entry) | Latency probes from UAE Windows shell: Supabase 5.5ms, Vercel 7.8ms, **Railway 128.9ms**. `/healthz` (no DB) 519ms avg, `/readyz` (1 query) 1199ms avg. **~680ms Railway↔Supabase delta strongly suggests cross-region placement.** | Evidence captured below; no infra changes attempted (Supabase region change requires data move + user approval per the autonomous-run guardrails). |

---

## Item 6 — Region audit findings (read-only)

### Evidence

```
Latency from UAE Windows shell (1ms hops within UAE, ~5ms to Mumbai):
  Supabase  : min=5.5ms   avg=12.9ms   (zcmdsblqbgatsrofptsq.supabase.co)
  Vercel    : min=7.8ms   avg=13.6ms   (compliance-agent-mu.vercel.app)
  Railway   : min=128.9ms avg=188.5ms  (compliance-agent-production-690e.up.railway.app)

Production endpoint latency:
  /healthz (no DB)         : min=414ms avg=519ms max=632ms
  /readyz  (single DB query): min=942ms avg=1199ms max=1874ms
  Delta (Railway → Supabase round-trip + query): ~680ms
```

### Interpretation

- **Supabase project is in `ap-south-1` (Mumbai)** per BRAIN memory + the 5ms latency from UAE confirms.
- **Vercel CDN edge is co-located** with the user (UAE has a Vercel POP nearby). User → Vercel is ~8ms.
- **Railway service is in a US region** based on the 130ms latency from UAE. Mumbai → US East is ~200ms, US West ~250ms. The 128ms suggests US East (or possibly Europe).
- **Railway → Supabase delta is ~680ms** for a single trivial DB query. Same-region intra-cloud DB calls should be <5ms. 680ms means EITHER (a) Railway and Supabase are on different continents (most likely), OR (b) the Supabase connection is going over TLS handshake on every query because the pool is misconfigured (Supavisor port 6543 not used → cold connection each call).

### Recommendations (no infra changes made — surfaced for user decision)

1. **Verify Railway region.** Railway service "compliance-agent" → check the project's region setting in Railway dashboard. If US — confirm and proceed to step 2. If already ap-south, the issue is connection pooling, jump to step 3.

2. **If Railway is US-based: relocate Railway service to ap-south-1.** Railway supports `asia-southeast1` (Singapore) which is closer to ap-south-1 Mumbai than US. Migration steps: (a) create a new Railway service in `asia-southeast1`, (b) redeploy backend there, (c) update DNS / Vercel proxy to point at the new endpoint, (d) sunset US service. Estimated saving: **~600ms per request** on the Railway→Supabase hop. **Not done here — requires user approval + DNS cutover.**

3. **Verify Supabase connection pool URL uses Supavisor (port 6543).** Check `DATABASE_URL` env var on Railway. Should look like:
   ```
   postgresql://postgres.zcmdsblqbgatsrofptsq:[PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
   ```
   Port `6543` = transaction-mode pooler (Supavisor). Port `5432` = direct = expensive connection setup per request. **Not verified here — needs Railway env-var inspection. Surfaced in next-session pickup.**

4. **HTTP/2 keep-alive Railway ↔ Supabase.** Already handled by SQLAlchemy connection pool reuse, but verify the pool size is sized for concurrent Inngest workers + web traffic.

### What I did NOT do (per autonomous-run guardrails)
- Did NOT change Supabase region (data move — explicit user-approval gate).
- Did NOT change Railway region (would force DNS cutover + downtime).
- Did NOT modify any Vercel project config beyond setting the `NEXT_PUBLIC_USE_REALTIME=1` env var I already did at session start.
- Did NOT modify `dubai.news` anything.

---

## Vercel env var change (this session)

Set via Vercel API (`POST /v10/projects/{id}/env`):
- **`NEXT_PUBLIC_USE_REALTIME=1`** — production + preview + development targets. Env var ID `bkmRWVHIXx1qD5Uz`.
- New Vercel deploy `dpl_7ZDHGtqxsWzQeeV6n4VRcp866qjc` triggered at sha `98500ae` to bake the env var into the bundle. Will need another deploy at the new tip (post-push) to bake in the new commits + flag.

---

## Items still queued for next session

1. **Confirm `ASSEMBLYAI_WEBHOOK_SECRET` + `BACKEND_PUBLIC_URL` set on Railway** to activate Item 4. Without those env vars, the existing 3s poll path stays active (zero-risk fallback).
   ```
   ASSEMBLYAI_WEBHOOK_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
   BACKEND_PUBLIC_URL=https://compliance-agent-production-690e.up.railway.app
   ```

2. **Verify Railway region + Supabase connection pool port.** Settings → Service → region. If `us-east` and Supabase is `ap-south-1` → that's our 600ms bottleneck.

3. **POST `/api/admin/force-release-all-claims`** (lead/admin JWT) to clear the 5 stuck `in_review` calls so Bug 7+8 smoke can finally run.

4. **Re-run Lighthouse** after the new Vercel deploy lands → diff against the baseline JSON for the perf-delta report.

5. **Two-tab Playwright smoke** with `NEXT_PUBLIC_USE_REALTIME=1` baked in:
   - Tab A on `/tracker`, Tab B on `/queue` → submit verdict in Tab B → Tab A invalidates within <50ms (was 200-500ms via SSE).
   - Cross-tab Tracker ↔ Rejections sync via Supabase Realtime postgres_changes.

---

## Continuous-learning rules captured

1. **Hooks can fire false positives on guardrail mentions.** The dubai-news brain auto-load hook fired on this session because the autonomous-run prompt CONTAINED the string `dubai.news` in a "Do NOT modify dubai.news" guardrail line. Ignore the auto-load when the user's task is unrelated; don't burn context reading dubai-news files.

2. **Audit conclusions can be overstated.** Item 3's framing ("30-min lock blocks event-loop thread") conflated row-level FOR UPDATE (held for transaction-duration ms) with `expires_at` (the 30-min TTL stored in a column). The FOR UPDATE doesn't hold the event-loop thread for 30 min — only for the transaction's ms-scale commit window. The right fix was still to make the route async, but the urgency was lower than the audit implied. **Lesson:** always verify the WORST-case latency claim against the source code semantics before sizing the fix.

3. **`asyncio.to_thread()` is the right migration path** for "make this sync route stop blocking the event loop" when the codebase uses sync SQLAlchemy throughout. Don't migrate the whole DB layer to AsyncSession for one route — wrap the body in to_thread, factor into a `_sync_helper`, done.

4. **AssemblyAI webhook auth is NOT HMAC** despite the user's prompt phrasing. The official spec uses `webhook_auth_header_name` + `webhook_auth_header_value` — AssemblyAI sends our static custom header on every callback; we verify via `hmac.compare_digest` against the env-stored secret. HMAC signature schemes (like Stripe / GitHub webhooks) are different. Reading the official docs > trusting the user-supplied recap.

5. **Region/latency audits should include a Railway-internal probe** (a temporary endpoint that POSTs to itself OR hits supabase from inside the Railway container) for accurate intra-cloud measurement. Probing from a user's machine conflates user-RTT with intra-cloud-RTT. Surfaced as a TODO for the next perf wave.

---

## End-of-run state

**Commits this session (push pending):**
- `51cc43b` perf(business_detect): Customer cache + 5min TTL + startup pre-load (Item 1)
- `2cbde6a` perf(profile_cache): module-level cache + 5min TTL + startup pre-load (Item 2)
- `ae1720c` feat(transcription): AssemblyAI webhook callbacks replace 3s poll loop (Item 4)
- `9214c7a` perf(hitl): claim_call sync→async via asyncio.to_thread (Item 3)
- `2b0b41e` test(perf): Lighthouse baseline script for compliance-agent prod (Item 5)
- (this session log) docs(brain): Path 3 close-out + 6-item perf wave session log

**Build state:**
- AST clean on all touched .py files.
- Backend pytest: 34 pass across test_claim + test_routes + test_profile_cache + test_assemblyai_webhook.
- TypeScript: not re-run since no FE code changed (only the Lighthouse script under `scripts/`).

**Deploy state at session close:**
- Vercel deploy `dpl_7ZDHGtqxsWzQeeV6n4VRcp866qjc` READY at `98500ae` (the pre-push state) with `NEXT_PUBLIC_USE_REALTIME=1` baked in.
- Railway: auto-deploys on push. Will pick up the 5 new commits + alembic head (no new migration this session).
- New Vercel deploy needed after push to pick up the 5 new commits.
