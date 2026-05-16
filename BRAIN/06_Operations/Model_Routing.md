---
created: 2026-05-16
updated: 2026-05-16
tags: [operations, llm, model-routing, cost, openrouter, anthropic]
---

# Model routing — which OpenRouter model goes on which agent

> Single source of truth for picking an LLM tier on every agent in this
> backend. When adding a new `_call_llm` site, read the **decision rubric**
> below and assign one of the 3 tiers. Do not introduce a 4th tier without
> updating this doc.

## Prices (OpenRouter, verified 2026-05-16)

| Model | OpenRouter id | Input / 1M | Output / 1M |
|---|---|---|---|
| Opus 4.7 (premium) | `anthropic/claude-opus-4.7` | $5.00 | $25.00 |
| Sonnet 4.6 (balanced) | `anthropic/claude-sonnet-4.6` | $3.00 | $15.00 |
| Haiku 4.5 (cheap) | `anthropic/claude-haiku-4.5` | $1.00 | $5.00 |

Prompt caching: when the LLM call passes a `system=` block, the provider
tags it with `cache_control: ephemeral` and input tokens for that prefix
are billed at ~10% on subsequent calls within a 5-minute TTL. Already
wired in `analysis._call_anthropic` for the 4 sites that use `system=`:
`analyze_compliance_v2`, `analyze_compliance_watt`, `quality_agent`,
`rejection_advisor`.

## Decision rubric — pick a tier for a NEW agent

Ask 4 questions, in order. The first "yes" decides.

### 1. Does the output drive a verdict or change which rubric runs?

**Examples**: grading a checkpoint, classifying a recording into segments,
extracting the supplier name, picking a script variant, deciding whether
to merge two customers.

→ **T0 — Opus 4.7.** Wrong output here cascades. Cost is justified.

### 2. Is this a one-shot ingest path (script upload, phrase pack, fixture)?

**Examples**: building checkpoint rules from a PDF script; building a
phrase pack from a markdown doc.

→ **T0 — Opus 4.7.** Volume is near-zero (once per script per release).
Accuracy matters because the output is then graded against thousands of
calls.

### 3. Is the output reviewer-facing text the reviewer will edit BEFORE close-out?

**Examples**: rejection category, short reason summary, suggested fix
text, outcome narrative, feedback abstraction.

→ **T3 — Haiku 4.5.** The human edit step is the safety net. Save 80%.

### 4. Is there a deterministic pre-pass (regex / lookup) and the LLM only fires when it misses?

**Examples**: MPAN/MPRN extraction (regex first, LLM fallback); agent-name
extraction (regex pre-pass with LLM second); date extraction (regex first,
LLM fallback).

→ **T3 — Haiku 4.5.** Falling back on the regex when the LLM hallucinates
keeps the failure mode bounded. Save 80%.

### Otherwise (medium-stakes generator, no edit step, no pre-pass)

→ **T2 — Sonnet 4.6.** 40% off Opus, much better at long-form generation
than Haiku.

## Current production wiring (2026-05-16)

### T0 — Opus 4.7 (grading + identity + cascade)

| Site | Function | Why T0 |
|---|---|---|
| `backend/app/checkpoint_analyzer.py` | `_analyze_batch`, `analyze_all_checkpoints`, `analyze_single_checkpoint` | This IS the grading. ~21 batches per call. |
| `backend/app/analysis.py:830` | `analyze_compliance_watt` | Watt-grounded verdict, cached `system=`. |
| `backend/app/analysis.py:775` | `analyze_compliance_v2` | V2 fallback grading, cached `system=`. |
| `backend/app/agents/content_classifier.py` | `classify_content` | Wrong segmentation → wrong rubric on every downstream step. |
| `backend/app/quality_agent.py` | `resolve_identity` | Cross-call customer merge. |
| `backend/app/extraction/vulnerability.py` | `_stage2_llm` | Compliance-critical (vulnerable customer). |
| `backend/app/agents/script_checkpoint_extractor.py` | `_extract_once`, `_extract_per_page`, `extract_checkpoints_from_markdown` | Ingest-only — accuracy matters, volume near-zero. |
| `backend/app/agents/phrase_pack_extractor.py` | `_extract_chunk`, `extract_phrase_pack` | Same — ingest-only. |
| `backend/app/analysis.py:465` | `detect_supplier` | T0 by user mandate 2026-05-16 (Sonnet was unreliable on noisy transcripts). |
| `backend/app/analysis.py:492` | `detect_call_type` | T0 by user mandate. |
| `backend/app/analysis.py:660` | `detect_names` | T0 by user mandate. Has regex pre-pass for the agent slot. |
| `backend/app/analysis.py:730` | `detect_script_variant` | Picks rubric variant — same accuracy class as detect_supplier. |
| `backend/app/business_detect.py` | `detect_business_name` | Drives deal-merge collapse — T0 by user mandate. |

