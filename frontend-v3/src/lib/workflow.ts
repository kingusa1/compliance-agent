/**
 * Shared single source of truth for the supplier → workflow rule
 * (2026-05-14 redesign per Aly's clarification).
 *
 * THE TWO-STAGE MODEL
 *
 *   Every supplier follows the same 2 top-level deal stages:
 *
 *     Opener  — Lead Gen agent's first call. ONE segment: `lead_gen`.
 *     Closer  — The contract-binding call. Multiple segments INSIDE:
 *                 * Non-E.ON : pre_sales + verbal (LOA is a DocuSign
 *                              document, NOT a recording — never a
 *                              compliance segment in this system).
 *                 * E.ON     : pre_sales + verbal + loa (LOA wording is
 *                              bundled INTO the closer recording for
 *                              E.ON — it stays a compliance segment).
 *
 * The inner-segment list is what the AI content_classifier emits
 * (lead_gen / pre_sales / verbal / loa). The two top-level stages
 * (Opener / Closer) are the UX grouping the reviewer sees on the
 * customer drilldown + dashboard.
 *
 * Mirrors backend/app/deal_lifecycle.py:SUPPLIER_PHASE_MATRIX and
 * the LOA-strip in backend/app/agents/content_classifier.py.
 */

export type SegmentStage = "lead_gen" | "pre_sales" | "verbal" | "loa";
export type TopLevelStage = "opener" | "closer";

/** Inner segments — what the AI emits. */
export const SEGMENT_LABEL: Record<SegmentStage, string> = {
  lead_gen: "Lead Gen",
  pre_sales: "Pre-Sales",
  verbal: "Verbal",
  loa: "LOA",
};

/** Top-level stages — what the reviewer thinks of as "phases of the deal". */
export const TOPLEVEL_LABEL: Record<TopLevelStage, string> = {
  opener: "Opener",
  closer: "Closer",
};

/** Which top-level stage a given inner segment belongs to. */
export const SEGMENT_PARENT: Record<SegmentStage, TopLevelStage> = {
  lead_gen: "opener",
  pre_sales: "closer",
  verbal: "closer",
  loa: "closer",
};

const E_ON_NAMES = new Set<string>([
  "E.ON",
  "EON",
  "E.ON Next",
  "EON Next",
  "E.On Next",
  "E ON Next",
  "E.On Energy Solutions Ltd",
]);

/** True if the supplier label resolves to an E.ON variant. Case-insensitive. */
export function isEonSupplier(supplier: string | null | undefined): boolean {
  if (!supplier) return false;
  const norm = supplier.trim();
  if (E_ON_NAMES.has(norm)) return true;
  const lc = norm.toLowerCase();
  return lc.startsWith("eon") || lc.startsWith("e.on") || lc.startsWith("e on");
}

/** Inner segments the AI is allowed to emit for the given supplier. */
export function allowedSegmentsFor(
  supplier: string | null | undefined,
): SegmentStage[] {
  return isEonSupplier(supplier)
    ? ["lead_gen", "pre_sales", "verbal", "loa"]
    : ["lead_gen", "pre_sales", "verbal"];
}

/** Inner segments grouped under each top-level stage for the given supplier. */
export function segmentsByTopLevel(
  supplier: string | null | undefined,
): Record<TopLevelStage, SegmentStage[]> {
  if (isEonSupplier(supplier)) {
    return {
      opener: ["lead_gen"],
      closer: ["pre_sales", "verbal", "loa"],
    };
  }
  return {
    opener: ["lead_gen"],
    closer: ["pre_sales", "verbal"],
  };
}

/** Required top-level stages — always Opener + Closer. */
export const REQUIRED_TOP_STAGES: readonly TopLevelStage[] = [
  "opener",
  "closer",
] as const;

/** Stage count for the headline pill — always 2 now. */
export function workflowStageCount(
  _supplier: string | null | undefined,
): 2 {
  return 2;
}

/** Human-readable summary suitable for tooltips or banner copy. */
export function workflowSummary(
  supplier: string | null | undefined,
): string {
  if (isEonSupplier(supplier)) {
    return `${supplier ?? "E.ON"} runs the 2-stage Opener (Lead Gen) → Closer (Pre-Sales + Verbal + LOA) flow. LOA wording is bundled INSIDE the Closer recording.`;
  }
  return `${supplier ?? "This supplier"} runs the 2-stage Opener (Lead Gen) → Closer (Pre-Sales + Verbal) flow. LOA is a DocuSign paper document, NOT a recording.`;
}

/** Tone classification for the pill — emerald for E.ON, blue for others. */
export function workflowTone(
  supplier: string | null | undefined,
): "emerald" | "blue" | "neutral" {
  if (!supplier) return "neutral";
  return isEonSupplier(supplier) ? "emerald" : "blue";
}

// ── Back-compat shims ────────────────────────────────────────────
// Older callers used the legacy 4/6-stage names; the matrix below
// is still consumed by customers/[slug] to render its progress strip.

export type LifecyclePhase = SegmentStage | "amendment" | "c_call";

export const PHASE_LABEL: Record<LifecyclePhase, string> = {
  ...SEGMENT_LABEL,
  amendment: "Amendment",
  c_call: "C-Call",
};

export const CORRECTIVE_PHASES: readonly LifecyclePhase[] = [
  "c_call",
  "amendment",
] as const;

/** Canonical required INNER segments for the given supplier. */
export function requiredPhasesFor(
  supplier: string | null | undefined,
): LifecyclePhase[] {
  return allowedSegmentsFor(supplier) as LifecyclePhase[];
}

/** Inner segments to render in the customer-drilldown step list — required
 *  inner segments only. Corrective stages live elsewhere now (per Aly:
 *  Amendment / C-Call are post-sale fixes, not in the main workflow). */
export function workflowStepsFor(
  supplier: string | null | undefined,
): LifecyclePhase[] {
  return [...requiredPhasesFor(supplier)];
}
