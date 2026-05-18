---
created: 2026-05-18
updated: 2026-05-18
tags: [session, names, agent-name, customer-name, regex, pii, ai-detection]
---

# 2026-05-18 — Smart agent + customer name detection (5-layer fix)

User reported (verbatim): *"the name of the agent and the name of the
customer not appearing in the transcript, please fix that ASAP. Make
the AI detect them. Why doesn't the AI detect them? and I want the
full system to be so smart."*

**Tip before:** `ec4bd2e` on
`fix/westbury-supplier-prepass-agent-smell-chips-removed`.
**This commit:** `b7928e0` on the same branch (PR #2).

---

## Why the AI was missing names (root cause)

Playwright baseline on `/calls` (commit ec4bd2e prod) showed five
concrete failure modes across 15 calls:

| Symptom on prod | Cause in code | Files |
|---|---|---|
| `agent_name = "Is"` on Muhammad Mukhtar call | `_NAME_STOPWORDS` was missing `is`, `name`, `mine`, `it` → the regex made `is` optional after `my name`, so `"my name is is calling"` captured `Is` as a name | `app/analysis.py` |
| `agent_name = "Sort Of"` | `_AGENT_NAME_FILLER_TOKENS` smell test missed filler tokens in 2-word LLM outputs (Westbury fix already shipped — confirmed working as designed) | `app/analysis.py` |
| `agent_name = "Art Engineer"` | Same as Sort Of — Westbury smell test catches it (working) | `app/analysis.py` |
| `customer_name = "—"` on Sort Of / Art Engineer / Alyssa rows | Customer slot had **zero regex pre-pass**, depended 100% on the LLM. When LLM returned Unknown nothing fired | `app/analysis.py` |
| `customer_name = "—"` plus deal was linked | Pipeline never consulted `CustomerDeal.customer_name` / `Customer.legal_name` as a fallback when `detect_names` gave up | `app/pipeline.py` |
| `[PERSON_NAME]` literal captured (Crosby Grange) | Deepgram redactor emitted bracketed markers; LLM captured one verbatim; PII strip collapsed result to Unknown without recovery | `app/analysis.py` |

The frontend wiring was correct all along — `TranscriptPlayer.tsx`
already renders `agentName` / `customerName` above the AGENT/CUSTOMER
chip when present. The fix is entirely backend.

---

## The 5-layer fix shipped

### A1 — Stopword leakage tokens (`_NAME_STOPWORDS`)

Added 18 tokens that only ever appear as intro fragments, never as
real first names: `is`, `am`, `name`, `names`, `mine`, `it`, `that`,
`who`, `this`, `these`, `those`, `myself`, `yourself`, `calls`,
`call`, plus question/auxiliary words (`what`, `where`, `when`, `why`,
`how`, `could`, `would`, `should`, `can`, `may`, `might`, `must`,
`have`, `has`, `had`, `do`, `does`, `did`, `be`, `been`, `being`).

`my name is is calling from watt utilities` now returns `None` from the
regex pre-pass instead of `"Is"`.

### A2 — Customer-side regex pre-pass (`_extract_customer_name_regex`)

New deterministic pre-pass for the customer slot (was zero-coverage).
Three pattern families:

* **Agent-side cues** — `am I speaking to <X>`, `is that <X>`, `could
  I speak to <X>`, `please confirm your name <X>`, `may I take your
  name <X>`, `talking to <X>`, plus 5 more.
* **Customer self-intro** — `yes this is <X>`, `yeah it's <X>`, `hi
  this is <X>`, `that's me, I'm <X>`.
* **Phone-pickup trail** — `<X> speaking`.

Scans the first 3000 chars (was 1500 for agent regex) because the
customer often only names themselves AFTER the agent's 60s TPI
preamble. **Collision guard:** rejects any capture whose first name
matches the agent's first name (almost always a same-speaker
re-introduction).

### A3 — Sharpen `DETECT_NAMES_PROMPT`

* Acknowledges that the transcript is diarized with `Agent:` /
  `Customer:` labels but warns the LLM that the diarizer can
  mis-attribute turns, so wording cues take precedence over labels.
* Lists the new customer-side cues (`am I speaking to X`, etc.).
* Adds explicit "reject PII redaction markers like `[PERSON_NAME]`"
  rule.
* Increases the transcript slice from 600 → 1200 words (the
  customer name often arrives later than the first 5 minutes).

### A4 — Pipeline customer fallback to linked Deal

`_step_detect_metadata` now falls back to `CustomerDeal.customer_name`
or `Customer.legal_name` when `detect_names` returns `Unknown` for
the customer slot AND `Call.customer_name` is genuinely empty (never
overwrites an existing AI/reviewer value). The deal-linker already
knows who the call is about — surface that to the UI.

### A5 — AAI transcript retry

When DG-based `detect_names` leaves either slot Unknown, retry
against `call.assemblyai_transcript`. The two engines redact PII
using different substitution patterns (DG = `[PERSON_NAME]`, AAI =
`#####`), so a name lost on one stream is often intact on the other.
Only retries when AAI text exists and differs from DG to avoid
duplicate LLM cost.

**Bonus — `_AGENT_NAME_FILLER_TOKENS` expansion:** added `please`,
`thanks`, `thank`, `today`, `tomorrow`, `yesterday`, `again`, `soon`,
`later`, `sure`, `alright`. Catches the customer regex's surname-slot
trap (`"Pete please"` → `"Pete"`, `"Margaret today"` → `"Margaret"`).

