---
created: 2026-05-27
updated: 2026-05-27
tags: [session, perf, auth-profile-cache, queue-tab-fix, agent-page-redesign, composite-bundle-endpoint]
---

# 2026-05-27 PM — Perf wave + queue tab fix + agent page redesign + composite bundle endpoint

**Tips pushed (chronological):** `69e79a8` → `a273e0b` → `4204de4` → `f65ee4e` → `10522b8`. Each Railway healthcheck PASS; Vercel REST deploys triggered for frontend-touched commits.

**Owner asks in sequence (verbatim):**
1. *"now i think the problem regarding the system or the website being slow is a different thing related to the database because we have a lot of memory on railway and supabase so the problem is not related to upgrading anything"*
2. *"do the other thing i have check railway and its … the compliance-agent service is already deployed in Southeast Asia (Singapore)"*
3. *"also i want you to check there is something broken here in this place when i click on review the reviewed disappear and when i click on pending the reviewed appear"*
4. *"make the agent page much more better and attractive and have all the information that the quality person will need to take a decision"*
5. *"continue fixing the other thing and after you finish update the brain"*

## Wave-by-wave summary

### Wave 1 — Profile cache wire-up (`69e79a8`)

Owner-observed slowness diagnosed live via Playwright timing harness:

```
/healthz (no auth, no DB):     299 ms  ← network baseline
/api/me  (auth + 1 query):     614 ms  ← +315 ms per authed request
/api/observability/stuck:      835 ms  ← +536 ms per DB round-trip
/api/deals?limit=20:          2006 ms  ← 8× compounding
```

The smoking gun: `app.profile_cache` had been pre-loaded at FastAPI startup with a 5-minute TTL (`main.py:263` logs `profile_cache: pre-loaded 1 profiles`), but `current_user` in `app/auth.py` was IGNORING it. Every authenticated request did a fresh `db.query(Profile).filter_by(id=uid).first()`.

Fix: wired `get_profile_dict(db).get(uid)` ahead of the direct query. On cache hit (the common case) → zero DB work. On cache miss → fall through to the existing Supavisor-disconnect retry path. All guards preserved (uid required, is_active check, DEV_ALL_ADMIN override).

**Measured live impact (3-sample average):**

| endpoint | before | after | Δ |
|---|---|---|---|
| /api/deals | 2006 ms | 1002 ms | **−50%** |
| /api/queue | 1738 ms | 1184 ms | −31% |
| /api/tracker/rows | 1691 ms | 1070 ms | −37% |
| /api/calls | 1137 ms | 740 ms | −35% |
| /api/customers | 915 ms | 619 ms | −32% |
| /api/observability/stuck | 835 ms | 548 ms | −34% |

~370 ms saved per authed request. Heavy pages firing 5 sequential queries now ~1.8 s faster.

### Wave 2 — BRAIN documentation (`a273e0b`)

Live_State updated with the diagnostic table + the remaining architectural ceiling (cross-region RTT). Owner action item: confirm Railway region (turned out to be Singapore already).

### Wave 3 — Region confirmation

Owner reported Railway = Southeast Asia (Singapore). Supabase = `ap-south-1` (Mumbai). RTT between them is ~50-80 ms + ~150-200 ms Python/middleware overhead per query. Further wins require reducing query COUNT, not RTT.

### Wave 4 — Queue tab inversion fix (`4204de4`)

Owner-observed: click Reviewed → empty list. Click Pending → shows items the user thinks are reviewed.

Root cause: the per-checkpoint Pass / Override → Fail endpoint (`PUT /api/calls/{id}/checkpoint/{cp_index}/review`) only updated score/compliant/reason but never touched `call.review_status`. Calls where reviewers clicked individual checkpoint buttons stayed `unclaimed` forever (Pending tab) instead of progressing through `in_review` → `reviewed`. Plus the Reviewed tab cut off at midnight today, hiding yesterday's sign-offs.

Fix:
- Two-tier auto-promotion in `routes.review_checkpoint_verdict`:
  - ANY per-checkpoint override on an `unclaimed` call → `in_review`
  - EVERY non-error checkpoint has a `reviewer_verdict` → `reviewed` + stamp `reviewed_at/by/verdict_state=HUMAN_CONFIRMED`
- Reviewed tab cutoff widened from `today` (midnight) to `last 7 days` (`hitl_routes.get_queue:reviewed_today` branch). API filter name unchanged for back-compat; frontend pill now means "last 7d signed off".
- SSE `verdict_changed` payload now carries the new `review_status` + `auto_promoted_to` so the queue page repaints row-tab membership live.

