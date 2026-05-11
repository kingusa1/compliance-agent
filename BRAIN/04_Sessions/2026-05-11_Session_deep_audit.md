---
created: 2026-05-11
tags: [session, audit, scripts, ai-verdict, ingestion]
---

# Session 2026-05-11 — Deep audit + the V1-fallback fix

## What the user said

> "I think the AI verdict is not right. And the scripts basically is not
> ingested. If you go to the scripts and you check the scripts, I don't
> think that it's right. It doesn't relate."

They were right on both counts. This session validated and fixed the
root cause.

## Root cause — every call was being graded against 3 rules

The pipeline matches each call to a `Script` row and reads
`script.checkpoints` (a JSON array of rule dicts). When that array is
empty, the analyzer correctly falls through to the V1 third-party-
disclosure path which only has **3 universal rules**:

  1. The agent explicitly states the company is a third party
  2. The agent states the company is NOT an energy supplier
  3. The agent identifies as an independent broker or intermediary

ALL 15 seeded `Script` rows in prod had `checkpoints = "[]"`. So
EVERY one of the 37 calls was being graded against just those 3 rules.
That's why every score clustered at 0/3, 2/3, 3/3 — the system was
mathematically incapable of producing anything else.

A typical E.ON Closer (verbal contract reading) should be graded
against ~26 supplier-specific rules: contract length, unit rate p/kWh,
standing charge, VAT/CCL, cooling-off, Ombudsman, customer-yes
affirmation blocks, LOA wording bundle, etc.

## Three additional bugs surfaced during the audit

1. **`/api/calls` list endpoint dropped `call_type`** — the SELECT was
   missing the column, so even after the AI classifier + backfill set
   real call_types in DB, every UI surface showed NULL stage.

2. **`POST /api/calls/{id}/reanalyze` was a no-op in prod.** It emits
   the `call/reanalyze` Inngest event, but `USE_INNGEST_PIPELINE=false`
   in production. The events fire into a void with no consumer. So my
   first attempt to reanalyze 34 calls (after script-checkpoint
   ingestion) did nothing.

3. **Phase-2 docs missing from Railway image.** `.planning/phase2-docs/`
   lives at the repo root but the Dockerfile only `COPY`'d
   `backend/{app,scripts,alembic}`. The script-checkpoint extractor
   on prod 500'd with `FileNotFoundError`. Fixed by adding
   `COPY .planning/phase2-docs/ /.planning/phase2-docs/`.

## Code shipped

### `app/agents/script_checkpoint_extractor.py` (new)

`extract_checkpoints_from_markdown(supplier, script_name, md)` — Opus
4.7 reads each supplier-script markdown extract and emits the canonical
checkpoint JSON shape:

```json
{
  "section": 1,
  "name": "Verbal contract: contract length",
  "required": "Closer must state the fixed-term length...",
  "key_phrases": ["contract length", "fixed for", "months", "12 months"],
  "customer_response_required": true,
  "strictness": "verbatim",
  "line_number": 14
}
```

Hardened: strips ` ``` ` fences, retries by clipping from first `[`
to last `]` on parse drift, drops malformed rows, defaults strictness
to `mandatory` on unknown values.

### `POST /api/admin/ingest-script-checkpoints?apply=&only_empty=`

Walks every `Script` row, fuzzy-matches it to the right markdown
extract via 6-char-window substring score, runs the extractor, writes
`Script.checkpoints`. Default `only_empty=true` keeps populated rows
untouched.

### `POST /api/admin/reanalyze-all?apply=&only_script_id=`

Runs `_step_analyze_checkpoints + _step_score + _step_finalize`
**synchronously** per call. Bypasses the Inngest-event reanalyze that
prod drops on the floor.

### `Dockerfile`

Now bundles `.planning/phase2-docs/` so prod can read the markdown
extracts.

### `/api/calls` list response

Now selects `Call.call_type` and `Call.deal_id` (was previously
omitted).

## Prod results

### Script-checkpoint ingestion (`apply=true&only_empty=true`)

- 15 scripts scanned, 10 populated, **164 checkpoints written**
- Empty (5 scripts): BGL × 2, EDF V11, Pozitive, Scottish Power × 3 —
  these are the 5 unparseable scripts on Aly's reformat list. Their
  markdown extracts are OCR garbage that even Opus can't parse cleanly.
- E.ON Next NHH+HH (`960d6668`): **26 checkpoints**. This is the script
  used by 27 of 37 calls (73%). High-impact win.

| Script | Checkpoints |
|---|---|
| E.ON Next NHH+HH Verbal Contract (TPI) | 26 |
| E.ON Next Gas Verbal Contract (TPI) | 25 |
| E.ON Next Gas Verbal Contract (legacy) | 25 |
| E.ON Next Elec Verbal Contract (legacy) | 24 |
| British Gas Broker Acquisition V0.2 | 21 |
| British Gas Renewal/Deemed V03 | 20 |
| EDF Pre-amble | 12 |
| E.ON TPI Verbal LOA V2 | 11 |
| Total | **164** |

### Reanalyze-all

Triggered against 34 of 37 calls (3 lack transcript / word_data /
script_id). Running synchronously in the background — Opus 4.7 grades
each call against the new per-script checkpoints. Expected runtime
~10-12 minutes (~26 LLM calls × 34 calls).

## What still needs Aly

Per the existing `comms/2026-05-11_Aly_ask.md`:

1. E.On parent vs E.On Next — same supplier or split?
2. Standalone LOA — ever a standalone audio recording?
3. 5 scripts need reformatting with plain `1. 2. 3.` numbering
   (BGL V7, BG Acq V0.2, BG Renewal V03, EDF V11, Pozitive). These
   stayed empty after the LLM extractor because their markdown extract
   is OCR garbage.
4. Sample audio for non-E.ON closes.

Note: Scottish Power × 3 also came back empty from the LLM extractor.
That's unexpected (those PDFs looked clean). Worth re-investigating
once the higher-priority items are settled — could be that the
checkpoint extractor's prompt needs to be a bit more lenient for that
supplier's script structure.

## What I'm watching for next

The reanalyze finishes → I'll Playwright /tracker /scripts /calls
detail pages to confirm:

- Scores move from N/3 to N/M with M up to 26 for E.ON Closer calls
- /calls detail page renders all 26 checkpoint rows per call
- /scripts page shows checkpoint counts > 0
- /tracker rows reflect the new compliance verdicts

Then BRAIN log + final commit.
