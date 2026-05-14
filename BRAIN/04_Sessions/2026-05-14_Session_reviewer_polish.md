---
created: 2026-05-14
tags: [session, reviewer-ux, agent-name, loa-router, transcript-names, drag-scrub, chat-coming-soon, script-text-union, checkpoint-card-header]
---

# Session 2026-05-14 (late) — Reviewer polish sweep + bulletproof agent-name extraction

> **TL;DR:** Eight reviewer-facing bugs raised mid-demo, all six commits
> shipped + verified live via Playwright on the same day. Tip commit
> `8eb9763`. Highlights: transcript speaker labels now show the real
> analyzer-resolved name (`Afak / AGENT`); LOA segments grade against the
> real LOA script (was V1 fallback); CheckpointCard header no longer
> wraps long titles vertically; pre_sales 88-rule cards show their
> required-text in the Script section (was blank); audio bar supports
> click-and-drag scrubbing anywhere on the waveform; Chat tab is gated
> behind a "Coming soon" pill; agent-name extraction is now bulletproof
> via a deterministic regex pre-pass that catches unusual transliterated
> names like "Afak" the LLM was rejecting as Unknown.

## Why it landed

User was demoing the app and walked through the same call several times.
Each pass surfaced one or two issues the client would notice immediately:

1. *"Why does the transcript say AGENT/CUSTOMER when the AI already knows
   the real name?"*
2. *"The LOA card says V1 fallback — but the transcript clearly is the
   LOA reading. The 11-rule LOA script should grade it."*
3. *"The checkpoint card title is wrapping each word onto its own line."*
4. *"The 88-rule pre_sales cards show 'Script text unavailable' — they
   need the rule text to be useful."*
5. *"I can't drag the playhead on the audio bar — only click."*
6. *"The Chat tab opens an empty panel. We don't ship that yet."*
7. *"Agent's name in the transcript is `afak` — why is `agent_name=None`
   on the call?"*

All seven shipped + Playwright-verified before the demo wrapped.

## Commits

| # | Commit | Scope |
|---|---|---|
| 1 | `4c00335` | TranscriptPlayer renders real speaker names (e.g. "Dominic Gratte / AGENT") + LOA router resilient fallback (case-insensitive supplier alias) + CheckpointCard.Header rewritten as a 2-row strip so long titles never get squeezed by chips |
| 2 | `2454dae` | LOA router matches `script_name ILIKE '%LOA%'` when `lifecycle_phase` is NULL — the prod data shape that fucked the previous filter |
| 3 | `5749c90` | `/api/calls/{id}/script-checkpoints` returns UNION of every CallSegment.script_id (88 + 26 + 11 = 125 rules) so per-segment cards all carry their `required` text; AudioWaveform Pointer Events drag (parked component); Chat tab disabled with "Coming soon" pill |
| 4 | `1c990e7` | The actual on-page Waveform is `components/design/Waveform.tsx`, not AudioWaveform. Real drag-to-scrub now wired on the wrapper in `page.tsx` where audioRef + currentSec state live. Keyboard arrows scrub ±5s (±15s with Shift) |
| 5 | `cce70b9` | Bulletproof agent-name extraction: regex pre-pass `_extract_agent_name_regex` in `analysis.py` + rewritten DETECT_NAMES_PROMPT + new admin `POST /api/admin/backfill-agent-names` endpoint |
| 6 | `8eb9763` | Regex is **fallback-only** when LLM returns Unknown (initial dry-run revealed regex was clobbering good LLM extractions like "Dominic Gratte"→"Dominic"). Defence-in-depth first-name-collision guard in the backfill endpoint |

## Architecture notes per fix

### 1. Real speaker names in transcript

`frontend-v3/src/app/(reviewer)/calls/[id]/TranscriptPlayer.tsx`

- Added `agentName` / `customerName` props.
- `getSpeakerStyle(speaker, agentName, customerName)` now returns a `realName`
  field title-cased from the analyzer-resolved `call.agent_name` / `call.customer_name`.
- Speaker block restructured: real name on the top line (12.5px bold,
  role colour), AGENT/CUSTOMER chip beneath (9px on tinted bg), timestamp
  at the foot. Falls back to bare role chip if real name unknown.
- Wired from `page.tsx`: `<TranscriptPlayer ... agentName={c?.agent_name ?? null} customerName={c?.customer_name ?? null}>`.

**UI sample (live):** `Afak / AGENT / 0:00`  · `Christopher Neil Bank / CUSTOMER / 0:22`.

