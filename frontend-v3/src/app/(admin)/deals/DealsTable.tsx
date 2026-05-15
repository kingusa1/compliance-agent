"use client";

import { useRouter } from "next/navigation";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { ScoreBar } from "@/components/shared/ScoreBar";
import type { DealRow } from "@/lib/queries/aggregator";

/**
 * DealsTable — comfortable-density rows that link to /deals/[id].
 * Columns mirror the R3 hi-fi customer-deal screen: customer +
 * supplier + lifecycle pill + score + created date. The lifecycle
 * pill is the primary status signal (per UX-D17 verdict aggregator);
 * `final_action` is rolled up there.
 */

export type DealsTableProps = {
  deals: DealRow[];
};

export function DealsTable({ deals }: DealsTableProps) {
  const router = useRouter();

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
      <Table>
        <TableHeader>
          <TableRow className="border-[var(--border-subtle)] hover:bg-transparent">
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Customer
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Supplier
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Lifecycle
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Score
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Created
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {deals.map((d) => (
            <TableRow
              key={d.id}
              data-testid="deal-row"
              data-deal-id={d.id}
              onClick={() => router.push(`/deals/${d.id}`)}
              className="cursor-pointer border-[var(--border-subtle)] hover:bg-[var(--bg-elev2)]"
            >
              <TableCell className="text-[13px] font-medium text-[var(--text-primary)]">
                {d.customer_name}
              </TableCell>
              <TableCell className="text-[13px] text-[var(--text-muted)]">
                {d.supplier ?? "—"}
              </TableCell>
              <TableCell>
                <LifecyclePill status={d.lifecycle_status} />
              </TableCell>
              <TableCell className="w-48">
                <ScoreBar value={d.final_score} />
              </TableCell>
              <TableCell className="whitespace-nowrap text-[13px] text-[var(--text-muted)]">
                {formatWhen(d.created_at)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// 2026-05-14 audit fix: align with the 7-state taxonomy emitted by
// backend/app/deal_lifecycle.py:derive_lifecycle_status. Previously this
// pill only knew about the old 3 CustomerDeal.status values; every live
// row now ships with one of the 7 derived values and was falling through
// to the neutral outline badge.
const LIFECYCLE_VISUAL: Record<string, { cls: string; label: string }> = {
  verified: {
    cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
    label: "Verified",
  },
  loa_done: {
    cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
    label: "LOA done",
  },
  verbal_done: {
    cls: "border-blue-500/30 bg-blue-500/10 text-blue-400",
    label: "Verbal done",
  },
  pre_sales_done: {
    cls: "border-blue-500/30 bg-blue-500/10 text-blue-400",
    label: "Pre-sales done",
  },
  lead_gen_done: {
    cls: "border-amber-500/30 bg-amber-500/10 text-amber-400",
    label: "Lead-gen done",
  },
  open: {
    cls: "border-amber-500/30 bg-amber-500/10 text-amber-400",
    label: "Open",
  },
  rejected: {
    cls: "border-red-500/30 bg-red-500/10 text-red-400",
    label: "Rejected",
  },
};

export function LifecyclePill({ status }: { status: string }) {
  const s = (status || "").toLowerCase();
  const v = LIFECYCLE_VISUAL[s];
  if (v) return <Badge className={v.cls}>● {v.label}</Badge>;
  return <Badge variant="outline">{status || "—"}</Badge>;
}

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}
