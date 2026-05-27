"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronRight } from "lucide-react";

import type { AdminCallRow } from "@/lib/queries/admin";
import { formatScorePercent } from "@/lib/score";
import { formatCustomerName } from "@/lib/customer";
import { Badge } from "@/components/ui/badge";

/**
 * Deal-grouped accordion view (default for admin /calls toggle).
 *
 * Group row: customer name + supplier chip + £ value + status pill + caret.
 * Expanded row reveals nested calls (When · Type · Agent · Score · Compliant).
 *
 * Calls without a deal_id fall under a synthetic "Unassigned" group so
 * everything still renders. Groups are sorted by most-recent call first.
 */
type DealGroup = {
  key: string; // deal_id or "unassigned"
  deal_ref?: string | null;
  customer_name: string | null;
  supplier: string | null;
  deal_value_gbp: number | null;
  status: string | null;
  most_recent: string;
  calls: AdminCallRow[];
};

export function DealGroupedView({ calls }: { calls: AdminCallRow[] }) {
  const groups = useMemo(() => groupByDeal(calls), [calls]);
  const router = useRouter();
  const [open, setOpen] = useState<Record<string, boolean>>(() => {
    // Default-expand the first group so the empty "click to expand" UX
    // doesn't make the page look empty.
    const m: Record<string, boolean> = {};
    if (groups.length > 0) m[groups[0].key] = true;
    return m;
  });

  return (
    <div
      data-slot="admin-deal-grouped"
      className="flex flex-col gap-3"
    >
      {groups.map((g) => {
        const isOpen = !!open[g.key];
        return (
          <section
            key={g.key}
            data-deal-group={g.key}
            className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]"
          >
            <button
              type="button"
              onClick={() =>
                setOpen((prev) => ({ ...prev, [g.key]: !prev[g.key] }))
              }
              className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-[var(--bg-elev2)]"
            >
              {isOpen ? (
                <ChevronDown className="h-4 w-4 text-[var(--text-muted)]" />
              ) : (
                <ChevronRight className="h-4 w-4 text-[var(--text-muted)]" />
              )}
              <span className="text-[14px] font-medium">
                {formatCustomerName(g.customer_name)}
              </span>
              {g.supplier && (
                <Badge variant="outline" className="font-normal">
                  {g.supplier}
                </Badge>
              )}
              {g.deal_value_gbp != null && (
                <span className="font-mono text-[13px] tabular-nums text-[var(--text-primary)]">
                  £{formatGbp(g.deal_value_gbp)}
                </span>
              )}
              {g.deal_ref && (
                <span className="font-mono text-[12px] text-[var(--text-dim)]">
                  {g.deal_ref}
                </span>
              )}
              {g.status && <StatusPill status={g.status} />}
              <span className="ml-auto text-[12px] text-[var(--text-muted)]">
                {g.calls.length} call{g.calls.length === 1 ? "" : "s"}
              </span>
            </button>

            {isOpen && (
              <ul className="divide-y divide-[var(--border-subtle)] border-t border-[var(--border-subtle)] bg-[var(--bg-canvas)]">
                {g.calls.map((c) => (
                  <li
                    key={c.id}
                    onClick={() => router.push(`/calls/${c.id}`)}
                    className="grid cursor-pointer grid-cols-[120px_1fr_1fr_80px_120px] items-center gap-3 px-6 py-2.5 text-[13px] hover:bg-[var(--bg-elev2)]"
                  >
                    <span className="text-[var(--text-muted)]">
                      {formatWhen(c.created_at)}
                    </span>
                    <span className="flex flex-wrap gap-1">
                      {/* Wave-26 — render one Badge per detected
                          segment (lead_gen + pre_sales + verbal + loa).
                          Falls back to call_type when segments[] is
                          empty (legacy data). */}
                      {Array.isArray(c.segments) && c.segments.length > 0 ? (
                        c.segments.map((s, k) => (
                          <Badge key={k} variant="outline" className="font-normal">
                            {(s.kind ?? "—").replace(/_/g, " ")}
                          </Badge>
                        ))
                      ) : (
                        <Badge variant="outline" className="font-normal">
                          {c.call_type ?? "—"}
                        </Badge>
                      )}
                    </span>
                    <span className="text-[var(--text-muted)]">
                      {c.agent_name ?? "—"}
                    </span>
                    <span className="font-mono tabular-nums">
                      {formatScorePercent(c.score)}
                    </span>
                    <CompliancePill status={c.compliance_status ?? c.status} />
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

function groupByDeal(calls: AdminCallRow[]): DealGroup[] {
  const map = new Map<string, DealGroup>();
  for (const c of calls) {
    const key = c.deal_id ?? "unassigned";
    if (!map.has(key)) {
      map.set(key, {
        key,
        deal_ref: c.deal_ref ?? null,
        customer_name: c.customer_name,
        supplier: c.detected_supplier,
        deal_value_gbp: c.deal_value_gbp ?? null,
        status: null,
        most_recent: c.created_at,
        calls: [],
      });
    }
    const g = map.get(key)!;
    g.calls.push(c);
    if (c.created_at > g.most_recent) g.most_recent = c.created_at;
  }
  return Array.from(map.values()).sort((a, b) =>
    a.most_recent < b.most_recent ? 1 : -1,
  );
}

function StatusPill({ status }: { status: string }) {
  const s = status.toLowerCase();
  let cls = "border-[var(--border-subtle)] bg-[var(--bg-elev2)] text-[var(--text-muted)]";
  if (s === "active" || s === "open" || s === "pass") cls = "border-emerald-500/30 bg-emerald-500/10 text-emerald-400";
  else if (s === "review" || s === "in_progress") cls = "border-amber-500/30 bg-amber-500/10 text-amber-400";
  else if (s === "fail" || s === "closed_lost") cls = "border-red-500/30 bg-red-500/10 text-red-400";
  return <Badge className={cls}>{status}</Badge>;
}

function CompliancePill({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge variant="outline">—</Badge>;
  const s = status.toLowerCase();
  if (s === "compliant" || s === "pass" || s === "completed")
    return (
      <Badge className="border-emerald-500/30 bg-emerald-500/10 text-emerald-400">
        {status}
      </Badge>
    );
  if (s === "non_compliant" || s === "fail" || s === "failed")
    return (
      <Badge className="border-red-500/30 bg-red-500/10 text-red-400">{status}</Badge>
    );
  return (
    <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-400">{status}</Badge>
  );
}

function formatWhen(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function formatGbp(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(Math.round(n));
}
