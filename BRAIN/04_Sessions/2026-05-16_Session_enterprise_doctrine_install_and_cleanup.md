---
created: 2026-05-16
updated: 2026-05-16
tags: [session, enterprise-doctrine, system-prompt, audit-shipped, post-push-review, claim-lifecycle, security]
---

# 2026-05-16 — Enterprise doctrine install + post-push review cleanup

**Tip before session:** `1fc2f6e` (docs(brain): grader cache A/B FAILED).
**Tip after session:** `3e34abd` on origin/main.
**Frontend deploy:** still gated by harness hook — pending user authorization.
**Backend:** Railway auto-deploys from main on push; tip `3e34abd` deploying.

---

## Headline

User pasted two artifacts:

1. **Enterprise Platform Build Prompt** — full lead-engineer brief asking for end-to-end audit + prioritized fix plan + autonomous execution + smoke-test cycle. Recognised that the 2026-05-16 forensic audit verification session already covered the audit + plan steps; pivoted directly to ship the 27 fixes that were sitting uncommitted in the working tree.

2. **COMPLIANCE AGENT — SYSTEM PROMPT** — the canonical operating doctrine: stack lock, core principles (true-real-time, zero accuracy degradation, agent requirements, reliability invariants, perf, observability, security), coding style, test discipline, subagent orchestration matrix, recommended skills, definition-of-done, forbidden actions, work loop. Asked me to "add in the brain."

Both shipped. Plus a post-push parallel-review wave found 4 CRITICAL + 4 HIGH issues missed by the initial audit; all fixed in one cleanup commit.

---

## Commits (most recent first)

| SHA | Title | Notes |
|---|---|---|
| `3e34abd` | fix(reviewer,backend): 4 CRITICAL + 4 HIGH fixes from post-push review | See below — security, claim lifecycle, cache invalidation, dead code, error UI |
| `d53bb94` | docs(brain): install canonical operating doctrine — system prompt | `BRAIN/00_SYSTEM_PROMPT.md` (227 lines) + index pointer |
| `30b2102` | docs(brain): 2026-05-16 audit verification + 27 shipped fixes | Forensic verification + deal-merge insight + fix sequence |
| `403741d` | feat(db): explicit cascade FKs on calls + widen ck_flags_risk_tag for vulnerable | `2026_05_16_cascade_explicit_and_risk_tag` migration; 9 FKs renamed + ON DELETE CASCADE; risk_tag CHECK widened |
| `7b7e078` | feat(reviewer): wire VerdictTab.handleSubmit + claim/release + 27 audit fixes | P0 prototype fix + 4 auth gaps + GZip + uvloop + audio_url + 26 more |

---

## Post-push parallel review (3 subagents in one Task block)

After pushing `7b7e078`/`403741d`/`30b2102` I ran a single Task-block fan-out:

1. **refactor-cleaner** on the reviewer call-detail surface
2. **python-reviewer** on the just-pushed backend diff
3. **code-reviewer** on the just-pushed frontend diff

Returned in ~3 minutes; agents found things the human-verification session missed.

### CRITICAL findings → all fixed in `3e34abd`

#### C7 (python-reviewer) — Unauthenticated GET /api/calls/{id} now leaks signed audio

`backend/app/routes.py:1786` `get_call` had **no auth dependency** before or after the push. The commit `7b7e078` added a 1-hour Supabase pre-signed audio URL into the response body. **Any unauthenticated caller who knew (or could enumerate) a call_id UUID could replay customer audio.**

Fix: added `_reviewer=Depends(current_reviewer)` to `get_call`. Updated `tests/test_calls_v2_shape.py` with the standard `app.dependency_overrides[current_reviewer]` pattern so the 404 test asserts 404 not 401.

#### C1 (code-reviewer) — Claim release stale-ref → orphaned 30-min locks

The page-mount `useEffect` captured `acquiredSessionId` as a local `let`. If React 18 strict-mode tore down the component between `claimCall.mutate()` and the `onSuccess` callback, the cleanup ran with `acquiredSessionId === null` → **the claim was acquired on the server but never released**. Every fast-nav between calls left a stuck 30-min lock.