### T2 — Sonnet 4.6 (medium-stakes generators)

| Site | Function | Why T2 |
|---|---|---|
| `backend/app/agents/rejection_advisor.py` | `advise_rejection` | Category + fix narrative land in tracker; reviewer edits before close. Cached `system=`. |

### T3 — Haiku 4.5 (low-stakes / fallback / reviewer-editable)

| Site | Function | Why T3 |
|---|---|---|
| `backend/app/agents/date_extractor.py` | `_llm_extract`, `extract_dates_for_call` | Date in ISO; tracker has edit affordance. |
| `backend/app/extraction/entities.py` | `_llm_fallback` | Only fires when regex misses. Tracker has edit affordance. |
| `backend/app/rejection_factory.py` | `_classify_category`, `_summarise_reason`, `_propose_fix`, `_propose_narrative` | Short reviewer-facing text — reviewer edits. |
| `backend/app/agent/feedback.py` | `abstract_and_store_review` | Internal feedback summarisation. |

### Free — regex / deterministic pre-pass

| Site | What |
|---|---|
| `backend/app/analysis._extract_agent_name_regex` | Regex pre-pass before `detect_names`. |
| `backend/app/extraction/entities` | Regex for MPAN/MPRN/DocuSign/Companies-House. |
| `backend/app/extraction/vulnerability` Stage 1 | Keyword pre-pass; Stage 2 LLM only fires after Stage 1 hit. |
| `backend/app/watt_compliance.script_detect` | Supplier regex. |
| `backend/app/watt_compliance.phrase_scan` | Compliance phrase regex. |

## How to apply a tier

```python
# In analysis._call_llm signature today:
async def _call_llm(prompt, timeout=60.0, system=None, *, cheap=False): ...

# Proposed (not yet shipped — needs user green-light):
async def _call_llm(
    prompt,
    timeout=60.0,
    system=None,
    *,
    tier: Literal["premium", "balanced", "cheap"] = "premium",
): ...
```

Until the `tier` parameter ships, use `cheap=False` for T0/T1/T2 and
`cheap=True` for T3. The `openrouter_cheap_model` config knob is
currently set to Opus 4.7 (defence-in-depth from 2026-05-16); after
the `tier` parameter ships we flip it back to Haiku 4.5.

## Forbidden swaps (operator-burned, do not revisit without explicit ask)

These were on Sonnet 4.6 (`cheap=True`) before 2026-05-16 and produced
unreliable output. Do NOT move them back to Sonnet without an explicit
operator instruction:

- `detect_supplier`
- `detect_call_type`
- `detect_names`
- `detect_business_name`
- `detect_script_variant`

The failure mode was: Sonnet returned person names where a business
name was asked for; mis-classified call_type on noisy transcripts;
picked the wrong supplier from the candidate set. Every one of these
errors cascades into wrong rubric → wrong grading.

## When evaluating a NEW model class (e.g. a future Haiku 5)

1. Run it on the 8 detectors above before recommending it for T0/T1.
2. Smoke-test 10 transcripts from `compliance-docs/AI Data/` —
   compare its output to Opus 4.7 word-for-word.
