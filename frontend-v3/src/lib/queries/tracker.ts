/**
 * /tracker page query hooks.
 *
 * Backend route: GET /api/tracker/rows?tab=active|fixed|dead|compliant
 * Returns rows in Watt's 17-col XLSX shape (cols A-Q).
 */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

/**
 * Per-field provenance source. Surfaced by `/api/tracker/rows` (B6) so the UI
 * can render AI/Human badges on each cell.
 *
 * - `human`: a reviewer typed/selected this value.
 * - `xlsx_import`: imported from Watt's tracker XLSX (back-fill).
 * - `integration`: came from an external system (CRM, supplier feed).
 * - `ai`: pipeline detection / LLM stamped this value.
 * - `placeholder`: stub value (e.g. "(missing)") — needs human attention.
 */
export type TrackerFieldSource =
  | "human"
  | "xlsx_import"
  | "integration"
  | "ai"
  | "placeholder";

export type TrackerRow = {
  customer_name: string | null;
  mpan_mprn: string | null;
  expected_live_date: string | null;
  deal_value_gbp: number | null;
  supplier: string | null;
  rejected_at: string | null;
  sales_agent: string | null;
  rejection_reason: string | null;
  category: string | null;
  fix_required: string | null;
  fix_assignee_id: string | null;
  status: string | null;
  last_action_date: string | null;
  deadline: string | null;
  outcome: string | null;
  /**
   * Reviewer free-text scratchpad (XLSX col P = "Notes"). Backed by
   * `Rejection.outcome_narrative`. 2026-05-14: renamed from `notes` to
   * `outcome_narrative` so the side-panel textarea reads what the
   * aggregator actually emits.
   */
  outcome_narrative: string | null;
  score: string | null;
  call_id: string | null;
  rejection_id: string | null;
  deal_id: string | null;
  /**
   * LLM-generated free-text fix narrative. Distinct from the enum
   * `fix_required` — XLSX ops use combo phrases the enum can't capture.
   * Only present on rejection rows; absent on compliant/awaiting_review.
   */
  fix_narrative?: string | null;
  /**
   * AI/HUMAN provenance gate. Drives the badge + Confirm-button flow.
   * Emitted on every row type post-2026-05-14 audit; legacy rows that
   * pre-date the migration default to "AI_PENDING".
   */
  verdict_state?: "AI_PENDING" | "HUMAN_CONFIRMED" | "HUMAN_OVERRIDDEN" | null;
  confirmed_by?: string | null;
  confirmed_at?: string | null;
  /**
   * Merged deal + rejection field provenance. Rejection sources win on key
   * conflict. Empty `{}` for rows pre-dating B1 migration. Use `?.[fieldName]`
   * with a `??` fallback when rendering — the key may be absent.
   */
  field_sources?: Record<string, TrackerFieldSource>;
};

export type TrackerTab = "active" | "fixed" | "dead" | "compliant" | "awaiting_review";

export type TrackerFilters = {
  tab?: TrackerTab;
  month?: string;       // YYYY-MM
  category?: string[];  // category enum keys
  supplier?: string;
  search?: string;
};

type TrackerResponse = {
  tab: TrackerTab;
  count: number;
  rows: TrackerRow[];
};

export function useTrackerRowsQuery(filters: TrackerFilters) {
  const tab = filters.tab ?? "active";
  return useQuery<TrackerResponse, Error>({
    queryKey: ["admin", "tracker", filters],
    queryFn: async () => {
      const qs = new URLSearchParams({ tab });
      if (filters.month) qs.set("month", filters.month);
      if (filters.category && filters.category.length > 0) {
        qs.set("category", filters.category.join(","));
      }
      if (filters.supplier) qs.set("supplier", filters.supplier);
      if (filters.search) qs.set("search", filters.search);
      return apiFetch<TrackerResponse>(`/api/tracker/rows?${qs.toString()}`);
    },
    // Cached + revalidated lazily so /tracker feels instant after first load.
    // No background polling — reviewer manually refreshes if they want fresh data.
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
  });
}

export function trackerExportUrl(): string {
  return `/api/tracker/export.xlsx`;
}
