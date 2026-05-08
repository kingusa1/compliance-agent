# D — Supplier Scripts Analysis

---

## 1. Supplier Matrix

| supplier | script_type | call_class | latest_version | mandatory_phrase_count | distinguishing_features |
|---|---|---|---|---|---|
| BGL (British Gas Lite) | acquisition | gas / elec / dual | V7 (file: `bgl_broker_acquisition_script_v7_`) | ~18 mandatory confirmations | BGL-specific: webchat-only, smart-meter-mandatory, BGL STS product code, "British Gas Lite" brand separation from BG core |
| BGL (British Gas Lite) | acquisition | gas / elec / dual | V6 (file: `correct_-_bgl_acquisition_script`) | ~18 | Near-identical to V7; V7 adds fixed-fee + uplift disclosure side-by-side; **V6 superseded** |
| British Gas | acquisition | gas / elec | V0.2 | ~14 mandatory sections | Scottish Gas variant, Zero Carbon / REGO clause, multi-price step support, no-cooling-off stated verbatim |
| British Gas | renewal / upgrade / deemed | gas / elec | V03 (dated 03/07/23 on cover) | ~12 mandatory sections | Backdating clause, "deemed contract" scenario, start-today vs. future-start branch |
| EDF | acquisition | gas / elec (NHH + AMR) | V11 (ref: H3083.AcquisitionOnline.08/24) | ~15 checklist items + full DDWA script | EBDS discount mention, MyAccount / paperless mandate, Early Termination Fee formula, multi-MPAN billing note, 11 SCMH AMR threshold |
| EDF | acquisition preamble | gas / elec | n/a (undated companion to V11) | ~8 pre-contract read-alouds | TPI-specific preamble only; not a standalone contract script; mentions "Watt" commission |
| E.ON Next | acquisition or renewal | elec (NHH) | undated (file: `eon_next_elec_verbal_contract_script`) | 24 numbered items | Handlebars template variables ({{quote_elec_plan_rate_term}} etc.), multi-register rate table |
| E.ON Next | acquisition or renewal | gas | undated (file: `eon_next_gas_verbal_contract_script`) | 24 numbered items | Same template pattern; EBDS clause (item 15a) present; gas-specific rate table |
| E.ON Next | acquisition or renewal | gas | Jan 2026 (file: `eon_next_gas_verbal_contract_script_tpi_-_jan_26`) | 25 numbered items | Replaces undated gas script; item 9 adds charity/public-sector credit-check carve-out; item 19 adds Microbusiness/Small Business Consumer confirmation; URL updated to www.eonnext.com/policies |
| E.ON Next | acquisition or renewal | elec NHH + HH | Jan 2026 (file: `eon_next_nhh_&_hh_verbal_contract_script_tpi_-_jan_26`) | 26 numbered items | Replaces undated elec script; item 15 adds 100% renewable tariff clause; item 16 adds full ASC/excess-capacity detail block; Microbusiness confirmation item 20 |
| E.ON Next | LOA | gas / elec (any) | V2 (file: `eon_tpi_verbal_loa_script_2`) | 9 named confirmations | Separate LOA call class; 12-month validity; covers termination authority + objection resolution |
| Pozitive | acquisition + renewal | gas / elec | undated (PE suffix) | ~22 confirmations | GDPR T&C consent upfront; Micro Business euro threshold (£1,769,200); MOp/DA/DC explicit; 180-day renewal window; meter-read obligation within ±2 days; paper bill £2 fee; Pozitive portal focus; 30-day termination notice |
| Scottish Power | acquisition (single site) | gas / elec / dual | October 2024 | ~12 mandatory sections | "For Business" tariff: fixed + variable quarterly component; quarterly update dates (Jan/Apr/Jul/Oct); REGO "Renewable For Business" add-on; director personal guarantee clause; SP phone 0345 058 0002 |
| Scottish Power | renewal (single site) | gas / elec / dual | October 2024 | ~12 mandatory sections | Renewal-specific: check no prior renewal already agreed; existing DD confirmation branch |
| Scottish Power | acquisition (multisite) | gas / elec / dual | October 2024 | ~12 mandatory sections + table | Multi-site MPxN table; per-site billing statements; otherwise mirrors single-site acq |

---

## 2. Common Compliance Spine

