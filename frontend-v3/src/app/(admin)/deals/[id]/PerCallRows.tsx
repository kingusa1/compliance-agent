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
import { formatScorePercent } from "@/lib/score";
import type { DealCall } from "@/lib/queries/aggregator";

/**
 * PerCallRows — per-call breakdown for a deal's verdict. Click a row
 * to drop into the Reviewer call detail at /calls/[id]. Status pill
 * uses compliance_status if set, otherwise raw `status`.
 */

export type PerCallRowsProps = {
  calls: DealCall[];
};

export function PerCallRows({ calls }: PerCallRowsProps) {
  const router = useRouter();

  return (
    <section>
      <div className="mb-3 flex items-center gap-3">
        <h3 className="text-[15px] font-semibold text-[var(--text-primary)]">
          Per-call breakdown
        </h3>
        <Badge variant="outline" className="tabular-nums">
          {calls.length}
        </Badge>
      </div>

      <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
        <Table>
          <TableHeader>
            <TableRow className="border-[var(--border-subtle)] hover:bg-transparent">
              <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
                Call type
              </TableHead>
              <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
                Agent
              </TableHead>
              <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
                Status
              </TableHead>
              <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
                Score
              </TableHead>
              <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
                Completed
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {calls.length === 0 && (
              <TableRow className="border-[var(--border-subtle)]">
                <TableCell
                  colSpan={5}
                  className="py-8 text-center text-[13px] text-[var(--text-dim)]"
                >
                  No calls uploaded yet.
                </TableCell>
              </TableRow>
            )}

            {calls.map((c) => (
              <TableRow
                key={c.id}
                onClick={() => router.push(`/calls/${c.id}`)}
                data-testid="per-call-row"
                className="cursor-pointer border-[var(--border-subtle)] hover:bg-[var(--bg-elev2)]"
              >
                <TableCell>
                  <Badge variant="outline" className="font-mono text-[11px]">
                    {c.call_type ?? "—"}
                  </Badge>
                </TableCell>
                <TableCell className="text-[13px] text-[var(--text-muted)]">
                  {c.agent_name ?? "—"}
                </TableCell>
                <TableCell>
                  <CallStatusPill
                    status={c.compliance_status ?? c.status ?? null}
                  />
                </TableCell>
                <TableCell className="font-mono text-[13px] tabular-nums text-[var(--text-primary)]">
                  {formatScorePercent(c.score)}
                </TableCell>
                <TableCell className="whitespace-nowrap text-[13px] text-[var(--text-muted)]">
                  {formatWhen(c.completed_at ?? c.created_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

function CallStatusPill({ status }: { status: string | null }) {
  if (!status) return <Badge variant="outline">—</Badge>;
  const s = status.toLowerCase();
  if (s === "compliant" || s === "completed")
    return (
      <Badge className="border-emerald-500/30 bg-emerald-500/10 text-emerald-400">
        ● {status}
      </Badge>
    );
  if (s === "non_compliant" || s === "failed")
    return (
      <Badge className="border-red-500/30 bg-red-500/10 text-red-400">
        ● {status}
      </Badge>
    );
  if (s === "needs_review" || s === "pending" || s === "processing")
    return (
      <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-400">
        ● {status}
      </Badge>
    );
  return <Badge variant="outline">{status}</Badge>;
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
