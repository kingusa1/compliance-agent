/**
 * Admin-lane TanStack Query keys + queryFns.
 *
 * Matches the patterns in `lib/queries/reviewer.ts`: domain types kept
 * permissive (backend evolves faster than openapi codegen), hook helpers
 * preferred over raw `useQuery(...)` so cache key + queryFn live together.
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

// ── Domain types ──────────────────────────────────────────────────
//
// These mirror the backend's `CustomerSummary`, `CustomerDetailResponse`,
// `CustomerDealCard` etc. shapes from openapi codegen. Kept loose so the
// page can narrow on render without breaking when the backend ships a
// minor schema tweak.

export type CustomerSummary = {
  slug: string;
  display_name: string;
  deal_count: number;
  call_count: number;
  agents: string[];
  suppliers: string[];
  worst_action: string | null;
  last_seen: string | null;
  open_directives: number;
  critical_flag_count: number;
  has_duplicate_hint: boolean;
  // W1.1 (v3-watt-coverage): Watt portal deep-link integer.
  external_watt_site_id?: number | null;
};

export type CustomerListResponse = {
  customers: CustomerSummary[];
  total: number;
  has_more: boolean;
};

export type DealCallSlot = {
  id: string;
  call_type: string | null;
  status: string | null;
  score: string | null;
  created_at: string | null;
};

export type CustomerDealCard = {
  id: string;
  deal_ref: string;
  supplier: string | null;
  deal_value_gbp: number | null;
  agent_name: string | null;
  status: string | null;
  final_action: string | null;
  open_directives: number;
  last_call_at: string | null;
  calls: DealCallSlot[];
};

export type CustomerDetailResponse = {
  customer: CustomerSummary;
  deals: CustomerDealCard[];
};

// rollup + timeline are loosely typed in openapi (untyped JSON), so we
// model them as records here. Pages read fields defensively.
export type CustomerRollup = {
  total_deals?: number;
  total_calls?: number;
  total_value_gbp?: number;
  open_directives?: number;
  open_rejections?: number;
  worst_action?: string | null;
  [k: string]: unknown;
};

export type CustomerTimelineRow = {
  id: string;
  created_at?: string | null;
  deal_id?: string | null;
  deal_ref?: string | null;
  call_type?: string | null;
  agent_name?: string | null;
  score?: string | null;
  // Backend's /timeline endpoint returns booleans for `compliant`. Older
  // builds used strings ("compliant" / "non_compliant"); keep both.
  compliant?: string | boolean | null;
  rejection?: string | null;
  [k: string]: unknown;
};

export type CustomerTimelineResponse = {
  // Backend returns `timeline: [...]`. Older builds used `rows: [...]`.
  // The fetcher normalises into `rows` so pages don't have to branch.
  rows: CustomerTimelineRow[];
  timeline?: CustomerTimelineRow[];
  [k: string]: unknown;
};

// ── Calls list (admin /calls page) ────────────────────────────────
//
// Lightweight shape for the admin /calls list. The reviewer-page version
// in `lib/api.ts#Call` is richer; admin only needs scan-and-decide
// columns + deal grouping fields.

export type AdminCallRow = {
  id: string;
  filename: string;
  customer_name: string | null;
  customer_slug?: string | null;
  detected_supplier: string | null;
  agent_name: string | null;
  score: string | null;
  compliance_status: string | null;
  status: string;
  // Boolean AI verdict — populated when the pipeline finishes scoring.
  // Used as the authoritative signal for the Compliant column on /calls;
  // `compliance_status` can drift (older calls were stamped before the
  // pass/coaching → "compliant" mapping was added) so the boolean wins.
  compliant?: boolean | string | null;
  created_at: string;
  deal_id?: string | null;
  deal_ref?: string | null;
  deal_value_gbp?: number | null;
  call_type?: string | null;
};

export type AdminCallsResponse = {
  calls: AdminCallRow[];
  total: number;
};

// ── Scripts (used by IntakeForm + scripts page in R7) ────────────
export type Script = {
  id: string;
  name: string;
  version?: number | string | null;
  active?: boolean;
};

export type ScriptsResponse = {
  scripts: Script[];
};

// ── Query keys ────────────────────────────────────────────────────
export const adminKeys = {
  calls: (params?: Record<string, unknown>) => ["admin", "calls", params ?? {}] as const,
  customers: (params?: Record<string, unknown>) =>
    ["admin", "customers", params ?? {}] as const,
  customer: (slug: string) => ["admin", "customer", slug] as const,
  customerRollup: (slug: string) => ["admin", "customer", slug, "rollup"] as const,
  customerTimeline: (slug: string) => ["admin", "customer", slug, "timeline"] as const,
  scripts: () => ["admin", "scripts"] as const,
};

// ── Fetchers ──────────────────────────────────────────────────────
export function fetchAdminCalls(params: { limit?: number; offset?: number } = {}): Promise<AdminCallsResponse> {
  const qs = new URLSearchParams();
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  const tail = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<AdminCallsResponse>(`/api/calls${tail}`);
}

export function fetchAdminCustomers(params: { q?: string; limit?: number; offset?: number } = {}): Promise<CustomerListResponse> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const tail = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<CustomerListResponse>(`/api/customers${tail}`);
}

export function fetchCustomerDetail(slug: string): Promise<CustomerDetailResponse> {
  return apiFetch<CustomerDetailResponse>(`/api/customers/${encodeURIComponent(slug)}`);
}

export function fetchCustomerRollup(slug: string): Promise<CustomerRollup> {
  return apiFetch<CustomerRollup>(`/api/customers/${encodeURIComponent(slug)}/rollup`);
}

export async function fetchCustomerTimeline(slug: string): Promise<CustomerTimelineResponse> {
  // Backend returns `{timeline: [...]}` (v2) but earlier builds used `{rows: [...]}`.
  // Normalise to `rows` so the page stays shape-agnostic.
  const raw = await apiFetch<CustomerTimelineResponse>(
    `/api/customers/${encodeURIComponent(slug)}/timeline`,
  );
  const rows = (raw.rows ?? raw.timeline ?? []) as CustomerTimelineRow[];
  return { ...raw, rows };
}

export function fetchScripts(): Promise<ScriptsResponse> {
  return apiFetch<ScriptsResponse>(`/api/scripts`);
}

// ── Hook helpers ──────────────────────────────────────────────────
export function useAdminCallsQuery(params: { limit?: number; offset?: number } = {}) {
  return useQuery({
    queryKey: adminKeys.calls(params),
    queryFn: () => fetchAdminCalls(params),
    // 2026-05-23 — /calls is realtime-driven via useCallEvents("*") (SSE
    // from `app/realtime_routes.py`) mounted in ScreenFrame + the
    // `useRealtimeInvalidate("calls", [["admin-calls"]])` hook on the
    // /tracker page (which shares this query key). The previous 60s
    // refetchInterval was a stale safety net from before SSE shipped
    // and caused the visible "page refreshes itself" flicker on
    // /calls + /tracker. Realtime is the contract; this query
    // stays Infinity-stale until invalidated.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
  });
}

export function useAdminCustomersQuery(params: { q?: string; limit?: number; offset?: number } = {}) {
  return useQuery({
    queryKey: adminKeys.customers(params),
    queryFn: () => fetchAdminCustomers(params),
    staleTime: 30_000,
  });
}

export function useCustomerDetailQuery(slug: string) {
  return useQuery({
    queryKey: adminKeys.customer(slug),
    queryFn: () => fetchCustomerDetail(slug),
    enabled: !!slug,
  });
}

export function useCustomerRollupQuery(slug: string) {
  return useQuery({
    queryKey: adminKeys.customerRollup(slug),
    queryFn: () => fetchCustomerRollup(slug),
    enabled: !!slug,
  });
}

export function useCustomerTimelineQuery(slug: string) {
  return useQuery({
    queryKey: adminKeys.customerTimeline(slug),
    queryFn: () => fetchCustomerTimeline(slug),
    enabled: !!slug,
  });
}

export function useScriptsQuery() {
  return useQuery({
    queryKey: adminKeys.scripts(),
    queryFn: () => fetchScripts(),
    staleTime: 5 * 60 * 1000,
  });
}