Phrases / confirmations present in **all or nearly all** supplier scripts:

| # | Compliance checkpoint | Typical phrasing pattern |
|---|---|---|
| 1 | Call recording disclosure | "This call is being recorded for training / compliance / verification purposes" |
| 2 | TPI independence declaration | "I am an independent [Broker/Consultant/TPI] and am not directly employed by [Supplier]" |
| 3 | Authority to act (LOA light) | "Can you confirm you give me authority to work on your behalf / agree this contract?" |
| 4 | Named authorised signatory | Confirm full name + position + authority to enter legally binding contract |
| 5 | Business name + address | Confirm correct legal business name and full site address inc. postcode |
| 6 | Commission / uplift disclosure | Estimated total commission, pence-per-kWh uplift, fixed fee per meter (where applicable) |
| 7 | Meter reference confirmation | Confirm full MPAN (bottom line) and/or MPRN |
| 8 | Price agreement | Confirm unit rate, standing charge (p/kWh and p/day); customer says "yes" |
| 9 | No cooling-off period | "There is no cooling off period" (BG, EDF, SP implied by "legally binding") |
| 10 | Contract duration + end date | State exact end date or term in years/months |
| 11 | Renewal / variable plan fallback | Supplier will write ~60 days before end; auto-rolls to variable rate if no action |
| 12 | Direct Debit setup | Verbal DD mandate: account name, sort code, account number |
| 13 | DD Guarantee offer | "Would you like me to read the DD Guarantee now or in your confirmation letter?" |
| 14 | DD Guarantee text (if requested) | 10-working-days notice; immediate refund if error; right to cancel via bank |
| 15 | Credit check consent | Credit reference agency search, potential fraud reporting |
| 16 | T&C / contract pack | Full T&C in welcome / contract pack within 10 working days |
| 17 | Marketing consent | Opt-in/opt-out for supplier contact by post / email / phone |
| 18 | Final verbal confirmation | "Please confirm with a clear yes you have understood and agree to enter into this contract" |

---

## 3. Per-Supplier Deltas

### BGL (British Gas Lite)

- Product brand is **British Gas Lite** — distinct system from British Gas core; existing BG customers get a transition notice
- Mandatory **webchat-only** service model; no call centres; stated on-script
- **Smart meter** requirement: customer must agree to installation; 90-day window; BGL may transfer to another product if installation fails
- Variable Direct Debit only — no fixed DD option
- Bank statement name: "British Gas Trading Ltd" (not BGL)
- Sole-trader credit check: DOB, home address, electoral roll
- Registration link sent by email; contract only live when BGL processes registration (i.e., verbal ≠ immediate activation)
- Renewal at 60 days; variable plan called out by URL: `www.britishgaslite.co.uk`
- V7 vs V6 difference: V7 discloses both pence-per-kWh uplift **and** a fixed fee per meter; V6 only states pence-per-kWh uplift

### British Gas (core)

- Covers **Scottish Gas** variant via `<British/Scottish Gas>` placeholders
- **Zero Carbon** clause (non-renewable sales): REGO + nuclear declarations
- **100% Renewable** add-on clause for renewable sales
- **Carbon Neutral Gas** clause (10% RGGOs + 90% carbon offset)
- Supply capacity KVA clause (actual vs. unknown DNO value)
- MOP/DA/DC direct-contract discount clause
- Multi-price step support (`until <step date>`)
- Backdating clause (Upgrade/Renewal script only)
- Deemed contract scenario (Upgrade/Renewal script only)
- Tariff name: **Fixed Price Energy Plan**

### EDF

- Tariff name: **Fixed for Business Online**
- Script ref code printed: `H3083.AcquisitionOnline.08/24.v1.E11` — use as version fingerprint
- **EBDS** (Energy Bills Discount Scheme) mention: directs customer to `edfenergy.com/rebates`
- **MyAccount** mandate: customer must manage account fully online
- **Early Termination Fee** formula (consumption × time remaining); explicitly waived on contract-end date
- Late payment charge: **£30 + 4% above BoE rate**
- Multi-MPAN: EDF bills total consumption against one meter on bill — customer must confirm acceptance
- Smart meter: contract may end if not installed within 3 months of eligibility
- AMR gas threshold: **11 SCMH** — requires separate Meter Operator agreement; National Grid installs data-logger
- DD script is a **separate mandatory verbatim section** (`DDWA.230216.v2`) with Q1–Q6 branching logic
- Preamble script is a **companion read-aloud** (not a standalone), references "Watt" commission
- VAT declaration for home/charity supplies via `edfenergy.com`

