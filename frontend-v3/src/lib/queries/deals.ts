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
  return useQuery({
    queryKey: ["admin", "deal", dealId, "composite-verdict"],
    queryFn: () =>
      apiFetch<DealCompositeVerdict>(
        `/api/deals/${encodeURIComponent(dealId)}/composite-verdict`,
      ),
    enabled: !!dealId,
    refetchInterval: 5000, // refresh while calls are still being scored
  });
}