---

## Tests

`backend/tests/test_smart_name_detection.py` — 14 new pytest cases:

* **A1** — 5 cases covering bare-`is` rejection, bare-`name` rejection,
  happy-path first-name preserved, first+surname preserved, defence
  check that the union contains all leak guards.
* **A2** — 9 cases covering each cue family, PII marker rejection,
  collision skip vs agent name, accept-different-name with agent
  context, empty-input path, 3000-char window proof, `could I speak
  to`, `please confirm your name`.

Local test run (`./venv/Scripts/python.exe -m pytest tests/...`):

```
test_smart_name_detection ........... 14 passed
test_supplier_prepass ............... 14 passed
test_pii_token_stripping ............ 11 passed
test_analysis ....................... 3 passed
test_transcription .................. 10 passed
                                     -----------
                                      42 passed
```

Wider run (`-q` over `test_analysis test_smart_name_detection
test_supplier_prepass test_pii_token_stripping test_transcription
test_pipeline test_business_detect`): 60 passed, 5 errors — all 5
errors are pre-existing Windows tmpfile teardown PermissionErrors
(`[WinError 32]`), documented as flakes in this BRAIN, and pass on
the Linux CI runner.

---

## Playwright baseline (commit `ec4bd2e` prod, before fix)

`/calls` page on `https://compliance-agent-mu.vercel.app/calls` —
15 calls, visible bugs:

| Row | Customer | Agent | Bug |
|---|---|---|---|
| 1 | — | Sort Of | filler-token agent (smell test should catch — verify regression) |
| 2 | — | Art Engineer | job-title agent (smell test should catch — verify regression) |
| 3 | Crosby Grange Properties | — | agent slot null (deferred per 2026-05-18 5h audit Finding #6) |
| 4 | Muhammad Mukhtar | **Is** | **Finding #3 — fixed by A1** |
| 5 | Mohammed Mugrabi | Tom Kelly | ✓ |
| 15 | — | Alyssa | customer null (fixable by A2 + A4 + A5) |

Drill-in to call `c9b3f559` (Muhammad Mukhtar / "Is") confirmed every
transcript turn renders `Is\nAGENT\n0:XX` — agent name "Is" is
literally written on every word turn. After deploy this same call
will continue to show "Is" because it's the cached value; new
uploads will be clean.

---

## What still needs to happen

1. **Wait for CI green** on commit `b7928e0` — `coverage` + `pytest`
   workflows still IN_PROGRESS at session checkpoint. `vitest` +
   `gate` SUCCESS, `playwright` SKIPPED.
2. **Merge PR #2** once CI green — Railway watches main, will
   auto-deploy.
3. **Validate fix on prod** — upload a new lead-gen call (`Crosby
   Grange` candidate available in `compliance-docs/COMPLIANCE
   XAI/`) and verify:
   * agent_name no longer falls into "Is" / "Sort Of" / "Art
     Engineer" buckets
   * customer_name populates from the customer regex even on calls
     where the LLM gives up
   * PII-redacted calls recover from AAI when DG redacted the name
4. **Backfill option** (not done): existing calls with broken names
   (`Is`, `Sort Of`, `Art Engineer`, `—`) will keep the broken
   values until reanalyzed. Reviewer can hit the Override metadata
   dialog OR an admin endpoint could batch reanalyze. Out of scope
   for this session.

---

## Continuous-learning rules captured

1. **A name extractor must have BOTH a regex pre-pass AND an LLM
   step for BOTH slots.** Only the agent had a regex pre-pass — that
   asymmetry created the long-standing "customer never appears"
   bug. New rule: any time we add a slot that requires named-entity
   extraction, ship deterministic + LLM together.
2. **Intro-fragment leak tokens travel in groups.** `is` was missing
   because nobody scanned the corpus for the pattern. The expanded
   stopword list now covers the entire auxiliary-verb + question-word
   + demonstrative-pronoun family so the next "name is X is calling"
   variant doesn't surprise us.
3. **PII redactors emit detectable markers per engine.** DG uses
   `[PERSON_NAME]`, AAI uses `#####`. Don't strip blindly — try the
   OTHER engine's transcript when one redacts.
4. **The deal-linker is the authoritative source for customer name
   when names fail.** It runs BEFORE detect_names in the pipeline
   and has its own multi-tier matcher. Fall back to it rather than
   leaving the slot Unknown.
5. **The customer regex's surname slot must use the UNION of
   stopwords + filler tokens.** Politeness markers (`please`,
   `thanks`, `today`) are NOT in `_NAME_STOPWORDS` because the agent
   regex never saw them in the surname position; the customer regex
   does. Centralising the stop-union solves this once.

---

## Resume guide

If picking this up later:

1. `gh pr view 2 --web` — read the PR description and CI status.
2. If CI green, merge.
3. Verify Railway redeploy: `curl
   https://compliance-agent-production-690e.up.railway.app/api/health`
   → confirm `git_sha` matches `b7928e0` (or whatever merge commit
   ends up on main).
4. Upload a NEW non-E.ON call, watch `/calls` for clean
   `agent_name` + `customer_name` columns.
5. If "Is" appears again → either the deploy didn't go out, OR
   there's a NEW intro-fragment leak we haven't seen — add to
   `_NAME_STOPWORDS` and re-test.