### E.ON Next

- All scripts use **Handlebars-style template variables** (`{{brokerage_name}}`, `{{quote_elec_plan_rate_term}}` etc.) — CRM populates these
- Rate tables are structured data blocks at end of each script
- **Undated elec script**: item 14 references `{{quote_elec_plan_rate_annualPrice}}`; EBDS item 15a only in gas version
- **Jan 2026 gas script** delta over undated: item 9 carves out charities/public sector from credit checks; item 19 explicitly states Microbusiness/Small Business Consumer classification; URL updated
- **Jan 2026 NHH & HH elec script** delta: item 15 adds 100% renewable tariff clause; item 16 expands ASC/excess-capacity explanation; item 20 adds Microbusiness classification
- ASC charge field: `{{quote_elec_plan_rate_unit_capacity}}` p/kVA/day; excess capacity: `{{elec_excess_capacity_charge}}`
- Debt-block objection: item 20 (undated) / item 22 (Jan 26) — "may object to you changing supplier if there is a debt balance"
- Contract described as **continuous** (no fixed end date stated in script body; ends when no meter points registered or new contract agreed)
- EON Next LOA script (V2): 12-month validity, 9 confirmations, separate call class

### Pozitive

- Opens with: "Is there anything you wish to clarify from **previous conversations**?" — unique welfare/fairness check
- **Micro Business thresholds stated verbatim**: <10 employees, turnover <£1,769,200 (euro threshold converted as of 4 Jul 2018), <100,000 kWh elec, <293,000 kWh gas
- **MOp/DA/DC explicit opt-in**: customer must confirm default or third-party provider
- **Landlord disclosure**: if leased premises, landlord name/email/phone required on recording
- Meter read obligation: ±2 days of supply commencement; failure = additional charges
- **Paper billing charge: £2.00 per invoice**; default is Customer Portal only
- Renewal window: **180 days before end date** (widest of all suppliers); 60-day supplier notice; **30-day written notice** to leave
- Credit vetting result communicated to broker (not directly to customer)
- Direct debit: DD guarantee read **mandatorily** (not optional offer)
- Closing line: "recording forwarded to Pozitive as legally binding contract" — explicit consent for call-as-contract

### Scottish Power

- Tariff name: **For Business** (both acquisition and renewal)
- **Hybrid pricing model**: fixed energy component + variable non-commodity component (network/social/environmental)
- Quarterly update dates stated verbatim: **1 Jan, 1 Apr, 1 Jul, 1 Oct**
- Customer must confirm quarterly price change understanding with a **clear "yes"**
- Commission stated as £ total **and** p/kWh equivalent — both required
- **Renewable For Business** add-on: 100% REGO-matched electricity from SP's UK renewable sources
- **Director personal guarantee** clause: director personally liable for amounts due; SP may require personal guarantee = previous quarter's electricity usage
- Three DD options: Monthly Fixed, Monthly Variable, Quarterly Variable — all available
- Smart meter SMART-functionality-loss warning: explicit customer "yes" required
- Multisite script uses a tabular format (MPxN / site address / EAC / rates / DD per site)
- SP contact number stated on-script: **0345 058 0002**
- Within 10 days of supply start: SP requests meter reading
- Objection right stated: SP can object to early transfer before contract end date

---

## 4. Ingestion Recipe

### Reference

`backend/app/workflows/rag_ingest.py` → `rag_ingest_script_fn` (triggered by `script/changed` event) → calls `ingest_script(script_id, db)` in `backend/app/rag/ingest.py`.

`ingest_script` chunks via `chunk_script(checkpoints)`, embeds via `embed_batch`, writes `ScriptChunk` rows with `script_id`, `script_version_id`, `checkpoint_idx`, `text`, `embedding`.

### Required Schema Additions to ScriptChunk

Add these metadata columns (or store as JSON in an existing `metadata` column):

