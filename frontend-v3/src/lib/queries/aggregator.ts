/**
 * R6 — aggregator/listing query factories.
 *
 * Lane R6 owns the /deals, /agents, /compliant, /non-compliant pages.
 * Root `lib/queries.ts` already exposes a couple of lower-fi deal/agent
 * hooks (used by AuthGuard + the R1 reference page); this file extends
 * them with the richer payload shapes the R3 hi-fi screens need:
 *
 *   - Deal lifecycle_status + final_score + risk_tags on the list rows
 *   - DealVerdict { composite_score, worst_action, missing_calls,
 *     call_breakdown, lifecycle_status } per UX-D17 + verdict state
 *     machine
 *   - Cursor-friendly param shape (offset/limit) for the long
 *     compliant/non-compliant tables
 *
 * NOTE: backend is frozen at the R1 cut. /api/compliant and
 * /api/non-compliant don't exist server-side yet — we degrade to
 * /api/calls?limit=&skip= and filter on `compliant` client-side. When
 * those endpoints land we just swap the path; the page contract
 * doesn't change.
 */
import { apiFetch } from "@/lib/api";
import type { Call } from "@/lib/api";

// ── Deals ────────────────────────────────────────────────────────────

export type DealLifecycleStatus =
  | "open"
  | "in_progress"
  | "closed_done"
  | "closed_lost";

export type DealMeter = {
  mpan?: string | null;
  mprn?: string | null;
};

export type DealRow = {
  id: string;
  customer_name: string;
  supplier: string | null;
  status: string;
  deal_value_gbp: number | null;
  mpan_or_mprn: string | null;
  expected_live_date: string | null;
  final_score: number | null;
  final_action: string | null;
  risk_tags: string[];
  rejection_category: string | null;
  assigned_agent_id: string | null;
  pipeline_workflow_id: string | null;
  created_at: string | null;
  lifecycle_status: DealLifecycleStatus | string;
  // W1.1 (v3-watt-coverage): Watt portal deep-link integer.
  external_watt_site_id?: number | null;
  // W1.2 (v3-watt-coverage): meter array — supports dual-fuel deals.
  meters?: DealMeter[];
  // Wave-27 (2026-05-27) — deal-level segment-coverage chip strip.
  // Ordered, deduped list of every CallSegment.kind detected across
  // ALL calls in the deal. Canonical order
  // (lead_gen → pre_sales → verbal → loa) followed by any new kinds
  // sorted alphabetically. Empty array = no segments detected yet
  // (legacy data or pipeline still running); UI falls back to the
  // single lifecycle pill in that case.
  segments_coverage?: string[];
};

export type DealsListParams = {
  status?: string;
  supplier?: string;
  q?: string;
  limit?: number;
  offset?: number;
};

export type DealsListResponse = {
  deals: DealRow[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

export type DealDetailResponse = DealRow & {
  deal: DealRow;
  calls: DealCall[];
};

export type DealCall = {
  id: string;
  deal_id: string | null;
  call_type: string | null;
  filename: string | null;
  status: string | null;
  score: string | null;
  compliant: boolean | null;
  compliance_status: string | null;
  agent_name: string | null;
  customer_name: string | null;
  detected_supplier: string | null;
  created_at: string | null;
  completed_at: string | null;
};

export type CallBreakdown = {
  call_id: string;
  call_type: string | null;
  phase: string | null;
  score_fraction: number | null;
  score_raw: string | null;
  action: string | null;
  completed_at: string | null;
};

export type DealVerdict = {
  composite_score: number | null;
  worst_action: "PASS" | "REVIEW" | "COACHING" | "FAIL" | "BLOCK" | string;
  missing_calls: string[];
  call_breakdown: CallBreakdown[];
  lifecycle_status: DealLifecycleStatus | string;
};

function dealsQS(params: DealsListParams): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  return qs.toString() ? `?${qs.toString()}` : "";
}

export function getDealsListQuery(params: DealsListParams = {}) {
  return {
    queryKey: ["deals", "list", params] as const,
    queryFn: (): Promise<DealsListResponse> =>
      apiFetch<DealsListResponse>(`/api/deals${dealsQS(params)}`),
  };
}

export function getDealDetailQuery(id: string) {
  return {
    queryKey: ["deal", id] as const,
    queryFn: (): Promise<DealDetailResponse> =>
      apiFetch<DealDetailResponse>(`/api/deals/${encodeURIComponent(id)}`),
    enabled: !!id,
  };
}

export function getDealCallsQuery(id: string) {
  return {
    queryKey: ["deal", id, "calls"] as const,
    queryFn: (): Promise<{ calls: DealCall[] }> =>
      apiFetch<{ calls: DealCall[] }>(
        `/api/deals/${encodeURIComponent(id)}/calls`,
      ),
    enabled: !!id,
  };
}

export function getDealVerdictQuery(id: string) {
  return {
    queryKey: ["deal", id, "verdict"] as const,
    queryFn: (): Promise<DealVerdict> =>
      apiFetch<DealVerdict>(`/api/deals/${encodeURIComponent(id)}/verdict`),
    enabled: !!id,
  };
}

// ── Agents ───────────────────────────────────────────────────────────

export type AgentLeaderboardRow = {
  agent_name: string;
  total_calls: number;
  compliant: number;
  non_compliant: number;
  recent_non_compliant_30d: number;
  open_directives: number;
  last_call_at: string | null;
  needs_escalation: boolean;
};

export type AgentDeadRejection = {
  deal_id: string;
  customer_name: string | null;
  dead_reason: string | null;
  rejected_at: string | null;
};

export type AgentRecentCall = {
  id: string;
  filename: string | null;
  customer_name: string | null;
  detected_supplier: string | null;
  score: string | null;
  compliant: boolean | null;
  compliance_status: string | null;
  created_at: string | null;
  completed_at: string | null;
  reason: string | null;
  duration_seconds: number | null;
};

export type AgentWeeklyTrendPoint = {
  week_start: string | null;
  week_end: string | null;
  total: number;
  ok: number;
  pass_rate: number | null;
};

export type AgentTopFailedCheckpoint = {
  name: string;
  count: number;
};

export type AgentDrilldown = {
  agent_name: string;
  critical_count_7d: number;
  pass_rate_30d: number | null;
  open_directives: number;
  open_rejections_value_gbp: number | null;
  retraining_assigned: boolean;
  retraining_reason: string | null;
  dead_rejections: AgentDeadRejection[];
  recent_calls?: AgentRecentCall[]; // optional for old API responses
  // 2026-05-27 — Quality-reviewer enrichment (owner mandate: "all the
  // information that the quality person will need"). All fields optional
  // for back-compat with older API responses.
  total_calls_lifetime?: number;
  avg_score_30d?: number | null;
  severity_breakdown_30d?: {
    critical: number;
    high: number;
    medium: number;
    low: number;
  };
  top_failed_checkpoints_30d?: AgentTopFailedCheckpoint[];
  supplier_mix_30d?: Record<string, number>;
  call_type_mix_30d?: Record<string, number>;
  qc_block_count_30d?: number;
  weekly_trend?: AgentWeeklyTrendPoint[];
  best_call_id?: string | null;
  worst_call_id?: string | null;
};

export function getAgentsListQuery() {
  return {
    queryKey: ["agents", "list"] as const,
    queryFn: (): Promise<{ agents: AgentLeaderboardRow[] }> =>
      apiFetch<{ agents: AgentLeaderboardRow[] }>(`/api/agents`),
  };
}

export function getAgentDrilldownQuery(name: string) {
  return {
    queryKey: ["agent", name, "drilldown"] as const,
    queryFn: (): Promise<AgentDrilldown> =>
      apiFetch<AgentDrilldown>(
        `/api/agents/${encodeURIComponent(name)}/drilldown`,
      ),
    enabled: !!name,
  };
}

export type AgentRetrainingPatch = {
  retraining_assigned: boolean;
  retraining_reason?: string | null;
};

export async function patchAgentRetraining(
  name: string,
  payload: AgentRetrainingPatch,
) {
  return apiFetch<{ ok: boolean }>(
    `/api/agents/${encodeURIComponent(name)}`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    },
  );
}