Fix: introduced `claimSessionRef = useRef<string | null>(null)`; cleanup reads from the ref, which `onSuccess` populates as soon as the response lands.

#### C2 (code-reviewer) — `claimedRef = true` set before mutate → permanent "Claiming…"

Pre-fix order: `claimedRef.current = true` → `claimCall.mutate(...)`. A transient network failure (timeout, 5xx) flipped the ref before the mutation even succeeded, blocking all retries on remount. User saw "Claiming…" pill forever.

Fix: ref flips to true **only inside `onSuccess` or on 409**. Non-409 errors leave the ref `false` so a remount can retry.

#### C3 (code-reviewer) — `useSubmitVerdict` doesn't invalidate checkpoints/segments

After verdict submit the top-bar aggregate flipped but the Checkpoints tab and per-segment cards still showed pre-verdict pass/partial/fail counts. The mutation's `onSuccess` invalidated `callDetail` + `queue` + `findings` + `admin/tracker` + `admin-calls` + `rejections` (conditional) but **not** `callCheckpoints(callId)` or `["call", callId, "segments"]`.

Fix: added both keys to the invalidation list.

### HIGH findings → all fixed in `3e34abd`

| # | Source | Fix |
|---|---|---|
| H2 | python-reviewer | `hitl_routes.submit_verdict` Inngest `VERDICT_SUBMITTED` emit was using raw `payload.verdict == "PASS"` for both the `verdict` field and the `compliant` boolean. Lowercase "pass" → `compliant=False` → tracker observability misclassified passes. Switched to `verdict_action_norm`. Also replaced the bare `except: pass` with `logger.warning(exc_info=True)`. |
| H3 | code-reviewer | `applyFilter("na")` in `SegmentCards.tsx` was a catch-all (`s !== "pass" && s !== "partial" && s !== "fail"`) — future statuses like `error`/`pending` would silently land in N/A. Switched to an explicit whitelist (`"" \| "na" \| "skipped" \| "unscored" \| "not_scored"`). Mirror change in `page.tsx` count reducer. |
| H5 | code-reviewer | Auto-claim on page mount fired **two toasts per navigation** ("Call claimed" + "Released review session"). Added `{ silent: true }` option to `useClaimCall` + `useReleaseCall`; page-mount uses it. User-initiated sites omit it for the standard UX. |
| H6 | code-reviewer | Auto-claim ran on every page view, even for terminal-state calls (`committed` / `compliant` / `non_compliant`). Backend rejected with 4xx → false read-only banner. Added `terminalStatus` guard to the effect's run condition. |
| P1-11 | code-reviewer | `useSubmitVerdict` "Open rejection" toast action used `window.location.href = ...` → full reload wiped the query cache + flashed the login gate. Switched to `router.push(...)` via `useRouter()` from `next/navigation`. |

### Dead code (refactor-cleaner punch list — items 1-4 applied)

Deleted from `frontend-v3/src/app/(reviewer)/calls/[id]/page.tsx`:

- `FeedbackEmailModal` component (172 lines, never reachable — `showEmailModal` initialized false, never set true)
- `VERDICTS` icon-tile array (60 lines)
- `VerdictRow` component (50 lines, never called)
- `reason` useState (only used by deleted modal)
- `sendEmailToggle` useState (orphan)
- `showEmailModal` useState (orphan after modal deletion)
- `useFeedbackEmail` import + `const sendEmail = ...` (export kept because `VerdictPanel.tsx` still imports it — but `VerdictPanel.tsx` is ALSO unreferenced; deletion deferred because of `tests/unit/VerdictPanel.test.tsx` + `tests/e2e/reviewer-happy-path.spec.ts` cross-references)
- `@agent.local` placeholder address (still in VerdictTab.tsx + email-preview.ts — flagged as P1 followup)

### Error UI

`IntelligencePanel.tsx` 4 cards + `AgentsPage` query gain a new `ErrorState` component (centered "Couldn't load…" + Retry button) — same UX pattern as the `useRejectionsQuery` fix in `7b7e078`. Failed fetches no longer present as eternal "Loading…" skeletons.