```
supplier          TEXT NOT NULL   -- e.g. "eon_next", "scottish_power", "edf", "british_gas", "bgl", "pozitive"
script_type       TEXT NOT NULL   -- "acquisition" | "renewal" | "loa" | "upgrade" | "deemed" | "preamble"
call_class        TEXT NOT NULL   -- "gas" | "elec" | "dual" | "nhh" | "hh" | "any"
version           TEXT            -- e.g. "V11", "V7", "Oct2024", "Jan2026"
effective_from    DATE            -- ISO date of version; NULL = undated/legacy
deprecated        BOOLEAN DEFAULT FALSE
```

### Chunk Size Strategy

- Scripts are structured (numbered items / section headers) — use **checkpoint-level chunking** (one chunk per numbered item or section).
- Target: **200–400 tokens per chunk** so a single mandatory phrase fits in one chunk with surrounding context.
- Do not split mid-sentence across chunks; respect the numbered-item boundary.
- For BGL/BG multi-page PDFs: treat each `## Page N` boundary as a natural split point.

### Metadata Fields per Chunk

```json
{
  "supplier": "eon_next",
  "script_type": "acquisition",
  "call_class": "gas",
  "version": "Jan2026",
  "effective_from": "2026-01-01",
  "deprecated": false,
  "item_number": 9,
  "section": "credit_check",
  "mandatory": true
}
```

### Namespace Strategy

Recommended Pinecone / pgvector namespace scheme:

```
scripts:{supplier}:{script_type}:{call_class}
```

Examples:
- `scripts:eon_next:acquisition:gas`
- `scripts:scottish_power:renewal:dual`
- `scripts:bgl:acquisition:dual`
- `scripts:eon_next:loa:any`

Benefits: at retrieval time, namespace can be pre-filtered by `(supplier, script_type, call_class)` tuple resolved from call metadata before vector search, reducing candidate pool and improving precision.

---

## 5. Auto-Detection Strategy

Given an audio transcript, the system picks the applicable supplier script by:

### Step 1 — Hard keyword match (deterministic, run first)

| Signal in transcript | Maps to |
|---|---|
| "British Gas Lite" / "BGL" / "britishgaslite.co.uk" / "webchat" + "no call centres" | BGL acquisition |
| "British Gas" / "Scottish Gas" (without "Lite") | British Gas acquisition or renewal |
| "EDF" / "MyAccount" / "Fixed for Business Online" / "H3083" / "edfenergy.com/rebates" | EDF acquisition |
| "E.ON Next" / "eonnext.com" / "{{brokerage" (template leak) | E.ON Next (fuel TBD from context) |
| "Pozitive" / "pozitive.energy" / "Customer Portal" + "£2" paper bill | Pozitive |
| "ScottishPower" / "0345 058 0002" / "scottishpower.co.uk/forbusiness" / "For Business tariff" + quarterly | Scottish Power |

### Step 2 — Call class detection

| Signal | call_class |
|---|---|
| "electricity" / "MPAN" / "kVA" without gas mention | elec |
| "gas" / "MPRN" / "kWh gas" without electricity mention | gas |
| Both MPAN and MPRN, or "Gas and Electricity" | dual |
| "half hourly" / "HH" / "AMR" / "ASC charge" | nhh or hh |
| No fuel mentioned | dual (default) |

### Step 3 — Script type detection

| Signal | script_type |
|---|---|
| "arrange the switch" / "acquisition" / "new contract" | acquisition |
| "renewal" / "renew your" / "arrange the renewal" / "current one ends" | renewal |
| "letter of authority" / "LOA" / "act on your behalf" without price terms | loa |
| "deemed" / "backdating" / "start date of your contract" (past date) | deemed / upgrade |

### Step 4 — Version resolution

- If supplier + call_class resolved, look up `deprecated=false` ScriptChunk rows for that (supplier, script_type, call_class) combination.
- Use the chunk with highest `effective_from` date.

### Step 5 — Fallback

- If no keyword match: use vector similarity search across all `scripts:*` namespaces with the first 500 tokens of the transcript.
- Return top-3 candidate scripts ranked by cosine similarity; human review flag if score < 0.75.

---

## 6. LOA Handling

### Two LOA artefact types in scope

| Artefact | Type | Call class |
|---|---|---|
| `eon_tpi_verbal_loa_script_2.md` | Verbal LOA script read over phone | `loa` |
| `compliance_xai__little_dowran_farm__letter_of_authority_watt_and_oakdene.md` | Signed PDF LOA (Signable.app) | `loa_document` |

