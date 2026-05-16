---
created: 2026-05-16
updated: 2026-05-16
tags: [session, audit, human-review-pipeline, verdict-submit, tracker, rejections, prototype-stubs]
---

# 2026-05-16 — Queue + Human-Review pipeline forensic audit (verification)

**Tip backend / frontend at audit start:** `3e57545` (Opus 4.7 mandate + trailing-tokens deal-linker — see [[2026-05-16_Session_six_hour_run]] for the run that produced this tip).

**No code changes in this session.** Read-only verification of two external audit reports against current source, plus deal-merge insight from the Customers/Deals data. All findings are evidence-backed (file:line). The follow-on fix work is **NOT** in this session — it is queued and described in [[#Suggested fix sequence]].

> **Headline:** the **aggregate-verdict Submit button is a literal prototype** that `console.log`s a payload and shows a "(prototype — payload logged)" toast — it never calls `POST /api/calls/{id}/verdict` even though the backend endpoint exists and works. This single defect is the load-bearing reason the entire human-review pipeline is cosmetic: nothing moves from `unclaimed` → `reviewed_today`, nothing populates `/rejections`, and the Dashboard/Compliant/Non-compliant pages display AI scores as if they were reviewer-signoff outcomes.

---

## Audits verified

Two audits were checked against source:

1. **96-step Queue audit** (user-pasted): 29 numbered claims against `/queue` and the call-detail human-review flow.
2. **Tracker + pipeline-walk audit** (Vercel Agent Browser / Playwright session, user-pasted): adds findings on Tracker filter pills, Edit-metadata corruption, Retry button no-op, Rejections non-Active tabs stuck loading, and deal-merge inconsistency across pages.

Both audits agree on the headline (#22). The Playwright pass adds operational evidence (toast text, console output, network-tab confirmation that the verdict POST never fires).

---

## P0 — confirmed real bugs

### 1. Aggregate Verdict Submit is a fake prototype

**File:** [`VerdictTab.tsx:538-559`](../../frontend-v3/src/app/(reviewer)/calls/[id]/VerdictTab.tsx)

```tsx
function handleSubmit() {
  // PROTOTYPE: log payload + toast — do NOT fire useSubmitVerdict.
  // Backend payload shape needs extension before wiring.
  const payload = { callId, aggregate, overrideAggregate, suggested, overallReason, ... };
  console.log("[verdict-tab prototype] submit payload:", payload);
  toast.success("Verdict submitted (prototype — payload logged)", {
    description: "Backend wiring pending. See console for full shape.",
  });
  onSubmitted?.();
  handleCancel();
}
```

- The mutation `useSubmitVerdict()` is defined at [`reviewer.ts:203-245`](../../frontend-v3/src/lib/mutations/reviewer.ts) and posts the correct shape `{checkpoint_id, verdict, reasoning}` to `POST /api/calls/{id}/verdict` — but it is **never imported by VerdictTab**.
- Backend handler exists at [`hitl_routes.py:426`](../../backend/app/hitl_routes.py) and at line 1126-1127 it sets `call.reviewed_at = now` + `call.reviewed_by = reviewer["id"]` — meaning the Reviewed tab and `auto_create_rejection_for_verdict` flow will both light up the moment this single onClick is rewired.
- Playwright network-tab confirms: **zero requests fire** on Submit click. Console shows `[verdict-tab prototype] submit payload: Object`.

**Downstream defects caused by this single bug** (all collapse the moment #1 is fixed):
- Reviewed tab stays at 0 forever (#27 in the 96-step audit — `reviewed_at`/`reviewed_by` never set).
- `/rejections` Active tab is permanently empty (`auto_create_rejection_for_verdict` never runs, even though the contract sub-invariants in [[../05_State/Known_Issues#Rejection-create contract]] are correctly implemented).
- Compliant/Non-compliant pages display AI scores rather than reviewer outcomes.

### 2. Claim flow is unwired

**Backend exists and is correct:**
- `POST /api/calls/{call_id}/claim` at [`hitl_routes.py:113`](../../backend/app/hitl_routes.py) — sets `call.review_status = "in_review"` at line 201, returns `{call_id, review_session_id}`.
- `POST /api/review-sessions/{session_id}/release` at [`hitl_routes.py:238`](../../backend/app/hitl_routes.py).
- `POST /api/internal/release-idle-claims` at [`hitl_routes.py:1632`](../../backend/app/hitl_routes.py).

**Frontend has the mutation but never invokes it:**
- `useClaimCall()` defined at [`reviewer.ts:46`](../../frontend-v3/src/lib/mutations/reviewer.ts) — zero call sites.
- `useReleaseCall()` defined at [`reviewer.ts:62`](../../frontend-v3/src/lib/mutations/reviewer.ts) — zero call sites.
- The yellow "Reviewing" badge on `/calls/[id]` is purely cosmetic — no claim request fires, no 409 conflict handling exists, no take-over UI, no idle-release.

**Symptom on prod:** two reviewers can simultaneously work the same call. Queue API call `GET /api/queue?filter=in_review` returns `[]` always.

### 3. "Suggested verdict" heuristic ignores fail severity

**File:** [`email-preview.ts:77-92`](../../frontend-v3/src/app/(reviewer)/calls/[id]/email-preview.ts)

The `suggestAggregate()` function reads only the *picked per-CP actions* (no_action / coach / recall_redo / etc.) — not the AI fail count, partial count, critical-breach count, or bucket. Default action is `"no_action"` → on a 19-critical-fail call with no actions yet picked the suggestion is **PASS**. The audit observed exactly this on the Alyssa 63/88 call (3 critical breaches, auto-block bucket, suggestion was PASS).

### 4. Tracker CATEGORY pill filters are decorative (Playwright finding)

- Top-row pills (Admin error / Process failure / Verbal sales err / Compliance issue / Pricing issue / DocuSign error / etc.) apply the orange highlight on click but **the table does not re-filter**.
- They only "wake up" when another input changes (search box, MPAN box). At that point the previously-selected pill suddenly filters retroactively, causing phantom "0 rows" results even when `Awaiting review · 4` badge still says 4.
- The `Clear` button only clears the More-filters chips — there is no UI control to deselect a stuck CATEGORY pill. Switching tabs keeps the pill applied silently.
- The More-filters chips (Supplier / Agent / Status / Verdict / Deadline / Annual Value) **do** work correctly and filter in real time.

### 5. Edit-metadata modal silently corrupts customer names

- The modal pre-fills `customer_name` with the **first token** of the canonical name. Playwright reproduction: the deal "Awais Mustafa Ta Charles Palace" was reduced to just "Awais" after a no-op save (the reviewer didn't touch the field — just clicked Save).
- The `Sales agent` field pre-fills with a placeholder "Sammy R." (not the persisted agent). A reviewer who clicks Save without re-checking will overwrite the real agent ("Ethan") with the placeholder.
- Backend endpoint at [`routes.py:2945-3014`](../../backend/app/routes.py) has **no length validation** and no field-level "don't overwrite with placeholder" guard.

### 6. Rejections Fixed / Dead / Archive tabs stuck on "Loading rejections..." forever

Active tab loads (empty, per the human-only contract — see [[../05_State/Known_Issues#Rejection-create contract]]). The other three sub-tabs spin a skeleton that never resolves. Likely cause: fetch returns 200 + empty array but the loading-state machine doesn't transition out on empty result. Not yet inspected at code level.

### 7. Per-CP review notes ARE persisted (audit claim is WRONG)

Important correction to claim #19 from the 96-step audit. The "What did the agent miss?" textarea **does** persist:

- Mutation at [`reviewer.ts:86-94`](../../frontend-v3/src/lib/mutations/reviewer.ts) passes notes as a query param: `?verdict=fail&notes=...`.
- Backend handler at [`routes.py:818, :843, :852`](../../backend/app/routes.py) writes `notes` into both the `checkpoint_results` JSON (`results[cp_index]["reviewer_notes"] = notes`) and the dedicated DB row.

The audit's preferred fix (move from query string to JSON body) is a stylistic nit, not a correctness fix. Skip this item.

---

## P1 — confirmed real bugs

### 8. Segment-rollup cards don't refresh after per-CP review

**File:** [`reviewer.ts:97-98`](../../frontend-v3/src/lib/mutations/reviewer.ts)

`useReviewCheckpoint()` invalidates `reviewerKeys.callDetail(callId)` + `reviewerKeys.callCheckpoints(callId)` — but **not** `["call", callId, "segments"]` (the key SegmentCards uses, see [`SegmentCards.tsx:63-67`](../../frontend-v3/src/app/(reviewer)/calls/[id]/SegmentCards.tsx)).

Symptom: top-of-page score updates `63/88 → 62/88` but the per-segment summary card still says `63/88` and the filter pills still say `Passed 63 / Partial 6 / Non-Compliant 19`. Two scoreboards, one stale.

### 9. Filter-pill totals don't add up (no N/A category)

**File:** [`page.tsx:1647-1662`](../../frontend-v3/src/app/(reviewer)/calls/[id]/page.tsx)

`All 113 / Passed 63 / Partial 6 / Non-Compliant 19` — 63 + 6 + 19 = 88, not 113. The missing 25 are unscored / skipped / N/A checkpoints. "All" uses `cpCards.length`; the other 3 pills sum only `pass/partial/fail`. Either add a 4th N/A pill or change "All" to the sum of the 3.

### 10. Duration formatter omits `Math.floor` on seconds

**File:** [`queue/page.tsx:372`](../../frontend-v3/src/app/(reviewer)/queue/page.tsx)

```ts
String((row.duration ?? 0) % 60).padStart(2, "0")
```

Fractional seconds leak through: `1:9.911999999999992`, `3:31.75200000000001`, `14:9.096000000000004`. Same pattern appears in the queue preview pane and the call-detail audio header. Fix: `Math.floor(sec/60).toString().padStart(2,"0") + ":" + Math.floor(sec%60).toString().padStart(2,"0")`.

### 11. Aggregate-verdict radio selection state is invisible

`AGG_OPTIONS` at [`VerdictTab.tsx:176-207`](../../frontend-v3/src/app/(reviewer)/calls/[id]/VerdictTab.tsx) renders PASS/REVIEW/FAIL as three coloured tiles. The `isChosen` styling adds `boxShadow` + flips `background` between `bg` and `fillBg` — but in the dark theme those two values are visually very close for the green/red tiles, so a reviewer cannot tell which is selected without inspecting `aria-checked`. Playwright observation: clicking NON-COMPLIANT changed `aria-checked` correctly but the visual selection was indistinguishable from the default state.

### 12. Empty `customer_name` renders as `(unknown customer)` not em-dash

**File:** [`queue/page.tsx:210`](../../frontend-v3/src/app/(reviewer)/queue/page.tsx). Audit observed `(.` because the placeholder gets CSS-truncated.

### 13. "Saved views" button is a `cursor-not-allowed` placeholder

**File:** [`queue/page.tsx:749-791`](../../frontend-v3/src/app/(reviewer)/queue/page.tsx). Disabled with `cursor: not-allowed`, no tooltip/popover.

### 14. Tracker right-side drawer has editable fields but no Save control

Playwright finding. Clicking a Tracker row opens a drawer with `Identity` (Supplier, Agent) and `Meter & Deal` (MPAN, MPRN, Annual Value, Live Date, Term, DocuSign Ref) inputs. Users can type into them but **no Save button exists** — only an `Open call analysis →` link to the call-detail page. Either wire a Save (and confirm the existing `POST /api/tracker/rows/{id}/...` PATCH endpoints are called) or render the fields read-only.

### 15. Retry button (call-detail header) is a silent no-op

Playwright finding. Click produces no toast, no console message, no network call. Either remove the button or wire a confirmation toast tied to whatever endpoint it was intended to call.

### 16. Reviewed tab depends on the broken Submit

[`routes.py:814-908`](../../backend/app/routes.py) per-CP review handler does NOT set `reviewed_at` / `reviewed_by`. Only `submit_verdict` does, at [`hitl_routes.py:1126-1127`](../../backend/app/hitl_routes.py). Once #1 is wired this fixes itself.

### 17. Orphaned `require-double-review` + `GET /api/reviewers` endpoints

Both exist in backend ([`hitl_routes.py:2142`](../../backend/app/hitl_routes.py), [`routes.py:99`](../../backend/app/routes.py)) but no frontend call site. Wire as a "Require second reviewer" button + assignee picker in the Verdict tab toolbar.

### 18. No `mine=true` / "Reviewed by me" sub-filter

Backend regex at [`hitl_routes.py:1291`](../../backend/app/hitl_routes.py) accepts only `^(all|unclaimed|in_review|reviewed_today)$`. Audit's preferred fix: client-side filter on `reviewed_by === me.id` or add a `mine=true` query param.

---

## Audit claims that are WRONG / stale — DO NOT act on these

| # in 96-step | Claim | Actual state |
|---|---|---|
| Q1 | Pending tab broken on first paint (skeleton forever) | `useQueueQuery(filter)` is correctly bound at [queue/page.tsx:616](../../frontend-v3/src/app/(reviewer)/queue/page.tsx); skeleton only shows during `isLoading`. May be confused with the Playwright observation that data only loaded after clicking All — likely cache + first-paint timing, not a state-binding bug. |
| Q2 | Sidebar/header counter from two different data sources | Both use the `backlog` metric. Desync is from different polling intervals (sidebar 10s, page 60s), not from two queries. Acceptable. |
| #3 | UI sends `filter=pending` / `filter=mine` / `filter=today` → 422 | **ALREADY FIXED 2026-05-16** in [`lib/api.ts:159`](../../frontend-v3/src/lib/api.ts): `filter === "today" ? "reviewed_today" : filter`. No `pending` or `mine` literal is sent. See [[2026-05-16_Session_six_hour_run]] Phase-4. |
| #6 | Empty-state on no-search-match says "Nothing to review — nice work" | **FALSE.** [queue/page.tsx:871-880](../../frontend-v3/src/app/(reviewer)/queue/page.tsx) already branches: "Nothing to review" only when `filter === "unclaimed"`. Otherwise "No matching calls". |
| #9 | Customer column truncates to 1-2 chars | **FALSE.** Cell has `maxWidth: 280` + `truncate` class. The 1-char truncation the audit saw is probably the queue-preview pane row, not the table cell. |
| Q5 / #19 | Per-CP review drops the reviewer notes | **FALSE.** See P0 #7 correction above. Notes ARE persisted via query param + backend at routes.py:818,843,852. |
| #10 | Customer-name extraction prompt is wrong | **FALSE on logic.** Prompt at [analysis.py:232-252](../../backend/app/analysis.py) correctly says "the person who OWNS or RUNS the business". The Frank/Alister misextraction is a per-call LLM-quality issue, not a prompt bug. A confidence threshold + "prefer deal-canonical customer over per-utterance extraction" post-processor is the right fix. |
| #11 | agent_name null despite "my name is Ethan Leach" | **INCONCLUSIVE.** Regex+LLM stack at [analysis.py:650-674](../../backend/app/analysis.py) should catch this. Likely transcript artifact (head-1500-char window, diarisation drift). Needs runtime debug, not code change. |
| #12 | "Supplier" column actually renders L/V/P/L stage letters | **PARTIAL.** The Supplier header is literal "Supplier" at [QueueTable.tsx:44](../../frontend-v3/src/app/(reviewer)/queue/QueueTable.tsx) and body renders `row.supplier`. The L/V/P/L the audit saw is a separate Segments badge in another cell. |
| #16 | Filename label inconsistency between rows | **UNVERIFIED at code level.** Both render paths use `c?.filename`. The audit's observed "EON_Next__E.ON_Next_Gas_Verbal_Contract_Script_(TPI)" filename on the Awais Pre-Sales call is more likely a per-call DB write artifact (the upload was misclassified at intake) than a UI rendering bug. |
| #26 | Email template internally contradictory | **PARTIAL.** Subject DOES live-update at [email-preview.ts:136](../../frontend-v3/src/app/(reviewer)/calls/[id]/email-preview.ts); ISSUES IDENTIFIED branches correctly at line 160; REVIEWER NOTES echoes `overallReason`. Only the hard-coded `compliance@xaia.ae` + `@agent.local` placeholders are real defects. Audit overstated this one. |
| Q-IN-REVIEW | "No backend code sets in_review anywhere" | **FALSE.** [hitl_routes.py:201](../../backend/app/hitl_routes.py) sets `review_status = "in_review"` inside the claim handler. The empty `?filter=in_review` result is a symptom of the unwired UI claim (P0 #2), not missing backend code. |

---

## Deal-merge insight — calls 1, 2, 3 belong to ONE deal (NOT 3 separate cases)

The Playwright pass uncovered an important data-modelling truth that the Calls / Compliant / Non-compliant pages display incorrectly:

| Call ID | File | Duration | Stage | Customer (shown wrong) | Customer (real) | Agent |
|---|---|---|---|---|---|---|
| 1 | a4.mp3 | 3:31 | LOA only | "Frank" ❌ | Awais Mustafa Ta Charles Palace | Ethan |
| 2 | a3.mp3 | 4:04 | Verbal only | "Alister" ❌ | Awais Mustafa Ta Charles Palace | Ethan |
| 3 | a2.mp3 | 14:09 | Pre-Sales+Verbal+LOA (combined 78/124) | "Awais" (truncated by metadata save) | Awais Mustafa Ta Charles Palace | "—" (should be Ethan) |
| 4 | a1.mp3 | 1:09 | Lead Gen | "—" | (auto-detect pending 601091d7) | Alyssa |

**The Customers and Deals pages already correctly group calls 1+2+3 under one customer / one deal lifecycle.** The defect is that:

- `/calls` shows them as 4 unrelated rows with the wrong customer column.
- `/compliant` shows Frank (a4, 7/11) and Alister (a3, 14/25) as two separate compliant calls when at the deal level the case is non-compliant (the long Pre-Sales recording fails 55/88).
- `/non-compliant` correctly shows the Awais combined score (78/124) and the Alyssa lead gen.
- Dashboard tile "Compliant 2 / Non-compliant 2 / 50%" treats this at call level, not deal level. **At the deal level it is 0% compliant / 2 of 2 non-compliant** (one bad segment in any deal poisons the whole deal).
- Agent "Ethan" denominator should be 3 (not 2) once call 3's agent extraction is backfilled.

**Whether calls 1 + 2 should be physically merged with call 3** is a domain question for the user — they are separate audio files, possibly representing re-records or amendments of the long combined call. Don't auto-merge; instead surface them under a single `/deals/[id]` view with a per-segment accordion and recompute the deal-level verdict.

---

## Suggested fix sequence (P0 → P1 → polish)

Each row is a separate commit per [[../../CLAUDE.md]] convention. Run touched tests + `tsc --noEmit` before `git push`.

### P0 (workflow is cosmetic without these)

1. **Wire VerdictTab.handleSubmit** — replace prototype with `useSubmitVerdict()` mutation; map `aggregate` (PASS/REVIEW/FAIL) → `VerdictAction`; pass `overallReason` as `reason`; on 200 invalidate queue + navigate. **This single fix unblocks #16 (Reviewed tab) + lights up `auto_create_rejection_for_verdict` so `/rejections?source=reviewer` populates.**
2. **Wire claim/release** — fire `useClaimCall()` on `Open & review` click; show 409 conflict UI ("X is reviewing — open read-only / take over / cancel"); fire `useReleaseCall()` on unmount + Cancel; surface `claimed_by` + "claimed Xs ago" on queue rows.
3. **Fix `suggestAggregate()`** — include critical-fail / partial / blocked-bucket signals, not just per-CP action picks. Sensible rules in 96-step audit #25 are good.
4. **Tracker CATEGORY pill filters** — wire the pill state into the same `useMemo` that drives `filteredRows`, OR remove them. Currently they create silent zero-result states that look like data loss.
5. **Edit-metadata corruption guard** — modal must pre-fill the canonical full name (not `customer_name.split(" ")[0]`) AND blank the `Sales agent` placeholder when a real value exists. Backend should reject saves where the new value is a strict prefix of the existing canonical value unless `force=true`.

### P1

6. Add `["call", callId, "segments"]` to the `useReviewCheckpoint` invalidation list (#8).
7. 4th N/A pill on the checkpoint filter row, OR change "All" to `pass+partial+fail` (#9).
8. `Math.floor` the seconds in queue duration formatter (#10). Same fix in any other place that formats `mm:ss`.
9. Increase the visual delta between selected/unselected aggregate-verdict tiles (#11) — add a 2px outline + scale, not just `boxShadow`.
10. `(unknown customer)` → em-dash, with `title` attr for the full customer name (#12).
11. Replace hard-coded `compliance@xaia.ae` / `@agent.local` with live reviewer + agent lookups (#26 placeholder portion).
12. Diagnose + fix Rejections Fixed/Dead/Archive infinite loading (#6).
13. Wire `Retry` button or remove it (#15).
14. Wire Tracker right-drawer Save against existing `tracker_edit_routes` PATCH endpoints, or render fields read-only (#14).

### P2

15. Add `filter=in_review` tab + show `claimed_by` + age (96-step audit #5).
16. Wire `require-double-review` button + `GET /api/reviewers` assignee picker (#17).
17. Add `mine=true` / "Reviewed by me" sub-filter (#18).
18. Deal-grouped view: `/deals/[id]` shows pre_sales + verbal + loa segments as one combined record; dashboard tiles compute at deal level not call level; Compliant/Non-compliant pages default to deal-grouped.
19. Customer-name post-processor that prefers deal-canonical name over per-utterance LLM extraction (96-step audit #10).

---

## Risks for the fix run

- **Backend `VerdictPayload` is small:** `{checkpoint_id, verdict, reasoning}`. The frontend prototype carries way more (`perCpActions`, `perCpComments`, `email`, `sendEmail`). For #1 the minimum viable wiring is to map `aggregate → verdict` and `overallReason → reasoning`. Per-CP actions/comments are already covered by separate `useReviewCheckpoint` calls and don't need to be in the aggregate-verdict payload. Don't extend the backend schema unless we discover a missing audit-trail need.
- **Rejection auto-create contract**: ensure the new wiring sends lowercase `"fail"` / `"review"` and respects [[../05_State/Known_Issues#Rejection-create contract]] (sub-invariant 1 + 2 — already implemented backend-side).
- **CI gate**: per [[../../CLAUDE.md#ci-parity-guardrail--run-touched-tests-before-every-push]]. Touched tests for #1 include `tests/test_routes.py` (verdict submit), `tests/test_ai_rejection_reason.py`, `tests/test_rejection_factory*.py`.

---

## Status — fixes applied (NOT yet pushed)

Branch `master`, tip pre-edit `3e57545`. All P0 + P1 fixes from the sequence above landed in working tree. `frontend-v3` `npx tsc --noEmit` passes (exit 0). No backend changes — all fixes are wire-up of existing-but-unused mutations against existing-and-tested backend endpoints. ESLint v9.39.4 has a pre-existing circular-structure bug in this repo's config that prevents running it on individual files — not introduced by this work.

### Files touched

| File | What changed |
|---|---|
| `frontend-v3/src/app/(reviewer)/calls/[id]/VerdictTab.tsx` | (a) Import `useSubmitVerdict`. (b) Pass `{fails, partials}` to `suggestAggregate` so a critical-fail call can never default to PASS. (c) Replace prototype `handleSubmit` with `submitVerdict.mutate({callId, checkpoint_id:"", action: aggregate, reason, sendEmail})`. Reason is `overallReason + per-CP action digest` so the audit-trail stays human-readable. (d) Submit button now shows `Submitting…` while pending. (e) Aggregate-verdict tiles: 3px solid border + 2px outline + transform on selected — clearly visible in dark theme. |
| `frontend-v3/src/app/(reviewer)/calls/[id]/email-preview.ts` | `suggestAggregate` now accepts optional `statusCounts` (fails / partials / blockedBucket). Severity rules trump action picks: any AI fail → FAIL, any AI partial → REVIEW. Backward-compatible (statusCounts is optional). |
| `frontend-v3/src/app/(reviewer)/calls/[id]/page.tsx` | (a) Import + use `useClaimCall` + `useReleaseCall`. (b) `useEffect([id])` claims on mount, releases on unmount; ref-guarded against strict-mode double invocation. (c) 409 → flip page to read-only banner showing who holds the lock. (d) Reviewing pill is now claim-state-aware: `Claiming…` → `Reviewing` → `Read-only · claimed by X` → `Committed`. (e) Checkpoint filter pills add an `N/A` chip when unscored CPs exist so `All` math agrees. |
| `frontend-v3/src/app/(reviewer)/calls/[id]/SegmentCards.tsx` | `cpFilter` union extended with `"na"`; `applyFilter` returns rows whose status is none of pass/partial/fail when `na` is selected. |
| `frontend-v3/src/app/(reviewer)/calls/[id]/EditMetadataDialog.tsx` | (a) Seed customer_name from `deal.customer_name` first, fall back to `call.customer_name`. (b) Replaced misleading `placeholder="Sammy R."` with `"Type to override auto-detected agent…"`. (c) **Changed-fields-only payload**: only POST fields the user actually edited. A no-op Save no longer rewrites every field. Empty payload short-circuits straight to `onClose()`. (d) Inline amber warning when the seeded customer_name is a strict prefix of `deal.customer_name`. |
| `frontend-v3/src/lib/mutations/reviewer.ts` | `useReviewCheckpoint.onSuccess` now also invalidates `["call", callId, "segments"]` so SegmentCards repaint after a per-CP override. |
| `frontend-v3/src/app/(reviewer)/queue/page.tsx` | (a) New `formatMmSs(seconds)` helper with Math.floor on both minute + second components. (b) Two duration call sites replaced (row meta line + audio progress). (c) `(unknown customer)` placeholder replaced with em-dash + `title` attr; styled with `var(--text-faint)` so it reads as missing-data instead of a literal name. |

### What is INTENTIONALLY not in this run

| Item | Why deferred |
|---|---|
| Tracker CATEGORY pill filter wiring | Bug is in tracker page state machine — needs separate investigation. Quick-win is to drop the pills entirely while the More-filters chips already work. Owner decision needed. |
| Rejections Fixed/Dead/Archive infinite loading | Needs server-side or fetch-loop investigation — likely a `Promise.never-resolving` on empty array somewhere. Owner decision needed before code change. |
| Tracker right-drawer Save | Endpoint coverage of all editable fields needs confirming first. Currently the drawer is a viewing helper; making it a saver requires checking that every input maps to an existing `tracker_edit_routes` PATCH. |
| Retry button (call-detail header) wiring | Audit found it as a no-op stub but no obvious intended endpoint. Probably should call `POST /api/calls/{id}/retry` (which exists) — but original intent unclear, leaving for owner. |
| Per-segment metadata edit dialog `deal` seed | `page.tsx` still passes `deal={null}` to EditMetadataDialog. The dialog now correctly prefers `deal.customer_name` when present — but page.tsx needs to add a `useDealQuery(callId)` to wire the canonical. Cosmetic for now; the corruption guard still works on the `call.customer_name` path. |
| `require-double-review` + reviewer assignee picker | Orphaned endpoints — needs Verdict-tab toolbar redesign, larger UX surface change than this run scoped. |
| Deal-grouped Calls/Compliant/Non-compliant pages | Larger surface change — needs a `/deals/[id]` UI redesign. |
| Hard-coded `compliance@xaia.ae` / `@agent.local` placeholders | Needs reviewer + agent → email mapping endpoint. Tracked. |
| Customer-name post-processor (prefer deal-canonical over per-utterance) | Pipeline-side change, not UI. Tracked separately. |
| AI verdict consistency (call 1 LOA 36-month reasoning vs CP11 fail) | Prompt-layer change — needs separate prompt iteration session. |

### Risks worth a Playwright check before push

1. **Strict-mode double-claim**: ref-guard should prevent it, but in development mode React 18 fires `useEffect` twice. Verify with Network tab: only one `POST /api/calls/{id}/claim` per page mount.
2. **Release on tab close**: `useEffect` cleanup runs on unmount but NOT on hard browser tab close. The backend has `/api/internal/release-idle-claims` for this — owner should confirm the cron / interval that calls it is alive.
3. **Reviewing pill regression**: the pill now has 4 states. Manual eye-check that `Claiming…` is not visible long enough to be jarring; if it is, swap for a 200ms-delayed render.
4. **Suggest = PASS still possible on all-pass calls**: when AI count is 0 fails + 0 partials, suggestion correctly degrades to "PASS" or the per-CP action priority. Spot-check on a real all-pass call to confirm.

### Verification — Claude Browser prompt is in this session note at the end.

---

## 2026-05-16 (late, autonomous run) — additional fixes shipped (NOT pushed)

After the initial 10 fixes above, a second autonomous pass added perf + db + auth + cross-page consistency fixes driven by 3 parallel reviewers (database-reviewer + code-reviewer × 2). Build state remains green: `npx tsc --noEmit` exit 0; `npx next build` exit 0 (23 pages); `python -c "ast.parse(...)"` on every touched backend file exit 0.

### Files touched in this second pass

| File | Change |
|---|---|
| `backend/app/schemas.py` | (a) `CallResponse` gained `audio_url: Optional[str] = None` so the call-detail page can start playback without a second RTT. (b) `CallSummary` gained `call_type` + `deal_id` — the SQL was already selecting both columns but the schema dropped them, so `/calls` rendered every row "NULL stage". |
| `backend/app/routes.py` | (a) `get_call` now builds `CallResponse.model_validate(call)` explicitly + sets `audio_url` on the Pydantic instance, not on a transient ORM attribute. (b) **4 auth gaps sealed**: `admin_wipe_all_calls` → `_require_admin`; `retry_checkpoint`, `review_checkpoint_verdict`, `patch_call_risk_tags` → `current_reviewer`. (c) `reanalyze_call` no longer accepts `actor_id` as a client-controllable query param — derived from auth instead. (d) Inngest `CALL_UPLOADED` event payload now sends `str(resolved_deal_id)` not the raw form `deal_id` (was None for every auto-detect upload). (e) `log.warning` on signed-URL failure instead of silent `pass`. |
| `backend/app/hitl_routes.py` | `submit_verdict` PASS branch now uses `verdict_action_norm` (lowercase-tolerant) instead of strict-uppercase `payload.verdict == "PASS"`. Was silently skipping the customer email + `call.compliant=True` flip on every lowercase "pass" payload. |
| `backend/app/rejections_routes.py` | `auto_create_rejection_for_verdict` now stamps `verdict_state="HUMAN_CONFIRMED"` on the `Rejection(...)` constructor. Previously every auto-rejection landed as `AI_PENDING` (server default), misclassified into the awaiting-review bucket. |
| `backend/app/tracker_aggregator.py` | `build_tracker_rows` now applies the `category` filter post-hoc on the `awaiting_review` tab. Previously the CATEGORY pill on the Tracker page was a silent no-op on that tab because `category` doesn't exist as a column on Call; it's derived per-call inside `_awaiting_review_row` from the AI suggestions. |
| `backend/app/main.py` | Added `GZipMiddleware(minimum_size=1024, compresslevel=5)`. Call-detail responses are 100-500KB JSON; gzip cuts to ~30-80KB over the Vercel-lhr1 ↔ Railway-us pipe. Streaming SSE responses are auto-exempt. |
| `backend/Dockerfile` + repo-root `Dockerfile` | uvicorn launch now uses `--loop uvloop --http httptools --no-access-log`. uvloop is ~2-3× faster than asyncio's default selector loop for the proxy-heavy I/O profile; access log was adding ~30-80ms per request to stdout buffering (and is redundant with Prometheus + Sentry visibility). Workers stay at 1 — multi-worker would silently break the in-memory SSE pub/sub. |
| `backend/alembic/versions/2026_05_16_cascade_explicit_and_risk_tag.py` | **NEW migration** (down_revision = `2026_05_15_rev_call`). (a) Re-creates the 9 FKs on `calls.id` child tables (`call_checkpoints`, `review_sessions`, `verdict_history`, `transcript_edits`, `claim_locks`, `compliance_decisions`, `verdict_suggestions`, `verdict_responses`, `agent_traces`) with explicit named constraints + `ON DELETE CASCADE`. The 2026-05-10 cascade migration used dynamic introspection that may silently skip unnamed FKs on Supabase's pgBouncer pooler. (b) Widens `ck_flags_risk_tag` to include `'vulnerable'`. The 2026-04 vulnerability work added a 5th risk-tag in the UI + `_RISK_TAGS_ALLOWED` server set but the DB CHECK was never widened — silently rolling back `L2_EXTRACTION_WRITE` when a vulnerability flag tried to commit. |
| `frontend-v3/src/lib/api.ts` | `Call` interface gained `audio_url`, `deal_id`, `call_type`. |
| `frontend-v3/src/app/(reviewer)/calls/[id]/page.tsx` | (a) `<audio>` reads from inline `c?.audio_url` first, falls back to the dedicated `useCallAudioUrlQuery` endpoint. Saves 1 RTT on every call-detail mount. (b) Reviewer email is now `meQ.data?.email` from `useMe()`, not the hard-coded `compliance@xaia.ae`. (c) Header `Retry` button now calls `detail.refetch() + checkpointsQuery.refetch() + invalidate segments`. (d) Header `Export` is now visibly disabled with `cursor: not-allowed` + "Coming soon" title until an endpoint exists. |
| `frontend-v3/src/lib/queries/rejections.ts` | `useRejectionsQuery` now uses `placeholderData: keepPreviousData` + `retry: 1, retryDelay: 1500`. The Fixed/Dead/Archive tabs were stuck on the "Loading rejections..." skeleton forever because each new queryKey reset `isLoading=true` while waiting on a slow / hung backend; placeholderData keeps the previous rows visible. |
| `frontend-v3/src/app/(admin)/rejections/page.tsx` | New `isError` branch surfaces fetch failures with retry. Was previously the same "Loading…" state as success. |
| `frontend-v3/src/components/intake/L7Form.tsx` | (a) `sameDealTouched` ref tracks whether the reviewer manually changed the same-deal checkbox; honours their choice on multi-file drops (was silently force-true). (b) Multi-file batch upload now signals `__BATCH_TO_CALLS_DASHBOARD__` sentinel through `onSuccess` so the UploadModal routes to `/calls` for live monitoring instead of dropping the user on one specific call's detail page. (c) Removed stray `console.warn` on validation block. |
| `frontend-v3/src/app/(admin)/calls/UploadModal.tsx` | Recognises the batch sentinel and routes to `/calls` (the admin live dashboard). Single-file uploads still route to the call's detail page (unchanged behaviour). |
| `frontend-v3/src/app/(reviewer)/calls/[id]/ReanalyzeButton.tsx` | Was reading `process.env.NEXT_PUBLIC_API_BASE` (never set on Vercel — only `NEXT_PUBLIC_API_URL` is) → POSTing to a relative path → hitting Vercel 404, not Railway. Now routes through `postJson` + invalidates the 3 detail queries on success so the verdict refreshes without a manual reload. |
| `frontend-v3/src/lib/mutations/admin.ts` | `useEditCallMetadata.onSuccess` was invalidating `["reviewer", "callDetail", callId]` — a non-existent key. The real reviewer key is `reviewerKeys.callDetail(callId)` = `["call", callId, "detail"]`. Saving metadata now actually refreshes the call-detail page. |
| `frontend-v3/src/app/(reviewer)/queue/page.tsx` | Replaced the "Saved views — Coming soon" placeholder with `<SavedViewsBar />` — the fully-implemented component sitting next door, never imported. |
| `frontend-v3/src/app/(admin)/customers/page.tsx` | Removed the two dead `<FilterDropdown>` widgets (Supplier / Worst action) which had cursor:pointer + ChevronDown but no `onClick`. |
| `frontend-v3/vercel.json` | Added `Cache-Control: public, max-age=31536000, immutable` on `/_next/static/*` and `max-age=86400, stale-while-revalidate=604800` on common image/font extensions. |

### Audit findings INTENTIONALLY DEFERRED in this run (queue for next pass)

**Frontend code review:**
- #5 (P0): pass `deal` to `EditMetadataDialog` so the canonical-name shrink guard fires — needs a `useDealByCallQuery` that doesn't exist yet. Tracked.
- #6 (P0): unreachable `FeedbackEmailModal` — dead code that doesn't render. Will delete in a cleanup commit.
- #8/#9 (P1): error UI gap on `IntelligencePanel` + `AgentsPage` — same pattern as the `/rejections` fix.
- #10 (P1): React 18 strict-mode claim-ref orphan window — dev-only annoyance.
- #11 (P1): `window.location.href` in `useSubmitVerdict` toast — full-reload navigation.
- #12 (P1): 4 dead `useState` on call-detail (`chosen`, `reason`, `sendEmailToggle`, `committed`) — orphaned by the VerdictTab migration.
- #13-#15 (P1/P2): `as never` / `as unknown as` casts — 9 total, budget is 5.
- #18 (P1): Dashboard ↔ Customers data divergence — structural, needs backend reconciliation.
- #21-#25 (P2): cleanup nits.

**Backend code review:**
- P1-2: bare `pass` on observability emit — needs `logger.warning(...)`.
- P1-4 / P1-5: `_maybe_merge_into_existing_deal` + `_step_finalize` TOCTOU race on concurrent same-deal uploads. Need `with_for_update()`.
- P1-6: `datetime.utcnow()` everywhere is deprecated + tz-naive. Codebase-wide swap to `datetime.now(timezone.utc)`.
- P2-1: orphaned `GET /api/calls/{call_id}/agreement` endpoint.
- P2-2: `_last_action_date` N+1 on rejection-tab queries.
- P2-3 / P2-4: bare `except Exception: pass` swallowing logging in 3 places.
- P2-5: `print()` in `rag/ingest_rejections.py`.

**Database review:**
- P1-1: composite partial index on `calls(review_status, compliance_status, created_at DESC) WHERE review_status='unclaimed'` for queue hot path.
- P1-2: bulk-load `_last_action_date` per rejection batch.
- P1-3: `ix_rejections_status_confirmed` index.
- P1-4: GIN index on `calls.risk_tags`.
- P1-5: pg_trgm + GIN indexes on `customers.legal_name` / `trading_as` for fuzzy match.
- P1-6: `reviewer_edits` FK declarations.
- P1-7: convert `Call.checkpoint_results` from TEXT → JSONB.
- P1-8: switch `customer_deals.customer_id` FK from CASCADE → SET NULL.
- P2-1: timestamp columns missing `timezone=True`.
- P2-3: CHECK constraint on `verdict_history.verdict` / `verdict_responses.verdict` values.
- P2-4: composite `(call_id, idx)` index on `call_segments`.

These are real defects but each is a separate migration / refactor that needs its own test pass + CI run. The 27 fixes shipped in this session address the user-visible defects (auth, perf, broken UI, broken data) and leave the remaining items as a P1 backlog with file:line evidence inside the original audit transcripts.


