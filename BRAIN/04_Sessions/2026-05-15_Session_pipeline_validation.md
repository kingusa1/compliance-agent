---
created: 2026-05-15
updated: 2026-05-15
tags: [session, rejection-pipeline, andrew, segment-score, n+1, brain, playwright]
---

# 2026-05-15 — End-of-day session: pipeline contract validation

**Owner:** Mohamed Hisham Ismail (kingusa1)
**Tip:** `5708bcf` (Railway), Vercel deploy in flight (`compliance-agent-mmxgtxlrl…`)

## Why this session existed

User opened the Andrew call (`2652a095…`) and saw:
- **LOA segment header**: `82% · 0/11 · Coaching` — read as a math contradiction (82% of 11 = 9 passed, but 0/11 says 0 passed)
- **CP09 / CP24**: top badge `Passed` / `Passed` while **Human Review** below = `✗ Reviewed · Fail`
- **CP20 "Microbusiness/Small Business status"**: `Not Yet Scored` with no AI verdict

Plus a friend's tracker N+1 diagnosis to verify, and an explicit request to walk the rejection pipeline end-to-end with Playwright.

## What was actually broken — 5 distinct bugs

| # | Where | Bug | Commit |
|---|---|---|---|
| 1 | `tracker_aggregator.py` (3 branches) | Per-row `.first()` for Call+Deal inside the loop → 301 SQL roundtrips / 100-row page on Supabase pooler | `0f56394` (this morning) |
| 2 | `pipeline.py:_step_analyze_checkpoints` | Per-segment analyzer occasionally double-scored some CPs and silently dropped others (Andrew CP20 case). Flat `Call.checkpoint_results` came out 37 entries but with the wrong distribution. | `0f56394` |
| 3 | `checkpoint_analyzer.py:780-803` | Medium-only breaches always bucketed as `coaching/compliant=true` — even at 0% pass rate. Andrew's LOA at 0/11 sat in "coaching" instead of "review". | `a83e441` |
| 4 | `SegmentCards.tsx` `ConfidenceDial` | Classifier confidence rendered as a bare percentage right next to the score string. "82% · 0/11" read as "82% passed but 0/11" mathematically wrong. Now shows pass-rate% derived from the score; classifier confidence is dots-only with hover-title. | `a83e441` |
| 5 | `CheckpointCard.tsx` `state` derivation | Top badge always reflected the AI verdict — never the reviewer's override. Reviewer marks Fail, badge still says "Passed". Now top badge tracks `reviewer_verdict` with a `· Human` suffix to show provenance. | `af3e0af` |

Plus two **rejection-pipeline P0s** Playwright caught:

| # | Where | Bug | Commit |
|---|---|---|---|
| 6 | `hitl_routes.py:552` | `if payload.verdict in ("FAIL", "REVIEW")` — uppercase only. Frontend sends lowercase. Auto-rejection branch never fired. `submit_verdict` returned 200 with `auto_rejection_id=null`. | `c03e0af` |
| 7 | `rejections_routes.py:1028` `auto_create_rejection_for_verdict` | `Rejection(...)` instantiated without `confirmed_by`. The `/rejections?source=reviewer` filter (`confirmed_by IS NOT NULL`) excluded reviewer-created rows. Reviewer creates a rejection → it doesn't appear in /rejections. | `5708bcf` |

## What Playwright proved end-to-end (post-fix)

After all 7 fixes deployed to Railway, the pipeline test (POST `/api/calls/{id}/verdict` with `verdict: "fail"`):

```
initial_awaiting: 6
initial_rejections_for_call: 0
submit_status: 200
submit_body.auto_rejection_id: "ca58e4c1-80d4-4e78-b816-4cf20499f932"
after_rejections_for_call: 6        ← 1 per failing CP (multi-rejection FAIL)
confirmed_by_set: true              ← every row has confirmed_by populated
sample_rejection.confirmed_by: true
after_awaiting_count: 5             ← call moved out of awaiting tab
call_still_in_awaiting: false
call_in_active_count: 6             ← all 6 rejections appear in tracker active tab
```

Every gate works:
1. AI alone → no rejection (verified earlier: 6 awaiting, 0 rejections in DB)
2. Reviewer FAIL → 6 rejections created (1 per failing CP, by design)
3. All 6 have `confirmed_by=user.id` → visible in `/rejections?source=reviewer`
4. Call disappeared from awaiting-review tab
5. Call now lives in the tracker active tab
6. Test artifacts deleted afterwards (6 rejections cleaned up)

## Friend's N+1 verification

Their diagnosis was **TRUE** for our codebase (lines 524 / 549 / 598-600 had the exact per-row `.first()` calls they described). Their second claim ("ours uses bulk helpers like `_last_action_dates_bulk`") was **FALSE** — those don't exist in our tree. They were looking at a different fork or hallucinating. We fixed it the way they suggested: 2 `IN(...)` queries → dict lookup.

## What's NOT yet deployed

- All backend fixes (1, 2, 3, 6, 7) → live on Railway as of `5708bcf`.
- Frontend fixes (4 segment pass-rate%, 5 reviewer-badge override) → built but stuck in Vercel queue. Multiple deploys (`fcx00n7le`, `czseianv0`) sat in UNKNOWN state for 10+ minutes. Latest `mmxgtxlrl` may resolve.
- Old aliased prod (`compliance-agent-mu.vercel.app`) still serves the build from this morning (`0f56394`). The CP20 → "Not Scored" label is live (shipped in `0f56394`); the pass-rate%/reviewer-badge fixes are not.

## Backfills applied

- `POST /api/admin/normalize-checkpoint-results?call_id=2652a095…&apply=true`
  - Filled CP20 with `status="not_scored"`
  - Re-derived segment scores: verbal `23/26 → 22/26` (dedup), LOA `0/11/coaching/compliant=true → 0/11/review/compliant=false`

## BRAIN updates needed

- This session log: ✓ here
- `Known_Issues`: bring this pipeline contract chain into a dedicated invariant. Already pinned the "rejection-create human-only" contract; the case-sensitivity sub-bug should live under it.

## Open items

- Wait for Vercel build queue to drain and alias to promote (or trigger a manual redeploy if stuck >30 min).
- Walk the UI button-by-button on /queue, /tracker, /rejections once the Vercel deploy is live.
