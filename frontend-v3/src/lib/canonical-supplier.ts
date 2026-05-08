/**
 * Supplier-name canonicalisation map (W1.3 of v3-watt-coverage).
 *
 * Watt's rejection-tracker XLSX ships 27 distinct supplier strings (case
 * + typo + whitespace variations like "British Gas Buisness" vs
 * "British Gas Business" or "BGL" vs "British Gas Lite"). This module
 * maps any dirty input to the 14 canonical entries declared in
 * `frontend-v3/src/lib/schemas/l7-intake.ts:SUPPLIERS`.
 *
 * Source: `.planning/v3-rebuild/2026-05-03-watt-xlsx-deep-dive.md` §4.
 */
import type { Supplier } from "@/lib/schemas/l7-intake";

/**
 * Lowercase + whitespace-collapsed dirty-string → canonical Supplier key.
 * Lookup is via {@link canonicalizeSupplier} which normalises the input
 * before consulting this table.
 */
export const SUPPLIER_ALIASES: Record<string, Supplier> = {
  // E.ON Next Energy — most common, 7+ raw variants
  "e.on next energy ltd": "E.ON Next Energy",
  "e.on next energy": "E.ON Next Energy",
  "e.on next": "E.ON Next Energy",
  "eon next": "E.ON Next Energy",
  "e.on nexT": "E.ON Next Energy",
  "e.on next energy ltd.": "E.ON Next Energy",

  // E.ON (Energy Solutions Ltd) — distinct from E.ON Next
  "e.on energy solutions ltd": "E.ON",
  "e.on energy solutions": "E.ON",
  "e.on": "E.ON",
  "eon": "E.ON",

  // British Gas family — 4 distinct flavours
  "british gas lite": "British Gas Lite",
  "bg lite": "British Gas Lite",
  "bgl": "British Gas Lite",
  "british gas buisness": "British Gas Business",
  "british gas business": "British Gas Business",
  "bgb": "British Gas Business",
  "british gas trading ltd": "British Gas Trading",
  "british gas trading": "British Gas Trading",
  "british gas core": "British Gas Core",
  "bg core": "British Gas Core",
  // Ambiguous fallback — tracker uses bare "british gas" rarely; default to Core.
  "british gas": "British Gas Core",

  // Smaller suppliers
  "pozitive energy ltd": "Pozitive Energy",
  "pozitive energy": "Pozitive Energy",
  "yu energy retail ltd": "Yu Energy",
  "yu energy": "Yu Energy",
  "smartestenergy ltd": "Smartest Energy",
  "smartest energy": "Smartest Energy",
  "affect energy ltd": "Affect Energy",
  "affect energy": "Affect Energy",
  "britannia gas": "Britannia Gas",
  "united gas and power": "United Gas & Power",
  "united gas & power": "United Gas & Power",

  // TotalEnergies (out-of-matrix in spec — warn user when selected)
  "totalenergies gas & power ltd": "TotalEnergies",
  "total gas and power ltd": "TotalEnergies",
  "totalenergies": "TotalEnergies",
  "total": "TotalEnergies",
};

/** Lowercase + collapse whitespace + trim. */
function _normalise(s: string): string {
  return s.trim().toLowerCase().replace(/\s+/g, " ");
}

/**
 * Map a raw supplier string (any casing/typo) to a canonical Supplier key.
 *
 * Returns the alias-mapped name when known; otherwise falls back to the
 * 14-key whitelist's "Other" catch-all so downstream code always handles
 * a known shape.
 */
export function canonicalizeSupplier(input: string | null | undefined): Supplier {
  if (!input) return "Other";
  const key = _normalise(input);
  return SUPPLIER_ALIASES[key] ?? "Other";
}