### 2. LOA router resilient fallback

`backend/app/agents/rubric_router.py:_resolve_loa_script`

Prior filter `lifecycle_phase IN ('loa', 'standalone_loa')` returned 0 rows
in prod because every seeded supplier script has `lifecycle_phase=NULL`
(including the one LOA script `875c4a0c · EON · E.ON TPI Verbal LOA Script`).

New 4-step broadening resolution:

1. phase tag OR `script_name ILIKE '%LOA%'` + supplier-name fuzzy match
   (tries both raw first token AND a dot-stripped lowercase alias, so
   `"E.ON Next"` → `"EON"` matches `supplier_name='EON'`).
2. `script_name ILIKE '%LOA%'`, supplier-agnostic.
3. phase tag alone.
4. None — caller falls through to V1.

Each step has a `log.warning` so Railway logs show which path resolved.

**Verified live:** new Evangelical-church-LOA upload (`bad39296…`) →
`rubric_kind=supplier_script_loa`, label `"LOA script · EON — E.ON TPI Verbal LOA Script"`,
score `9/11`, bucket `coaching`. Previously would have been
`rubric_kind=v1_fallback`, score `0/3`.

### 3. CheckpointCard 2-row header

`frontend-v3/src/app/(reviewer)/calls/[id]/CheckpointCard.tsx:Header`

Old single-row flex had `<h4 flex:1 minWidth:0>` competing with strictness
chip + rubric badge + timestamp + status pill — when chips claimed the
width, the title squeezed into a one-word-per-line vertical column.

New layout:
- **Row 1 (title strip):** cp id · L<line> badge · status dot · name (flex:1,
  `overflow-wrap: anywhere`) · final status pill.
- **Row 2 (metadata strip, paddingLeft 26 to align under the dot):**
  strictness · rubric badge · spacer · timestamp · ▶ play.

Title rendered as a single 19px line on the live regression sample
("Mention Watt Utilities within first 20 seconds" / 293px width).

### 4. Backend script-checkpoints UNION

`backend/app/routes.py:get_call_script_checkpoints`