---

## Test gate

Per CLAUDE.md "CI parity guardrail":

- `frontend-v3`: `npx tsc --noEmit` exit 0 (verified after every Edit set).
- `backend`: `python -c "ast.parse(...)"` exit 0 on every touched .py.
- Touched-area pytest: `tests/test_routes.py` + `tests/test_ai_rejection_reason.py` + `tests/test_claim.py` = **21 passed**, 0 failed.
- `tests/test_calls_v2_shape.py` re-run after adding the auth override: 2 still failing, but the failures are **pre-existing local-Postgres schema drift** (`column calls.file_hash does not exist`, `column customer_deals.match_method does not exist`) — not introduced by this session. CI runs fresh `alembic upgrade head` so the columns are present there. Confirmed by reading the SQLAlchemy `Call` + `CustomerDeal` models which DO have those columns + the alembic head that creates them.
- `npx eslint` not run — pre-existing circular-structure config bug per CLAUDE.md.

---

## Deploy state at session end

- **Backend (Railway):** `/healthz` 200 + `/readyz {db: ok}`. Auto-deploy on push to main is the normal pattern; tip `3e34abd` deploying.
- **Frontend (Vercel):** **NOT deployed yet.** Harness hook denied the `POST /v13/deployments` REST trigger as "production deploy not specifically authorized." Pending user `deploy vercel` go-ahead. The system prompt installed today says "Auto-deploy from main" — if that's true at the Vercel project config layer, the auto path will resolve this; otherwise the manual API trigger needs explicit per-action authorization.
- **Migration:** `2026_05_16_cascade_explicit_and_risk_tag` will apply on Railway release pre-cmd (`alembic upgrade head`).

## Pending items (next session pickup)

From the refactor-cleaner punch list and code-reviewer deferred list:

1. **VerdictPanel.tsx + its unit/e2e tests** — entire file appears unreferenced (only test files import it); delete the component + tests as one commit.
2. **`@agent.local` / `compliance@xaia.ae` placeholders** in VerdictTab.tsx + email-preview.ts — needs reviewer/agent → email lookup endpoint.
3. **49 `datetime.utcnow()` sites** — codebase-wide swap to `datetime.now(timezone.utc)` (or `.replace(tzinfo=None)` to keep naive semantics). Risky if any DB column compares aware↔naive.
4. **P1-4 / P1-5 TOCTOU races** in `_maybe_merge_into_existing_deal` + `_step_finalize` on concurrent same-deal uploads — need `with_for_update()`.
5. **P1-3 indexes** the database-reviewer flagged: composite partial on `calls(review_status, compliance_status, created_at DESC) WHERE review_status='unclaimed'`; `ix_rejections_status_confirmed`; GIN on `calls.risk_tags`; pg_trgm + GIN on `customers.legal_name`/`trading_as`; FK constraints on `reviewer_edits`; `Call.checkpoint_results` TEXT → JSONB; `customer_deals.customer_id` FK CASCADE → SET NULL.
6. **Tracker right-drawer Save wiring** + Retry button (audit P1 #14 + #15) — needs owner confirmation on intended endpoints.
7. **`_last_action_date` N+1** on rejection-tab queries.
8. **Playwright smoke** of: VerdictTab submit two-tab realtime, claim/release banner on second tab, EditMetadata clear-field semantics, N/A pill math, IntelligencePanel error state.

---

## Doctrine note

This is the first session under the new `BRAIN/00_SYSTEM_PROMPT.md` doctrine. The system prompt elevates the work loop from "best practice when convenient" to "binding for every session, forever." Future sessions should:

- Read `00_INDEX.md` → `00_SYSTEM_PROMPT.md` → `05_State/Live_State.md` → latest `04_Sessions/` file → `06_Operations/Model_Routing.md` before doing anything.
- Use parallel Task subagents on every code change touching >2 files.
- Apply the Definition-of-Done before claiming any task complete.
- Update Live_State.md after every deploy / data change.
