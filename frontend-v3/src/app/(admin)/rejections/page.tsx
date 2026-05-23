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
import { ChevronRight, AlertTriangle, Clock, Radio } from "lucide-react";
import {
  useRejectionsGroupedQuery,
  useRejectionQuery,
  type RejectionTab,
  type RejectionGroup,
} from "@/lib/queries/rejections";
import type { Rejection } from "@/lib/schemas/rejections";
import { RejectionDetailPanel } from "./RejectionDetailPanel";
import { useRealtimeInvalidate } from "@/lib/hooks/useRealtimeInvalidate";

const CATEGORY_TONE: Record<string, string> = {
  ADMIN_ERROR: "#FFC000",
  PROCESS_FAILURE: "#00B0F0",
  VERBAL_SALES_ERROR: "#FF6B6B",
  COMPLIANCE_ISSUE: "#FFFF00",
  COMPLIANCE_ERROR: "#92D050",
  PRICING_ISSUE: "#FF8080",
  PRICING_ERROR: "#C00000",
  DOCUSIGN_ERROR: "#BDD7EE",
  FAILED_CREDIT_CHECK: "#FFD966",
};

function categoryColor(cat: string): string {
  return CATEGORY_TONE[cat] ?? "var(--text-muted)";
}

function relativeDeadline(iso: string | null): {
  label: string;
  overdue: boolean;
} {
  if (!iso) return { label: "no deadline", overdue: false };
  const ms = new Date(iso).getTime() - Date.now();
  const overdue = ms < 0;
  const abs = Math.abs(ms);
  const days = Math.floor(abs / 86_400_000);
  const hours = Math.floor((abs % 86_400_000) / 3_600_000);
  const tail = days > 0 ? `${days}d ${hours}h` : `${hours}h`;
  return { label: overdue ? `${tail} overdue` : `in ${tail}`, overdue };
}

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

  // 2026-05-23 redesign — the page now lists CALLS (each card collapses
  // many rejections from the same call into one row). Source filter
  // stays at "reviewer" so AI-only auto-rejections still don't leak.
  const listQ = useRejectionsGroupedQuery({
    tab,
    search: search || undefined,
    source: "reviewer",
    limit: 100,
  });
  const detailQ = useRejectionQuery(selectedId);

  const groups = listQ.data?.groups ?? [];
  const total = listQ.data?.total_groups ?? 0;
  const totalRejections = listQ.data?.total_rejections ?? 0;

  // Tracks which call cards are expanded. Initially-empty so the page
  // loads as a quick scan; user expands what they want to triage.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggleExpand = (callId: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(callId)) next.delete(callId);
      else next.add(callId);
      return next;
    });

  // Backend's list_rejections payload already includes a `counts` map with
  // every tab's count computed off the same base set, so the badges stay
  // populated regardless of which tab is selected. Falling back to the
  // active-tab `total` keeps the chip non-empty during the first paint.
  const serverCounts = listQ.data?.counts ?? null;
  const tabCounts = useMemo<Record<RejectionTab, number | undefined>>(() => {
    if (serverCounts) {
      return {
        active: serverCounts.active ?? 0,
        fixed: serverCounts.fixed ?? 0,
        dead: serverCounts.dead ?? 0,
        archive: serverCounts.archive ?? 0,
      };
    }
    return {
      active: tab === "active" ? total : undefined,
      fixed: tab === "fixed" ? total : undefined,
      dead: tab === "dead" ? total : undefined,
      archive: tab === "archive" ? total : undefined,
    };
  }, [serverCounts, tab, total]);

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
            ) : !listQ.isLoading && groups.length === 0 ? (
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
              <div className="flex flex-col gap-3 p-4">
                {/* Live + total ribbon */}
                <div
                  className="flex items-center gap-3 text-[11.5px] text-[var(--text-muted)]"
                  data-slot="rejection-summary-ribbon"
                >
                  <span
                    className="inline-flex items-center gap-1.5 rounded-full border border-[var(--emerald-border)] bg-[var(--emerald-bg)] px-2 py-0.5 text-[var(--emerald-400)]"
                    title="Updates push from Supabase Realtime"
                  >
                    <Radio size={10} /> Live
                  </span>
                  <span>
                    {groups.length} call{groups.length === 1 ? "" : "s"} ·{" "}
                    {totalRejections} rejection{totalRejections === 1 ? "" : "s"}{" "}
                    in {TAB_LABELS[tab]}
                  </span>
                  <span className="flex-1" />
                  <span>{expanded.size} expanded</span>
                </div>

                {listQ.isLoading
                  ? Array.from({ length: 4 }).map((_, i) => (
                      <RejectionGroupSkeleton key={i} />
                    ))
                  : groups.map((g) => (
                      <RejectionGroupCard
                        key={g.call_id}
                        group={g}
                        expanded={expanded.has(g.call_id)}
                        onToggle={() => toggleExpand(g.call_id)}
                        selectedId={selectedId}
                        onSelectRejection={selectRow}
                      />
                    ))}
              </div>
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
                : "Expand a call card on the left and click any rejection to see its detail + audit log here."}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Group card ────────────────────────────────────────────────────────

