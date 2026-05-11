"use client";

/**
 * WorkflowTypePill — color-coded `3-stage · E.ON` or `4-stage · British Gas`
 * badge driven by the AI-detected supplier label.
 *
 * Emerald = 3-stage (E.ON variants, LOA bundled into Closer).
 * Blue    = 4-stage (everyone else, separate Standalone LOA call).
 * Neutral = supplier not yet detected.
 *
 * Mirrors backend `deal_lifecycle.SUPPLIER_PHASE_MATRIX`. See
 * `lib/workflow.ts` for the source-of-truth resolver.
 */
import { Pill } from "@/components/design/Pill";
import {
  isEonSupplier,
  workflowStageCount,
  workflowSummary,
  workflowTone,
} from "@/lib/workflow";

interface WorkflowTypePillProps {
  supplier: string | null | undefined;
  /** When true, omits supplier name — useful in cramped table cells. */
  compact?: boolean;
  /** Override displayed supplier label (e.g. fall back to "Detecting…"). */
  supplierLabel?: string;
}

export function WorkflowTypePill({
  supplier,
  compact = false,
  supplierLabel,
}: WorkflowTypePillProps) {
  if (!supplier) {
    return (
      <Pill tone="neutral" dot>
        <span title="Supplier not detected yet — workflow type unknown.">
          ? stages
        </span>
      </Pill>
    );
  }
  const count = workflowStageCount(supplier);
  const tone = workflowTone(supplier);
  const display = supplierLabel ?? supplier;
  const summary = workflowSummary(supplier);
  const eon = isEonSupplier(supplier);

  return (
    <Pill tone={tone} dot>
      <span title={summary}>
        {count}-stage
        {!compact && (
          <>
            <span style={{ opacity: 0.55, margin: "0 4px" }}>·</span>
            <span>{display}</span>
            <span
              style={{
                opacity: 0.55,
                marginLeft: 4,
                fontSize: 10,
              }}
            >
              {eon ? "LOA bundled" : "separate LOA"}
            </span>
          </>
        )}
      </span>
    </Pill>
  );
}
