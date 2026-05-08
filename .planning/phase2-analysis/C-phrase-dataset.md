# C-Phrase Dataset — Compliance Spec Analysis

**Source:** `compliance_xai__watt_ai_phrase_detection_dataset_1.md`
**Purpose:** Convert 120-entry phrase-detection dataset into actionable compliance agent spec.

---

## 1. Dataset Shape

Each row represents a single compliance risk event observable on a UK utilities sales call. Rows are grouped by:

- **Stage** — `lead_generation` (76 rows) or `verbal_confirmation` (44 rows)
- **Category** — 11 categories across both stages (see §2)
- **Severity** — `Critical` / `High` / `Medium` (maps directly to action)

Recommended developer fields defined in the dataset header:
`rule_id | call_stage | category | severity | trigger_phrase_or_pattern | detection_type | why_flagged | approved_alternative | action`

Severity-to-action mapping from the source:
- `Critical` → block / escalate
- `High` → manual review
- `Medium` → coaching

Total examples: 120 across 11 categories.

---

## 2. Phrase Categories

| # | Category | Stage | Count | Primary Risk Type |
|---|----------|-------|-------|-------------------|
| 1 | Identity and transparency | Lead gen | 20 | Regulatory / misleading identity |
| 2 | Qualification and authority | Lead gen | 12 | Unsuitable sale / consent |
| 3 | Pricing and savings claims | Lead gen | 20 | Unsubstantiated financial claims |
| 4 | Market comparison and search scope | Lead gen | 12 | False competitive claims |
| 5 | Pressure and vulnerability | Lead gen | 12 | Coercion / vulnerable customer |
| 6 | Supplier and industry claims | Lead gen | 12 | Misinformation / scare tactics |
| 7 | Script framing and legal nature | Verbal conf. | 5 | Legal significance downplayed |
| 8 | Commission disclosure | Verbal conf. | 5 | Remuneration transparency |
| 9 | Contract terms | Verbal conf. | 8 | Missing/misread key terms |
| 10 | Understanding and consent | Verbal conf. | 8 | Coerced or ambiguous consent |
| 11 | Script delivery and call quality | Verbal conf. | 6 | Enforceability / clarity |

---

## 3. Per-Category Detail

### Cat-1 · Identity and Transparency
**Detection mode:** Hybrid — regex pre-pass for known trigger strings, LLM for semantic variants.

Representative triggers:
- FAIL: `I'm from your energy provider` — supplier impersonation (Critical)
- FAIL: `I'm calling from E.ON` when caller is a broker (Critical)
- FAIL: `We are your renewal department` — false authority (High)
- FAIL: caller states first name only, no business name (Critical)
- PASS: `Hi, it's [name] calling from Watt Utilities, an energy consultancy`

**Verdict when triggered:** Absence of company name + role in first 20 seconds → escalate. Impersonation phrases → block + escalate.

---

### Cat-2 · Qualification and Authority
**Detection mode:** LLM — semantic pattern needed to detect downstream continuation despite objection cues.

Representative triggers:
- FAIL: `I'll just get this renewed for you` — assumed authority (High)
- FAIL: agent continues after `my partner handles that` (High)
- FAIL: customer mentions prepayment meter, agent continues (Critical)
- FAIL: no consent before pricing handover (High)
- PASS: `Are you the person who deals with the business energy contract?`

**Verdict:** Continuing after authority warning → review. Unsuitable meter type → escalate.

---

### Cat-3 · Pricing and Savings Claims
**Detection mode:** Regex pre-pass (absolute-guarantee patterns) + LLM for qualified savings language.

Representative triggers:
- FAIL: `We will get you cheaper rates` — guaranteed saving (Critical)
- FAIL: `Prices will definitely go up` — unsubstantiated forecast (Critical)
- FAIL: `I can guarantee this is the cheapest you'll get` (Critical)
- FAIL: `You'd be mad not to take this now` — pressure (High)
- PASS: `We can review available options and compare rates, but savings are not guaranteed`

**Verdict:** Guarantee language → escalate. Pressure + urgency combo → review.

---

### Cat-4 · Market Comparison and Search Scope
**Detection mode:** Regex for absolute-breadth phrases, LLM for scope-misrepresentation.

Representative triggers:
- FAIL: `I have checked the whole market` (High)
- FAIL: `We work with all suppliers` — false panel breadth (High)
- FAIL: `Nobody can beat this` (High)
- FAIL: `We've searched everywhere` (Medium)
- PASS: `best rate we can obtain from the suppliers we approached`

