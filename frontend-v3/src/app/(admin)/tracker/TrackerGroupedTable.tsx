"use client";

/**
 * TrackerGroupedTable — same data feed as TrackerTable, but collapses
 * N rejection rows from one call into a single header row that expands
 * to reveal the individual rejections.
 *
 * Used on the Active / Fixed / Dead tabs where the backend returns one
 * row per Rejection (so a non-compliant call with 49 failing
 * checkpoints produces 49 rows). The owner-reported pain: the page is
 * unreadable. This component fixes it without changing the backend.
 *
 * Compliant + Awaiting tabs continue to use TrackerTable because each
 * row is already a call (1 row per call, not 1 row per rejection).
 *
 * Selection: clicking a sub-row sets the same `selectedRow` that
 * TrackerTable does, so the existing TrackerSidePanel keeps working.
 */
import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Clock,
  ChevronRight,
  Phone,
} from "lucide-react";

import { Pill } from "@/components/design/Pill";
import { CategoryChip } from "./CategoryChip";
import type { TrackerRow, TrackerTab } from "@/lib/queries/tracker";

type Props = {
  rows: TrackerRow[];
  tab: TrackerTab;
  selectedRowId: string | null;
  onSelect: (row: TrackerRow) => void;
};

type Group = {
  call_id: string;
  customer_name: string | null;
  supplier: string | null;
  sales_agent: string | null;
  score: string | null;
  count: number;
  category_mix: Record<string, number>;
  status_mix: Record<string, number>;
  oldest_deadline: string | null;
  rows: TrackerRow[];
};

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

function parseScorePct(score: string | null): number | null {
  if (!score) return null;
  const m = score.match(/^(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)/);
  if (m && parseFloat(m[2]) > 0) {
    return Math.round((parseFloat(m[1]) / parseFloat(m[2])) * 100);
  }
  if (/^\d+%/.test(score)) return parseInt(score, 10);
  return null;
}

function groupByCall(rows: TrackerRow[]): Group[] {
  const map = new Map<string, Group>();
  for (const r of rows) {
    if (!r.call_id) continue;
    let g = map.get(r.call_id);
    if (!g) {
      g = {
        call_id: r.call_id,
        customer_name: r.customer_name,
        supplier: r.supplier,
        sales_agent: r.sales_agent,
        score: r.score,
        count: 0,
        category_mix: {},
        status_mix: {},
        oldest_deadline: null,
        rows: [],
      };
      map.set(r.call_id, g);
    }
    g.count += 1;
    g.rows.push(r);
    if (r.category) g.category_mix[r.category] = (g.category_mix[r.category] ?? 0) + 1;
    if (r.status) g.status_mix[r.status] = (g.status_mix[r.status] ?? 0) + 1;
    if (r.deadline && (g.oldest_deadline == null || r.deadline < g.oldest_deadline)) {
      g.oldest_deadline = r.deadline;
    }
    // First-encountered customer/agent/supplier/score wins if some rows are sparse.
    g.customer_name = g.customer_name ?? r.customer_name;
    g.sales_agent = g.sales_agent ?? r.sales_agent;
    g.supplier = g.supplier ?? r.supplier;
    g.score = g.score ?? r.score;
  }
  // Sort: worst (most rejections) first, then oldest deadline ascending.
  return Array.from(map.values()).sort((a, b) => {
    if (b.count !== a.count) return b.count - a.count;
    return (a.oldest_deadline ?? "9999") < (b.oldest_deadline ?? "9999") ? -1 : 1;
  });
}