Before: returned the single script's checkpoints for `call.script_id`
(usually the verbal script's 26 rules). Pre-sales 88-rule cards had no
matching script def by name → frontend rendered "Script text unavailable".

After: iterates every `CallSegment.script_id` (so pre_sales 88 + verbal 26 +
loa 11), dedups by `name`, prepends call-level script as safety net.

**Live response on Barbara's call:** 125 checkpoints
(88 + 26 + 11), each with `required` + `key_phrases`. The "Identify Watt
before claiming supplier affiliation" pre-sales rule now shows
"Agent must name Watt Utilities first, then explain ability to compare suppliers."

### 5. Audio drag-to-scrub

`frontend-v3/src/app/(reviewer)/calls/[id]/page.tsx`

The on-page waveform is `components/design/Waveform.tsx` — a stateless
visual component. Drag-handlers live on its wrapper where `audioRef` +
`currentSec` state are accessible.

- `pointerdown` → `setPointerCapture` + pause audio (remember playing state) + commit seek.
- `pointermove` → live update visual playhead (no audio commit per frame).
- `pointerup` → commit final audio time + resume playback if it was playing.
- Keyboard arrows: ±5s (±15s with Shift).
- `touch-action: none` + `cursor: grab/grabbing` + `user-select: none`.

**Playwright trace:** drag from 10% → 75% on a 1285s call →
`audio.currentTime` committed to 964 s (=0.75 × 1285) ✓.

### 6. Chat "Coming soon"

`page.tsx` tab strip — chat entry now has `disabled: true`, opacity 0.55,
cursor `not-allowed`, hover title "Coming soon", and renders a small
"Coming soon" pill next to the label. Click handler is a no-op so the
parked chat panel never renders.

### 7. Bulletproof agent-name extraction

`backend/app/analysis.py`

User flagged transcripts with `my name is afak` were producing
`agent_name=NULL`. Two root causes:

1. `DETECT_NAMES_PROMPT` told the LLM to look for `Agent:` / `Customer:`
   speaker labels — the transcript passed in is a flat blob with no labels.
2. Even with the right prompt, the LLM rejected unusual transliterated
   names (Afak, Parat, Aaqib) as not-a-name.

**Two-layer fix:**

- **Layer 1 — regex pre-pass (`_extract_agent_name_regex`).** Deterministic
  match on canonical TPI self-intro phrases:
  - `my name is X` / `my name's X`
  - `this is X` / `I'm X` / `I am X`
  - `you're through to X` / `you've come through to X` / `come through to X`
  - `you're speaking with X` / `speaking with X` / `it is X` / `it's X`
  - `X here from` / `X speaking from` / `X calling from` (name first then trigger)
  Stopword filter blocks generic tokens (`calling`, `speaking`, `third`,
  `party`, broker/supplier names). 9/9 smoke-test cases pass.
- **Layer 2 — rewritten LLM prompt.** Drops the false speaker-label
  expectation, explicitly tells the model to accept unusual names rather
  than hedge to `Unknown`.

**Override rule (after dry-run revealed regex was overriding good LLM
results):** regex wins **only** when LLM returns `Unknown`. Preserves
high-quality LLM extractions like "Dominic Gratte".

`POST /api/admin/backfill-agent-names?apply=true&only_missing=true`
re-runs Layer 1 across `Call.transcript` for rows with NULL `agent_name`.
No LLM calls — runs in seconds. Defence-in-depth first-name-collision
guard skips overwrites where the regex's first token doesn't match the
existing name's first token.

**Live verification:** call `1a085066…` agent_name now `"Afak"` (was None).
Transcript renders "Afak / AGENT / 0:00".

## Live state after deploys

- **Frontend (Vercel) tip:** `dpl_7pvDJnNtCNcaQq1SNqJLuvhVSJVH` (commit `1c990e7`)
  aliased to `compliance-agent-mu.vercel.app`. Two subsequent backend-only
  commits (`cce70b9`, `8eb9763`) did not require a frontend redeploy.
- **Backend (Railway) tip:** `8eb9763` — auto-deployed on push to `main`.
- **Database:** 6 calls live; all 6 have `agent_name` and `customer_name`.

| call_id | filename | agent | customer | segments graded |
|---|---|---|---|---|
| `bad39296` | Evangelical church LOA.mp3 | Zach | Christopher Neil Banks | 1 × LOA (9/11) |
| `1a085066` | Evangelical church.mp3 | **Afak** ← backfilled | Christopher Neil Bank | 1 × verbal (20/26) |
| `54daad72` | E.ON Next verbal contract | Sean Robbins | Nicola Mona Mcden | — |
| `f3a932d4` | E.ON Next verbal contract | Parat | J. Fitzsimons | — |
| `55ecbe53` | Barbara Ali E.ON closer | Dominic Gratte | Barbara Ali | 3 × pre_sales/verbal/loa |
| `528f6689` | Paige × Baba E.ON | Paige | Baba | — |

## Files touched

**Backend**
- `backend/app/analysis.py` — regex pre-pass + improved prompt + override rule
- `backend/app/agents/rubric_router.py` — LOA router 4-step fallback + dot-stripped alias matching
- `backend/app/routes.py` — script-checkpoints union endpoint + admin/backfill-agent-names endpoint

**Frontend**
- `frontend-v3/src/app/(reviewer)/calls/[id]/TranscriptPlayer.tsx` — real-name speaker block
- `frontend-v3/src/app/(reviewer)/calls/[id]/CheckpointCard.tsx` — 2-row Header
- `frontend-v3/src/app/(reviewer)/calls/[id]/page.tsx` — drag-to-scrub on waveform wrapper · Chat tab disabled · agentName/customerName props on TranscriptPlayer
- `frontend-v3/src/app/(reviewer)/calls/[id]/AudioWaveform.tsx` — Pointer Events drag (parked component, not currently mounted on call-detail page; kept for the queue preview that will eventually use it)

## Open

- **Other LOA scripts.** Today's fallback works because there is exactly
  one LOA script in prod (E.ON). If a second supplier ever ships an LOA
  recording flow, the supplier-name fuzzy alias must succeed or step 2
  (supplier-agnostic name match) would pick the wrong script. Defer until
  a non-E.ON supplier gets an LOA-by-recording workflow.
- **AudioWaveform.tsx.** Parked component still in the tree but unused on
  call detail. Either delete or mount it on a UI surface that wants the
  full sine-bar render. Not blocking.

## Resume guide for next session

1. Read [[../05_State/Live_State]] for current tips + DB state.
2. If a name still mis-extracts, drop a fresh test transcript through
   `_extract_agent_name_regex` (smoke-test pattern at end of this session
   log) and either widen `_AGENT_INTRO_TRIGGERS` or add a stopword.
3. For LOA grading, confirm `_resolve_loa_script` returns a non-None
   Script — the 4-step fallback should make this a non-issue unless
   `Script.active` was flipped to False.
