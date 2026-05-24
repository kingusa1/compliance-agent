"use client";

import { useRouter } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { ScoreBar } from "@/components/shared/ScoreBar";
import { formatCustomerName, isPlaceholderCustomerName } from "@/lib/customer";
import type { DealRow } from "@/lib/queries/aggregator";

/**
 * DealsTable — full-width grid (mirrors /customers density).
 *
 * 2026-05-24 redesign: was a 5-column shadcn Table inside a max-w-7xl
 * container; reviewers couldn't fit MPAN + value + live date in the
 * same row, and customer-name placeholders rendered as the literal
 * "(pending audio upload)" string instead of the canonical "Unknown".
 *
 * New layout: a CSS-Grid 9-column row matching the /customers grid so
 * the two screens feel like one product. Customer name routes through
 * `formatCustomerName` + shows the amber "AI couldn't read" chip on
 * placeholder values. Risk-tag dots render inline next to the action
 * pill so reviewers can scan for vulnerable / ombudsman flags without
 * opening the detail page.
 */

export type DealsTableProps = {
  deals: DealRow[];
};

// Customer · Supplier · Lifecycle · Score · Action · Value · MPAN/MPRN · Live date · Created
const COL = "1.6fr 1.1fr 110px 160px 90px 90px 130px 100px 100px";

function HeaderCell({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 11,
        fontWeight: 500,
        color: "var(--text-faint)",
        textTransform: "uppercase",
        letterSpacing: "0.06em",
      }}
    >
      {children}
    </div>
  );
}

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

const ACTION_VISUAL: Record<string, string> = {
  PASS: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
  REVIEW: "border-amber-500/30 bg-amber-500/10 text-amber-400",
  COACHING: "border-blue-500/30 bg-blue-500/10 text-blue-400",
  FAIL: "border-red-500/30 bg-red-500/10 text-red-400",
  BLOCK: "border-red-500/30 bg-red-500/10 text-red-400",
};

function ActionPill({ action }: { action: string | null }) {
  if (!action) return <span className="text-[12px] text-[var(--text-dim)]">—</span>;
  const cls = ACTION_VISUAL[action.toUpperCase()] ?? "border-[var(--border-subtle)] text-[var(--text-muted)]";
  return <Badge className={cls}>{action}</Badge>;
}

function formatGBP(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "GBP",
    maximumFractionDigits: 0,
  }).format(v);
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    // 2026-05-24 — `new Date(null)` returns 1970-01-01 (no throw); `new
    // Date("invalid")` returns Invalid Date (no throw, `toLocaleDateString`
    // returns "Invalid Date" string). Catching only `try/catch` missed
    // both: the column rendered "01 Jan 70" on a null `expected_live_date`.
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleDateString("en-GB", {
      day: "2-digit",
      month: "short",
      year: "2-digit",
    });
  } catch {
    return "—";
  }
}

export function DealsTable({ deals }: DealsTableProps) {
  const router = useRouter();

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
      {/* Header */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: COL,
          gap: 12,
          alignItems: "center",
          padding: "12px 16px",
          borderBottom: "1px solid var(--border-subtle)",
          background: "var(--bg-elev2)",
        }}
      >
        <HeaderCell>Customer</HeaderCell>
        <HeaderCell>Supplier</HeaderCell>
        <HeaderCell>Lifecycle</HeaderCell>
        <HeaderCell>Score</HeaderCell>
        <HeaderCell>Action</HeaderCell>
        <HeaderCell>Value</HeaderCell>
        <HeaderCell>MPAN/MPRN</HeaderCell>
        <HeaderCell>Live date</HeaderCell>
        <HeaderCell>Created</HeaderCell>
      </div>

      {/* Rows */}
      {deals.map((d) => {
        const placeholder = isPlaceholderCustomerName(d.customer_name);
        return (
          <div
            key={d.id}
            data-testid="deal-row"
            data-deal-id={d.id}
            onClick={() => router.push(`/deals/${d.id}`)}
            style={{
              display: "grid",
              gridTemplateColumns: COL,
              gap: 12,
              alignItems: "center",
              padding: "12px 16px",
              borderBottom: "1px solid var(--border-subtle)",
              cursor: "pointer",
              fontSize: 13,
            }}
            className="hover:bg-[var(--bg-elev2)]"
          >
            {/* Two-line layout — name truncates on line 1; placeholder
                chip + risk dots live on line 2. Avoids the 1366px-laptop
                clipping where `truncate` on a parent containing the chip
                shoved it off the right edge (code-review HIGH 1). */}
            <div className="min-w-0">
              <div
                className="truncate font-medium text-[var(--text-primary)]"
                title={placeholder ? "AI couldn't read this customer name from the audio." : (d.customer_name ?? undefined)}
              >
                {formatCustomerName(d.customer_name)}
              </div>
              {(placeholder || (d.risk_tags && d.risk_tags.length > 0)) && (
                <div className="mt-0.5 flex items-center gap-1.5">
                  {placeholder && (
                    <span
                      className="rounded-sm bg-amber-100 px-1 py-0.5 text-[9px] font-medium uppercase text-amber-900"
                      aria-label="AI couldn't read"
                    >
                      AI couldn&apos;t read
                    </span>
                  )}
                  {d.risk_tags?.slice(0, 3).map((tag) => (
                    <span
                      key={tag}
                      className="inline-block size-1.5 rounded-full bg-red-500"
                      title={tag}
                      aria-label={`Risk: ${tag}`}
                    />
                  ))}
                </div>
              )}
            </div>
            <div className="truncate text-[var(--text-muted)]">{d.supplier ?? "—"}</div>
            <div><LifecyclePill status={d.lifecycle_status} /></div>
            <div><ScoreBar value={d.final_score} /></div>
            <div><ActionPill action={d.final_action} /></div>
            <div className="tabular-nums text-[var(--text-primary)]">{formatGBP(d.deal_value_gbp)}</div>
            <div className="truncate font-mono text-[11.5px] text-[var(--text-muted)]" title={d.mpan_or_mprn ?? undefined}>
              {d.mpan_or_mprn ?? "—"}
            </div>
            <div className="whitespace-nowrap text-[var(--text-muted)]">{formatDate(d.expected_live_date)}</div>
            <div className="whitespace-nowrap text-[var(--text-muted)]">{formatDate(d.created_at)}</div>
          </div>
        );
      })}
    </div>
  );
}
