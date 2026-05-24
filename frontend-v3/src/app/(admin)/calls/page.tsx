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

export default function CallsPage() {
  const { get, setMany } = useUrlState();
  const [search, setSearch] = useState(() => get("q"));
  const debouncedSearch = useDebouncedValue(search, 300);
  const calls = useAdminCallsQuery({ limit: 200 });
  const rows = calls.data?.calls ?? [];

  // 2026-05-24 wiring audit MEDIUM — mount realtime invalidation so
  // new uploads + verdict updates push into the list without window
  // focus; matches the pattern in queue / tracker / rejections.
  useRealtimeInvalidate("calls", [["admin", "calls"]]);

  const filtered = useMemo(() => {
    const q = debouncedSearch.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((c) =>
      [c.customer_name, c.detected_supplier, c.agent_name, c.filename]
        .filter(Boolean)
        .some((s) => (s as string).toLowerCase().includes(q)),
    );
  }, [rows, debouncedSearch]);

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
