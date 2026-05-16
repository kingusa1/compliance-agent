/**
 * Sprint Task B — composite Deal verdict query.
 *
 * The richer /deals + /deals/[id] queries live in `lib/queries/aggregator.ts`
 * (`getDealsListQuery`, `getDealDetailQuery`, `getDealVerdictQuery`). This
 * file is the home for the new `useDealCompositeVerdictQuery` hook the
 * /deals/[id] page uses to render the composite donut + per-call breakdown.
 *
 * Backend route: `GET /api/deals/{id}/composite-verdict` — see
 * `backend/app/deals_composite.py` for math + weights.
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export type DealCompositeVerdict = {
  deal_id: string;
  composite_pct: number | null;
  threshold_pct: number;
  threshold_met: boolean;
  worst_action: "PASS" | "REVIEW" | "FAIL" | "PENDING";
  calls_scored: number;
  calls_total: number;
  per_call: Array<{
    id: string;
    call_type: string;
    score: number | null;
    weight: number;
    status: string | null;
    agent: string | null;
  }>;
};

export function useDealCompositeVerdictQuery(dealId: string) {
  // 2026-05-16 audit Wave 4 fix: the 5s refetchInterval ran 12 wasted
  // requests per minute on every deal view. Replaced with passive query
  // — the consuming page should mount useRealtimeInvalidate("calls",
  // [["admin", "deal", dealId, "composite-verdict"]]) so any pipeline
  // step transition pushes a Supabase Realtime / SSE event and
  // invalidates this key on demand. Background safety-net via
  // refetchOnWindowFocus stays on.
  return useQuery({
    queryKey: ["admin", "deal", dealId, "composite-verdict"],
    queryFn: () =>
      apiFetch<DealCompositeVerdict>(
        `/api/deals/${encodeURIComponent(dealId)}/composite-verdict`,
      ),
    enabled: !!dealId,
    staleTime: 30_000,
  });
}
