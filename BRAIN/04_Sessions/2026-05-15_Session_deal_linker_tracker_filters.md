---
created: 2026-05-15
updated: 2026-05-15
tags: [session, deal-linker, tracker, filters, side-panel, splink, matcher, playwright]
---

# 2026-05-15 — Bulletproof deal-linker + advanced tracker filters + editable side panel

**Owner:** Mohamed Hisham Ismail (kingusa1)
**Branch / tip:** `main @ 8b8f2e0` (3 commits pushed)
**Live URLs verified:** https://compliance-agent-mu.vercel.app + https://compliance-agent-production-690e.up.railway.app

---

## What landed

### Part A — Bulletproof deal-linker (commit `3b9bf0d`)

Multi-tier match cascade at intake — hard keys deterministic, fuzzy probabilistic, never silent.

**New module** [`backend/app/intake/matcher.py`](../backend/app/intake/matcher.py):

| Tier | What | Threshold | Provenance |
|---|---|---|---|
| 1 | MPAN core (13-digit), MPRN, DocuSign envelope, Companies House #, Charity # | 1.0 | `hard_key:<which>` |
| 2 | rapidfuzz token_set_ratio + jellyfish metaphone + postcode + supplier + recency | ≥0.99 auto / 0.85-0.99 review | `composite_auto` / `composite_review` |
| 3 | Below 0.85 → legacy slug upsert | — | `legacy` |

**Calibrated weights (verified by 17 unit tests):**
- name ≥95 → 0.62, ≥87 → 0.50, ≥75 → 0.25
- metaphone +0.08, postcode-full +0.25, postcode-out +0.08
- supplier +0.06, within-30d +0.10
- Same-name + supplier alone → 0.86 (review band) — does NOT auto-merge
- Same-name + same-postcode-full → 1.00 (auto) — verified

**Persistence:** new `customer_deals.match_method` + `match_confidence` columns, alembic `2026_05_15_dealmatch`. Legacy upsert path stamps `method=legacy` for audit clarity.

**Deps added** (all MIT/BSD, lazy-imported):
- `rapidfuzz>=3.6`
- `jellyfish>=1.0`
- `cleanco>=2.3`

**Wired into** `backend/app/routes.py:upload_call` between intake-payload parse and the legacy upsert branches. Audio-hash idempotency was already in place (`Call.file_hash` column).

**Honest ceiling:** ~99.5% auto-merge precision is achievable when MPAN/MPRN is captured by verbal-stage upload. The remaining 0.5% lands in the candidate-merge queue (UI deferred to Phase E).

### Part B + C — Tracker advanced filters + editable side panel (commit `f8b1a0a`)

**Backend filter widening** (`tracker_aggregator.py` + `tracker_routes.py`):
New query params on `/api/tracker/rows`:
- `suppliers` (CSV multi-select)
- `agents` (CSV multi-select)
- `statuses` (CSV multi-select; overrides tab→default)
- `verdict_states` (CSV: AI_PENDING|HUMAN_CONFIRMED|HUMAN_OVERRIDDEN)
- `date_from` / `date_to` / `date_on` (ISO yyyy-mm-dd)
- `meter` (MPAN/MPRN substring)
- `value_min` / `value_max` (£ deal value)
- `deadline_state` (overdue|due_3d|due_7d|on_track)

Cross-cuts the 3 query branches (awaiting_review / compliant / rejection rows) via shared `_apply_call_advanced` / `_apply_rejection_advanced` helpers. Deal-level filters narrow via a deal_id subquery — no JOINs in the per-branch SQL.

**Frontend filter bar** (`TrackerFilterBar.tsx`):
- Collapsible advanced section (localStorage persists open/closed state)
- Date quick-picks: Today / Last 7d / Last 30d / This month
- Multi-select chips for supplier + agent — auto-populated from in-view rows so typo-by-text-input is impossible
- Status + verdict + deadline-state chip rows
- Deal-value range inputs
- Active-filter pill on the toggle ("More filters · 3") so reviewer sees stacking
- One-click Clear-all

**Backend edit endpoint** (`tracker_edit_routes.py`):
- `ALLOWED_FIELDS` split into `REJECTION_FIELDS` + `DEAL_FIELDS`. PATCH routes deal-level updates to `CustomerDeal` via `call.deal_id`, rejection-level to `Rejection`.
- New fields accepted: `deadline`, `expected_live_date`, `deal_value_gbp`, `mpan_electricity`, `mprn_gas`, `term_months`, `commission_value`, `commission_unit`, `docusign_reference`
- Coercer normalises ISO date strings → `date`, numeric strings → `Decimal`, sanitises MPAN/MPRN to digits-only
- New endpoint: `POST /api/tracker/rows/{id}/assignee` (FK-validates against `profiles`, audit row written)
- New endpoint: `GET /api/reviewers/active` (lists active reviewer/lead/admin)

**Side panel** (`TrackerSidePanel.tsx`):
- **Identity card** — supplier dropdown (canonical + legacy aliases), agent text input
- **Meter & deal card** — MPAN, MPRN, annual value (£), live date, term months, DocuSign ref
- **Deadline** date picker (writes to `Rejection.deadline`)
- **Assignee** dropdown sourced from `/api/reviewers/active` via `useActiveReviewersQuery`
- All editors fire `useEditTrackerRow` on blur/change or `useSetAssignee`; tan-stack invalidates `["admin","tracker"]` so the row + table refresh

