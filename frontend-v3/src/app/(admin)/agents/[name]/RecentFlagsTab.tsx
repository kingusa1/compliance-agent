"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import type { AgentDrilldown } from "@/lib/queries/aggregator";

/**
 * RecentFlagsTab — recent critical/medium flags for an agent. The
 * /agents/{name}/drilldown endpoint exposes a `critical_count_7d`
 * scalar but no per-row flag list yet — until that lands we surface
 * the count and a placeholder explaining where the rows will come
 * from. The table layout matches the R3 hi-fi screen so dropping in
 * data later is a one-liner.
 *
 * TODO(post-backend): when flags rows become part of the drilldown
 * payload, swap the stub for the real list.
 */

type FlagRow = {
  when: string;
  severity: "HIGH" | "MEDIUM" | "LOW";
  rule: string;
  evidence: string;
  fix_status: "open" | "in-review" | "fixed";
  rejection: string | null;
};

export function RecentFlagsTab({ data }: { data: AgentDrilldown }) {
  const rows: FlagRow[] = [];

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
      <Table>
        <TableHeader>
          <TableRow className="border-[var(--border-subtle)] hover:bg-transparent">
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              When
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Severity
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Rule
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Evidence
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Fix Status
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Rejection
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow className="border-[var(--border-subtle)]">
            <TableCell
              colSpan={6}
              className="py-8 text-center text-[13px] text-[var(--text-dim)]"
            >
              {data.critical_count_7d > 0 ? (
                <>
                  {data.critical_count_7d} critical flag
                  {data.critical_count_7d === 1 ? "" : "s"} in the last 7 days
                  · per-row drilldown wires up post-backend.
                </>
              ) : (
                "No flags in the last 7 days."
              )}
            </TableCell>
          </TableRow>

          {rows.map((r, i) => (
            <TableRow key={i} className="border-[var(--border-subtle)]">
              <TableCell className="whitespace-nowrap text-[13px] text-[var(--text-muted)]">
                {r.when}
              </TableCell>
              <TableCell>
                <SeverityPill severity={r.severity} />
              </TableCell>
              <TableCell className="text-[13px] text-[var(--text-primary)]">
                {r.rule}
              </TableCell>
              <TableCell className="font-mono text-[12px] text-[var(--text-muted)]">
                {r.evidence}
              </TableCell>
              <TableCell>
                <FixPill fix={r.fix_status} />
              </TableCell>
              <TableCell className="font-mono text-[12px] text-red-400">
                {r.rejection ?? "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function SeverityPill({ severity }: { severity: FlagRow["severity"] }) {
  if (severity === "HIGH")
    return (
      <Badge className="border-red-500/30 bg-red-500/10 text-red-400">
        ● HIGH
      </Badge>
    );
  if (severity === "MEDIUM")
    return (
      <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-400">
        ● MEDIUM
      </Badge>
    );
  return <Badge variant="outline">● {severity}</Badge>;
}

function FixPill({ fix }: { fix: FlagRow["fix_status"] }) {
  if (fix === "fixed")
    return (
      <Badge className="border-emerald-500/30 bg-emerald-500/10 text-emerald-400">
        ● fixed
      </Badge>
    );
  if (fix === "in-review")
    return (
      <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-400">
        ● in-review
      </Badge>
    );
  return <Badge variant="outline">● open</Badge>;
}
