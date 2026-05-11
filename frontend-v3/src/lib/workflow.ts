/**
 * Shared single source of truth for the supplier → workflow rule.
 *
 *   E.ON variants → 3 required stages (Lead Gen → Passover → Closer)
 *                   LOA is bundled into the Closer.
 *   Everyone else → 4 required stages (… → Standalone LOA)
 *
 * Mirrors `backend/app/deal_lifecycle.py:SUPPLIER_PHASE_MATRIX`.
 * The supplier label comes from `Call.detected_supplier` (AI-detected at
 * upload time) or `CustomerDeal.supplier`, so the workflow type below
 * is auto-derived end-to-end. No manual tagging.
 */

export type LifecyclePhase =
  | "lead_gen"
  | "passover"
  | "closer"
  | "standalone_loa"
  | "amendment"
  | "c_call";

export const CORRECTIVE_PHASES: readonly LifecyclePhase[] = [
  "c_call",
  "amendment",
] as const;

export const PHASE_LABEL: Record<LifecyclePhase, string> = {
  lead_gen: "Lead Gen",
  passover: "Passover",
  closer: "Closer",
  standalone_loa: "Standalone LOA",
  amendment: "Amendment",
  c_call: "C-Call",
};

/** Sublabel surfaced underneath a phase chip to explain the supplier-specific
 *  twist (e.g. E.ON's Closer includes the LOA verbatim). */
export const PHASE_SUBLABEL: Partial<Record<string, string>> = {
  // Keyed by `${supplierKey}.${phase}`.
  "eon.closer": "LOA bundled",
  "eon_next.closer": "LOA bundled",
  "non_eon.standalone_loa": "Separate LOA call",
};

const E_ON_NAMES = new Set<string>([
  "E.ON",
  "EON",
  "E.ON Next",
  "EON Next",
  "E.On Next",
  "E ON Next",
  "E.On Energy Solutions Ltd", // pending Aly Q2 — treat as E.ON-flavoured for now
]);

const NON_EON_PHASES: LifecyclePhase[] = [
  "lead_gen",
  "passover",
  "closer",
  "standalone_loa",
];
const EON_PHASES: LifecyclePhase[] = ["lead_gen", "passover", "closer"];

/** True if the supplier label resolves to an E.ON variant. Case-insensitive. */
export function isEonSupplier(supplier: string | null | undefined): boolean {
  if (!supplier) return false;
  const norm = supplier.trim();
  if (E_ON_NAMES.has(norm)) return true;
  const lc = norm.toLowerCase();
  return lc.startsWith("eon") || lc.startsWith("e.on") || lc.startsWith("e on");
}

/** Canonical required phases for the given supplier. Falls back to 4-stage
 *  when the supplier is unknown (safer — surfaces the LOA gap). */
export function requiredPhasesFor(
  supplier: string | null | undefined,
): LifecyclePhase[] {
  return isEonSupplier(supplier) ? EON_PHASES : NON_EON_PHASES;
}

/** Required-stage count (3 or 4). Used for headline pills. */
export function workflowStageCount(
  supplier: string | null | undefined,
): 3 | 4 {
  return isEonSupplier(supplier) ? 3 : 4;
}

/** Stages to render in the progress bar — required + corrective tail. */
export function workflowStepsFor(
  supplier: string | null | undefined,
): LifecyclePhase[] {
  return [...requiredPhasesFor(supplier), ...CORRECTIVE_PHASES];
}

/** Human-readable summary suitable for tooltips or banner copy. */
export function workflowSummary(
  supplier: string | null | undefined,
): string {
  if (isEonSupplier(supplier)) {
    return `${supplier ?? "E.ON"} bundles the LOA into the Closer call, so this deal needs 3 stages: Lead Gen → Passover → Closer.`;
  }
  return `${supplier ?? "This supplier"} requires a separate LOA call after the Closer, so this deal needs 4 stages: Lead Gen → Passover → Closer → Standalone LOA.`;
}

/** Tone classification for the pill — emerald for E.ON, blue for others. */
export function workflowTone(
  supplier: string | null | undefined,
): "emerald" | "blue" | "neutral" {
  if (!supplier) return "neutral";
  return isEonSupplier(supplier) ? "emerald" : "blue";
}