### Verbal LOA (E.ON)

- 9 mandatory confirmations: identity, business, decision-maker status, data access, termination authority, third-party database access, objection resolution, price negotiation
- Valid 12 months from call date
- No pricing terms — this is the distinguishing negative signal (LOA vs. contract script)
- Must be stored as call_class = `loa`, script_type = `loa`

### Signed LOA Document (Little Dowran Farm)

- Issued by Watt Utilities Ltd / Utility Preference Service Ltd via Oakdene Group Ltd
- Covers: electricity, gas, water
- Grants: market search, price negotiation, contract agreement, termination notice, objection resolution, billing dispute management
- Valid 1 year from signing date
- Signed digitally via Signable (audit trail included with fingerprint + timestamps)
- Commission disclosed as uplift in contracted rates
- Must be stored as `document_type = loa_document`, not as a script chunk

### Storage recommendation

- Verbal LOA calls: ingest via `rag_ingest_script_fn` with `script_type="loa"`, `call_class="any"`, namespace `scripts:eon_next:loa:any`
- PDF LOA documents: ingest via `backend/app/rag/ingest_loa.py` (already exists); store with metadata `{customer_name, site_address, tpi_name, valid_until, mpan_mprn, document_fingerprint}`
- LOA detection in transcript: look for "letter of authority" / "authorise [TPI] to request" / "termination notice on your behalf" without any price terms → route to LOA call class, skip contract compliance checks

---

## 7. Script Versioning

### EON Next Gas: undated vs. Jan 2026

| Attribute | Undated | Jan 2026 |
|---|---|---|
| File | `eon_next_gas_verbal_contract_script.md` | `eon_next_gas_verbal_contract_script_tpi_-_jan_26.md` |
| Item count | 24 | 25 |
| Credit check carve-out | No | Yes (item 9: charity/public sector exempt) |
| Microbusiness confirmation | No | Yes (item 19) |
| T&C URL | Not specified | www.eonnext.com/policies |
| `effective_from` | NULL | 2026-01-01 |
| `deprecated` | TRUE | FALSE |

### EON Next Elec: undated vs. Jan 2026

| Attribute | Undated | Jan 2026 |
|---|---|---|
| File | `eon_next_elec_verbal_contract_script.md` | `eon_next_nhh_&_hh_verbal_contract_script_tpi_-_jan_26.md` |
| Item count | 24 | 26 |
| Renewable tariff clause | No | Yes (item 15) |
| ASC detail | Minimal | Full explanation with DNO recalculation clause |
| Microbusiness confirmation | No | Yes (item 20) |
| `effective_from` | NULL | 2026-01-01 |
| `deprecated` | TRUE | FALSE |

### BGL V6 vs. V7

| Attribute | V6 (correct file) | V7 |
|---|---|---|
| Commission disclosure | pence-per-kWh uplift only | Uplift + fixed fee per meter, both disclosed |
| `deprecated` | TRUE | FALSE |

### Deprecation policy

1. When a new version of a supplier script is confirmed (by explicit version marker, date in filename, or supplier notification), set `deprecated = TRUE` on all prior `ScriptChunk` rows for that `(supplier, script_type, call_class)` combination.
2. Deprecated chunks are excluded from auto-detection (filter: `WHERE deprecated = FALSE`).
3. Deprecated chunks are retained for audit — compliance reviews of historical calls must match against the script version active at call time.
4. Version resolution at ingest: compare `effective_from`; if NULL (undated), treat as superseded by any dated version for the same (supplier, script_type, call_class).
5. Emit a `script/deprecated` Inngest event when deprecating to allow downstream audit-trail hooks.
6. Never hard-delete deprecated chunks — Ofgem / FCA audit trails require retention for a minimum of 6 years.

### British Gas Upgrade/Renewal V03 dating note

- V03 cover page shows `3253/07/23` — interpreted as version `03`, date `07/2023`.
- More recent Scottish Power scripts (Oct 2024) and EDF (Aug 2024) post-date it.
- No newer British Gas renewal script provided; treat V03 as current until superseded.
- Set `effective_from = 2023-07-01` for all BG Upgrade/Renewal chunks.
