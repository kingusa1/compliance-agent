"use client";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ScoreBar } from "@/components/reviewer/ScoreBar";
import type { QueueCall } from "@/lib/api";
import { shortFilename } from "@/lib/filename";

/**
 * Comfortable-density queue master table (UX-D02 pick).
 *
 * Six columns: When · Customer (filename below) · Supplier · Agent · Score · Status.
 * Click a row → fires `onSelect(callId)`. Currently-selected row gets an
 * emerald left-border + elevated background to mirror the queue.jsx mock.
 */
export function QueueTable({
  rows,
  selectedId,
  onSelect,
}: {
  rows: QueueCall[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="overflow-hidden">
      <Table>
        <TableHeader className="sticky top-0 z-[1] bg-[var(--bg-elev1)]">
          <TableRow className="border-[var(--border-subtle)] hover:bg-transparent">
            <TableHead className="w-[110px] text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
              When
            </TableHead>
            <TableHead className="text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
              Customer
            </TableHead>
            <TableHead className="text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
              Supplier
            </TableHead>
            <TableHead className="text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
              Agent
            </TableHead>
            <TableHead className="w-[120px] text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
              Score
            </TableHead>
            <TableHead className="w-[110px] text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
              Status
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <QueueRow
              key={r.id}
              row={r}
              selected={r.id === selectedId}
              onClick={() => onSelect(r.id)}
            />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function QueueRow({
  row,
  selected,
  onClick,
}: {
  row: QueueCall;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <TableRow
      data-testid="queue-row"
      data-call-id={row.id}
      data-selected={selected || undefined}
      onClick={onClick}
      className={`cursor-pointer border-[var(--border-subtle)] ${
        selected
          ? "bg-[var(--bg-elev2)]"
          : "hover:bg-[var(--bg-elev2)]"
      }`}
      style={{
        borderLeft: `2px solid ${selected ? "var(--emerald-pass)" : "transparent"}`,
      }}
    >
      <TableCell className="whitespace-nowrap text-[13px] text-[var(--text-muted)] tabular-nums">
        {formatWhen(row.created_at)}
      </TableCell>
      <TableCell className="text-[13px]" style={{ maxWidth: 280 }}>
        <div
          className="font-medium text-[var(--text-primary)] truncate"
          title={row.customer_name ?? row.filename ?? row.id}
        >
          {row.customer_name ?? shortFilename(row.filename)}
        </div>
        <div
          className="mt-0.5 font-mono text-[11px] text-[var(--text-dim)] truncate"
          title={row.filename ?? undefined}
        >
          {row.filename ? shortFilename(row.filename) : row.id.slice(0, 8)}
        </div>
      </TableCell>
      <TableCell className="text-[13px] text-[var(--text-primary)]">
        {row.supplier ?? "—"}
      </TableCell>
      <TableCell className="text-[13px] text-[var(--text-muted)]">
        {row.agent_name ?? "—"}
      </TableCell>
      <TableCell>
        <ScoreBar score={row.score ?? null} />
      </TableCell>
      <TableCell>
        <StatusPill status={row.review_status} />
      </TableCell>
    </TableRow>
  );
}

function StatusPill({ status }: { status: string }) {
  const s = (status || "").toLowerCase();
  if (s === "reviewed" || s === "completed")
    return (
      <Badge className="border-emerald-500/30 bg-emerald-500/10 text-[var(--emerald-pass)]">
        ● Reviewed
      </Badge>
    );
  // unclaimed / in_review legacy values collapse to "Pending".
  return (
    <Badge variant="outline" className="border-[var(--border-strong)] text-[var(--text-muted)]">
      ● Pending
    </Badge>
  );
}

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    const diffMs = Date.now() - d.getTime();
    const mins = Math.round(diffMs / 60_000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return iso;
  }
}