3. If accuracy diverges on any cascade-class output, keep T0/T1 on Opus.
4. If parity at lower price, propose a tier promotion — but log the
   smoke evidence in this file before flipping any callsite.

## Expected savings vs all-Opus (rough, per 100 uploads at 30% failure rate)

| Optimisation | $ saved / 100 uploads |
|---|---|
| Demote 7 T3 sites (date, entities-fallback, 4× rejection_factory, feedback) | ~$1.10 |
| Demote rejection_advisor T0 → T2 | ~$0.30 |
| **Total per 100 uploads** | **~$1.40** |

The big lever is NOT model tiering — it's reducing checkpoint-analyzer
Opus volume (70% of spend lives there). Future work:
1. Reuse the cached `system=` prefix across the 21 batches per call.
2. Haiku-first triage on obviously-passing checkpoints; Opus only on
   ambiguous ones.
3. Skip batches when the regex pre-pass already gave a high-confidence
   pass with evidence.

Neither of those is part of this routing matrix — they're future
optimisations to the grader itself.

## 2026-05-16 deep-search update — confirmed Anthropic-only for grader

Independent benchmarks + community reports from late-2025 / early-2026
(Vellum, artificialanalysis.ai, llm-stats, Kenodo, Glukhov on Medium)
all say the same thing about long-rubric JSON grading at 10k+ context:

- **Opus 4.7 still owns SWE-bench / agentic** (87.6 vs Gemini 3 Pro 80.6
  vs GPT-5.2 80.0). The compliance-grader workload lives in this class.
- **DeepSeek V3.2 + Qwen3 fail strict JSON schema** — documented open
  bug in vLLM #41132, LangChain `with_structured_output()` returns
  errors. Do NOT route the grader to them.
- **The Anthropic family is the gold standard for "schema adherence +
  output stability across repeated identical calls"** — exactly what
  the 26-rule rubric needs.
- **Sonnet 4.6 leads GDPval-AA enterprise reliability** — safe to use
  on the grader behind a confidence-gated Opus fallback.

Cited cascade-pattern papers (proven Opus-equivalent accuracy at 30-70%
lower cost):

- FrugalGPT (Chen et al., arXiv:2305.05176, ICLR 2025) — up to 98%
  cost saving with cascade + scoring function.
- Cascade Routing (ETH Zürich, dekoninck2024cascaderouting) — unified
  cheap-first router that pareto-dominates routing-only.
- 3× Haiku self-consistency vote ≈ Sonnet quality at ~60% Sonnet cost
  on classification / extraction.

## Future optimisation — grader cascade (not yet shipped, owner-decision-pending)

The grader (`checkpoint_analyzer._analyze_batch`) is **70% of all
OpenRouter spend**. Moving it to a cascade is the only optimisation
worth shipping:

```
batch → Sonnet 4.6 with rubric+transcript (cache hit on rubric prefix)
      → if any cp confidence < 0.8 OR status == 'partial' OR JSON malformed
          → escalate THAT cp's row to Opus 4.7
```

Expected save: **~55-65%** of grader spend (~$0.55 per upload at 21
batches × ~$0.045 each). Accuracy preserved because Opus reviews every
uncertain checkpoint. Anthropic prompt caching keeps the rubric prefix
billable at ~10% across the 21 batches within the 5-minute TTL.

**Hard NO on:**

- Routing the grader to a non-Anthropic model. Schema faithfulness drops
  outside the Anthropic family per independent reports.
- Skipping the Opus fallback. The whole point of the cascade is that
  Sonnet's mistakes are caught — without the fallback this is just a
  blind downgrade.

## Self-update protocol

Update this file whenever:
- A new `_call_llm` site lands (assign its tier).
- A new model class is benchmarked (add a row to the price table).
- An operator overrides a tier (note it under "Forbidden swaps" with a date).
- A T2/T3 site produces a failure that warrants promotion to T0 (log
  the failure mode + commit hash where the promotion shipped).
- An external research update changes the recommendation — log under
  the "deep-search update" section with a date and citations.
