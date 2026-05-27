"use client";

/**
 * /calls — flat call-list view, comfortable density.
 *
 * Restored 2026-05-10 late session. The dashboard "All Calls" tile and
 * the /compliant empty-state both link here, and the tracker's per-row
 * focus is rejection-shaped — reviewers still need a flat call list to
 * find/filter/delete recordings.
 */
import { useMemo, useState } from "react";
import Link from "next/link";
import { Search, Inbox } from "lucide-react";

import { useAdminCallsQuery } from "@/lib/queries/admin";
import { useDebouncedValue } from "@/lib/hooks/useDebouncedValue";
import { useUrlState } from "@/lib/hooks/useUrlState";
import { useRealtimeInvalidate } from "@/lib/hooks/useRealtimeInvalidate";
import { CallsList } from "./CallsList";
import { EmptyState } from "@/components/design/EmptyState";

type CallsStatusFilter = "all" | "compliant" | "non_compliant" | "processing";
const STATUS_FILTER_LABELS: Record<CallsStatusFilter, string> = {
  all: "All",
  compliant: "Compliant",
  non_compliant: "Non-compliant",
  processing: "Processing",
};

function parseStatusFilter(raw: string): CallsStatusFilter {
  return (Object.keys(STATUS_FILTER_LABELS) as string[]).includes(raw)
    ? (raw as CallsStatusFilter)
    : "all";
}

export default function CallsPage() {
  const { get, setMany } = useUrlState();
  const [search, setSearch] = useState(() => get("q"));
  const debouncedSearch = useDebouncedValue(search, 300);
  // Wave-28 — URL-persisted filter chips + supplier dropdown.
  const statusFilter = parseStatusFilter(get("status") || "all");
  const supplierFilter = get("supplier") || "";
  const calls = useAdminCallsQuery({ limit: 200 });
  const rows = calls.data?.calls ?? [];

  // 2026-05-24 wiring audit MEDIUM — mount realtime invalidation so
  // new uploads + verdict updates push into the list without window
  // focus; matches the pattern in queue / tracker / rejections.
  useRealtimeInvalidate("calls", [["admin", "calls"]]);

  const supplierOptions = useMemo(() => {
    const set = new Set<string>();
    for (const c of rows) {
      if (c.detected_supplier) set.add(c.detected_supplier);
    }
    if (supplierFilter) set.add(supplierFilter);
    return Array.from(set).sort();
  }, [rows, supplierFilter]);

  const filtered = useMemo(() => {
    const q = debouncedSearch.trim().toLowerCase();
    return rows.filter((c) => {
      // Status filter
      if (statusFilter !== "all") {
        const terminal = c.status === "completed" || c.status === "needs_manual_review";
        const cTrue = c.compliant === true || (typeof c.compliant === "string" && c.compliant.toLowerCase() === "true");
        const cFalse = c.compliant === false || (typeof c.compliant === "string" && c.compliant.toLowerCase() === "false");
        if (statusFilter === "compliant" && !(terminal && cTrue)) return false;
        if (statusFilter === "non_compliant" && !(terminal && cFalse)) return false;
        if (statusFilter === "processing" && c.status !== "processing") return false;
      }
      // Supplier filter
      if (supplierFilter && (c.detected_supplier || "") !== supplierFilter) return false;
      // Search filter
      if (q) {
        const hay = [c.customer_name, c.detected_supplier, c.agent_name, c.filename]
          .filter(Boolean)
          .some((s) => (s as string).toLowerCase().includes(q));
        if (!hay) return false;
      }
      return true;
    });
  }, [rows, debouncedSearch, statusFilter, supplierFilter]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", minWidth: 0 }}>
      {/* Top bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "14px 24px",
          borderBottom: "1px solid var(--border-subtle)",
          flexShrink: 0,
        }}
      >
        <h1
          style={{
            fontSize: 19,
            fontWeight: 600,
            letterSpacing: "-0.018em",
            margin: 0,
            color: "var(--text-primary)",
          }}
        >
          All Calls
        </h1>
        <span
          style={{
            fontSize: 11,
            color: "var(--text-muted)",
            background: "var(--bg-elev2)",
            padding: "2px 8px",
            borderRadius: 999,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {calls.data?.total ?? rows.length} calls
        </span>
        <div style={{ width: 1, height: 18, background: "var(--border-subtle)", margin: "0 4px" }} />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            height: 32,
            padding: "0 10px",
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
            width: 320,
          }}
        >
          <Search size={14} style={{ color: "var(--text-dim)" }} />
          <input
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setMany({ q: e.target.value || null });
            }}
            placeholder="Search customer, supplier, agent, file…"
            style={{
              background: "transparent",
              border: "none",
              outline: "none",
              color: "var(--text-primary)",
              fontSize: 13,
              flex: 1,
              fontFamily: "inherit",
            }}
          />
        </div>
        {/* Wave-28 — supplier filter dropdown. Client-side filter
            sourced from the current page's distinct suppliers. */}
        <label className="flex items-center gap-2 text-[12px] text-[var(--text-muted)]">
          <span className="uppercase tracking-wide text-[10px] text-[var(--text-faint)]">Supplier</span>
          <select
            value={supplierFilter}
            onChange={(e) => setMany({ supplier: e.target.value || null })}
            aria-label="Supplier filter"
            data-testid="calls-supplier-filter"
            className="h-8 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] px-2 text-[12px] text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-blue)]"
          >
            <option value="">All</option>
            {supplierOptions.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>

        {/* Wave-28 — status filter tabs (All / Compliant / Non-compliant
            / Processing). URL-persisted via ?status=. */}
        <div
          className="flex items-center gap-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-0.5"
          role="tablist"
          aria-label="Status filter"
        >
          {(Object.keys(STATUS_FILTER_LABELS) as CallsStatusFilter[]).map((k) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={statusFilter === k}
              onClick={() => setMany({ status: k === "all" ? null : k })}
              data-testid={`calls-status-${k}`}
              className={
                statusFilter === k
                  ? "rounded-sm bg-[var(--bg-elev3)] px-2.5 py-1 text-[12px] font-medium text-[var(--text-primary)]"
                  : "rounded-sm px-2.5 py-1 text-[12px] font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              }
            >
              {STATUS_FILTER_LABELS[k]}
            </button>
          ))}
        </div>

        <div style={{ flex: 1 }} />
        <Link
          href="/tracker"
          style={{
            fontSize: 12,
            color: "var(--text-muted)",
            textDecoration: "none",
          }}
        >
          Switch to Tracker view →
        </Link>
      </div>

      {/* List */}
      <div style={{ flex: 1, overflowY: "auto", padding: "16px 24px" }} className="ca-scroll">
        {calls.isLoading ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Loading…</div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={<Inbox size={20} />}
            title={debouncedSearch ? "No calls match that search" : "No calls yet"}
            body={debouncedSearch ? "Try a different keyword." : "Use the Upload Call button on the dashboard."}
          />
        ) : (
          <CallsList calls={filtered} />
        )}
      </div>
    </div>
  );
}
