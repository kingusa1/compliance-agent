"""Per-supplier supplemental word_boost terms.

Keyed by the supplier name as it appears in `Script.supplier_name`. Lookup
is exact-match in `loader.load_supplier_glossary`; unknown suppliers fall
back to the base WATT_BASE_TERMS only.
"""

SUPPLIER_TERMS: dict[str, list[str]] = {
    "E.ON":                ["E.ON", "E.ON Energy Solutions"],
    "E.ON Next Energy":    ["E.ON Next", "E.ON Next Energy"],
    "British Gas Core":    ["British Gas Core", "BGC", "BG Core"],
    "British Gas Lite":    ["British Gas Lite", "BGL", "BG Lite"],
    "British Gas Business":["British Gas Business", "BGB"],
    "British Gas Trading": ["British Gas Trading", "BG Trading"],
    "Pozitive":            ["Pozitive", "Pozitive Energy"],
    "Yu Energy":           ["Yu Energy", "Yu Energy Retail"],
    "Smartest Energy":     ["Smartest", "Smartest Energy"],
    "Affect Energy":       ["Affect", "Affect Energy"],
    "Britannia Gas":       ["Britannia", "Britannia Gas"],
    "United Gas & Power":  ["UGP", "United Gas", "United Gas and Power"],
}