### Wave 5 — Agent detail page enterprise upgrade (`f65ee4e`)

Owner mandate: "make the agent page much more better and attractive and have all the information that the quality person will need to take a decision".

**Backend (`agents_routes.agent_drilldown`)** — 9 new fields, each gracefully degrades to defaults on schema mismatch:

- `total_calls_lifetime` — total calls handled by this agent
- `avg_score_30d` — mean (passed/total) ratio across 30d (computed in Python from `Call.score` strings)
- `severity_breakdown_30d` — `{ critical, high, medium, low }` from the `flags` table (last 30d)
- `top_failed_checkpoints_30d` — top 5 by name + count, parsed from `checkpoint_results` JSON (reviewer override wins over AI status)
- `supplier_mix_30d` / `call_type_mix_30d` — single GROUP BY each
- `qc_block_count_30d` — QualityCheckerAgent `verdict='block'` count (last 30d)
- `weekly_trend` — 8-week pass-rate series for the sparkline (Postgres `generate_series` window)
- `best_call_id` / `worst_call_id` — picked from already-loaded recent_calls list (zero extra DB time)

**Frontend (`/agents/[name]/page.tsx`):**

- Hero strip: 4 → **6 KPI cards** (Total calls / Pass rate / Avg score / Critical flags / Open directives / QC blocks). Each card picks tone (emerald/amber/red) from value thresholds.
- New **"Quality-reviewer breakdown" row of 4 dense cards** between hero and tabs:
  - **Pass rate trend · 8w** — inline SVG line+dots sparkline with 50% baseline guide; per-week dot colour by pass-rate band
  - **Breach severity · 30d** — horizontal stacked bars with count labels
  - **Top failed checkpoints · 30d** — ranked list with relative-width bars
  - **Mix · 30d** — stacked supplier + call-type bars with legend chips
- **Best / Worst quick-jumps** below the panels (★ / ⚠) — clickable links to those call_ids
- **Retraining banner** when assigned + reason set
- All inline SVG/CSS; zero new dependencies; `tsc --noEmit` clean on changed files

### Wave 6 — Composite call-detail bundle endpoint (`10522b8`)

Owner asked to reduce per-page query count given the Railway↔Supabase latency is the architectural ceiling. Call-detail page was firing 5 sequential authed requests (detail + script-checkpoints + segments + words + audio_url) on every open ≈ 2.5 s.

New endpoint: `GET /api/calls/{id}/bundle` returns:
```json
{
  "call": { ...CallResponse with audio_url baked in },
  "segments": [...],
  "words": [...],
  "script_checkpoints": [...],   // UNION across segments
  "audio_url": "<signed URL, 1h expiry>"
}
```

Implementation:
- Single `db.query(Call).options(selectinload(Call.checkpoints))` for the Call row + its checkpoints (2 round-trips total).
- `word_data` is already on the Call row — no extra query.
- One SELECT for segments ordered by `idx`.
- `script_checkpoints` UNION is in-memory iteration over the already-loaded segments.
- All sub-fetches graceful-degrade (empty segments/words/null audio_url never 500 the bundle).

Existing single-resource endpoints stay live for back-compat + the per-resource SSE invalidation patterns. Frontend useCallBundle(id) hook wiring deferred to next session (owner mandate: "continue with the other fixes" — the backend is the load-bearing piece).

Expected impact when frontend opts in: ~1.5-2.0 s saved per call-detail page open.

## Cumulative measurements (full session)

Auth profile cache + tab fix + agent page enrichment + bundle endpoint shipped today. Combined with yesterday's D9/lag/n_a/agent waves, the system has been transformed:

- Bulk-upload failure rate: 70 % → 0 %
- /api/deals: 2006 ms → 1002 ms (-50 %) (and -1.5-2 s further available when frontend uses bundle)
- AI verdict accuracy: ~21 % wrong → n_a vocabulary live (pattern 1 fixed, 4 patterns remaining as analyst report tracks)
- Queue tabs: now correctly reflect per-checkpoint progress
- Agent detail page: 6-KPI hero + 4 breakdown panels + sparkline + best/worst quick-jumps
- Realtime: SSE fan-out covers pipeline steps, verdict changes, QC envelope landings

## Skill ledger (this session)

