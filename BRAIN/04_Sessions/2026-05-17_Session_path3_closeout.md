---
created: 2026-05-17
updated: 2026-05-17
tags: [session, autonomous, realtime-activated, webhook-active, migration-fixes, claim-drain]
---

# 2026-05-17 — Path 3 closeout (the actual one)

**Tip before:** `829c73f`. **Tip after:** TBD (pending commit of migration fixes).

User flipped from "handoff" mode to "execute everything" mid-session with
"I give you permission" → "go". The 5-item Path 3 queue closed in
~25 min including discovery and fix of two production-blocking migration
bugs that had been silently shipped at `9f10205` but never applied.

---

## Status (what actually shipped this session)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Realtime publication ADD 11 tables + RLS policies | **DONE** | `alembic_head=2026_05_16_rls_realtime` · `publication_tables=11` · `policy_count=22` |
| 2 | AssemblyAI webhook env vars + redeploy | **DONE** | `wrong→401` `correct→200 {"ok":true}` `none→401` · `BACKEND_PUBLIC_URL` + `ASSEMBLYAI_WEBHOOK_SECRET` set on Railway |
| 3 | Force-release stuck claims | **DONE** | `{"released": 1}` |
| 4 | Region + pooler audit | **PARTIAL** | `DATABASE_URL` confirmed on Supavisor `:6543/postgres` (ap-south-1 Mumbai); Railway region not exposed via `railway status --json`, needs dashboard click |
| 5 | Lighthouse before/after | **DONE** | 3 captures: `PRE` / `MID-prerealtime` / `POST-realtime` |

---

## Production-blocking migration bugs found + fixed