**Verdict:** Whole-market claims without documented panel → review. False panel breadth → review/escalate.

---

### Cat-5 · Pressure and Vulnerability
**Detection mode:** LLM (primary) — behavioural signals, interruption count, customer objection markers.

Representative triggers:
- FAIL: customer says `not interested`, agent continues hard sell (Critical)
- FAIL: customer mentions illness/language barrier, agent continues (Critical)
- FAIL: `I'm only trying to help you save money` after refusal — guilt language (High)
- FAIL: `please remove me` not acknowledged — no suppression workflow (High)
- PASS: respects objection or offers one brief final question with permission

**Verdict:** Ignored vulnerability indicators → escalate. No suppression after opt-out → review.

---

### Cat-6 · Supplier and Industry Claims
**Detection mode:** Regex for named-supplier attack phrases, LLM for context-sensitive claims.

Representative triggers:
- FAIL: `British Gas are about to put their prices up` — unsourced (High)
- FAIL: `The regulator is forcing everyone to move now` — false regulatory claim (High)
- FAIL: `The ombudsman is flooded with complaints against them` — unsourced fear (High)
- FAIL: `This contract is green / fully renewable` without substantiation (High)
- PASS: use only public, evidenced information; cite basis internally

**Verdict:** Invented regulatory urgency → escalate. Unsubstantiated environmental claim → review.

---

### Cat-7 · Script Framing and Legal Nature
**Detection mode:** Regex for exact downplaying phrases, LLM for paraphrase detection.

Representative triggers:
- FAIL: `just a formality` describing the confirmation script (Critical)
- FAIL: `we're just locking prices in` without reference to contract (High)
- FAIL: wrong script used for supplier/situation (Critical)
- FAIL: agent paraphrases large script sections (High)
- PASS: `This is a legally binding verbal contract confirmation`

**Verdict:** Formality downplay → escalate. Script mismatch → escalate.

---

### Cat-8 · Commission Disclosure
**Detection mode:** Regex for explicit no-fee claims, LLM to verify presence of disclosure language.

Representative triggers:
- FAIL: no mention of commission embedded in rates (Critical — absence trigger)
- FAIL: `You don't pay anything for our service` (High)
- FAIL: `We are paid by the supplier` without rate-impact explanation (High)
- FAIL: commission wording delivered too fast to be intelligible (Medium)
- PASS: `Watt receives commission from the supplier and it is included in the rates quoted`

**Verdict:** Missing disclosure → escalate. Evasive answer under challenge → review.

---

### Cat-9 · Contract Terms
**Detection mode:** Regex for absence patterns (supplier name, term, unit rate, standing charge), LLM for mismatch detection against contract data.

Representative triggers:
- FAIL: supplier name not clearly stated (Critical — absence)
- FAIL: contract term/duration omitted (Critical — absence)
- FAIL: unit rate not stated or unclear (Critical — absence)
- FAIL: `Fixed` described as meaning nothing can ever change — blanket inaccuracy (High)
- PASS: all four principal terms stated clearly and accurately

**Verdict:** Any missing principal term → escalate. Rate mismatch vs. contract data → separate discrepancy flag.

---

### Cat-10 · Understanding and Consent
**Detection mode:** LLM (primary) — ambiguous consent language, agent-answers-for-customer pattern.

Representative triggers:
- FAIL: no clear `yes` at script end (Critical — absence)
- FAIL: `I think so` / `probably` treated as full consent (Critical)
- FAIL: agent answers script questions for the customer (Critical)
- FAIL: customer says `what does that mean?`, agent continues (Critical)
- PASS: clear, audible affirmative; customer answers for themselves

**Verdict:** Ambiguous or coached consent → escalate. Known confusion ignored → escalate.

---

### Cat-11 · Script Delivery and Call Quality
**Detection mode:** LLM / audio analytics — pace analysis, silence detection, tone scoring.

Representative triggers:
- FAIL: script read at excessive speed with minimal pauses (High)
- FAIL: agent skips sections to save time (High)
- FAIL: agent interrupts customer answers during the script (High)
- FAIL: no closing recap of next steps (High)
- PASS: full approved script read at measured pace; customer responses uninterrupted

**Verdict:** Skipped sections → escalate. Excessive speed / interruptions → review.

---

## 4. Phrase Taxonomy Table

> This table is the test-fixture seed. One row per distinct trigger. `verdict_action` values: `ESCALATE | REVIEW | COACH`.

