/**
 * Shared customer-name helpers.
 *
 * 2026-05-24 — owner-reported bug: when the AI fails to extract a customer
 * name from a call (low audio quality, agent never said it, name garbled by
 * accent) the row would render as either:
 *   - `—` (em-dash on most lists)
 *   - `Untitled` (tracker side-panel header)
 *   - `(no customer)` (tracker grouped table)
 *   - `(pending audio upload)` (backend stub before pipeline finished)
 *   - the filename (queue + dashboard fallback chain)
 *
 * Five different placeholders meant reviewers couldn't tell at a glance
 * whether the AI failed or the upload was still processing. `Unknown` is
 * now the single canonical placeholder for all "name not extracted"
 * states; `formatCustomerName` is the only function that decides what to
 * render. Centralised so the next bug fix touches one file.
 */

/** Internal placeholders that mean "AI hasn't filled in a real name yet". */
const PLACEHOLDER_NAMES = new Set([
  "(pending audio upload)",
  "(no customer)",
  "Untitled",
]);

/**
 * Convert a possibly-null customer name into the string the UI should
 * render. Returns `Unknown` when the name is missing, blank, or one of
 * the known backend stub placeholders.
 *
 * Pass `fallback` to override the default `"Unknown"` — useful for cells
 * that historically rendered `—` and want to stay visually compact.
 */
export function formatCustomerName(
  name: string | null | undefined,
  fallback: string = "Unknown",
): string {
  if (name === null || name === undefined) return fallback;
  const trimmed = name.trim();
  if (trimmed === "") return fallback;
  if (PLACEHOLDER_NAMES.has(trimmed)) return fallback;
  // The auto-detect placeholder uses a parenthesised prefix we don't
  // pin to one phrasing — match any "(auto-detect pending...)" form.
  if (/^\(auto-detect pending/i.test(trimmed)) return fallback;
  return trimmed;
}

/**
 * Returns true when the stored name is one of the backend stubs (vs.
 * a real customer name). Components use this to decide whether to show
 * an "AI couldn't read this name" warning chip next to the placeholder.
 */
export function isPlaceholderCustomerName(
  name: string | null | undefined,
): boolean {
  if (name === null || name === undefined) return true;
  const trimmed = name.trim();
  if (trimmed === "") return true;
  if (PLACEHOLDER_NAMES.has(trimmed)) return true;
  if (/^\(auto-detect pending/i.test(trimmed)) return true;
  return false;
}
