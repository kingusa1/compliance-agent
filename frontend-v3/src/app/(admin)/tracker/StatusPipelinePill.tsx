/**
 * 7-step horizontal pipeline pill mirroring Watt's tracker col L:
 *   NOT_STARTED → IN_PROGRESS → FIXED → BATCHED_TO_PORTAL →
 *   SUBMITTED_TO_PORTAL → FIXED_AND_APPROVED
 *      ↘ DEAD (terminal off-pipeline)
 */

const PIPELINE_STEPS = [
  "NOT_STARTED",
  "IN_PROGRESS",
  "FIXED",
  "BATCHED_TO_PORTAL",
  "SUBMITTED_TO_PORTAL",
  "FIXED_AND_APPROVED",
] as const;

const LABELS: Record<string, string> = {
  NOT_STARTED: "Not started",
  IN_PROGRESS: "In progress",
  FIXED: "Fixed",
  BATCHED_TO_PORTAL: "Batched",
  SUBMITTED_TO_PORTAL: "Submitted",
  FIXED_AND_APPROVED: "Approved",
  DEAD: "Dead",
};

export function StatusPipelinePill({ status }: { status: string | null }) {
  if (!status) return <span className="text-[var(--text-muted)]">—</span>;
  if (status === "DEAD") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-medium text-red-800 border border-red-200">
        <span className="block h-2 w-2 rounded-full bg-red-500" />
        Dead
      </span>
    );
  }
  // Synthetic status emitted by tracker_aggregator._awaiting_review_row.
  // Render as a distinct amber chip so reviewers don't see "1/6
  // AWAITING_REVIEW" (the old fallback path that treated unknown statuses
  // as step 1 of the rejection pipeline, which is wrong — the call hasn't
  // entered the rejection flow at all).
  if (status === "AWAITING_REVIEW") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-800 border border-amber-200">
        <span className="block h-2 w-2 rounded-full bg-amber-500" />
        Awaiting review
      </span>
    );
  }
  const idx = PIPELINE_STEPS.indexOf(status as typeof PIPELINE_STEPS[number]);
  const safeIdx = idx === -1 ? 0 : idx;
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-[11px] font-medium text-[var(--text-default)] border border-[var(--border-subtle)]">
      <span className="text-[10px] text-[var(--text-muted)]">
        {safeIdx + 1}/{PIPELINE_STEPS.length}
      </span>
      {LABELS[status] ?? status}
    </span>
  );
}

export { PIPELINE_STEPS, LABELS as PIPELINE_LABELS };
