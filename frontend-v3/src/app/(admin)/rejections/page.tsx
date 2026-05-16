"use client";

/**
 * /rejections — master-detail rejection management page.
 *
 * Replaces the prior 1-line redirect stub. Uses the existing
 * RejectionsTable + RejectionDetailPanel components plus a tab strip
 * (Active / Fixed / Dead / Archive) and a search box.
 *
 * Wiring: useRejectionsQuery → table; selecting a row hydrates the
 * detail panel via useRejectionQuery on the right side. Status pipeline
 * lives in the panel.
 */
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  useRejectionsQuery,
  useRejectionQuery,
  type RejectionTab,
} from "@/lib/queries/rejections";
import { RejectionsTable } from "./RejectionsTable";
import { RejectionDetailPanel } from "./RejectionDetailPanel";
import { useRealtimeInvalidate } from "@/lib/hooks/useRealtimeInvalidate";

const TABS: RejectionTab[] = ["active", "fixed", "dead", "archive"];

const TAB_LABELS: Record<RejectionTab, string> = {
  active: "Active",
  fixed: "Fixed",
  dead: "Dead",
  archive: "Archive",
};

export default function RejectionsPage() {
  const router = useRouter();
  const sp = useSearchParams();
  const [tab, setTab] = useState<RejectionTab>("active");
  const [search, setSearch] = useState("");

  // Supabase Realtime — any change on `rejections` (new auto-rejection,
  // status flip, confirmed_by stamp) refreshes the list within ~50ms.
  // Feature-flagged on NEXT_PUBLIC_USE_REALTIME=1. Path 3 of 2026-05-16.
  useRealtimeInvalidate("rejections", [["rejections"]]);
  // 2026-05-15: hydrate ``selectedId`` from ``?id=<uuid>`` so other pages
  // can deep-link straight to a specific rejection (the /tracker side
  // panel and /calls/[id] both expose "View in rejections" links). The
  // selected id stays mirrored in the URL on row clicks so reviewers can
  // share/bookmark a rejection.
  const [selectedId, setSelectedId] = useState<string | null>(
    () => sp.get("id"),
  );
  useEffect(() => {
    const next = sp.get("id");
    if (next !== selectedId) setSelectedId(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp]);

  const selectRow = (id: string | null) => {
    setSelectedId(id);
    const params = new URLSearchParams(sp.toString());
    if (id) params.set("id", id);
    else params.delete("id");
    const qs = params.toString();
    router.replace(qs ? `?${qs}` : "?", { scroll: false });
  };

  // 2026-05-14 audit fix: Phase 4 gate. The /rejections page must only
  // show reviewer-initiated rejections — AI auto-created rows pre-dated
  // the gate and were leaking through because the page wasn't passing
  // a `source` filter. Backend defaults to "all" so the explicit
  // "reviewer" passthrough is required.
  const listQ = useRejectionsQuery({
    tab,
    search: search || undefined,
    source: "reviewer",
    limit: 100,
  });
  const detailQ = useRejectionQuery(selectedId);

  const rejections = listQ.data?.rejections ?? [];
  const total = listQ.data?.total ?? 0;

  const tabCounts = useMemo<Record<RejectionTab, number | undefined>>(() => {
    return {
      active: tab === "active" ? total : undefined,
      fixed: tab === "fixed" ? total : undefined,
      dead: tab === "dead" ? total : undefined,
      archive: tab === "archive" ? total : undefined,
    };
  }, [tab, total]);

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="flex items-center justify-between border-b border-[var(--border-subtle)] px-6 py-4">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight">Rejections</h1>
          <p className="mt-0.5 text-[12.5px] text-[var(--text-muted)]">
            Open rejections by category, owner, supplier — track to fixed or dead.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/tracker"
            className="inline-flex items-center gap-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev2)] px-3 py-1.5 text-[12px] hover:bg-[var(--bg-elev3)]"
          >
            Open in Tracker →
          </Link>
        </div>
      </header>


      {/* Tab strip */}
      <div className="flex flex-wrap items-center gap-1 border-b border-[var(--border-subtle)] px-6 py-2">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => {
              setTab(t);
              selectRow(null);
            }}
            className={`rounded-md px-3 py-1 text-[12.5px] transition-colors ${
              tab === t
                ? "bg-[var(--bg-elev3)] text-[var(--text-primary)]"
                : "text-[var(--text-muted)] hover:bg-[var(--bg-elev1)]"
            }`}
          >
            {TAB_LABELS[t]}
            {tabCounts[t] != null && (
              <span
                className={`ml-1.5 rounded-full px-1.5 text-[11px] tabular-nums ${
                  tab === t
                    ? "bg-[var(--emerald-bg-strong)] text-[var(--emerald-400)]"
                    : "bg-[var(--bg-elev3)] text-[var(--text-dim)]"
                }`}
              >
                {tabCounts[t]}
              </span>
            )}
          </button>
        ))}

        <div className="flex-1" />

        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search customer / agent / reason…"
          className="w-72 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1 text-[12px]"
        />
      </div>

      {/* Master / detail */}
      <div className="grid min-h-0 flex-1 grid-cols-[60%_40%] overflow-hidden">
        <div className="flex min-w-0 flex-col overflow-hidden border-r border-[var(--border-subtle)]">
          <div className="flex-1 overflow-y-auto">
            {listQ.isError ? (
              <div className="m-6 rounded-xl border border-red-300 bg-red-50 p-6 text-center">
                <p className="text-[13.5px] font-medium text-red-900">
                  Couldn’t load rejections.
                </p>
                <p className="mx-auto mt-1 max-w-[440px] text-[12px] text-red-800">
                  {listQ.error instanceof Error ? listQ.error.message : "Unknown error"}
                </p>
                <button
                  type="button"
                  onClick={() => listQ.refetch()}
                  className="mt-4 inline-flex items-center gap-2 rounded-md border border-red-300 bg-white px-3 py-1.5 text-[12.5px] text-red-900 hover:bg-red-100"
                >
                  Retry
                </button>
              </div>
            ) : !listQ.isLoading && rejections.length === 0 ? (
              <div className="m-6 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-6 text-center">
                <p className="text-[13.5px] font-medium text-[var(--text-primary)]">
                  No rejections in the {TAB_LABELS[tab]} tab.
                </p>
                <p className="mx-auto mt-1 max-w-[440px] text-[12px] text-[var(--text-muted)]">
                  Rejections appear here once the pipeline (or a reviewer)
                  flags a call. They flow{" "}
                  <span className="text-[var(--text-primary)]">Active → Fixed</span> on
                  resolution, or <span className="text-[var(--text-primary)]">Active → Dead</span>
                  {" "}when the deal is unrecoverable.
                </p>
                <Link
                  href="/tracker"
                  className="mt-4 inline-flex items-center gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev2)] px-3 py-1.5 text-[12.5px] hover:bg-[var(--bg-elev3)]"
                >
                  Open the Tracker →
                </Link>
              </div>
            ) : (
              <RejectionsTable
                rejections={rejections}
                selectedId={selectedId}
                tab={tab}
                onSelect={selectRow}
                isLoading={listQ.isLoading}
              />
            )}
          </div>
        </div>

        <div className="flex min-w-0 flex-col overflow-hidden bg-[var(--bg-elev1)]">
          {detailQ.data ? (
            <RejectionDetailPanel rejection={detailQ.data} />
          ) : (
            <div className="m-6 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-6 text-center text-[12.5px] text-[var(--text-muted)]">
              {selectedId
                ? "Loading rejection…"
                : "Select a rejection on the left to see its detail and audit log."}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