| id | category | trigger | verdict_action | severity | example_pass | example_fail |
|----|----------|---------|----------------|----------|--------------|--------------|
| C1-01 | identity_transparency | No company name in first 20 seconds | ESCALATE | Critical | `Hi, it's [name] from Watt Utilities` | Caller gives only first name |
| C1-02 | identity_transparency | `I'm from your energy provider` | ESCALATE | Critical | `I'm from Watt Utilities, an independent energy consultancy` | `I'm from your energy provider` |
| C1-03 | identity_transparency | `I'm calling from E.ON` (broker caller) | ESCALATE | Critical | `Watt Utilities is a broker, not your supplier` | `I'm calling from E.ON` |
| C1-04 | identity_transparency | `We are your renewal department` | REVIEW | High | `We help businesses review renewal options` | `We are your renewal department` |
| C1-05 | identity_transparency | `We said we'd call you back` (no logged contact) | REVIEW | High | `I'm calling regarding your business energy renewal window` | `We said we'd call you back` |
| C1-06 | identity_transparency | Caller says `survey` or `account check` as purpose | ESCALATE | High | States real sales purpose accurately | `Just doing an account check` |
| C2-01 | qualification_authority | No decision-maker confirmation question | REVIEW | Critical | `Are you the person who deals with the business energy contract?` | Proceeds without authority check |
| C2-02 | qualification_authority | Continues after `my partner handles that` | REVIEW | High | Offers callback or stops | Continues pitch regardless |
| C2-03 | qualification_authority | Customer mentions prepayment meter, agent continues | ESCALATE | Critical | Stops and explains separate handling | Continues as normal |
| C2-04 | qualification_authority | `I'll just get this renewed for you` | REVIEW | High | Uses conditional wording until authority confirmed | `I'll just get this renewed for you` |
| C2-05 | qualification_authority | No consent before passing to pricing team | REVIEW | High | `Can I connect you to our pricing team — is that okay?` | Transfers without asking |
| C3-01 | pricing_savings | `We will get you cheaper rates` | ESCALATE | Critical | `We can compare rates but savings are not guaranteed` | `We will get you cheaper rates` |
| C3-02 | pricing_savings | `This will save you money` as stated fact | ESCALATE | Critical | `Depending on your usage, you may see a saving` | `This will save you money` |
| C3-03 | pricing_savings | `Best price in the market` | ESCALATE | Critical | `Best rate from our panel of suppliers` | `Best price in the market` |
| C3-04 | pricing_savings | `Prices will definitely go up` | ESCALATE | Critical | `Energy prices have historically been volatile` | `Prices will definitely go up` |
| C3-05 | pricing_savings | `I can guarantee this is the cheapest you'll get` | ESCALATE | Critical | Removes guarantee language entirely | `I can guarantee this is the cheapest` |
| C3-06 | pricing_savings | `Lock this in now before the market jumps tomorrow` | REVIEW | High | References genuine expiring pricebook only | `Lock in now before the market jumps` |
| C3-07 | pricing_savings | `You'd be mad not to take this now` | REVIEW | High | Presents options neutrally | `You'd be mad not to take this` |
| C3-08 | pricing_savings | `This is fixed so nothing else can change` | REVIEW | High | Describes fixed elements accurately | `Nothing else can change` |
| C4-01 | market_comparison | `I have checked the whole market` (panel only used) | REVIEW | High | `Best rate from the suppliers we approached` | `I've checked the whole market` |
| C4-02 | market_comparison | `We work with all suppliers` | REVIEW | High | States actual panel size | `We work with all suppliers` |
| C4-03 | market_comparison | `Nobody can beat this` | REVIEW | High | Comparative wording where evidenced | `Nobody can beat this` |
| C4-04 | market_comparison | `We've searched everywhere` | COACH | Medium | Uses precise scope language | `We've searched everywhere` |
| C4-05 | market_comparison | `The regulator is forcing everyone to move now` | ESCALATE | High | Never invents regulatory urgency | `Regulator is forcing everyone to move` |
| C5-01 | pressure_vulnerability | Customer says `not interested`, agent continues | ESCALATE | Critical | Respects objection; ends or asks one final question | Continues hard sell |
| C5-02 | pressure_vulnerability | Customer mentions illness / language barrier, agent continues | ESCALATE | Critical | Pauses and arranges alternative contact | Continues regardless |
| C5-03 | pressure_vulnerability | Customer confused, agent pushes for transfer | ESCALATE | Critical | Slows down, clarifies, or stops | `Don't worry, just let me put you through` |
| C5-04 | pressure_vulnerability | `I'm only trying to help you save money` after refusal | REVIEW | High | Accepts refusal respectfully | Guilt language after refusal |
| C5-05 | pressure_vulnerability | `please remove me` not acknowledged | REVIEW | High | Triggers suppression workflow immediately | Ignored opt-out |
| C5-06 | pressure_vulnerability | Threat-like urgency about missing prices | ESCALATE | Critical | Stick to factual options only | Threat-framed urgency wording |
| C6-01 | supplier_industry | Named-supplier price-rise claim without source | REVIEW | High | Uses only public evidenced information | `British Gas are about to put prices up` |
| C6-02 | supplier_industry | `Your supplier is terrible` / derogatory comments | REVIEW | High | Factual, neutral commentary | `Your supplier is terrible` |
| C6-03 | supplier_industry | False regulatory urgency (`regulator forcing move`) | ESCALATE | High | Never invents regulatory mandate | `Regulator forcing everyone to move` |
| C6-04 | supplier_industry | `This contract is green / fully renewable` without proof | REVIEW | High | Only make sustainability claims when evidenced | `Fully renewable` without substantiation |
| C7-01 | script_framing | `just a formality` describing the VC script | ESCALATE | Critical | `This is a legally binding verbal contract confirmation` | `Just a formality` |
| C7-02 | script_framing | `just locking prices in` without mentioning contract | REVIEW | High | States it confirms a binding contract | `Just locking prices in` |
| C7-03 | script_framing | Wrong script for supplier / situation | ESCALATE | Critical | Correct script selected for supplier | Script mismatch |
| C8-01 | commission_disclosure | No commission disclosure in VC | ESCALATE | Critical | Explains commission embedded in rates | No mention at all |
| C8-02 | commission_disclosure | `You don't pay anything for our service` | REVIEW | High | `Commission is included in the unit rate` | `You don't pay anything for our service` |
| C8-03 | commission_disclosure | `We are paid by the supplier` — no rate-impact mention | REVIEW | High | Clarifies rate-impact of commission | `We are paid by the supplier` (alone) |
| C9-01 | contract_terms | Supplier name not clearly stated in VC | ESCALATE | Critical | Supplier name stated clearly | Name omitted or mumbled |
| C9-02 | contract_terms | Contract term / duration omitted | ESCALATE | Critical | Term stated in months/years | Duration omitted |
| C9-03 | contract_terms | Unit rate not stated or unclear | ESCALATE | Critical | Unit rate read clearly and accurately | Rate omitted |
| C9-04 | contract_terms | Standing charge omitted where applicable | ESCALATE | Critical | Standing charge stated | Standing charge omitted |
| C9-05 | contract_terms | Rates in script do not match contract data | ESCALATE | High | Script values match contract exactly | Mismatched values read |
| C9-06 | contract_terms | `Fixed` = absolutely nothing can change | REVIEW | High | Describes only what is genuinely fixed | Blanket fixed guarantee |
| C10-01 | understanding_consent | No clear `yes` or equivalent at script end | ESCALATE | Critical | Clear, audible affirmative obtained | No audible agreement |
| C10-02 | understanding_consent | `I think so` / `probably` treated as full consent | ESCALATE | Critical | Clarifies until explicit agreement | Accepts ambiguous response |
| C10-03 | understanding_consent | Agent answers script questions for customer | ESCALATE | Critical | Customer answers for themselves | Agent answers on behalf |
| C10-04 | understanding_consent | `What does that mean?` ignored, agent continues | ESCALATE | Critical | Pauses and explains before continuing | Continues over confusion |
| C10-05 | understanding_consent | Customer indicates someone else signs contracts, agent proceeds | ESCALATE | Critical | Stops and involves authorised person | Proceeds without authority |
| C10-06 | understanding_consent | Repeatedly leading prompts for same answer | REVIEW | High | Neutral questioning used | Repeated leading prompts |
| C11-01 | script_delivery | Script skipped sections | ESCALATE | High | Full approved script read | Sections skipped |
| C11-02 | script_delivery | Script read at excessive speed | REVIEW | High | Pace thresholds met; pauses observed | Rushed delivery |
| C11-03 | script_delivery | Agent interrupts customer answers during script | REVIEW | High | Full customer responses allowed | Agent talks over customer |
| C11-04 | script_delivery | No closing recap of next steps | REVIEW | High | End-of-call recap provided | No next-steps summary |