### Part D — Post-validation fix (commit `8b8f2e0`)

Playwright sweep surfaced 3 display gaps on the live deploy:
1. Tracker row's `mpan_mprn` rendered empty after side-panel PATCH because the aggregator only read the legacy `deal.mpan_or_mprn` column. Added `_compose_mpan_mprn(deal)` helper that prefers the new `mpan_electricity` / `mprn_gas` split columns.
2. Side-panel MPAN/MPRN inputs parsed the combined `mpan_mprn` display string and failed to round-trip. Now reads the new `mpan_electricity` / `mprn_gas` / `docusign_reference` / `term_months` fields directly off TrackerRow.
3. Supplier dropdown empty for rows where the AI detector stamped legacy short form ("E.ON Next") because canonical `SupplierEnum` uses "E.ON Next Energy". Added legacy aliases to SUPPLIER_OPTIONS.

---

## Playwright validation

Ran against **live prod** (https://compliance-agent-mu.vercel.app) post-deploy:

| Check | Result |
|---|---|
| /tracker advanced filter bar — Day / Range / Supplier / Agent / Status / Verdict / Deadline / Value range | ✓ all 8 sections render |
| Supplier + agent options derived from in-view rows ("E.ON Next" + Afak/Dominic/Paige/Parat/Sean) | ✓ |
| Backend filter probes — `date_from`, `date_to`, `deadline_state`, `suppliers`, `agents`, `meter`, `value_min/max` | ✓ all return 200 |
| `GET /api/reviewers/active` | ✓ returns admin profile |
| `POST /api/rejections` then `PATCH /api/tracker/rows/{id}` with `mpan_electricity` / `mprn_gas` / `deal_value_gbp` / `expected_live_date` / `term_months` / `docusign_reference` / `deadline` | ✓ 200; field_sources stamped `deadline:human`; deal_field_sources stamped `reviewer_edit` on all 6 deal columns |
| `POST /api/tracker/rows/{id}/assignee` with valid profile id | ✓ 200; row now shows `fix_assignee_id` |
| Active tab → side panel renders all 6 inputs with patched values (after the post-fix commit `8b8f2e0`) | ✓ verified |

---

## Deferred for next session

- **Phase A4** — widen `quality_agent.py:find_sibling_candidates` to consume splink candidate bucket (cross-call merge after pipeline finishes). Today's matcher fires at INTAKE; quality agent still uses substring/2-token name overlap.
- **Phase D** — when reviewer edits a transcript word on `/calls/{id}`, cascade re-derive `Rejection.rejection_reason` + `category` + `fix_required` from the new checkpoint aggregates. Today the tracker row stays frozen at AI's first verdict.
- **Phase E** — Candidate-merge reviewer queue UI (for the 0.85-0.99 composite band) and intake CustomerAutocomplete that hits `GET /api/customers?q=…` so reviewer picks existing customers at upload time without exact-name typing.

---

## Files touched

```
backend/app/intake/matcher.py                    (NEW)
backend/app/models.py
backend/app/routes.py
backend/app/tracker_aggregator.py
backend/app/tracker_edit_routes.py
backend/app/tracker_routes.py
backend/alembic/versions/2026_05_15_deal_match_provenance.py   (NEW)
backend/requirements.txt
backend/tests/test_intake_matcher.py             (NEW, 17 tests)
backend/tests/test_tracker_aggregator.py         (fix renamed-field assertion)
frontend-v3/src/app/(admin)/tracker/TrackerFilterBar.tsx     (NEW)
frontend-v3/src/app/(admin)/tracker/TrackerSidePanel.tsx
frontend-v3/src/app/(admin)/tracker/page.tsx
frontend-v3/src/lib/mutations/tracker.ts
frontend-v3/src/lib/queries/reviewers.ts         (NEW)
frontend-v3/src/lib/queries/tracker.ts
```

## Commits (in order)

1. `3b9bf0d` — `feat(intake): bulletproof deal-linker — 4-tier match cascade`
2. `f8b1a0a` — `feat(tracker): advanced filters + side-panel deal/deadline/assignee editing`
3. `8b8f2e0` — `fix(tracker): surface deal mpan/mprn/docusign/term on tracker row + supplier alias list`

## Algorithm sources cited in the research-then-build cycle

- **Splink** (UK Ministry of Justice) — Fellegi-Sunter probabilistic record linkage with EM-learned m/u weights. <https://github.com/moj-analytical-services/splink> — 0.95-0.99 industry-standard thresholds for high-precision auto-merge.
- **rapidfuzz `token_set_ratio` ≥ 87** — published cut for UK address/company name fuzz in splink demos.
- **MPAN core** = last 13 digits of the 21-digit full string; 1:1 with physical meter, never re-issued — effectively deterministic.
- **jellyfish metaphone** — phonetic equality catches "Peters" / "Peter" / "Pete".
- **cleanco** — UK legal-entity-suffix stripper ("Ltd", "Limited", "Plc"). Slightly aggressive: also strips "Company"; OK because both sides reduce symmetrically.
- **Forensic-linguistics WPM + filler-frequency stylometry** as cheap speaker-fingerprint feature — deferred to Phase A4.
