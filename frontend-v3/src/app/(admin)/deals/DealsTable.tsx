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

export function LifecyclePill({ status }: { status: string }) {
  const s = (status || "").toLowerCase();
  if (s === "closed_done")
    return (
      <Badge className="border-emerald-500/30 bg-emerald-500/10 text-emerald-400">
        ● closed_done
      </Badge>
    );
  if (s === "closed_lost")
    return (
      <Badge className="border-red-500/30 bg-red-500/10 text-red-400">
        ● closed_lost
      </Badge>
    );
  if (s === "in_progress")
    return (
      <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-400">
        ● in_progress
      </Badge>
    );
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
