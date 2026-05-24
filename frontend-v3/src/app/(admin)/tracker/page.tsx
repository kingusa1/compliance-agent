"use client";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { TrackerTable } from "./TrackerTable";
import { TrackerGroupedTable } from "./TrackerGroupedTable";
import { TrackerSidePanel } from "./TrackerSidePanel";
import { TrackerFilterBar } from "./TrackerFilterBar";
import { CATEGORY_KEYS, CATEGORY_LABEL, CATEGORY_HEX } from "./CategoryChip";
import {
  useTrackerRowsQuery,
  downloadTrackerXlsx,
  type TrackerFilters,
  type TrackerRow,
  type TrackerTab,
} from "@/lib/queries/tracker";
import { UploadModal } from "@/app/(admin)/calls/UploadModal";
import { useRealtimeInvalidate } from "@/lib/hooks/useRealtimeInvalidate";

const TABS: TrackerTab[] = ["awaiting_review", "active", "fixed", "dead", "compliant"];

export default function TrackerPage() {
  const router = useRouter();
  const sp = useSearchParams();
  const tab = (sp.get("tab") ?? "active") as TrackerTab;
  const [month, setMonth] = useState(sp.get("month") ?? "");
  const [search, setSearch] = useState(sp.get("search") ?? "");
  const [categories, setCategories] = useState<Set<string>>(
    new Set((sp.get("category") ?? "").split(",").filter(Boolean)),
  );
  // Advanced filters (date, multi-select, ranges) — held in one state blob
  // so URL persistence and reviewer "Clear all" are trivial.
  const [advanced, setAdvanced] = useState<Partial<TrackerFilters>>(() => ({
    suppliers: (sp.get("suppliers") ?? "").split(",").filter(Boolean) || undefined,
    agents: (sp.get("agents") ?? "").split(",").filter(Boolean) || undefined,
    statuses: (sp.get("statuses") ?? "").split(",").filter(Boolean) || undefined,
    date_from: sp.get("date_from") ?? undefined,
    date_to: sp.get("date_to") ?? undefined,
    date_on: sp.get("date_on") ?? undefined,
    meter: sp.get("meter") ?? undefined,
    value_min: sp.get("value_min") ? Number(sp.get("value_min")) : undefined,
    value_max: sp.get("value_max") ? Number(sp.get("value_max")) : undefined,
    deadline_state: (sp.get("deadline_state") as TrackerFilters["deadline_state"]) ?? undefined,
  }));
  const [selectedRow, setSelectedRow] = useState<TrackerRow | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  // 2026-05-24 tracker enterprise polish — track XLSX export in flight
  // so the button can show "Exporting…" feedback and prevent double-click.
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const filters = useMemo<TrackerFilters>(() => ({
    tab,
    month: month || undefined,
    search: search || undefined,
    category: categories.size > 0 ? [...categories] : undefined,
    ...advanced,
  }), [tab, month, search, categories, advanced]);

  const q = useTrackerRowsQuery(filters);
  const rows = q.data?.rows ?? [];
  const counts = q.data?.count ?? 0;

  // Supabase Realtime — INSERT/UPDATE/DELETE on `calls`, `rejections`, or
  // `customer_deals` invalidates the tracker query so the table refreshes
  // within ~50ms of a DB write. Gated on NEXT_PUBLIC_USE_REALTIME=1; when
  // off, the existing SSE-driven invalidation in useCallEvents handles
  // updates (slower path, ~200-500ms). Path 3 of the 2026-05-16 realtime
  // overhaul.
  useRealtimeInvalidate("calls", [["admin", "tracker"]]);
  useRealtimeInvalidate("rejections", [["admin", "tracker"]]);
  useRealtimeInvalidate("customer_deals", [["admin", "tracker"]]);

  // 2026-05-15: re-sync ``selectedRow`` to the freshly-fetched row whenever
  // the query refetches (e.g. after the side-panel Save mutation invalidates
  // the cache). Without this, the side panel keeps holding the stale row
  // reference and its draft diff against the original never resets to
  // zero — the Save button stays "Save (1)" forever even though the server
  // accepted the change.
  useEffect(() => {
    if (!selectedRow) return;
    const id = selectedRow.rejection_id ?? selectedRow.call_id;
    if (!id) return;
    const next = rows.find(
      (r) => (r.rejection_id ?? r.call_id) === id,
    );
    if (next && next !== selectedRow) setSelectedRow(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);

  // 2026-05-16 audit Bug 1 fix: when the active tab IS awaiting_review,
  // read the count from the SAME query that drives the table (rows.length
  // after backend's tab + filter predicates). The previous duplicate
  // `useTrackerRowsQuery({ tab: "awaiting_review" })` query fired
  // unfiltered, so the badge stayed at the unfiltered total while any
  // category/search filter made the table show fewer rows — count != rows.
  //
  // When on OTHER tabs (active/fixed/dead/compliant), we still need a
  // background fetch so the chip shows the queue depth at a glance —
  // that secondary query is intentionally unfiltered so it counts ALL
  // awaiting work, not just what matches the current tab's filters.
  const isOnAwaitingTab = tab === "awaiting_review";
  const awaitingBgQ = useTrackerRowsQuery({
    tab: "awaiting_review",
    // No filters — this is a pure backlog ping for the OTHER-tab badge.
  });
  // Background counts for the inactive tabs so EVERY chip carries its
  // backlog number, not just "Awaiting review". Each query is unfiltered
  // and uses the same React-Query cache as the active tab's row query, so
  // navigating between tabs reuses the cached payload instantly.
  const activeBgQ = useTrackerRowsQuery({ tab: "active" });
  const fixedBgQ = useTrackerRowsQuery({ tab: "fixed" });
  const deadBgQ = useTrackerRowsQuery({ tab: "dead" });
  const compliantBgQ = useTrackerRowsQuery({ tab: "compliant" });
  const awaitingCount = isOnAwaitingTab ? rows.length : (awaitingBgQ.data?.count ?? 0);
  const tabCount = (t: TrackerTab): number | null => {
    if (t === tab) return rows.length;
    const q = ({
      awaiting_review: awaitingBgQ,
      active: activeBgQ,
      fixed: fixedBgQ,
      dead: deadBgQ,
      compliant: compliantBgQ,
    } as const)[t];
    return q.data?.count ?? null;
  };

  const setTab = (t: TrackerTab) => {
    const params = new URLSearchParams(sp.toString());
    params.set("tab", t);
    router.replace(`?${params.toString()}`, { scroll: false });
    setSelectedRow(null);
  };

  const availableMonths = useMemo(() => {
    const set = new Set<string>();
    for (const r of rows) {
      const d = r.rejected_at ? new Date(r.rejected_at) : null;
      if (!d || isNaN(d.getTime())) continue;
      set.add(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
    }
    return [...set].sort().reverse();
  }, [rows]);

  // Derive supplier + agent multi-select options from the current row set
  // so the dropdowns surface only values actually present (avoids the
  // "typo in free-text input → no results" UX trap the legacy version had).
  const availableSuppliers = useMemo(() => {
    const set = new Set<string>();
    for (const r of rows) {
      if (r.supplier && r.supplier.trim()) set.add(r.supplier.trim());
    }
    return [...set].sort();
  }, [rows]);
  const availableAgents = useMemo(() => {
    const set = new Set<string>();
    for (const r of rows) {
      if (r.sales_agent && r.sales_agent.trim()) set.add(r.sales_agent.trim());
    }
    return [...set].sort();
  }, [rows]);

  return (
    <div className="flex h-full">
      <div className={`flex flex-1 flex-col ${selectedRow ? "w-[60%]" : "w-full"}`}>
        <header className="flex items-center justify-between border-b border-[var(--border-subtle)] px-6 py-3">
          <div>
            <h1 className="text-base font-semibold">Tracker</h1>
            <p className="text-[11px] text-[var(--text-muted)]">
              {counts} rows · mirrors Watt&apos;s compliance tracker
              {q.isFetching && !q.isLoading && (
                <span className="ml-2 inline-flex items-center gap-1 text-[var(--text-muted)]" aria-live="polite">
                  <span
                    className="inline-block size-1.5 animate-pulse rounded-full bg-emerald-500"
                    aria-hidden
                  />
                  Refreshing
                </span>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setUploadOpen(true)}
              className="inline-flex items-center gap-1 rounded-md bg-emerald-600 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-emerald-700"
            >
              + Upload Call
            </button>
            <button
              type="button"
              disabled={exporting}
              onClick={async () => {
                // 2026-05-24 wiring audit C7 — direct fetch+Blob carries
                // the Supabase Bearer token; the prior bare <a href> worked
                // only because the Next proxy forwarded session cookies.
                setExportError(null);
                setExporting(true);
                try {
                  await downloadTrackerXlsx();
                } catch (e) {
                  setExportError(e instanceof Error ? e.message : String(e));
                } finally {
                  setExporting(false);
                }
              }}
              className="inline-flex items-center gap-1 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-3 py-1.5 text-[12px] hover:bg-[var(--bg-elev2)] disabled:cursor-wait disabled:opacity-60"
              aria-label="Export tracker rows to XLSX"
              aria-busy={exporting}
            >
              {exporting ? "Exporting…" : "↓ Export to XLSX"}
            </button>
          </div>
        </header>

        <div
          className="flex flex-wrap items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--surface-1)] px-6 py-2"
          role="tablist"
          aria-label="Tracker view"
        >
          {TABS.map((t) => {
            const isAwaiting = t === "awaiting_review";
            const active = tab === t;
            const baseCls = `rounded-full px-3 py-1 text-[12px] inline-flex items-center gap-1`;
            const cls = active
              ? isAwaiting
                ? `${baseCls} bg-amber-500 text-white`
                : `${baseCls} bg-emerald-600 text-white`
              : `${baseCls} text-[var(--text-muted)] hover:bg-[var(--bg-elev2)]`;
            const labels: Record<TrackerTab, string> = {
              awaiting_review: "Awaiting review",
              active: "Active",
              fixed: "Fixed",
              dead: "Dead",
              compliant: "Compliant",
            };
            const n = tabCount(t);
            return (
              <button
                key={t}
                role="tab"
                aria-selected={active}
                aria-current={active ? "page" : undefined}
                onClick={() => setTab(t)}
                className={cls}
              >
                {isAwaiting && (
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <circle cx="12" cy="12" r="9" />
                    <path d="M12 8v4l3 3" />
                  </svg>
                )}
                {n == null ? labels[t] : `${labels[t]} · ${n}`}
              </button>
            );
          })}
        </div>

        {/* 2026-05-24 tracker enterprise polish — surface query/export
            errors prominently so the reviewer never wonders why the
            table flashed. Previously errors silently dropped rows to []
            and the empty state masqueraded as "no data". */}
        {(q.isError || exportError) && (
          <div
            role="alert"
            className="mx-6 mt-2 flex items-start justify-between gap-3 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-[12.5px] text-red-900"
          >
            <div>
              <strong className="font-semibold">
                {q.isError ? "Couldn't load tracker rows." : "Export failed."}
              </strong>{" "}
              {q.isError
                ? (q.error instanceof Error ? q.error.message : "Unknown error.")
                : exportError}
            </div>
            <button
              type="button"
              onClick={() => {
                setExportError(null);
                if (q.isError) q.refetch();
              }}
              className="rounded border border-red-400 bg-white px-2 py-0.5 text-[11.5px] font-medium text-red-900 hover:bg-red-100"
            >
              Retry
            </button>
          </div>
        )}

        {tab !== "compliant" && availableMonths.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border-subtle)] px-6 py-2">
            <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Month</span>
            <button onClick={() => setMonth("")} className={`rounded-full border px-2 py-0.5 text-[11px] ${!month ? "border-emerald-500 bg-emerald-50 text-emerald-900" : "border-[var(--border-subtle)] text-[var(--text-muted)]"}`}>All</button>
            {availableMonths.map((m) => {
              const label = new Date(m + "-01").toLocaleDateString("en-GB", { month: "short" });
              return (
                <button key={m} onClick={() => setMonth(month === m ? "" : m)} className={`rounded-full border px-2 py-0.5 text-[11px] ${month === m ? "border-emerald-500 bg-emerald-50 text-emerald-900" : "border-[var(--border-subtle)] text-[var(--text-muted)]"}`}>
                  {label}
                </button>
              );
            })}
          </div>
        )}

        {tab !== "compliant" && (
          <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border-subtle)] px-6 py-2">
            <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Category</span>
            {CATEGORY_KEYS.map((k) => {
              const on = categories.has(k);
              return (
                <button key={k} onClick={() => {
                  const next = new Set(categories);
                  if (on) next.delete(k); else next.add(k);
                  setCategories(next);
                }} className={`rounded-full border px-2 py-0.5 text-[11px] ${on ? "border-2 text-[var(--text-default)]" : "border-[var(--border-subtle)] text-[var(--text-muted)]"}`}
                style={on ? { borderColor: CATEGORY_HEX[k] } : {}}>
                  <span className="inline-block h-2 w-2 rounded-full mr-1" style={{ backgroundColor: CATEGORY_HEX[k] }} />
                  {CATEGORY_LABEL[k]}
                </button>
              );
            })}
          </div>
        )}

        <TrackerFilterBar
          filters={{ ...advanced, search }}
          onChange={(next) => {
            // Search is a top-level state so the input stays controlled
            // independently; everything else lives in ``advanced``.
            setSearch(next.search ?? "");
            const { search: _s, ...rest } = next;
            setAdvanced(rest);
          }}
          supplierOptions={availableSuppliers}
          agentOptions={availableAgents}
        />

        <div className="flex-1 overflow-auto px-6 py-3">
          {q.isLoading || q.isFetching ? (
            // Show skeleton while ANY fetch is in flight (initial OR refetch
            // after a tab switch) — was flashing the empty state for ~1s
            // between request fire and response, which made the user think
            // the tracker was broken. Only show empty state when we have
            // confirmed-zero data from a settled response.
            <div className="m-2 space-y-2 p-2" aria-busy>
              {[0,1,2,3,4,5].map((i) => (
                <div
                  key={i}
                  className="h-9 rounded-md bg-[var(--bg-elev1)]"
                  style={{ opacity: 0.6 - i * 0.08 }}
                />
              ))}
            </div>
          ) : rows.length === 0 ? (
            <div className="m-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-6 text-center">
              <div className="mx-auto mb-3 grid size-10 place-items-center rounded-full bg-[var(--bg-elev3)]">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-[var(--emerald-400)]"><path d="M3 12a9 9 0 1 0 9-9"/><path d="M3 4v5h5"/></svg>
              </div>
              <p className="text-[14px] font-medium text-[var(--text-primary)]">
                Nothing in the {tab.replace("_", " ")} tab yet
              </p>
              <p className="mx-auto mt-1 max-w-[440px] text-[12.5px] text-[var(--text-muted)]">
                {tab === "compliant"
                  ? "Calls signed off as compliant land here. Upload a clean call to populate the audit trail."
                  : tab === "awaiting_review"
                    ? "Calls flagged by the AI sit here until a reviewer claims them. Upload a call or wait for the next pipeline run."
                    : "Once a call processes through the pipeline its rejections (if any) populate this tab. Upload your first call to get started."}
              </p>
              <button
                type="button"
                onClick={() => setUploadOpen(true)}
                className="mt-4 inline-flex items-center gap-2 rounded-md bg-emerald-600 px-3.5 py-2 text-[12.5px] font-medium text-white hover:bg-emerald-700"
              >
                + Upload Call
              </button>
            </div>
          ) : tab === "active" || tab === "fixed" || tab === "dead" ? (
            // 2026-05-23 redesign — rejection-driven tabs collapse the
            // N-rows-per-call wall into expandable cards. One call =
            // one card; expanding reveals every individual rejection
            // with its reason + status. Awaiting + Compliant tabs are
            // already 1 row per call so they keep the dense table.
            <TrackerGroupedTable
              rows={rows}
              tab={tab}
              selectedRowId={selectedRow ? (selectedRow.rejection_id ?? selectedRow.call_id) : null}
              onSelect={setSelectedRow}
            />
          ) : (
            <TrackerTable
              rows={rows}
              tab={tab}
              selectedRowId={selectedRow ? (selectedRow.rejection_id ?? selectedRow.call_id) : null}
              onSelect={setSelectedRow}
            />
          )}
        </div>
      </div>

      {selectedRow && (
        <div className="w-[40%]">
          <TrackerSidePanel row={selectedRow} onClose={() => setSelectedRow(null)} />
        </div>
      )}

      <UploadModal
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onSuccess={(callId) => {
          setUploadOpen(false);
          if (callId === "__BATCH_TO_CALLS_DASHBOARD__") {
            router.push("/calls");
            return;
          }
          if (callId) router.push(`/calls/${callId}`);
        }}
      />
    </div>
  );
}
