/**
 * Sales-agent name canonicalisation (W1.4 of v3-watt-coverage).
 *
 * Watt's tracker XLSX has ~25 distinct agent strings for ~22 actual humans
 * (typo + casing collisions like "Bradley Clayton" vs "Bradley Claytob",
 * "Jack Shaw" vs "jack shaw" vs "Jack shaw"). This module mirrors the
 * backend `sales_agent_aliases` table at the client edge so the L7 form
 * can preview a canonical match before submit.
 *
 * The authoritative map is the backend table — populated via the Settings
 * tab (W4 — not yet shipped). Until then this client map is empty and the
 * helper is a pass-through.
 *
 * Source: `.planning/v3-rebuild/2026-05-03-watt-xlsx-deep-dive.md` §X7.
 */

/**
 * In-memory alias map. Empty by default; populate at runtime via
 * {@link setAgentAliases} after fetching `/api/agents/aliases` (endpoint
 * lands in W4 alongside the Settings UI). Keys are normalised
 * lowercase-trim strings.
 */
let _aliases: Record<string, string> = {};

/** Lowercase + collapse whitespace + trim. */
function _normalise(s: string): string {
  return s.trim().toLowerCase().replace(/\s+/g, " ");
}

/**
 * Replace the client-side alias map with the latest authoritative set.
 * Caller fetches the canonical list from the backend.
 */
export function setAgentAliases(map: Record<string, string>): void {
  const next: Record<string, string> = {};
  for (const [alias, canonical] of Object.entries(map)) {
    next[_normalise(alias)] = canonical;
  }
  _aliases = next;
}

/** Snapshot of the current in-memory map (mostly for tests). */
export function getAgentAliases(): Record<string, string> {
  return { ..._aliases };
}

/**
 * Map a raw agent string to its canonical display name.
 *
 * Falls through to the original input when no alias is registered — admins
 * backfill the table over time, so unknown inputs are expected.
 */
export function canonicalizeAgent(input: string | null | undefined): string | null {
  if (!input) return input ?? null;
  const key = _normalise(input);
  return _aliases[key] ?? input;
}
