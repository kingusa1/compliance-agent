---
created: 2026-05-10
updated: 2026-05-10
tags: [domain, watt, compliance, ofgem]
---

# Watt Compliance Taxonomy

## 8 Standards (Ofgem TPI Code, mapped by Watt)
Defined in `backend/app/watt_compliance/taxonomy.py` as the `Standard` enum:

1. **S1 Identity & Disclosure** — third-party disclosure, broker identification, NOT-an-energy-supplier
2. **S2 Consumer Vulnerability** — protection of vulnerable customers (age, disability, financial hardship, language)
3. **S3 Pre-contract Information** — accurate price quote, contract terms, cooling-off, cancellation
4. **S4 Verbal Contract Integrity** — full script read, no missing required statements
5. **S5 Consent & Authority** — explicit yes/no on offer, decision-maker confirmation, LOA capture
6. **S6 Truth & Fairness** — no mis-selling, no false savings claims, no guaranteed-rates language
7. **S7 Record-keeping & LOA** — Letter of Authority captured + valid for non-E.ON suppliers
8. **S8 Complaints & Ombudsman** — disclosure of complaint process, Ombudsman referral right

## 27 Rejection codes (R01–R27)
Loaded from `taxonomy.WATT_REJECTION_CATEGORIES`. A few keystones:

| Code | Title | Maps to Standard |
|---|---|---|
| R01 | Third-party not disclosed | S1 |
| R02 | Falsely claimed to be supplier | S1 / S6 |
| R03 | Missing broker name | S1 |
| R07 | Missing decision-maker confirmation | S5 |
| R09 | Vulnerable customer not flagged | S2 |
| R12 | Verbal contract incomplete | S4 |
| R14 | Price misquoted | S6 |
| R17 | LOA not captured | S7 |
| R19 | False savings claim | S6 |
| R23 | Customer Yes never recorded | S5 |
| R27 | Ombudsman not disclosed | S8 |

(Full list: `WATT_REJECTION_CATEGORIES` in `taxonomy.py`)

## Severity tiers
```python
class Severity(str, Enum):
    CRITICAL  = "critical"   # Auto-rejection. Must fix or kill deal.
    MAJOR     = "major"      # Reviewer must investigate, default reject.
    MINOR     = "minor"      # Note in audit log, no reject by default.
    INFO      = "info"       # Pass with comment.
```

## Verdict actions
```python
class VerdictAction(str, Enum):
    AUTO_REJECT      = "auto_reject"
    NEEDS_REVIEW     = "needs_review"
    AUTO_APPROVE     = "auto_approve"
    AMENDMENT_NEEDED = "amendment_needed"
```

## Master categories (rejection grouping)
- `R-CONTRACT` (verbal contract issues)
- `R-COMPLIANCE` (script omissions)
- `R-VULNERABILITY` (consumer protection)
- `R-AUTHORITY` (LOA / decision-maker / consent)

## Phrase pre-pass (regex)
`backend/app/watt_compliance/phrase_regex.py` runs cheap regex on EVERY call before the LLM. Catches 15 high-confidence patterns. 9 are Critical-tier. Examples:
- `CP-IDENTITY-FALSE-EMPLOY` — "I work for E.ON" said by a broker
- `CP-PRICE-VAT-INCLUSION-MIS` — VAT-not-clear quote
- `CP-MISSELL-SAVINGS-MISREP` — "you'll save £XYZ" without baseline
- `CP-MISSELL-GUARANTEED-RATES` — "rates are guaranteed for the contract"
- `C2-01` — vulnerability indicators (carer, hospital, hardship)

These are seeded into the LLM prompt as "high-confidence Critical hits" so the analyzer doesn't have to re-discover them.

## Workflow states & transitions
```python
class WorkflowState(str, Enum):
    NEW            # just created
    NOT_STARTED    # in queue
    IN_PROGRESS    # claimed by reviewer
    FIXED          # reviewer signed off Pass
    DEAD           # deal unrecoverable (cancelled / non-contactable)
    BATCHED_TO_PORTAL    # bundled for supplier-portal upload
    SUBMITTED_TO_PORTAL  # uploaded
    FIXED_AND_APPROVED   # supplier confirmed
```

`ALLOWED_WORKFLOW_TRANSITIONS` is a DAG enforcing:
- NEW → NOT_STARTED → IN_PROGRESS → FIXED | DEAD
- FIXED → BATCHED_TO_PORTAL → SUBMITTED_TO_PORTAL → FIXED_AND_APPROVED

See [[02_Domain/Lifecycle]] for the supplier-side phase rule (2-stage E.ON, 3-stage others).