function RejectionGroupCard({
  group,
  expanded,
  onToggle,
  selectedId,
  onSelectRejection,
}: {
  group: RejectionGroup;
  expanded: boolean;
  onToggle: () => void;
  selectedId: string | null;
  onSelectRejection: (id: string | null) => void;
}) {
  const deadline = relativeDeadline(group.oldest_deadline);
  const dominantCategory = Object.entries(group.category_mix).sort(
    ([, a], [, b]) => b - a,
  )[0]?.[0];
  const dominantColor = dominantCategory ? categoryColor(dominantCategory) : "var(--border-strong)";
  // Parse "20/88" → 22% for the inline score bar.
  const scoreMatch = (group.score ?? "").match(
    /^(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)/,
  );
  const scorePct = scoreMatch
    ? Math.round((parseFloat(scoreMatch[1]) / parseFloat(scoreMatch[2])) * 100)
    : null;
  const scoreTone =
    scorePct == null
      ? "var(--border-strong)"
      : scorePct >= 80
        ? "var(--emerald)"
        : scorePct >= 60
          ? "var(--amber)"
          : "var(--red)";

  return (
    <article
      data-slot="rejection-group-card"
      className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev2)] transition-colors hover:border-[var(--border-strong)]"
      style={{ borderLeft: `3px solid ${dominantColor}` }}
    >
      {/* Header row — always visible */}
      <button
        type="button"
        onClick={onToggle}
        className="grid w-full grid-cols-[auto_minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)_auto_auto_auto] items-center gap-4 px-4 py-3 text-left"
        aria-expanded={expanded}
      >
        <ChevronRight
          size={14}
          className={`text-[var(--text-faint)] transition-transform ${expanded ? "rotate-90" : ""}`}
        />
        {/* Customer / agent block */}
        <div className="min-w-0">
          <div className="truncate text-[14px] font-medium text-[var(--text-primary)]">
            {group.customer_name ?? group.customer_slug ?? "(no customer)"}
          </div>
          <div className="mt-0.5 truncate text-[12px] text-[var(--text-muted)]">
            <span>{group.agent_name ?? "—"}</span>
            {group.supplier && (
              <>
                <span className="px-1.5 text-[var(--text-faint)]">·</span>
                <span>{group.supplier}</span>
              </>
            )}
            {group.call_type && (
              <>
                <span className="px-1.5 text-[var(--text-faint)]">·</span>
                <span className="font-mono text-[11px] uppercase tracking-wide">
                  {group.call_type}
                </span>
              </>
            )}
          </div>
        </div>

        {/* Score bar + % */}
        <div className="flex min-w-0 items-center gap-2">
          <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--bg-elev3)]">
            {scorePct != null && (
              <div
                className="h-full transition-[width] duration-200"
                style={{
                  width: `${Math.min(100, Math.max(0, scorePct))}%`,
                  background: scoreTone,
                }}
              />
            )}
          </div>
          <span className="w-12 text-right font-mono text-[12px] tabular-nums text-[var(--text-primary)]">
            {scorePct != null ? `${scorePct}%` : "—"}
          </span>
        </div>

        {/* Category mix chips (top 3) */}
        <div className="flex flex-wrap items-center gap-1">
          {Object.entries(group.category_mix)
            .sort(([, a], [, b]) => b - a)
            .slice(0, 3)
            .map(([cat, n]) => (
              <span
                key={cat}
                className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-medium"
                style={{
                  borderColor: `${categoryColor(cat)}55`,
                  background: `${categoryColor(cat)}1f`,
                  color: "var(--text-primary)",
                }}
                title={cat}
              >
                <span
                  className="inline-block size-1.5 rounded-full"
                  style={{ background: categoryColor(cat) }}
                />
                {cat.replace(/_/g, " ").toLowerCase()} · {n}
              </span>
            ))}
          {Object.keys(group.category_mix).length > 3 && (
            <span className="text-[10.5px] text-[var(--text-faint)]">
              +{Object.keys(group.category_mix).length - 3}
            </span>
          )}
        </div>

        {/* Rejection count pill */}
        <span
          className="inline-flex min-w-[64px] items-center justify-center gap-1 rounded-full bg-[var(--red-bg)] px-2 py-0.5 text-[12px] font-semibold text-[var(--red)]"
          title={`${group.rejection_count} rejection rows from this call`}
        >
          <AlertTriangle size={11} /> {group.rejection_count}
        </span>

        {/* Deadline pill */}
        <span
          className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] ${
            deadline.overdue
              ? "border-[var(--red-border)] bg-[var(--red-bg)] text-[var(--red)]"
              : "border-[var(--border-subtle)] bg-[var(--bg-elev3)] text-[var(--text-muted)]"
          }`}
          title={group.oldest_deadline ?? ""}
        >
          <Clock size={10} /> {deadline.label}
        </span>

        {/* Status mix */}
        <div className="flex items-center gap-1">
          {Object.entries(group.status_mix)
            .sort(([, a], [, b]) => b - a)
            .map(([st, n]) => (
              <span
                key={st}
                className="rounded bg-[var(--bg-elev3)] px-1.5 py-0.5 font-mono text-[10.5px] text-[var(--text-muted)]"
                title={st}
              >
                {st.split("_")[0].toLowerCase()}·{n}
              </span>
            ))}
        </div>
      </button>

      {/* Expanded body — individual rejections */}
      {expanded && (
        <div
          className="border-t border-[var(--border-subtle)] bg-[var(--bg-elev1)]"
          data-slot="rejection-group-expanded"
        >
          <ul className="divide-y divide-[var(--border-subtle)]">
            {group.rejections.map((r: Rejection, i: number) => (
              <li
                key={r.id}
                className={`grid cursor-pointer grid-cols-[40px_auto_minmax(0,1fr)_120px] items-center gap-3 px-4 py-2.5 transition-colors hover:bg-[var(--bg-elev2)] ${
                  r.id === selectedId ? "bg-[var(--bg-elev3)]" : ""
                }`}
                onClick={() =>
                  onSelectRejection(r.id === selectedId ? null : r.id)
                }
              >
                <span className="font-mono text-[10.5px] text-[var(--text-faint)] tabular-nums">
                  #{i + 1}
                </span>
                <span
                  className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-medium"
                  style={{
                    borderColor: `${categoryColor(r.category ?? "")}55`,
                    background: `${categoryColor(r.category ?? "")}1f`,
                    color: "var(--text-primary)",
                  }}
                >
                  <span
                    className="inline-block size-1.5 rounded-full"
                    style={{ background: categoryColor(r.category ?? "") }}
                  />
                  {(r.category ?? "—").replace(/_/g, " ").toLowerCase()}
                </span>
                <span
                  className="truncate text-[12.5px] text-[var(--text-primary)]"
                  title={r.rejection_reason ?? ""}
                >
                  {r.rejection_reason ?? "(no reason)"}
                </span>
                <span className="text-right font-mono text-[10.5px] uppercase tracking-wide text-[var(--text-muted)]">
                  {(r.status ?? "—").replace(/_/g, " ").toLowerCase()}
                </span>
              </li>
            ))}
          </ul>
          <div className="flex items-center gap-2 border-t border-[var(--border-subtle)] bg-[var(--bg-elev2)] px-4 py-2 text-[11px] text-[var(--text-muted)]">
            <Link
              href={`/calls/${encodeURIComponent(group.call_id)}`}
              className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev3)] px-2 py-1 text-[var(--text-primary)] hover:bg-[var(--bg-elev2)]"
            >
              Open call →
            </Link>
            <span className="flex-1" />
            <span>
              First rejection{" "}
              {group.first_rejected_at
                ? new Date(group.first_rejected_at).toLocaleString()
                : "—"}
            </span>
          </div>
        </div>
      )}
    </article>
  );
}

function RejectionGroupSkeleton() {
  return (
    <div
      className="h-[58px] animate-pulse rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev2)]"
      aria-hidden
    />
  );
}