export function TrackerGroupedTable({
  rows,
  tab,
  selectedRowId,
  onSelect,
}: Props) {
  const groups = useMemo(() => groupByCall(rows), [rows]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (callId: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(callId)) next.delete(callId);
      else next.add(callId);
      return next;
    });

  if (groups.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 p-2" data-tracker-tab={tab}>
      <div className="flex items-center gap-3 text-[11.5px] text-[var(--text-muted)]">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--emerald-border)] bg-[var(--emerald-bg)] px-2 py-0.5 text-[var(--emerald-400)]">
          ● Live
        </span>
        <span>
          {groups.length} call{groups.length === 1 ? "" : "s"} ·{" "}
          {rows.length} rejection{rows.length === 1 ? "" : "s"}
        </span>
        <span className="flex-1" />
        <span>{expanded.size} expanded</span>
      </div>

      {groups.map((g) => (
        <TrackerGroupCard
          key={g.call_id}
          group={g}
          expanded={expanded.has(g.call_id)}
          onToggle={() => toggle(g.call_id)}
          selectedRowId={selectedRowId}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function TrackerGroupCard({
  group,
  expanded,
  onToggle,
  selectedRowId,
  onSelect,
}: {
  group: Group;
  expanded: boolean;
  onToggle: () => void;
  selectedRowId: string | null;
  onSelect: (row: TrackerRow) => void;
}) {
  const dominantCat =
    Object.entries(group.category_mix).sort(([, a], [, b]) => b - a)[0]?.[0];
  const dominantColor = dominantCat
    ? CATEGORY_TONE[dominantCat] ?? "var(--border-strong)"
    : "var(--border-strong)";
  const scorePct = parseScorePct(group.score);
  const scoreTone =
    scorePct == null
      ? "var(--border-strong)"
      : scorePct >= 80
        ? "var(--emerald)"
        : scorePct >= 60
          ? "var(--amber)"
          : "var(--red)";
  const deadline = relativeDeadline(group.oldest_deadline);

  return (
    <article
      className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev2)] transition-colors hover:border-[var(--border-strong)]"
      style={{ borderLeft: `3px solid ${dominantColor}` }}
      data-slot="tracker-group-card"
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="grid w-full grid-cols-[auto_minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)_auto_auto_auto] items-center gap-4 px-4 py-3 text-left"
      >
        <ChevronRight
          size={14}
          className={`text-[var(--text-faint)] transition-transform ${expanded ? "rotate-90" : ""}`}
        />

        {/* Customer + meta */}
        <div className="min-w-0">
          <div className="truncate text-[14px] font-medium text-[var(--text-primary)]">
            {group.customer_name ?? "(no customer)"}
          </div>
          <div className="mt-0.5 truncate text-[12px] text-[var(--text-muted)]">
            <span>{group.sales_agent ?? "—"}</span>
            {group.supplier && (
              <>
                <span className="px-1.5 text-[var(--text-faint)]">·</span>
                <span>{group.supplier}</span>
              </>
            )}
          </div>
        </div>

        {/* Score bar */}
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

        {/* Category mix (top 3) */}
        <div className="flex flex-wrap items-center gap-1">
          {Object.entries(group.category_mix)
            .sort(([, a], [, b]) => b - a)
            .slice(0, 3)
            .map(([cat, n]) => (
              <span
                key={cat}
                className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-medium"
                style={{
                  borderColor: `${CATEGORY_TONE[cat] ?? "var(--border-subtle)"}55`,
                  background: `${CATEGORY_TONE[cat] ?? "var(--bg-elev3)"}1f`,
                  color: "var(--text-primary)",
                }}
                title={cat}
              >
                <span
                  className="inline-block size-1.5 rounded-full"
                  style={{ background: CATEGORY_TONE[cat] ?? "var(--text-muted)" }}
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

        {/* Rejection count badge */}
        <span
          className="inline-flex min-w-[64px] items-center justify-center gap-1 rounded-full bg-[var(--red-bg)] px-2 py-0.5 text-[12px] font-semibold text-[var(--red)]"
          title={`${group.count} tracker rows from this call`}
        >
          <AlertTriangle size={11} /> {group.count}
        </span>

        {/* Deadline */}
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

      {expanded && (
        <div
          className="border-t border-[var(--border-subtle)] bg-[var(--bg-elev1)]"
          data-slot="tracker-group-expanded"
        >
          <ul className="divide-y divide-[var(--border-subtle)]">
            {group.rows.map((r, i) => {
              const rowId = r.rejection_id ?? r.call_id;
              const isSelected = rowId === selectedRowId;
              return (
                <li
                  key={r.rejection_id ?? `${r.call_id}-${i}`}
                  className={`grid cursor-pointer grid-cols-[40px_minmax(0,1fr)_120px_110px] items-center gap-3 px-4 py-2.5 transition-colors hover:bg-[var(--bg-elev2)] ${
                    isSelected ? "bg-[var(--bg-elev3)]" : ""
                  }`}
                  onClick={() => onSelect(r)}
                >
                  <span className="font-mono text-[10.5px] tabular-nums text-[var(--text-faint)]">
                    #{i + 1}
                  </span>
                  <span
                    className="truncate text-[12.5px] text-[var(--text-primary)]"
                    title={r.rejection_reason ?? ""}
                  >
                    {r.rejection_reason ?? "(no reason)"}
                  </span>
                  <CategoryChip category={r.category} />
                  <span className="text-right font-mono text-[10.5px] uppercase tracking-wide text-[var(--text-muted)]">
                    {(r.status ?? "—").replace(/_/g, " ").toLowerCase()}
                  </span>
                </li>
              );
            })}
          </ul>
          <div className="flex items-center gap-2 border-t border-[var(--border-subtle)] bg-[var(--bg-elev2)] px-4 py-2 text-[11px] text-[var(--text-muted)]">
            <a
              href={`/calls/${encodeURIComponent(group.call_id)}`}
              className="inline-flex items-center gap-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev3)] px-2 py-1 text-[var(--text-primary)] hover:bg-[var(--bg-elev2)]"
            >
              <Phone size={11} /> Open call →
            </a>
            <span className="flex-1" />
            {dominantCat && (
              <span title="Dominant rejection category for this call">
                Dominant: <Pill tone="neutral">{dominantCat.toLowerCase()}</Pill>
              </span>
            )}
          </div>
        </div>
      )}
    </article>
  );
}
