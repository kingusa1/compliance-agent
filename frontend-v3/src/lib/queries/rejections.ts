/**
 * W2 (v3-watt-coverage): TanStack Query keys + fetchers for /rejections.
 *
 * Mirrors the patterns in lib/queries/admin.ts: domain types kept loose and
 * pages narrow on render. Mutation wrappers + invalidation rules live in
 * lib/mutations/rejections.ts.
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type {
  DeadReasonsResponse,
  Rejection,
  RejectionAuditLogResponse,
  RejectionsListResponse,
} from "@/lib/schemas/rejections";

export type RejectionTab = "active" | "fixed" | "dead" | "archive";

export type RejectionsListParams = {
  tab?: RejectionTab;
  category?: string;
  search?: string;
  /** W4.6 — restrict the dead tab to one of DEAD_REASONS keys. */
  dead_reason?: string;
  /**
   * Phase 4 reviewer-initiated gate. Backend default is "all" (legacy AI
   * rows visible); the /rejections page passes "reviewer" to hide
   * AI-auto-generated rows that pre-dated the gate.
   */
  source?: "reviewer" | "ai" | "all";
  offset?: number;
  limit?: number;
};

export const rejectionsKeys = {
  all: () => ["rejections"] as const,
  list: (params?: RejectionsListParams) =>
    ["rejections", "list", params ?? {}] as const,
  detail: (id: string) => ["rejections", id] as const,
  auditLog: (id: string) => ["rejections", id, "audit-log"] as const,
  /** W4.6 — vocab + glosses for the Dead-tab filter chips. */
  deadReasons: () => ["rejections", "dead-reasons"] as const,
  /** W4.5 — supplier-grouped FIXED rejections for the portal-batches page. */
  portalBatches: (supplier?: string) =>
    ["portal-batches", { supplier: supplier ?? null }] as const,
};

function _qs(params: Record<string, unknown>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export function fetchRejections(
  params: RejectionsListParams = {},
): Promise<RejectionsListResponse> {
  return apiFetch<RejectionsListResponse>(`/api/rejections${_qs(params as Record<string, unknown>)}`);
}

export function fetchRejection(id: string): Promise<Rejection> {
  return apiFetch<Rejection>(`/api/rejections/${encodeURIComponent(id)}`);
}

export function fetchRejectionAuditLog(
  id: string,
): Promise<RejectionAuditLogResponse> {
  return apiFetch<RejectionAuditLogResponse>(
    `/api/rejections/${encodeURIComponent(id)}/audit-log`,
  );
}

export function useRejectionsQuery(params: RejectionsListParams = {}) {
  return useQuery({
    queryKey: rejectionsKeys.list(params),
    queryFn: () => fetchRejections(params),
    // Operational page — must reflect every new reviewer-created rejection
    // within seconds. Inherits global window-focus + reconnect refresh.
    staleTime: 0,
    refetchInterval: 3_000,
  });
}

export function useRejectionQuery(id: string | null | undefined) {
  return useQuery({
    queryKey: rejectionsKeys.detail(id ?? ""),
    queryFn: () => fetchRejection(id as string),
    enabled: !!id,
  });
}

export function useRejectionAuditLogQuery(id: string | null | undefined) {
  return useQuery({
    queryKey: rejectionsKeys.auditLog(id ?? ""),
    queryFn: () => fetchRejectionAuditLog(id as string),
    enabled: !!id,
  });
}

/** Reviewers list (for Fix Assignee dropdown). Backend exposes /api/reviewers. */
export type ReviewerOption = {
  id: string;
  name: string;
  email: string;
  role: string;
};

export function fetchReviewers(): Promise<{ reviewers: ReviewerOption[] }> {
  return apiFetch<{ reviewers: ReviewerOption[] }>(`/api/reviewers`);
}

export function useReviewersQuery() {
  return useQuery({
    queryKey: ["reviewers"] as const,
    queryFn: fetchReviewers,
    staleTime: 5 * 60 * 1000,
  });
}

// ── W4.6 — dead-reasons vocab ─────────────────────────────────────────

export function fetchDeadReasons(): Promise<DeadReasonsResponse> {
  return apiFetch<DeadReasonsResponse>(`/api/rejections/dead-reasons`);
}

export function useDeadReasonsQuery() {
  return useQuery({
    queryKey: rejectionsKeys.deadReasons(),
    queryFn: fetchDeadReasons,
    // Vocab is effectively static — cache aggressively.
    staleTime: 24 * 60 * 60 * 1000,
  });
}

// ── W4.5 — portal-batches list ────────────────────────────────────────

/** Wire shape returned by GET /api/portal-batches. Matches the FastAPI
 *  serializer in ``rejections_routes.list_portal_batches``. */
export type PortalBatchRejection = {
  id: string;
  customer_name: string | null;
  customer_slug: string | null;
  external_watt_site_id: number | null;
  rejection_reason: string;
  category: string;
  status: string;
  fixed_at: string | null;
};

export type PortalBatch = {
  supplier: string;
  count: number;
  rejections: PortalBatchRejection[];
};

export type PortalBatchesResponse = {
  batches: PortalBatch[];
};

export function fetchPortalBatches(
  supplier?: string,
): Promise<PortalBatchesResponse> {
  const qs = supplier ? `?supplier=${encodeURIComponent(supplier)}` : "";
  return apiFetch<PortalBatchesResponse>(`/api/portal-batches${qs}`);
}

export function usePortalBatchesQuery(supplier?: string) {
  return useQuery({
    queryKey: rejectionsKeys.portalBatches(supplier),
    queryFn: () => fetchPortalBatches(supplier),
    staleTime: 10_000,
  });
}
