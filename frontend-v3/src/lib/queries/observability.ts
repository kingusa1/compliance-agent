/**
 * Observability TanStack Query keys + queryFns.
 *
 * Backs /observability — runs list + stuck banner + redispatch action.
 * Loose typing: backend evolves the run shape (Inngest event flat vs
 * structured), so the page reads fields defensively.
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export type RunRow = {
  workflow?: string | null;
  run_id?: string | null;
  call_id?: string | null;
  started_at?: string | null;
  status?: string | null;
  duration_ms?: number | null;
  [k: string]: unknown;
};

export type RunsResponse = {
  runs: RunRow[];
  inngest_status?: string | null;
};

export type StuckRow = {
  call_id: string;
  reason?: string | null;
  age_minutes?: number | null;
  [k: string]: unknown;
};

export type StuckResponse = {
  stuck: StuckRow[];
};

export const obsKeys = {
  runs: (params?: Record<string, unknown>) => ["obs", "runs", params ?? {}] as const,
  run: (id: string) => ["obs", "run", id] as const,
  stuck: () => ["obs", "stuck"] as const,
  orphans: () => ["obs", "orphans"] as const,
};

export function fetchRuns(
  params: { status?: string; range?: string; q?: string } = {},
): Promise<RunsResponse> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const tail = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<RunsResponse>(`/api/observability/runs${tail}`);
}

export function fetchStuck(): Promise<StuckResponse> {
  return apiFetch<StuckResponse>(`/api/observability/stuck`);
}

export function redispatchOrphan(callId: string): Promise<unknown> {
  return apiFetch(`/api/observability/orphans/${encodeURIComponent(callId)}/redispatch`, {
    method: "POST",
  });
}

export function useRunsQuery(params: { status?: string; range?: string; q?: string } = {}) {
  return useQuery({
    queryKey: obsKeys.runs(params),
    queryFn: () => fetchRuns(params),
    staleTime: 5_000,
  });
}

export function useStuckQuery() {
  return useQuery({
    queryKey: obsKeys.stuck(),
    queryFn: () => fetchStuck(),
    staleTime: 10_000,
  });
}


// ── Pipeline flow viz (per-call steps + merged terminal feed) ────────────
// Polls every 2s while the page is open so the waterfall + terminal feed
// stay live during a real call upload. Stops when the route unmounts.

export type PipelineStep = {
  id: string;
  step_name: string;
  status: "running" | "ok" | "err" | string;
  payload_in: unknown;
  payload_out: unknown;
  error_message: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
};

export type StepsResponse = {
  call_id: string;
  steps: PipelineStep[];
};

export type FeedEvent =
  | {
      kind: "step";
      ts: string | null;
      ended_at: string | null;
      step_name: string;
      status: string;
      duration_ms: number | null;
      error_message: string | null;
      payload_in: unknown;
      payload_out: unknown;
    }
  | {
      kind: "trace";
      ts: string | null;
      run_id: string;
      turn: number;
      role: "user" | "assistant" | "tool" | string;
      tool_name: string | null;
      model: string | null;
      latency_ms: number | null;
      content: string;
    };

export type FeedResponse = {
  call_id: string;
  count: number;
  events: FeedEvent[];
};

export function fetchSteps(callId: string): Promise<StepsResponse> {
  return apiFetch<StepsResponse>(`/api/observability/runs/${encodeURIComponent(callId)}/steps`);
}

export function fetchFeed(callId: string, since?: string | null): Promise<FeedResponse> {
  const qs = since ? `?since=${encodeURIComponent(since)}` : "";
  return apiFetch<FeedResponse>(`/api/observability/runs/${encodeURIComponent(callId)}/feed${qs}`);
}

export function useStepsQuery(
  callId: string | null | undefined,
  isActive: boolean = true,
) {
  // Active runs poll fast; completed runs poll slowly (just freshness check).
  return useQuery({
    queryKey: ["obs", "steps", callId],
    queryFn: () => fetchSteps(callId!),
    enabled: !!callId,
    refetchInterval: isActive ? 2_000 : 30_000,
    staleTime: isActive ? 0 : 10_000,
  });
}

export function useFeedQuery(
  callId: string | null | undefined,
  isActive: boolean = true,
) {
  return useQuery({
    queryKey: ["obs", "feed", callId],
    queryFn: () => fetchFeed(callId!),
    enabled: !!callId,
    refetchInterval: isActive ? 2_000 : 30_000,
    staleTime: isActive ? 0 : 10_000,
  });
}
