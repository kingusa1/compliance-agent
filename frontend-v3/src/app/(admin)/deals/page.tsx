"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Inbox, Search } from "lucide-react";

import { AuthGuard } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import {
  getDealsListQuery,
  type DealRow,
} from "@/lib/queries/aggregator";
import { useDebouncedValue } from "@/lib/hooks/useDebouncedValue";
import { useUrlState } from "@/lib/hooks/useUrlState";
import { useRealtimeInvalidate } from "@/lib/hooks/useRealtimeInvalidate";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { CursorPagination } from "@/components/shared/CursorPagination";
import { DealsTable } from "./DealsTable";

// 2026-05-14 audit fix: align with the 7-state taxonomy emitted by
// backend/app/deal_lifecycle.py:derive_lifecycle_status. Previous values
// (in_progress / closed_done / closed_lost) matched the old CustomerDeal
// status column but the list endpoint now overwrites `lifecycle_status`
// at response-time with the derived 7-state value — so the old filter
// always returned zero rows. Three convenience buckets collapse the 7
// states into the user-facing categories on the tab strip; the in-memory
// filter at line ~88 expands them again before comparing.
type LifecycleFilter =
  | "all"
  | "in_progress"     // open / lead_gen_done / pre_sales_done / verbal_done / loa_done
  | "verified"        // all required phases done
  | "rejected";       // any phase ended in rejection

const FILTER_LABELS: Record<LifecycleFilter, string> = {
  all: "All",
  in_progress: "In progress",
  verified: "Verified",
  rejected: "Rejected",
};

const FILTER_KEYS: readonly LifecycleFilter[] = [
  "all",
  "in_progress",
  "verified",
  "rejected",
] as const;

const _IN_PROGRESS_STATES = new Set([
  "open",
  "lead_gen_done",
  "pre_sales_done",
  "verbal_done",
  "loa_done",
]);

function parseFilter(raw: string): LifecycleFilter {
  return (FILTER_KEYS as readonly string[]).includes(raw)
    ? (raw as LifecycleFilter)
    : "all";
}

const DEALS_PAGE_LIMIT = 50;

export default function DealsPage() {
  return (
    <AuthGuard allowedRoles={["lead", "admin"]}>
      <DealsPageBody />
    </AuthGuard>
  );
}