---

## 5. Coverage Gaps

The following risks are absent or underrepresented for a UK utilities sales compliance system:

1. **Direct Debit / payment method framing** — no rules on misrepresenting payment collection methods or DD implications.
2. **Cooling-off period disclosure** — no check that the 14-day statutory cooling-off right is stated during or after VC.
3. **Early Termination Charges (ETCs)** — no rule requiring ETCs to be mentioned at pre-sale or VC stage.
4. **Data protection / GDPR consent** — no check that marketing consent and data use are explained before lead qualification questions.
5. **Complaint-handling signpost** — no rule requiring the agent to mention the complaints process or Ombudsman route.
6. **Annual Quantity (AQ) / consumption confirmation** — no check that the agent confirms or uses accurate usage data before quoting.
7. **Deemed / out-of-contract rate comparison** — missing rules for when a customer is already on a deemed rate and the agent must explain this.
8. **Domestic vs. microbusiness threshold** — no rule for agents to check annual spend or consumption against the Ofgem microbusiness definition before applying SME protections.
9. **Third-Party Intermediary (TPI) registration** — no check for agent claims about Ofgem TPI code compliance.
10. **Multi-site handling** — no rules for calls involving customers with multiple supply points, where quoting on one site without scoping all sites can mislead.

---

## 6. Implementation Notes