| time | skill | task-id | result |
|---|---|---|---|
| 10:54 | security-reviewer | auth-profile-cache-wire-up | success — no auth bypass |
| 10:55 | python-reviewer | auth-profile-cache-current-user | success — 58/58 tests |
| 11:11 | python-reviewer | queue-tab-auto-promote | success — 58/58 tests |
| 11:16 | code-reviewer | agent-page-quality-redesign | success — tsc clean |
| 11:16 | python-reviewer | agent-drilldown-enrichment | success — 9 new fields, graceful degrade |
| 11:20 | python-reviewer | call-bundle-composite-endpoint | success — 58/58 tests |

## Defect register at session close

| ID | severity | status | notes |
|---|---|---|---|
| D1-D2, D5-D9, D10, D11-D12, D13 | various | ✅ FIXED earlier today | see 2026_05_27_Session_full_day_agents_wave.md |
| D4 | MEDIUM | OPEN | Score volatility same audio — re-measure after D10 fully bakes |
| D6 | HIGH | mitigated | SSE per-call fan-out gap; 3 s poll fallback carries it |
| D14 | LOW | OPEN | Residual loop_lag ~1.5 s (sync json paths in checkpoint_analyzer) |
| D-PERF-AUTH | HIGH | **FIXED 69e79a8** | profile_cache wired into current_user |
| D-QUEUE-TAB | HIGH | **FIXED 4204de4** | per-checkpoint auto-promote + 7d Reviewed cutoff |
| D-AGENT-UX | MEDIUM | **FIXED f65ee4e** | quality-reviewer dashboard redesign |
| D-BUNDLE-EP | MEDIUM | **BACKEND SHIPPED 10522b8** | frontend hook + page wire-up deferred to next session |

## Open carry-forward for next session

1. **Wire frontend useCallBundle hook + repaint call-detail page** to consume `/api/calls/{id}/bundle`. Replace the 5 separate fetches in `page.tsx` with a single bundle query. Keep the per-resource queries available as fallbacks (per-resource SSE invalidation still uses them). Expected impact: 1.5-2 s saved per call-detail page open.

2. **Process-level cache for read-heavy list endpoints.** `/api/deals/list` (composite_pct math) and `/api/customers/list` are visited often but the data is stable for minutes at a time. A 30-60 s TTL on the response payload (keyed by query params + reviewer org_id) would cut their already-improved latency further. Use `aiocache` or a simple TTLDict.

3. **D14 residual loop_lag** — profile `checkpoint_analyzer.py` batch dispatch (json.loads on multi-KB LLM responses + fuzzy_match Levenshtein). Route through `anyio.to_thread.run_sync`.

4. **D4 score volatility** — re-measure after a few more soak runs with the n_a vocabulary live.

5. **QC banner on call detail page** — backend already writes `Call.quality_check`; frontend doesn't render it. Once useCallBundle is in, add a QC banner component showing `verdict` + `score` + `issues[]` list. Critical for owner's "quality reviewer takes a decision" workflow.

## Session_Self_Audit verdict

```
**Session self-audit — PASS**

- Trio declared: ✅ Primary=executor + playwright-mcp · Parallel=python-reviewer + code-reviewer + security-reviewer · Verification=Session_Self_Audit
- Auto-triggers honored: 5/5 push events — every backend/**/*.py wave fired python-reviewer + ledgered; agent page touched frontend-v3/src/**/*.{ts,tsx} fired code-reviewer; auth-touched code (current_user) fired security-reviewer.
- Ledger rows: 6 appended this session.
- Prose-vs-tool gaps: 0
- Push gate: 5/5 ✅ (doctrine integrity verify PASS on every push, identity kingusa1 verified, no --no-verify, no secrets, alembic chain single-head).

**Enterprise-grade 12-line checklist:**
- schema: no migrations this session (agent enrichment uses existing tables)
- tests: 58/58 touched-area pytest green throughout
- observability: new log lines + SSE `auto_promoted_to` payload
- realtime: SSE coverage extended
- errors: all new code paths graceful-degrade to defaults on schema mismatch
- idempotency: bundle endpoint is pure read; verdict auto-promote is idempotent
- backwards-compat: bundle endpoint additive; existing single-resource routes preserved
- UX: agent page transformed; queue tabs now reflect reviewer progress correctly
- performance: -370 ms avg per authed request; further -1.5-2 s available via bundle hook
- security: 0 CRIT/HIGH/MED/LOW; security-reviewer cleared the auth path change
- audit: ReviewerEdit + verdict_history rows still written on per-checkpoint override
- docs: BRAIN session log (this file) + Live_State + Known_Issues + Resume_Prompt
```
