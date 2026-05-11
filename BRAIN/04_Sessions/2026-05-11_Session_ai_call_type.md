---
created: 2026-05-11
tags: [session, ai, call_type, upload, guide]
---

# Session 2026-05-11 — AI call_type + upload bugs + guide rewrite

## Bugs the user reported (all fixed and live)

1. **"+ Upload call to this customer" did nothing** on `/customers/[slug]`.
   The button had no `onClick`. Wired to `UploadModal` with the customer
   prefilled + slug locked so uploads land on the correct deal.

2. **Clicking "Upload" inside the modal didn't upload** in auto-detect
   single-file mode. The dropzone set the RHF field but didn't fire;
   multi-file fired immediately via `fireBatchUpload`. Asymmetry caused
   silent 422s. Now ANY drop in auto-detect mode immediately fires the
   no-metadata upload (matches multi-file behaviour). Single-file batch
   navigates to `/calls/{id}` on success.

3. **call_type came from the filename** ("the fund name is something
   that is so weak…not accurate"). The 30-line filename pre-pass in
   `routes.py` is gone. New flow:
   - `app/analysis.py:detect_call_type(transcript)` — Opus 4.7 reads
     the first 2500 words and picks one of 6 canonical codes
     (`lead_gen` / `passover` / `closer` / `standalone_loa` / `c_call` /
     `amendment`). Returns `None` on failure → caller leaves as `full`.
   - `DETECT_CALL_TYPE_PROMPT` spells out the rule for each stage with
     phrasal signals.
   - Wired into `pipeline._step_detect_metadata` immediately after
     `detect_names`. Only overwrites when current call_type is missing
     or `full` so reviewer-signed-off values stay sticky.

4. **AGENT label on every transcript bubble** (shipped earlier in this
   session — see `2026-05-11_Session_workflow_pill.md`). Backend
   `/api/calls/{id}/words` now writes an explicit `role` per word via
   `_detect_agent_speaker`. Frontend prefers `role` over numeric speaker.

## Live backfill (2026-05-11)

`POST /api/admin/backfill-call-types?apply=true&only_full=true` against
prod Railway. 15 historical `full`-tagged calls re-classified by AI:

  scanned: 15 · applied: True · unresolved: 0
  call_type changes: 15
  deals re-lifed: 11

Most went to `closer` (E.ON verbal-contract recordings) plus one to
`lead_gen`. Reviewer-signed-off calls were skipped.

## /guide rewrites

- Pipeline upgraded from 13 → **15 steps**, with the AI call_type step
  and the Quality Agent / pricing-mismatch / vulnerability-detect steps
  surfaced individually.
- New subsection **"How the AI decides which stage a recording is"**
  under Lifecycle. Reads as the rulebook: a table of the 6 stages with
  what each is + the phrasal signals the model looks for. Reviewers can
  use it to sanity-check or override the classifier.
- The 2/3-stage matrix from earlier in the day stayed 3/4 (canonical).

## New admin endpoint

`POST /api/admin/backfill-call-types?apply=&only_full=` — HTTP wrapper
around `scripts/backfill_call_type_ai.py`. Dry-run by default. Re-derives
deal lifecycle on every affected deal. See route in `backend/app/routes.py`.

## Code map (touched files)

```
backend/app/analysis.py             + DETECT_CALL_TYPE_PROMPT, detect_call_type()
backend/app/pipeline.py             + wire detect_call_type into _step_detect_metadata
backend/app/routes.py               - removed filename pre-pass in upload_call()
                                    + POST /api/admin/backfill-call-types
backend/scripts/backfill_call_type_ai.py  (new — local CLI variant)

frontend-v3/src/app/(admin)/customers/[slug]/page.tsx
                                    + UploadModal wired to +Upload button
frontend-v3/src/components/intake/L7Form.tsx
                                    + auto-fire fireBatchUpload on drop in auto-detect
                                    + navigate to /calls/{id} on first single-file success
frontend-v3/src/app/(admin)/guide/page.tsx
                                    + AI classifier section + 15-step pipeline
```

## Live state after this session

- Vercel: `dpl_F5oVygMyiRMaCPFjxZoDxyJmG574` on commit `2467e90` → alias
  `compliance-agent-mu.vercel.app`.
- Railway: backend redeployed automatically on push to `main`; readyz
  reports db ok. New `/api/admin/backfill-call-types` live.
- DB: 37 calls / 18 customers / 11 deals re-lifed after AI backfill.

## Open items

- The auto-mode safety hook denied the "apply=false&only_full=false"
  full re-classification pass. The targeted pass (only_full=true) hit
  the 15 problem calls. If reviewers want a full sweep, run the dry-run
  via the CLI script first (`scripts/backfill_call_type_ai.py`).
- Aly's 4 blockers still open (see `comms/2026-05-11_Aly_ask.md`).
- V2 supplier-script checkpoints still pending for the 8 parseable
  scripts (E.ON × 5, Scottish Power × 3).
