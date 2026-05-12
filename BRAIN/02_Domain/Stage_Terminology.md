---
created: 2026-05-12
tags: [domain, terminology, aly, opener, closer, presales, loa]
---

# Stage terminology — Aly's mental model vs the docs vs the system

> Origin: user asked 2026-05-12 — "Aly told me Opener (lead-gen) and Closer
> (pre-sales / verbal / LOA), is that what the docs say and what the system
> implements?" Answer below.

## TL;DR

Aly's words are *almost* right; the docs use slightly different terminology
and treat **Pre-Sales = Lead Generation** as one call category, not a
sub-stage of Closer.

| Aly's mental model | Docs say | System routes |
|---|---|---|
| **Opener → Lead Gen** | Lead Generation (Pre-Sales) | `lead_gen` → 88-rule lead-gen phrase pack |
| **Closer → Pre-Sales** | Same as Lead Generation — synonyms | (no `pre_sales` call_type today; would route to the same 88-rule pack if added) |
| **Closer → Verbal** | Verbal Contract / Closing | `closer` / `verbal` / `full` → supplier verbal-contract script |
| **Closer → LOA** | Standalone LOA (when supplier accepts verbal LOA) | `standalone_loa` / `loa` → supplier LOA script |

## Source quotes

### `watt_ai_compliance_system.md` §1

> Build an AI system that transcribes, analyses, and flags compliance risks across:
> - Lead Generation Calls (Pre-Sales)
> - Verbal Contract Calls (Closing)

→ "Pre-Sales" is the **parenthetical synonym** for Lead Generation, not a
sub-stage of Closer. There are **two call categories** at the top level.

### `watt_ai_compliance_tech_spec.md` §3 — Call Segmentation Logic

> AI must segment calls into:
> - Introduction
> - Qualification
> - Pitch
> - Transfer / Passover
> - Verbal Contract
> - Close

→ These are **internal segments of ONE recording**, not separate calls. The
"Close" inside this list is the wrap-up at the end of the verbal-contract
recording, not a parent stage.

### `watt_sales_compliance_guide_.md` §p390-403 — Verbal LOAs

> Some of our partners do accept verbal LOAs from customers as long as the
> following conditions apply:
> - The verbal LOA script has been provided by or approved by the chosen
>   supplier.
> - The Partner follows the same principles as laid out on the Compliance
>   Standards above when completing the verbal LOA with the customer.

→ LOA can be **either** a separate audio recording **OR** paper/DocuSign.
When verbal it follows the supplier-specific LOA script.

## Doc-aligned hierarchy (the canonical one)

```
LEAD GEN (a.k.a. "Pre-Sales" in System §1)
    → 88-rule lead-gen phrase pack from Watt Phrase Detection Dataset

PASSOVER (warm handover sub-call, distinct recording in Watt's workflow)
    → 88-rule passover phrase pack (handover/authority focused subset of lead-gen rules)

CLOSER / VERBAL CONTRACT
    → supplier verbal-contract script
    → e.g. E.ON Next NHH+HH (TPI) Jan-26 = 26 cps
    → British Gas Acquisition V0.2 = 21 cps

STANDALONE LOA (when supplier accepts verbal LOA — non-E.ON only)
    → supplier LOA script
    → E.ON TPI Verbal LOA V2 = 11 cps (E.ON bundles LOA into Closer)

+ AMENDMENT (post-sale fix-up)
+ C-CALL (confirmation callback)
```

## What the system implements RIGHT NOW

Matches the doc-aligned model exactly:

| Uploaded call_type | rubric_router picks | Rules graded |
|---|---|---|
| `lead_gen` | Lead Gen phrase pack | **88** ✅ |
| `passover` | Passover phrase pack | **88** ✅ |
| `closer` / `verbal` / `full` | Supplier verbal script (e.g. E.ON NHH+HH) | **26** ✅ |
| `standalone_loa` / `loa` | Supplier LOA script (E.ON only) | **11** ✅ |
| `c_call` | C-call phrase pack | **32** ✅ |
| `amendment` | Amendment phrase pack | **32** ✅ |

## Two open questions for Aly

1. **Is "Pre-Sales" a SEPARATE uploaded call** in Watt's workflow, or just
   the inside-a-call segment the Tech Spec calls "Intro/Qualification"?
   - Docs say it's the latter (= same as Lead Generation).
   - If Watt operationally records it as a separate file, the system needs
     a new `pre_sales` call_type that routes to the same 88-rule lead-gen
     pack. **10-min code fix once confirmed.**

2. **Is the LOA always its own audio file for non-E.ON suppliers, or
   sometimes paper/DocuSign?** Compliance Guide §p390-403 says BOTH are
   allowed. If sometimes paper, the system needs a document-upload pathway
   on the deal record. Already in `comms/2026-05-11_Aly_ask.md` (Q2).

## How to add `pre_sales` if Aly confirms

If Aly's model is operationally real:

```python
# backend/app/agents/rubric_router.py
_CALL_TYPE_PHRASE_PACK_PHASE: dict[str, str] = {
    "lead_gen": "lead_gen",
    "pre_sales": "lead_gen",   # ← Aly's "Closer Pre-Sales" maps to same pack
    "passover": "passover",
    "c_call": "c_call",
    "amendment": "amendment",
}
```

And add `pre_sales` to:
- `app/deal_lifecycle.py:_CALL_TYPE_TO_PHASE`
- `frontend-v3/src/lib/schemas/l7-intake.ts:CallType` enum
- `app/analysis.py:DETECT_CALL_TYPE_PROMPT` (the AI classifier)

The 88-rule lead-gen phrase pack itself doesn't need changes — it's already
the right rubric for pre-sales content per the docs.

## Bottom line

Aly's "Opener / Closer{Pre-Sales, Verbal, LOA}" naming is closer to a
Watt operations-team mental model. The compliance docs use a flatter
"Lead Generation Calls | Verbal Contract Calls" naming. **The rule
mapping is identical between the two models** — only the labels differ.

The system uses the doc-aligned labels (`lead_gen`, `passover`, `closer`,
`standalone_loa`) and routes to the right rule set. If Aly insists on
"Pre-Sales" as a distinct upload, add it as an alias that routes to
lead_gen. No semantic difference — just a naming preference.
