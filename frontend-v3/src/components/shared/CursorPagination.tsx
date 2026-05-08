"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * CursorPagination — generic cursor + offset pager used by the long
 * /deals, /compliant, /non-compliant tables. Backend list endpoints
 * return `{rows, total, limit, offset, has_more}` so we model the
 * cursor as a page-number derived from offset/limit. Caller owns the
 * state — we just render Prev/Next + the "Showing X–Y of N" caption.
 */

export type CursorPaginationProps = {
  /** Current 0-based offset. */
  offset: number;
  /** Page size. */
  limit: number;
  /** Total rows in the dataset (server-reported). */
  total: number;
  /** Called with the new offset when the user clicks Prev/Next. */
  onChange: (newOffset: number) => void;
  /** Disabled state propagated during fetches so users can't double-click. */
  disabled?: boolean;
  className?: string;
};

export function CursorPagination({
  offset,
  limit,
  total,
  onChange,
  disabled = false,
  className,
}: CursorPaginationProps) {
  const safeLimit = Math.max(1, limit);
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + safeLimit, total);
  const canPrev = offset > 0 && !disabled;
  const canNext = offset + safeLimit < total && !disabled;

  return (
    <div
      className={cn(
        "flex items-center justify-between border-t border-[var(--border-subtle)] bg-[var(--bg-elev1)] px-4 py-3",
        className,
      )}
      data-testid="cursor-pagination"
    >
      <p className="text-[12px] text-[var(--text-muted)] tabular-nums">
        {total === 0 ? (
          "No results"
        ) : (
          <>
            Showing <span className="text-[var(--text-primary)]">{start}</span>–
            <span className="text-[var(--text-primary)]">{end}</span> of{" "}
            <span className="text-[var(--text-primary)]">{total}</span>
          </>
        )}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={!canPrev}
          onClick={() => onChange(Math.max(0, offset - safeLimit))}
          data-testid="cursor-prev"
        >
          <ChevronLeft className="size-3.5" />
          Prev
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!canNext}
          onClick={() => onChange(offset + safeLimit)}
          data-testid="cursor-next"
        >
          Next
          <ChevronRight className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}
