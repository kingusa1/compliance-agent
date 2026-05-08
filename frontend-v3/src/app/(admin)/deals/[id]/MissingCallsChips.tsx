"use client";

import { useRouter } from "next/navigation";
import { Plus, ArrowRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";

/**
 * MissingCallsChips — orange "missing required call" chips. Clicking a
 * chip should open the Upload modal with customer + deal pre-filled
 * and call_type locked. R5 owns the modal — until that lane lands and
 * we can `import { UploadModal } from "@/app/(admin)/calls/UploadModal"`,
 * we navigate to /calls?upload=true&… which the modal will pick up
 * from the URL when it ships.
 *
 * TODO(post-R5): replace router.push with the imperative
 *   <UploadModal customerSlug=… dealId=… callType=… />
 * once R5 publishes the component.
 */

export type MissingCallsChipsProps = {
  dealId: string;
  customerSlug: string;
  missingCalls: string[];
};

const CALL_TYPE_DESCRIPTIONS: Record<string, string> = {
  intro: "Introduction / disclosure call",
  qualification: "Customer qualification call",
  verbal_dpa: "Verbal DPA confirmation call",
  hard_objection: "Hard objection handling",
  contract: "Contract acceptance call",
};

export function MissingCallsChips({
  dealId,
  customerSlug,
  missingCalls,
}: MissingCallsChipsProps) {
  const router = useRouter();

  if (missingCalls.length === 0) return null;

  function openUpload(callType: string) {
    const qs = new URLSearchParams({
      upload: "true",
      customer: customerSlug,
      deal: dealId,
      call_type: callType,
    });
    router.push(`/calls?${qs.toString()}`);
  }

  return (
    <section data-testid="missing-calls">
      <div className="mb-3 flex items-center gap-3">
        <h3 className="text-[15px] font-semibold text-[var(--text-primary)]">
          Missing required calls
        </h3>
        <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-400 tabular-nums">
          {missingCalls.length}
        </Badge>
        <span className="text-[12px] text-[var(--text-dim)]">
          · click a chip to upload
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {missingCalls.map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => openUpload(c)}
            data-testid="missing-call-chip"
            data-call-type={c}
            className="group flex items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3.5 py-2.5 text-left transition-colors hover:bg-amber-500/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-500/60"
          >
            <span
              className="grid size-6 shrink-0 place-items-center rounded-full bg-amber-500/20 text-amber-400"
              aria-hidden="true"
            >
              <Plus className="size-3.5" />
            </span>
            <div>
              <div className="font-mono text-[13px] font-medium text-amber-300">
                {c}
              </div>
              <div className="text-[11px] text-[var(--text-muted)]">
                {CALL_TYPE_DESCRIPTIONS[c] ?? "Required call"} · missing
              </div>
            </div>
            <ArrowRight className="ml-1 size-3.5 text-amber-400 opacity-0 transition-opacity group-hover:opacity-100" />
          </button>
        ))}
      </div>
    </section>
  );
}
