---
created: 2026-05-10
updated: 2026-05-10
tags: [domain, suppliers]
---

# Suppliers

## The 6 canonical suppliers
Defined in `backend/app/watt_compliance/taxonomy.py` as the `Supplier` enum:

| Enum | `value` (DB) | Canonical label (UI) |
|---|---|---|
| `Supplier.BGL` | `bgl` | `British Gas Lite (BGL)` |
| `Supplier.BRITISH_GAS` | `british_gas` | `British Gas` |
| `Supplier.EDF` | `edf` | `EDF` |
| `Supplier.EON_NEXT` | `eon_next` | `E.ON Next` |
| `Supplier.POZITIVE` | `pozitive` | `Pozitive Energy` |
| `Supplier.SCOTTISH_POWER` | `scottish_power` | `Scottish Power` |

`SUPPLIER_LABELS` dict maps enum → UI label.

## Alias map (free-text → canonical)
`backend/app/watt_compliance/script_detect.py:_SUPPLIER_ALIAS_MAP` resolves:

```
"bgl" / "bg lite" / "british gas lite"          → BGL
"bg core" / "bg business" / "british gas"        → BRITISH_GAS
"british gas buisness"  (typo seen in tracker)   → BRITISH_GAS
"eon" / "e.on" / "e on" / "eon next" /
"e.on next" / "e.on next energy" /
"e.on energy solutions [ltd]"                    → EON_NEXT
"edf" / "edf energy"                             → EDF
"pozitive" / "pozitive energy"                   → POZITIVE
"sp" / "scottish power" / "scottishpower"        → SCOTTISH_POWER
```

`canonicalize_supplier(raw)` lowercases + strips punctuation + tries the map, then falls back to progressive trailing-token strips. Returns `None` for unknown.

## Why this matters (gotcha that ate hours today)
The seed scripts in the DB used `supplier_name = "EON"` (literally, no period). The LLM detected `"E.ON Next"`. ILIKE matching `'%E.ON Next%'` against `'EON'` returns no rows.

**Fix shipped 2026-05-10:** `pipeline.py` now pulls all active scripts and matches via `canonicalize_supplier(s.supplier_name) == canon` (Python-side enum compare). ILIKE is the FALLBACK, not the primary path.

## Sibling-supplier inheritance
A "Closer" or "LOA" call often skips the `"with E.ON"` intro because the customer already knows. `detect_supplier` returns `Unknown`. Two passes recover:

1. Same-deal sibling (cheap, certain): if any other call on the same `deal_id` has supplier set, inherit.
2. Cross-deal human-name sibling: find any call sharing the same human `customer_name` (bidirectional substring + token-overlap) that has a supplier; inherit.

Code: `pipeline.py:_step_detect_metadata` ~lines 380-440.

See also [[03_AI_Pipeline/Quality_Agent]] which is the *third* layer that catches everything heuristics miss.
