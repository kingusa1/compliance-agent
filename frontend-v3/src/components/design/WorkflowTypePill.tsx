"use client";

/**
 * WorkflowTypePill — color-coded badge for the supplier-specific deal
 * workflow. The 2026-05-14 redesign collapses to TWO top-level stages
 * for everyone (Opener / Closer); the supplier-specific twist is what
 * the Closer contains:
 *
 *   Emerald  · E.ON variants — LOA wording bundled INSIDE the Closer recording.
 *   Blue     · Everyone else — LOA is a DocuSign document (NO recording).
 *   Neutral  · Supplier not yet detected.
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
        Opener · Closer
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
              {eon ? "LOA bundled in Closer" : "LOA via DocuSign"}
            </span>
          </>
        )}
      </span>
    </Pill>
  );
}
