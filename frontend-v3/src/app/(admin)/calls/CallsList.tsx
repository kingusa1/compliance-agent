"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { useQueryClient, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";

import { formatScorePercent } from "@/lib/score";
import { apiFetch } from "@/lib/api";
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

function DeleteButton({ callId, customerName }: { callId: string; customerName: string | null }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const m = useMutation({
    mutationFn: () => apiFetch(`/api/calls/${callId}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Call deleted");
      qc.invalidateQueries({ queryKey: ["calls"] });
      qc.invalidateQueries({ queryKey: ["admin", "calls"] });
      qc.invalidateQueries({ queryKey: ["dashboard:stats"] });
      qc.invalidateQueries({ queryKey: ["customers"] });
      qc.invalidateQueries({ queryKey: ["deals"] });
      setConfirming(false);
    },
    onError: (e) => {
      toast.error("Couldn't delete call", {
        description: e instanceof Error ? e.message : String(e),
      });
      setConfirming(false);
    },
  });
  if (confirming) {
    return (
      <span className="flex items-center gap-1.5">
        <button
          type="button"
          disabled={m.isPending}
          onClick={(e) => {
            e.stopPropagation();
            m.mutate();
          }}
          className="rounded border border-red-500/40 bg-red-500/10 px-2 py-0.5 text-[11px] font-semibold text-red-300 hover:bg-red-500/20 disabled:opacity-50"
        >
          {m.isPending ? "…" : "Confirm"}
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setConfirming(false);
          }}
          className="rounded px-2 py-0.5 text-[11px] text-[var(--text-muted)] hover:bg-[var(--bg-elev3)]"
        >
          Cancel
        </button>
      </span>
    );
  }
  return (
    <button
      type="button"
      title={`Delete this call${customerName ? ` (${customerName})` : ""}`}
      onClick={(e) => {
        e.stopPropagation();
        setConfirming(true);
      }}
      className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--bg-elev3)] hover:text-red-400"
      aria-label="Delete this call"
    >
      <Trash2 className="size-3.5" />
    </button>
  );
}

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
            <Th><span className="sr-only">Actions</span></Th>
          </TableRow>
        </TableHeader>
        <TableBody>
          {calls.map((c) => (
            <TableRow
              key={c.id}
              onClick={() => router.push(`/calls/${c.id}`)}
              className="group cursor-pointer border-[var(--border-subtle)] hover:bg-[var(--bg-elev2)]"
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
                <CompliancePill
                  status={deriveCompliancePillStatus(c)}
                />
              </TableCell>
              <TableCell className="py-3 text-right">
                <DeleteButton callId={c.id} customerName={c.customer_name ?? null} />
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

/**
 * Resolve the Compliant column pill once per row.
 *
 * The DB has two parallel signals: `compliant` (boolean — current
 * pipeline) and `compliance_status` (string — older taxonomy). They
 * drifted: e.g. Sam Escrich (Bob's Glazing) has compliant=true but
 * compliance_status="pending", and Bradley Clayton has compliant=true
 * with compliance_status="non_compliant". Reading either alone gives
 * the wrong pill for ~80% of rows.
 *
 * Rule once the pipeline has finished (status is terminal):
 *   compliant === true   → "compliant"
 *   compliant === false  → "non_compliant"
 *   compliant === null   → fall back to compliance_status / status
 * Mid-pipeline we trust the lifecycle field so the pill shows
 * "processing" / "needs_manual_review" instead of guessing.
 */
function deriveCompliancePillStatus(c: AdminCallRow): string | null {
  const terminal = c.status === "completed" || c.status === "needs_manual_review";
  if (terminal) {
    const compliantTruthy =
      c.compliant === true ||
      (typeof c.compliant === "string" && c.compliant.toLowerCase() === "true");
    const compliantFalsy =
      c.compliant === false ||
      (typeof c.compliant === "string" && c.compliant.toLowerCase() === "false");
    if (compliantTruthy) return "compliant";
    if (compliantFalsy) return "non_compliant";
  }
  return c.compliance_status ?? c.status ?? null;
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