function DealsPageBody() {
  const { get, set, setMany } = useUrlState();
  const [q, setQ] = useState(() => get("q"));
  const debouncedQ = useDebouncedValue(q, 300);
  const filter = parseFilter(get("filter") || "all");
  const offset = Math.max(0, parseInt(get("offset") || "0", 10) || 0);

  const setFilter = (next: LifecycleFilter) =>
    setMany({ filter: next === "all" ? null : next, offset: null });

  // Mirror the debounced search into ?q= and reset offset on change.
  useEffect(() => {
    if (get("q") === debouncedQ) return;
    setMany({ q: debouncedQ || null, offset: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ]);

  // Realtime — when a reviewer edits customer_name (which dual-writes
  // to CustomerDeal.customer_name) or the pipeline finalises and
  // updates the deal's lifecycle / score, refetch the list silently.
  // No-op unless NEXT_PUBLIC_USE_REALTIME=1.
  useRealtimeInvalidate("customer_deals", [["deals", "list"]]);
  useRealtimeInvalidate("calls", [["deals", "list"]]);

  // Lifecycle filtering still happens in-memory (backend filter is
  // `status` not `lifecycle_status`); cursor pagination drives ?offset.
  const query = useQuery({
    ...getDealsListQuery({
      q: debouncedQ.trim() || undefined,
      limit: DEALS_PAGE_LIMIT,
      offset,
    }),
  });

  const deals: DealRow[] = useMemo(() => {
    if (!query.data) return [];
    if (filter === "all") return query.data.deals;
    return query.data.deals.filter((d) => {
      const s = (d.lifecycle_status || "").toLowerCase();
      if (filter === "in_progress") return _IN_PROGRESS_STATES.has(s);
      if (filter === "verified") return s === "verified";
      if (filter === "rejected") return s === "rejected";
      return false;
    });
  }, [query.data, filter]);

  return (
    // 2026-05-24 — switched from max-w-7xl box to /customers-style full-height
    // flex column so the table spans the same width as the rest of the admin
    // surfaces. The container caps scroll on the body, not the page, so
    // sticky filter bar + paginator stay anchored.
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        overflow: "hidden",
        minWidth: 0,
      }}
    >
      {/* Top bar — title + count + search + filter tabs */}
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
          Deals
        </h1>
        {query.isSuccess && (
          <Badge variant="outline" className="tabular-nums">
            {query.data.total} total
          </Badge>
        )}

        <div className="ml-4 relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-[var(--text-dim)]" />
          <Input
            placeholder="Search by customer…"
            className="h-8 w-72 pl-8 text-[13px]"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            data-testid="deals-search"
          />
          <span data-testid="deals-q-url" hidden>
            {get("q")}
          </span>
        </div>

        <div
          className="ml-auto flex items-center gap-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-0.5"
          role="tablist"
          aria-label="Lifecycle filter"
        >
          {(Object.keys(FILTER_LABELS) as LifecycleFilter[]).map((k) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={filter === k}
              onClick={() => setFilter(k)}
              data-testid={`filter-${k}`}
              className={
                filter === k
                  ? "rounded-sm bg-[var(--bg-elev3)] px-2.5 py-1 text-[12px] font-medium text-[var(--text-primary)]"
                  : "rounded-sm px-2.5 py-1 text-[12px] font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              }
            >
              {FILTER_LABELS[k]}
            </button>
          ))}
        </div>
      </div>

      {/* Scrollable table area */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "16px 24px",
        }}
      >
        {query.isLoading && <DealsTableSkeleton />}

        {query.isError && (
          <ErrorBanner error={query.error} onRetry={() => query.refetch()} />
        )}

        {query.isSuccess && deals.length === 0 && <EmptyState filter={filter} />}

        {query.isSuccess && deals.length > 0 && <DealsTable deals={deals} />}

        {query.isSuccess && query.data.total > 0 && (
          <div className="mt-4 overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
            <CursorPagination
              offset={offset}
              limit={DEALS_PAGE_LIMIT}
              total={query.data.total}
              disabled={query.isFetching}
              onChange={(next) => set("offset", next === 0 ? null : next)}
            />
          </div>
        )}

        {query.isSuccess && filter !== "all" && (
          <p className="mt-2 text-right text-[12px] text-[var(--text-dim)] tabular-nums">
            filter: <span className="text-[var(--text-muted)]">{FILTER_LABELS[filter]}</span>
          </p>
        )}
      </div>
    </div>
  );
}

function DealsTableSkeleton() {
  return (
    <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
      <div className="border-b border-[var(--border-subtle)] px-4 py-3">
        <Skeleton className="h-4 w-32" />
      </div>
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-4 border-b border-[var(--border-subtle)] px-4 py-3 last:border-b-0"
        >
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-4 w-28" />
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-4 w-20" />
        </div>
      ))}
    </div>
  );
}

function EmptyState({ filter }: { filter: LifecycleFilter }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-[var(--border-subtle)] bg-[var(--bg-elev1)] px-6 py-16 text-center">
      <Inbox className="h-8 w-8 text-[var(--text-dim)]" aria-hidden="true" />
      <div className="text-[15px] font-medium text-[var(--text-primary)]">
        No deals {filter !== "all" ? `with lifecycle "${filter}"` : "yet"}
      </div>
      <div className="max-w-sm text-[13px] text-[var(--text-muted)]">
        Once a customer has at least one upload, the pipeline groups it into a
        deal automatically.
      </div>
    </div>
  );
}

function ErrorBanner({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  const message =
    error instanceof ApiError
      ? `${error.status} ${error.body || error.message}`
      : error instanceof Error
        ? error.message
        : "Unknown error";
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-[13px] text-red-400"
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="flex-1">
        <div className="font-medium">Couldn’t load deals</div>
        <div className="mt-1 text-[12px] text-red-400/75">{message}</div>
      </div>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}
