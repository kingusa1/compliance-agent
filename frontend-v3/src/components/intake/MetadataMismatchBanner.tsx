"use client";

import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * MetadataMismatchBanner — orange-bordered card surfaced when the upload
 * pipeline returns METADATA_MISMATCH. The reviewer picks one of three
 * resolutions and the form re-submits with `supplier_override` set.
 *
 * This component is *passive* — it just renders + emits the picked action.
 * The parent form (L7Form / UploadModal) owns the re-submit mutation.
 */
export type MismatchChoice = "manual" | "auto" | "edit";

export interface MetadataMismatchBannerProps {
  field?: string; // e.g. "supplier"
  manual: string;
  auto: string;
  onPick: (choice: MismatchChoice) => void;
}

export function MetadataMismatchBanner({
  field = "supplier",
  manual,
  auto,
  onPick,
}: MetadataMismatchBannerProps) {
  return (
    <div
      role="alert"
      data-slot="metadata-mismatch-banner"
      data-mismatch-code="METADATA_MISMATCH"
      className="flex items-start gap-3 rounded-lg border border-amber-500/45 bg-amber-500/[0.06] px-4 py-3"
    >
      <AlertTriangle
        className="mt-0.5 h-5 w-5 shrink-0 text-amber-400"
        aria-hidden="true"
      />
      <div className="flex-1">
        <div className="text-[13px] font-medium text-amber-300">
          METADATA_MISMATCH detected
        </div>
        <div className="mt-1 text-[12px] text-[var(--text-primary)]">
          {field} · manual=
          <span className="font-mono text-[var(--text-primary)]">{manual}</span>
          {" · "}auto-detected=
          <span className="font-mono text-[var(--text-primary)]">{auto}</span>
          . Pick one to continue.
        </div>
      </div>
      <div className="flex shrink-0 gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          data-action="use-manual"
          onClick={() => onPick("manual")}
        >
          Use manual
        </Button>
        <Button
          type="button"
          variant="default"
          size="sm"
          data-action="use-auto"
          onClick={() => onPick("auto")}
        >
          Use auto
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          data-action="edit"
          onClick={() => onPick("edit")}
        >
          Edit
        </Button>
      </div>
    </div>
  );
}
