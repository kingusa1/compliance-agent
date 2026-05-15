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
 * - `human`: a reviewer typed/selected this value via inline edit.
 * - `xlsx_import`: imported from Watt's tracker XLSX (back-fill).
 * - `integration`: came from an external system (CRM, supplier feed).
 * - `ai`: pipeline detection / LLM stamped this value.
 * - `placeholder`: stub value (e.g. "(missing)") — needs human attention.
 * - `reviewer_edit`: deal-level inline edit via the tracker side panel
 *   (2026-05-15). Conceptually identical to ``human`` but stamped on the
 *   deal row's field_sources rather than the rejection row's. Kept as a
 *   distinct value so /customers + /deals can show "edited" vs "AI" badges.
 */
export type TrackerFieldSource =
  | "human"
  | "reviewer_edit"
  | "xlsx_import"
  | "integration"
  | "ai"
  | "placeholder";

export type TrackerRow = {
  customer_name: string | null;
  mpan_mprn: string | null;
  // 2026-05-15: separate columns also surfaced so the side-panel meter
  // inputs render the value the reviewer last saved (combined `mpan_mprn`
  // is for display only).
  mpan_electricity: string | null;
  mprn_gas: string | null;
  docusign_reference: string | null;
  term_months: number | null;
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

export type TrackerVerdictState =
  | "AI_PENDING"
  | "HUMAN_CONFIRMED"
  | "HUMAN_OVERRIDDEN";

export type TrackerDeadlineState =
  | "overdue"
  | "due_3d"
  | "due_7d"
  | "on_track";

export type TrackerFilters = {
  tab?: TrackerTab;
  // Legacy single-value filters — kept for back-compat with bookmarked URLs.
  month?: string;
  category?: string[];
  supplier?: string;
  search?: string;
  // 2026-05-15 advanced filters — multi-value lists win when both forms set.
  suppliers?: string[];
  agents?: string[];
  statuses?: string[];
  verdict_states?: TrackerVerdictState[];
  date_from?: string;       // YYYY-MM-DD inclusive
  date_to?: string;         // YYYY-MM-DD inclusive
  date_on?: string;         // YYYY-MM-DD single day
  meter?: string;           // MPAN/MPRN substring
  value_min?: number;       // £ annual deal value
  value_max?: number;
  deadline_state?: TrackerDeadlineState;
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
      // Advanced multi-select / range filters.
      const csv = (xs?: string[]) =>
        xs && xs.length > 0 ? xs.join(",") : null;
      const suppliersCsv = csv(filters.suppliers);
      if (suppliersCsv) qs.set("suppliers", suppliersCsv);
      const agentsCsv = csv(filters.agents);
      if (agentsCsv) qs.set("agents", agentsCsv);
      const statusesCsv = csv(filters.statuses);
      if (statusesCsv) qs.set("statuses", statusesCsv);
      const vsCsv = csv(filters.verdict_states);
      if (vsCsv) qs.set("verdict_states", vsCsv);
      if (filters.date_from) qs.set("date_from", filters.date_from);
      if (filters.date_to) qs.set("date_to", filters.date_to);
      if (filters.date_on) qs.set("date_on", filters.date_on);
      if (filters.meter) qs.set("meter", filters.meter);
      if (filters.value_min !== undefined && filters.value_min !== null) {
        qs.set("value_min", String(filters.value_min));
      }
      if (filters.value_max !== undefined && filters.value_max !== null) {
        qs.set("value_max", String(filters.value_max));
      }
      if (filters.deadline_state) qs.set("deadline_state", filters.deadline_state);
      return apiFetch<TrackerResponse>(`/api/tracker/rows?${qs.toString()}`);
    },
    // /tracker is the operational dashboard — must reflect every new
    // upload / verdict / rejection within 3 s. Inherits global defaults
    // (refetchOnWindowFocus, refetchOnReconnect) from QueryProvider.
    staleTime: 0,
    gcTime: 5 * 60 * 1000,
    refetchInterval: 3_000,
  });
}

export function trackerExportUrl(): string {
  return `/api/tracker/export.xlsx`;
}