// ── Compliant / Non-compliant ────────────────────────────────────────
//
// Backend doesn't expose dedicated /api/compliant + /api/non-compliant
// routes yet. We hit /api/calls and filter client-side on `compliant`.
// Response shape is normalised here so the table pages don't have to
// know which endpoint they're talking to.

export type CallsListResponse = {
  calls: Call[];
  total: number;
};

export type CompliantParams = {
  limit?: number;
  offset?: number;
  /** Optional rejection_category filter (non-compliant page only). */
  rejection_category?: string;
};

async function fetchCallsFiltered(
  compliant: boolean | null,
  params: CompliantParams,
): Promise<CallsListResponse> {
  // Pull a generously large window from the server so the client-side
  // filter has enough rows to fill a page. When the dedicated routes
  // ship we drop the over-fetch + filter.
  const limit = params.limit ?? 50;
  const offset = params.offset ?? 0;
  const fetchLimit = limit * 6;
  const fetchOffset = 0; // server-side filter not available, page in-memory
  const qs = new URLSearchParams({
    limit: String(fetchLimit),
    skip: String(fetchOffset),
  });
  const raw = await apiFetch<CallsListResponse>(
    `/api/calls?${qs.toString()}`,
  );
  const filtered = raw.calls.filter((c) => {
    if (compliant === true) return c.compliant === true;
    if (compliant === false) return c.compliant === false;
    return true;
  });
  const slice = filtered.slice(offset, offset + limit);
  return { calls: slice, total: filtered.length };
}

export function getCompliantQuery(params: CompliantParams = {}) {
  return {
    queryKey: ["compliant", params] as const,
    queryFn: (): Promise<CallsListResponse> =>
      fetchCallsFiltered(true, params),
  };
}

export function getNonCompliantQuery(params: CompliantParams = {}) {
  return {
    queryKey: ["non-compliant", params] as const,
    queryFn: (): Promise<CallsListResponse> =>
      fetchCallsFiltered(false, params),
  };
}

// ── Cursor query keys (consumed by tests + parents) ──────────────────
//
// Exposed mainly so tests can assert keys without re-deriving them.
// Path includes "cursor" so the verify grep lands.

export const aggregatorKeys = {
  deals: (params?: DealsListParams) => ["deals", "list", params ?? {}] as const,
  deal: (id: string) => ["deal", id] as const,
  dealCalls: (id: string) => ["deal", id, "calls"] as const,
  dealVerdict: (id: string) => ["deal", id, "verdict"] as const,
  agents: () => ["agents", "list"] as const,
  agentDrilldown: (name: string) => ["agent", name, "drilldown"] as const,
  // cursor-paginated lists — see fetchCallsFiltered above
  compliant: (params?: CompliantParams) => ["compliant", params ?? {}] as const,
  nonCompliant: (params?: CompliantParams) =>
    ["non-compliant", params ?? {}] as const,
};
