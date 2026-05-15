"use client";
/**
 * /tracker advanced filter bar (2026-05-15).
 *
 * Extracted from page.tsx so /tracker stays readable. Surfaces every filter
 * the backend now supports — date range, day, multi-select supplier/agent,
 * status, verdict-state, deal-value range, MPAN/MPRN search, deadline
 * state. Filters share a single ``TrackerFilters`` object so the parent
 * page can serialise the whole blob into the URL when the user reloads.
 *
 * Design rules:
 *   * Primary row (always visible): tab-scoped chips + free-text search.
 *   * Advanced section (collapsible): everything else. Persisted in
 *     ``localStorage`` so power reviewers don't have to expand every time.
 *   * Every input is controlled; parent owns the state.
 *   * Empty-string values are normalised to ``undefined`` before patching
 *     the parent — keeps the query-key stable across "field touched then
 *     emptied" cycles so we don't churn the cache.
 */
import { useEffect, useMemo, useState } from "react";
import type {
  TrackerDeadlineState,
  TrackerFilters,
  TrackerVerdictState,
} from "@/lib/queries/tracker";

type Props = {
  filters: TrackerFilters;
  onChange: (next: TrackerFilters) => void;
  supplierOptions: string[];
  agentOptions: string[];
};

const STATUS_OPTIONS = ["NOT_STARTED", "IN_PROGRESS", "FIXED", "DEAD"] as const;
const VERDICT_OPTIONS: TrackerVerdictState[] = [
  "AI_PENDING",
  "HUMAN_CONFIRMED",
  "HUMAN_OVERRIDDEN",
];
const VERDICT_LABEL: Record<TrackerVerdictState, string> = {
  AI_PENDING: "AI Pending",
  HUMAN_CONFIRMED: "Human Confirmed",
  HUMAN_OVERRIDDEN: "Human Overridden",
};
const DEADLINE_OPTIONS: { value: TrackerDeadlineState; label: string }[] = [
  { value: "overdue", label: "Overdue" },
  { value: "due_3d", label: "Due ≤3d" },
  { value: "due_7d", label: "Due ≤7d" },
  { value: "on_track", label: "On track" },
];

function MultiSelectChips({
  label,
  options,
  selected,
  onChange,
  emptyHint,
}: {
  label: string;
  options: string[];
  selected: string[] | undefined;
  onChange: (next: string[] | undefined) => void;
  emptyHint?: string;
}) {
  const set = useMemo(() => new Set(selected ?? []), [selected]);
  const toggle = (v: string) => {
    const next = new Set(set);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    onChange(next.size > 0 ? [...next] : undefined);
  };
  if (options.length === 0) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
          {label}
        </span>
        <span className="text-[11px] text-[var(--text-muted)]">
          {emptyHint ?? "—"}
        </span>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
        {label}
      </span>
      {options.map((opt) => {
        const on = set.has(opt);
        return (
          <button
            key={opt}
            type="button"
            onClick={() => toggle(opt)}
            className={`rounded-full border px-2 py-0.5 text-[11px] ${
              on
                ? "border-emerald-500 bg-emerald-50 text-emerald-900"
                : "border-[var(--border-subtle)] text-[var(--text-muted)] hover:bg-[var(--bg-elev2)]"
            }`}
          >
            {opt}
          </button>
        );
      })}
    </div>
  );
}