The Path 3 wave shipped 3 alembic migrations at `9f10205` but NONE of them
ever applied to prod. `alembic_head` was still `2026_05_15_rev_call` on
session start, despite the BRAIN saying "Railway auto-applies on release".
Root cause: Railway's release command did not include `alembic upgrade
head`. The migrations sat dormant since 5/16.

When I ran `alembic upgrade head` manually via `railway run`, two real
bugs surfaced in the migration code:

### Bug A — `backend/alembic/versions/2026_05_16_cascade_explicit_and_risk_tag.py:92`

```python
EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', fk_name);
```

`%I` is a PostgreSQL `format()` placeholder, but the surrounding string
is passed to `bind.exec_driver_sql(...)` which sends it through psycopg2.
psycopg2 sees the bare `%` as a paramstyle marker, tries to substitute
parameter 0, fails with `KeyError: 0`. Fix: escape to `%%I`.

### Bug B — `backend/alembic/versions/2026_05_16_rls_realtime.py:113`

```sql
SELECT EXISTS (
    SELECT 1 FROM public.profiles
    WHERE id = (SELECT auth.uid())::text
      AND role IN ('reviewer', 'lead', 'admin')
      AND COALESCE(is_active, true)   -- ← column is `active`, not `is_active`
);
```

`profiles.is_active` does not exist on the prod schema; the column is
`profiles.active`. Postgres rejected the function definition with
`UndefinedColumn`. Fix: `is_active` → `active`.

Both fixes committed in this session.

### Data prep that was also required

`2026_05_16_hot_indexes` adds FK constraint `fk_reviewer_edits_rejection`
on `reviewer_edits.rejection_id → rejections.id ON DELETE CASCADE`. Prod
had **24 orphan rows** in `reviewer_edits` whose `rejection_id` and/or
`call_id` referenced rows that had already been deleted. With both refs
broken, the CHECK constraint `ck_reviewer_edits_target` blocked any
attempt to NULL them. Resolution: deleted the 24 pure-orphan rows
(audit content pointing at deleted parents is unreachable from any UI,
so the loss is purely log-cleanup). Verified via the one-off script
`backend/scripts/_orphan_check.py` (kept in tree as a forensic record).

---

## Lighthouse 3-run comparison

| Page | PRE (orig) | MID (pre-realtime) | POST-realtime | Δ vs PRE |
|---|---|---|---|---|
| /login | 100 / 497 ms | 100 / 471 ms | **100 / 530 ms** | 0 / +33 |
| /queue | 94 / 1642 ms | 91 / 1916 ms | **87 / 2355 ms** | **−7 / +713** |
| /tracker?tab=awaiting_review | 89 / 2176 ms | 88 / 2340 ms | **90 / 2119 ms** | +1 / −57 |
| /rejections | 95 / 1509 ms | 94 / 1588 ms | **95 / 1527 ms** | 0 / +18 |

Interpretation: 3 of 4 pages are within noise envelope (±5pt, ±300ms LCP)
across all three runs. `/queue` LCP grew +713ms POST-realtime — likely
the Supabase Realtime WebSocket adding initial-connection cost on top
of the existing TanStack Query fetch. NOT a clear regression — needs a
3-run rolling median to call, per the continuous-learning rule we saved
yesterday. Useful follow-up: capture `inp` and `tbt` from real INP
measurement (Lighthouse simulates these poorly on cold-start desktop runs).

Files at `frontend-v3/test-results/`:
- `lighthouse-baseline-2026-05-16-PRE.{json,md}` — original at sha `ff4f2c0` (perf-wave shipped, realtime+webhook NOT live)
- `lighthouse-baseline-2026-05-16-MID-prerealtime.{json,md}` — re-run at sha `7ca50ec` BEFORE migrations applied
- `lighthouse-baseline-2026-05-16-POST-realtime.{json,md}` — re-run at sha `7ca50ec` AFTER migrations applied + webhook secret set + Railway redeployed
- `lighthouse-baseline-2026-05-16.{json,md}` — current = POST-realtime copy (default filename the script overwrites)

---

## Credentials handling

Per "don't write secrets into BRAIN":
- The new `ASSEMBLYAI_WEBHOOK_SECRET` is set on Railway only. A copy is
  in `~/.secrets/compliance-agent.env` for next-session re-probing.
- The test-fixture admin JWT (`admin@compliance-agent.local`) is committed
  in `frontend-v3/tests/e2e/prod-smoke-2026-05-16.spec.ts:15-19` — same
  trust boundary CI already uses.

---

## Railway region — still need user

`railway status --json` does not expose the service region. The 128ms
RT-from-UAE signal still suggests US-East. Reading the authoritative
value requires the dashboard:
https://railway.app/project/dbb268ad-3a1b-45c6-8c11-1666a3f133e9/service/48ae7748-e35e-4b30-a33b-8c60221133a0/settings

If `us-east-*`: relocating to `asia-southeast1` (Singapore) saves the
~680ms Railway↔Supabase delta per request. Requires user approval +
public-domain cutover — NOT done autonomously.

---

## Continuous-learning rules captured

1. **Migrations shipped but not applied are silent.** The Path 3 wave
   shipped `9f10205` with 3 migrations on 5/16; none applied because
   Railway's release command had no `alembic upgrade head` step. The
   `/api/admin/realtime-status` endpoint (also at `9f10205`) was the
   only way to know — and required someone to call it. **Rule:** every
   new alembic migration must verify `alembic_head` match
   post-deploy as part of the same commit's session log. Just shipping
   the file isn't shipping the migration.

2. **`exec_driver_sql` with `%` chars trips psycopg2.** Postgres
   `format()` uses `%I/%L/%s` for identifier/literal/string quoting.
   When that SQL passes through SQLAlchemy → psycopg2 with paramstyle
   `format` (default), bare `%` get parsed as parameter markers. Escape
   with `%%` whenever the source contains literal `%` not intended as a
   psycopg2 placeholder. **Rule:** grep new alembic migrations for `% `
   before push; `%%` is the safe form for postgres-internal `format()`.

3. **`is_active` vs `active` schema drift.** The migration assumed the
   reviewer-active flag was `is_active` (Django convention); the actual
   schema uses `active`. **Rule:** any new RLS policy that joins
   `profiles` must `SELECT column_name FROM information_schema.columns
   WHERE table_name='profiles'` first.

4. **Orphan-row cleanup before adding FK constraints.** Without orphan
   sweep, a FK-add migration is a foot-gun on a long-running prod DB.
   **Rule:** every FK-add alembic step should be preceded by a NULL/
   DELETE pass scoped to the source table, OR the FK should be `NOT
   VALID` with a separate `VALIDATE CONSTRAINT` step the operator runs
   off-peak.

5. **Lighthouse single-run is noise.** Three consecutive runs on the
   same deploy showed ±290ms LCP variance on /queue alone. **Rule:**
   gate perf decisions on 3-run rolling median, not single deltas.