### Tiered Smart Agent Architecture

The existing system (`backend/app/analysis.py`) implements:
- **Gemini 2.0 Flash** — first-pass agent (feature-flagged via `USE_AGENT_ANALYZER`)
- **Claude Sonnet 4.6** — main analysis via OpenRouter or direct Anthropic
- **GPT-4o** — fallback provider

Recommendation per category:

| Category | Recommended Mode | Rationale |
|----------|-----------------|-----------|
| Identity and transparency | **Regex pre-pass → LLM escalate** | Impersonation strings are exact; semantic variants handled by Gemini Flash first pass |
| Qualification and authority | **LLM (Gemini Flash)** | Behavioural pattern; no reliable regex. Flash cost-effective at high call volume |
| Pricing and savings claims | **Regex pre-pass → LLM** | Absolute-guarantee phrases (`guarantee`, `will save`, `definitely`) are exact-matchable; nuanced savings language needs LLM |
| Market comparison | **Regex pre-pass → LLM** | `whole market`, `all suppliers`, `nobody can beat` are exact triggers; scope-misrepresentation needs LLM |
| Pressure and vulnerability | **LLM (Claude Sonnet)** | Vulnerability detection requires tone inference and dialogue-state tracking; route to Sonnet directly |
| Supplier and industry claims | **Regex (named-supplier) + LLM** | Named-supplier attack phrases (`British Gas are about to`) can be regex-seeded; speculative claims need LLM |
| Script framing | **Regex** | `just a formality`, `just locking prices in` are near-exact; low variance |
| Commission disclosure | **Regex (absence check) + LLM** | Check for presence of commission disclosure string; LLM validates intelligibility |
| Contract terms | **Structured comparison** | Best handled by extracting entities (supplier, term, rate, standing charge) and comparing to contract record — not a simple regex or LLM prompt |
| Understanding and consent | **LLM (Claude Sonnet)** | Ambiguous consent requires semantic reasoning; escalate to Sonnet |
| Script delivery | **Audio analytics + LLM** | Pace and interruption are audio-layer features; Deepgram diarisation output + LLM for pattern scoring |

### Integration Points Already Wired

- Gemini Flash (`_call_gemini()`) — ready for first-pass cheap checks (Cat-1 regex pre-filter, Cat-3 guarantee scan).
- Claude Sonnet via OpenRouter (`_call_openrouter()`) — ready for high-stakes semantic analysis (Cat-5, Cat-10).
- Deepgram nova-3 (`transcription.py`) — diarisation output available for Cat-11 pace/interruption detection.
- pgvector (`agent_learnings`) — semantic search on prior flagged calls; relevant for Cat-4 scope claims and Cat-6 supplier claims where historical context helps.

### Suggested Regex Pre-Pass Patterns (seed list)

```
guarantee_exact:    \b(guarantee|guaranteed)\b
savings_absolute:   \b(will save|save you money|cheapest you.ll get)\b
market_scope:       \b(whole market|all suppliers|searched everywhere|nobody can beat)\b
identity_fail:      \b(from (E\.ON|British Gas|your (supplier|provider)))\b
script_downplay:    \b(just a formality|just lock(ing)? prices in)\b
commission_absent:  absence of \b(commission|included in (the|your) rate)\b within VC window
```

These six patterns can run as a synchronous pre-pass in `analysis.py` before any LLM call, short-circuiting to `ESCALATE` for the clearest violations without consuming LLM tokens.