export function TrackerFilterBar({
  filters,
  onChange,
  supplierOptions,
  agentOptions,
}: Props) {
  const [expanded, setExpanded] = useState<boolean>(false);

  // Persist the "Advanced expanded?" preference so power users don't have
  // to click it every visit.
  useEffect(() => {
    try {
      const stored = localStorage.getItem("tracker.filters.expanded");
      if (stored === "1") setExpanded(true);
    } catch {
      // localStorage unavailable in SSR / privacy mode — ignore.
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem("tracker.filters.expanded", expanded ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [expanded]);

  const patch = (delta: Partial<TrackerFilters>) =>
    onChange({ ...filters, ...delta });

  const activeCount = useMemo(() => {
    let n = 0;
    if (filters.date_from || filters.date_to || filters.date_on) n++;
    if (filters.suppliers && filters.suppliers.length > 0) n++;
    if (filters.agents && filters.agents.length > 0) n++;
    if (filters.statuses && filters.statuses.length > 0) n++;
    if (filters.verdict_states && filters.verdict_states.length > 0) n++;
    if (filters.meter) n++;
    if (filters.value_min !== undefined || filters.value_max !== undefined) n++;
    if (filters.deadline_state) n++;
    return n;
  }, [filters]);

  const clearAll = () =>
    onChange({
      tab: filters.tab,
      search: filters.search,
      category: filters.category,
    });

  return (
    <div className="border-b border-[var(--border-subtle)]">
      <div className="flex flex-wrap items-center gap-2 px-6 py-2">
        <input
          value={filters.search ?? ""}
          onChange={(e) => patch({ search: e.target.value || undefined })}
          placeholder="Search customer, agent, reason…"
          className="flex-1 min-w-[200px] rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1 text-[12px]"
        />
        <input
          value={filters.meter ?? ""}
          onChange={(e) => patch({ meter: e.target.value || undefined })}
          placeholder="MPAN / MPRN"
          className="w-40 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1 text-[12px]"
          aria-label="MPAN or MPRN substring search"
        />
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[12px] ${
            expanded
              ? "border-emerald-500 bg-emerald-50 text-emerald-900"
              : "border-[var(--border-subtle)] text-[var(--text-muted)] hover:bg-[var(--bg-elev2)]"
          }`}
          aria-expanded={expanded}
        >
          {expanded ? "Hide filters" : "More filters"}
          {activeCount > 0 && (
            <span className="rounded-full bg-emerald-600 px-1.5 py-0.5 text-[10px] font-medium text-white">
              {activeCount}
            </span>
          )}
        </button>
        {activeCount > 0 && (
          <button
            type="button"
            onClick={clearAll}
            className="text-[11px] text-[var(--text-muted)] hover:text-[var(--text-default)] underline"
          >
            Clear
          </button>
        )}
      </div>

      {expanded && (
        <div className="grid gap-2 px-6 pb-2">
          {/* Row 1 — date controls */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                Day
              </span>
              <input
                type="date"
                value={filters.date_on ?? ""}
                onChange={(e) =>
                  patch({
                    date_on: e.target.value || undefined,
                    date_from: undefined,
                    date_to: undefined,
                  })
                }
                className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-1.5 py-0.5 text-[12px]"
              />
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                Range
              </span>
              <input
                type="date"
                value={filters.date_from ?? ""}
                onChange={(e) =>
                  patch({ date_from: e.target.value || undefined, date_on: undefined })
                }
                className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-1.5 py-0.5 text-[12px]"
                aria-label="Date from"
              />
              <span className="text-[11px] text-[var(--text-muted)]">→</span>
              <input
                type="date"
                value={filters.date_to ?? ""}
                onChange={(e) =>
                  patch({ date_to: e.target.value || undefined, date_on: undefined })
                }
                className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-1.5 py-0.5 text-[12px]"
                aria-label="Date to"
              />
            </div>
            <DateQuickPicks
              filters={filters}
              patch={(delta) => onChange({ ...filters, ...delta })}
            />
          </div>

          {/* Row 2 — supplier multi-select */}
          <MultiSelectChips
            label="Supplier"
            options={supplierOptions}
            selected={filters.suppliers}
            onChange={(v) => patch({ suppliers: v })}
            emptyHint="No suppliers in view"
          />

          {/* Row 3 — agent multi-select */}
          <MultiSelectChips
            label="Agent"
            options={agentOptions}
            selected={filters.agents}
            onChange={(v) => patch({ agents: v })}
            emptyHint="No agents in view"
          />

          {/* Row 4 — status + verdict + deadline state */}
          <div className="flex flex-wrap items-center gap-4">
            <MultiSelectChips
              label="Status"
              options={[...STATUS_OPTIONS]}
              selected={filters.statuses}
              onChange={(v) => patch({ statuses: v })}
            />
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                Verdict
              </span>
              {VERDICT_OPTIONS.map((v) => {
                const on = (filters.verdict_states ?? []).includes(v);
                const next = (): TrackerVerdictState[] | undefined => {
                  const set = new Set(filters.verdict_states ?? []);
                  if (on) set.delete(v);
                  else set.add(v);
                  return set.size > 0 ? ([...set] as TrackerVerdictState[]) : undefined;
                };
                return (
                  <button
                    key={v}
                    type="button"
                    onClick={() => patch({ verdict_states: next() })}
                    className={`rounded-full border px-2 py-0.5 text-[11px] ${
                      on
                        ? "border-emerald-500 bg-emerald-50 text-emerald-900"
                        : "border-[var(--border-subtle)] text-[var(--text-muted)] hover:bg-[var(--bg-elev2)]"
                    }`}
                  >
                    {VERDICT_LABEL[v]}
                  </button>
                );
              })}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                Deadline
              </span>
              {DEADLINE_OPTIONS.map((opt) => {
                const on = filters.deadline_state === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() =>
                      patch({ deadline_state: on ? undefined : opt.value })
                    }
                    className={`rounded-full border px-2 py-0.5 text-[11px] ${
                      on
                        ? "border-amber-500 bg-amber-50 text-amber-900"
                        : "border-[var(--border-subtle)] text-[var(--text-muted)] hover:bg-[var(--bg-elev2)]"
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Row 5 — deal value range */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
              Annual value (£)
            </span>
            <input
              type="number"
              min={0}
              value={filters.value_min ?? ""}
              onChange={(e) =>
                patch({
                  value_min:
                    e.target.value === "" ? undefined : Number(e.target.value),
                })
              }
              placeholder="min"
              className="w-24 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1 text-[12px]"
              aria-label="Annual deal value min"
            />
            <span className="text-[11px] text-[var(--text-muted)]">→</span>
            <input
              type="number"
              min={0}
              value={filters.value_max ?? ""}
              onChange={(e) =>
                patch({
                  value_max:
                    e.target.value === "" ? undefined : Number(e.target.value),
                })
              }
              placeholder="max"
              className="w-24 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1 text-[12px]"
              aria-label="Annual deal value max"
            />
          </div>
        </div>
      )}
    </div>
  );
}

function DateQuickPicks({
  filters,
  patch,
}: {
  filters: TrackerFilters;
  patch: (delta: Partial<TrackerFilters>) => void;
}) {
  const today = new Date();
  const isoDate = (d: Date) => d.toISOString().slice(0, 10);
  const setRange = (daysAgo: number) => {
    const from = new Date();
    from.setDate(today.getDate() - daysAgo);
    patch({ date_on: undefined, date_from: isoDate(from), date_to: isoDate(today) });
  };
  const setToday = () => {
    patch({ date_on: isoDate(today), date_from: undefined, date_to: undefined });
  };
  const setThisMonth = () => {
    const first = new Date(today.getFullYear(), today.getMonth(), 1);
    patch({
      date_on: undefined,
      date_from: isoDate(first),
      date_to: isoDate(today),
    });
  };
  const isAny =
    Boolean(filters.date_on) ||
    Boolean(filters.date_from) ||
    Boolean(filters.date_to);
  return (
    <div className="flex flex-wrap items-center gap-1">
      <button
        type="button"
        onClick={setToday}
        className="rounded-full border border-[var(--border-subtle)] px-2 py-0.5 text-[11px] text-[var(--text-muted)] hover:bg-[var(--bg-elev2)]"
      >
        Today
      </button>
      <button
        type="button"
        onClick={() => setRange(7)}
        className="rounded-full border border-[var(--border-subtle)] px-2 py-0.5 text-[11px] text-[var(--text-muted)] hover:bg-[var(--bg-elev2)]"
      >
        Last 7d
      </button>
      <button
        type="button"
        onClick={() => setRange(30)}
        className="rounded-full border border-[var(--border-subtle)] px-2 py-0.5 text-[11px] text-[var(--text-muted)] hover:bg-[var(--bg-elev2)]"
      >
        Last 30d
      </button>
      <button
        type="button"
        onClick={setThisMonth}
        className="rounded-full border border-[var(--border-subtle)] px-2 py-0.5 text-[11px] text-[var(--text-muted)] hover:bg-[var(--bg-elev2)]"
      >
        This month
      </button>
      {isAny && (
        <button
          type="button"
          onClick={() =>
            patch({ date_on: undefined, date_from: undefined, date_to: undefined })
          }
          className="rounded-full border border-[var(--border-subtle)] px-2 py-0.5 text-[11px] text-[var(--text-muted)] hover:bg-[var(--bg-elev2)]"
        >
          Clear dates
        </button>
      )}
    </div>
  );
}
