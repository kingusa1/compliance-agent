"use client";

import Link from "next/link";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { useRunsQuery } from "@/lib/queries/observability";

const STATUS_TONE: Record<string, string> = {
  succeeded: "text-emerald-400",
  running: "text-amber-400",
  failed: "text-red-400",
  cancelled: "text-[var(--text-dim)]",
};

function StatusDot({ status }: { status: string | null | undefined }) {
  const s = (status ?? "unknown").toLowerCase();
  const tone = STATUS_TONE[s] ?? "text-[var(--text-muted)]";
  return (
    <span className={`inline-flex items-center gap-1.5 ${tone}`}>
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      <span className="text-[12px] capitalize">{s}</span>
    </span>
  );
}

export function RunsList({
  status,
}: {
  status?: string;
}) {
  const params = status && status !== "all" ? { status } : {};
  const { data, isLoading, isError } = useRunsQuery(params);

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }
  if (isError) {
    return (
      <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-6 text-[13px] text-[var(--text-muted)]">
        Could not load runs.
      </div>
    );
  }

  const runs = data?.runs ?? [];
  if (runs.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-6 text-[13px] text-[var(--text-muted)]">
        No runs match.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
      <Table>
        <TableHeader>
          <TableRow className="border-[var(--border-subtle)] hover:bg-transparent">
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Workflow
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Run
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Call
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Started
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Duration
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Status
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((r, i) => {
            const callId = typeof r.call_id === "string" ? r.call_id : null;
            const runId = typeof r.run_id === "string" ? r.run_id : "";
            const dur =
              typeof r.duration_ms === "number"
                ? `${(r.duration_ms / 1000).toFixed(1)}s`
                : "—";
            const started = r.started_at
              ? new Date(String(r.started_at)).toLocaleString()
              : "—";
            return (
              <TableRow
                key={runId || `${i}`}
                className="border-[var(--border-subtle)]"
              >
                <TableCell className="text-[13px] text-[var(--text-primary)]">
                  {r.workflow ?? "—"}
                </TableCell>
                <TableCell className="font-mono text-[12px] text-[var(--text-muted)]">
                  {runId ? runId.slice(0, 12) : "—"}
                </TableCell>
                <TableCell className="font-mono text-[12px]">
                  {callId ? (
                    <Link
                      href={`/calls/${callId}`}
                      className="text-[var(--blue-coaching)] hover:underline"
                    >
                      {callId.slice(0, 12)}
                    </Link>
                  ) : (
                    "—"
                  )}
                </TableCell>
                <TableCell className="text-[12px] text-[var(--text-muted)]">
                  {started}
                </TableCell>
                <TableCell className="font-mono text-[12px] tabular-nums text-[var(--text-muted)]">
                  {dur}
                </TableCell>
                <TableCell>
                  <StatusDot status={typeof r.status === "string" ? r.status : null} />
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
