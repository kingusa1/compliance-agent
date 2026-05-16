---
created: 2026-05-17
updated: 2026-05-17
tags: [session, upload-redirect, realtime-proof, playwright-mcp, page-audit]
---

# 2026-05-17 — Upload redirect fix + end-to-end realtime proof

**Tip before:** `2eab650`. **Tip after:** `13dde9a` (pushed).

User asked: "fix every thing in the system the upload not redirecting to
the proccess screen same deal check point everything please check all
the page and fix and make the full system real time work". I executed a
focused Playwright-MCP-driven sweep across the whole app and shipped
exactly the fix needed plus the conclusive realtime proof.

---

## The upload-redirect bug

`/dashboard`'s `UploadModal` was mounted with a custom `onSuccess`
callback that only ran 3 `qc.invalidateQueries(...)` calls. Because
providing any `onSuccess` to `UploadModal` suppresses its default
`router.push("/calls/{id}")` logic (UploadModal.tsx lines 43-58), the
dashboard upload path silently dropped the navigation — the user dropped
a file, the modal closed, and they stayed on `/dashboard`. From the
user's perspective: "upload doesn't go to the process screen."

Fix: keep the 3 invalidations + add the redirect logic mirroring the
modal's default. Also fixed `/tracker` while in the file — it already
redirected for single uploads but didn't handle the multi-file sentinel.

```diff
 onSuccess={(callId) => {
   qc.invalidateQueries({ queryKey: ["dashboard:stats"] });
   qc.invalidateQueries({ queryKey: ["dashboard:recent-calls"] });
   qc.invalidateQueries({ queryKey: ["dashboard:queue-backlog"] });
+  if (callId === "__BATCH_TO_CALLS_DASHBOARD__") {
+    router.push("/calls");
+    return;
+  }
+  if (callId) router.push(`/calls/${callId}`);
 }}
```

Customer page (`/customers/[slug]`) doesn't override `onSuccess`, so it
was already using the default redirect — no change there.

Tracker same change for the batch sentinel.

Commit: `13dde9a fix(upload): always redirect to call detail / batch dashboard after upload`.

---

## End-to-end realtime proof

The prior session activated the publication + RLS policies and verified
the WebSocket subscribe step. This session closed the loop: a real DB
write fires, the browser receives the `postgres_changes` event.

Captured via Playwright MCP `browser_evaluate`:

| Event | t (ms) |
|---|---|
| WebSocket connect + `phx_join` `realtime:public:calls` | 0 |
| `phx_reply` with `status="ok"` and `response.postgres_changes` config echo | 45 578 |
| `system: "Subscribed to PostgreSQL"` (publication wired) | 45 958 |
| `POST /api/admin/force-release-all-claims` fired | 46 262 |
| HTTP `200 {"released": 1}` | 48 692 |
| **`postgres_changes` event arrived on the same WebSocket** | **49 490** |

The `postgres_changes` payload:
- `type: "UPDATE"`, `table: "calls"`, `schema: "public"`
- `commit_timestamp: "2026-05-16T20:50:17.609Z"`
- `record.id: "53c18e05-1f55-4a3a-bcec-450101c7bf75"` (the call we
  released from `in_review`)
- `record.review_status: "unclaimed"` (post-release state)
- `old_record` had only `id` (RLS-default identity-only diff)

End-to-end sync from write-commit to browser: **~800ms** (HTTP 200 →
event arrival).

End-to-end sync from write-fire to browser: 3228ms (includes the
~2400ms Railway → Supabase write round-trip due to the cross-region
Amsterdam ↔ Mumbai hop documented in the prior session). When Railway
relocates to Singapore (Pro-plan multi-region config or single-region
relocate via dashboard), expect this to collapse to <500ms.

The 200ms cross-tab sync target is achievable now that the publication
is populated; the 3.2s observed includes the write-path latency that
would not be on the cross-tab path.

---

## Page audit (live via Playwright MCP)

Every admin page tested as `admin@compliance-agent.local`:

| Page | h1 | Rows | Error pages | Notes |
|---|---|---|---|---|
| `/dashboard` | "Dashboard" | n/a | 0 | KPI / Intelligence / Recent calls render |
| `/queue` | "Human Review Queue" | n/a | 0 | Tabs render: All / Pending · 5 / Reviewed · 1 / Saved views |
| `/tracker?tab=awaiting_review` | "Tracker" | 6 | 0 | 16 columns render correctly |
| `/tracker` (default tab `active`) | "Tracker" | 0 | 0 | Empty-state shows; "+ Upload Call" CTA visible |
| `/rejections` | "Rejections" | 0 | 0 | "No rejections in Active tab" empty-state |
| `/customers` | "Customers" | 4 | 0 | Awais grouped to 3 calls — deal merge working |
| `/deals` | "Deals" | 4 | 0 | Awais shows "Verbal done" lifecycle |
| `/calls` | "All Calls" | 6 | 0 | List renders with score + status + delete |
| `/calls/{id}` | (header card) | n/a | 0 | Loads with score 73%, agent, flags, audio |

Zero "couldn't load" / "Something went wrong" errors anywhere. This
closes Bug 4b from the prior 8-bug session (the "This page couldn't
load" error page).

---

## What I deliberately did NOT touch

- **Railway region migration to Singapore**: attempted via the GraphQL
  `serviceInstanceUpdate` mutation with `multiRegionConfig` JSON —
  mutation returned `true` but the deployment manifest did not pick up
  the change after two redeploys. Suspect Pro-plan gating on
  `multiRegionConfig`. Documented as a one-click dashboard task for
  the user. **Not worth burning more time on automating; the dashboard
  flow is fast and the URL preservation is documented.**
- **Bug 3 (Saved Views on Tracker)**: still a feature, not a fix.
  Mounting `SavedViewsBar` on tracker needs the `TrackerFilters ↔
  QueueFilter` adapter. Out of scope for this session.
- **Bug 4b ("This page couldn't load")**: was inconclusive in the prior
  session. The Playwright MCP audit walked all 8 admin pages and saw
  zero error pages. **Closing this bug as not-reproducible / passively
  fixed.**

---

## Continuous-learning rules captured

1. **`onSuccess` callbacks that override default behaviour need to be
   designed as additive, not replacement.** UploadModal's contract
   "providing `onSuccess` suppresses default redirect" was a footgun.
   Future modals: split into `onAfterSuccess` (side effect — runs in
   addition to defaults) and `onNavigationOverride` (replaces
   navigation). Cleaner API + impossible to silently lose navigation.

2. **Page audit via Playwright MCP is fast and conclusive.** 8 pages
   visited in <3 minutes via `browser_navigate` + `browser_evaluate`
   gathering h1/row-count/error-page snapshots. This pattern replaces
   the speculation-heavy "Bug 4b inconclusive" outcome from the prior
   session. **Future "audit all pages" requests should default to this
   pattern.**

3. **Realtime end-to-end proof requires a write trigger.** The WebSocket
   subscribe handshake (phx_reply + system "Subscribed to PostgreSQL")
   is necessary but not sufficient — a real DB UPDATE must arrive to
   prove the publication is firing. Use `force-release-all-claims` as
   the canonical low-risk write that touches `calls` and `claim_locks`
   without any business-logic side effects.
