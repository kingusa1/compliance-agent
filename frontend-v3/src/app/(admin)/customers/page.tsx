"use client";

/**
 * /customers — ported from
 * design/handoff-bundle/project/screens/customers.jsx.
 *
 * Top bar: H1 + count chip + search + 2 filter dropdowns + +Add Customer.
 * Comfortable density 8-col table with status-pilled "Worst Action".
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Search, ChevronDown, Inbox } from "lucide-react";

import { useAdminCustomersQuery } from "@/lib/queries/admin";
import { useDebouncedValue } from "@/lib/hooks/useDebouncedValue";
import { useUrlState } from "@/lib/hooks/useUrlState";
import { Pill, type PillTone } from "@/components/design/Pill";
import { EmptyState } from "@/components/design/EmptyState";
import { CursorPagination } from "@/components/shared/CursorPagination";
import { AddCustomerDialog } from "./AddCustomerDialog";

const PAGE_LIMIT = 50;

const COL = "1.6fr 1.2fr 70px 70px 1.2fr 110px 100px 70px";

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

function FilterDropdown({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        height: 30,
        padding: "0 10px",
        background: "var(--bg-elev2)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 6,
        fontSize: 12,
        color: "var(--text-primary)",
        cursor: "pointer",
      }}
    >
      <span style={{ color: "var(--text-faint)" }}>{label}:</span>
      <span>{value}</span>
      <ChevronDown size={12} style={{ color: "var(--text-muted)" }} />
    </div>
  );
}

function worstActionTone(action: string | null): PillTone {
  switch ((action || "").toUpperCase()) {
    case "PASS":
      return "emerald";
    case "REVIEW":
      return "amber";
    case "COACHING":
      return "blue";
    case "FAIL":
      return "red";
    case "BLOCK":
      return "violet";
    default:
      return "neutral";
  }
}

export default function CustomersListPage() {
  const router = useRouter();
  const { get, set, setMany } = useUrlState();
  const [search, setSearch] = useState(() => get("q"));
  const debouncedSearch = useDebouncedValue(search, 300);
  const offset = Math.max(0, parseInt(get("offset") || "0", 10) || 0);
  const [addOpen, setAddOpen] = useState(false);

  // Mirror the debounced search into the URL ?q= and reset offset to 0
  // when the search term changes. The mount-time value is also written
  // through, but identical writes are a no-op for router.replace.
  useEffect(() => {
    const current = get("q");
    if (current === debouncedSearch) return;
    setMany({ q: debouncedSearch || null, offset: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  const customers = useAdminCustomersQuery({
    q: debouncedSearch || undefined,
    limit: PAGE_LIMIT,
    offset,
  });
  const rows = customers.data?.customers ?? [];

  const total = customers.data?.total ?? rows.length;

  const filtered = useMemo(() => rows, [rows]);

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
          Customers
        </h1>
        <Pill tone="neutral" mono>
          {total} shown
        </Pill>
        <div
          style={{
            width: 1,
            height: 18,
            background: "var(--border-subtle)",
            margin: "0 4px",
          }}
        />
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
            width: 280,
          }}
        >
          <Search size={14} style={{ color: "var(--text-dim)" }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search customers, MPAN, deal id…"
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
        <FilterDropdown label="Supplier" value="All" />
        <FilterDropdown label="Worst action" value="All" />
        <div style={{ flex: 1 }} />
        <button
          type="button"
          onClick={() => setAddOpen(true)}
          data-testid="add-customer-trigger"
          style={{
            height: 32,
            padding: "0 12px",
            fontSize: 13,
            fontWeight: 500,
            background: "var(--emerald)",
            color: "#04201a",
            border: "1px solid var(--emerald)",
            borderRadius: 6,
            cursor: "pointer",
            fontFamily: "inherit",
            boxShadow: "var(--shadow-sm)",
          }}
        >
          + Add Customer
        </button>
        <AddCustomerDialog
          open={addOpen}
          onOpenChange={setAddOpen}
          onCreated={(slug) => router.push(`/customers/${encodeURIComponent(slug)}`)}
        />
      </div>

      {/* Table */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {filtered.length > 0 && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: COL,
              gap: 12,
              padding: "10px 24px",
              borderBottom: "1px solid var(--border-subtle)",
              background: "var(--bg-elev1)",
              position: "sticky",
              top: 0,
              zIndex: 1,
            }}
          >
            <HeaderCell>Customer</HeaderCell>
            <HeaderCell>Supplier</HeaderCell>
            <HeaderCell>Deals</HeaderCell>
            <HeaderCell>Calls</HeaderCell>
            <HeaderCell>Agents</HeaderCell>
            <HeaderCell>Worst Action</HeaderCell>
            <HeaderCell>Last Seen</HeaderCell>
            <HeaderCell>Flags</HeaderCell>
          </div>
        )}
        <div style={{ flex: 1, overflowY: "auto" }} className="ca-scroll">
          {customers.isLoading
            ? Array.from({ length: 10 }).map((_, i) => (
                <div
                  key={i}
                  style={{
                    display: "grid",
                    gridTemplateColumns: COL,
                    gap: 12,
                    padding: "14px 24px",
                    borderBottom: "1px solid var(--border-subtle)",
                  }}
                >
                  {[180, 110, 30, 30, 100, 80, 60, 30].map((w, j) => (
                    <div
                      key={j}
                      style={{
                        height: 10,
                        width: w,
                        background: "var(--bg-elev3)",
                        borderRadius: 3,
                        animation: "ca-pulse 1.5s ease-in-out infinite",
                      }}
                    />
                  ))}
                </div>
              ))
            : filtered.length === 0
              ? (
                <EmptyState
                  icon={<Inbox size={20} />}
                  title="No customers yet"
                  body="Add a customer to begin uploading and reviewing calls."
                />
              )
              : filtered.map((c) => {
                  const flags = c.critical_flag_count ?? 0;
                  const supplier = (c.suppliers ?? [])[0] ?? "—";
                  const agentLabel =
                    (c.agents ?? []).length === 0
                      ? "—"
                      : c.agents.length === 1
                        ? c.agents[0]
                        : `${c.agents[0]} +${c.agents.length - 1}`;
                  return (
                    <Link
                      key={c.slug}
                      href={`/customers/${encodeURIComponent(c.slug)}`}
                      style={{
                        display: "grid",
                        gridTemplateColumns: COL,
                        gap: 12,
                        alignItems: "center",
                        padding: "12px 24px",
                        borderBottom: "1px solid var(--border-subtle)",
                        fontSize: 13,
                        cursor: "pointer",
                        textDecoration: "none",
                        color: "inherit",
                      }}
                    >
                      <div style={{ color: "var(--text-primary)", fontWeight: 500 }}>
                        {c.display_name}
                      </div>
                      <div style={{ color: "var(--text-muted)" }}>{supplier}</div>
                      <div
                        style={{
                          color: "var(--text-primary)",
                          fontFamily: "var(--font-mono)",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {c.deal_count ?? 0}
                      </div>
                      <div
                        style={{
                          color: "var(--text-primary)",
                          fontFamily: "var(--font-mono)",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {c.call_count ?? 0}
                      </div>
                      <div
                        style={{
                          color: "var(--text-muted)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {agentLabel}
                      </div>
                      <div>
                        <Pill tone={worstActionTone(c.worst_action)} dot>
                          {c.worst_action || "—"}
                        </Pill>
                      </div>
                      <div
                        style={{
                          color: "var(--text-muted)",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {c.last_seen
                          ? new Date(c.last_seen).toLocaleDateString()
                          : "—"}
                      </div>
                      <div
                        style={{
                          color: flags > 0 ? "var(--amber)" : "var(--text-faint)",
                          fontFamily: "var(--font-mono)",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {flags > 0 ? `⚑ ${flags}` : "—"}
                      </div>
                    </Link>
                  );
                })}
        </div>
        {!customers.isLoading && total > 0 && (
          <CursorPagination
            offset={offset}
            limit={PAGE_LIMIT}
            total={total}
            disabled={customers.isFetching}
            onChange={(next) => set("offset", next === 0 ? null : next)}
          />
        )}
      </div>
    </div>
  );
}
