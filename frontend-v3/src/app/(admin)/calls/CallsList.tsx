"use client";

import { useRouter } from "next/navigation";

import { formatScorePercent } from "@/lib/score";
import type { AdminCallRow } from "@/lib/queries/admin";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

/**
 * Flat call-list view (rendered when the /calls toggle = "Call-list").
 * Comfortable density: When | Customer | Supplier | Agent | Score | Compliant.
 */
export function CallsList({ calls }: { calls: AdminCallRow[] }) {
  const router = useRouter();
  return (
    <div
      data-slot="admin-calls-list"
      className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]"
    >
      <Table>
        <TableHeader>
          <TableRow className="border-[var(--border-subtle)] hover:bg-transparent">
            <Th>When</Th>
            <Th>Customer</Th>
            <Th>Supplier</Th>
            <Th>Agent</Th>
            <Th>Score</Th>
            <Th>Compliant</Th>
          </TableRow>
        </TableHeader>
        <TableBody>
          {calls.map((c) => (
            <TableRow
              key={c.id}
              onClick={() => router.push(`/calls/${c.id}`)}
              className="cursor-pointer border-[var(--border-subtle)] hover:bg-[var(--bg-elev2)]"
            >
              <TableCell className="whitespace-nowrap py-3 text-[13px] text-[var(--text-muted)]">
                {formatWhen(c.created_at)}
              </TableCell>
              <TableCell className="py-3 text-[13px]">{c.customer_name ?? "—"}</TableCell>
              <TableCell className="py-3 text-[13px] text-[var(--text-muted)]">
                {c.detected_supplier ?? "—"}
              </TableCell>
              <TableCell className="py-3 text-[13px] text-[var(--text-muted)]">
                {c.agent_name ?? "—"}
              </TableCell>
              <TableCell className="py-3 text-[13px] tabular-nums">
                {formatScorePercent(c.score)}
              </TableCell>
              <TableCell className="py-3">
                <CompliancePill status={c.compliance_status ?? c.status} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
      {children}
    </TableHead>
  );
}

function CompliancePill({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge variant="outline">—</Badge>;
  const s = status.toLowerCase();
  if (s === "compliant" || s === "completed" || s === "pass")
    return (
      <Badge className="border-emerald-500/30 bg-emerald-500/10 text-emerald-400">
        {status}
      </Badge>
    );
  if (s === "non_compliant" || s === "failed" || s === "fail")
    return (
      <Badge className="border-red-500/30 bg-red-500/10 text-red-400">{status}</Badge>
    );
  if (s === "review" || s === "processing" || s === "queued" || s === "pending")
    return (
      <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-400">
        {status}
      </Badge>
    );
  return <Badge variant="outline">{status}</Badge>;
}

function formatWhen(iso: string): string {
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
