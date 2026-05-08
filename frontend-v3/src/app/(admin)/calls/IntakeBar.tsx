"use client";

import { Upload } from "lucide-react";

/**
 * IntakeBar — collapsible "Drop audio + customer info to start" pill
 * above the /calls main content. Click anywhere on the bar opens the
 * UploadModal. Kept minimal — actual form lives in L7Form via
 * UploadModal so we don't render it on every page load.
 */
export interface IntakeBarProps {
  onClick: () => void;
}

export function IntakeBar({ onClick }: IntakeBarProps) {
  return (
    <button
      type="button"
      data-testid="intake-bar"
      onClick={onClick}
      className="group flex w-full items-center gap-3 rounded-lg border border-dashed border-[var(--border-strong)] bg-[var(--bg-elev1)] px-4 py-3 text-left transition-colors hover:border-emerald-500/40 hover:bg-[var(--bg-elev2)]"
    >
      <Upload className="h-4 w-4 text-emerald-400" />
      <span className="text-[13px] text-[var(--text-primary)]">
        Drop audio + customer info to start
      </span>
      <span className="text-[12px] text-[var(--text-muted)]">
        — opens the L7 intake form (8 customer + 9 deal + 5 call fields)
      </span>
      <span className="ml-auto text-[12px] text-[var(--text-muted)] group-hover:text-emerald-400">
        Click to expand
      </span>
    </button>
  );
}
