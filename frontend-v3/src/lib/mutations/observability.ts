/**
 * Observability mutation hooks.
 *
 *   useRedispatchOrphan() — POST /api/observability/orphans/{call_id}/redispatch
 *
 * Used by /observability StuckBanner + the page-top "Redispatch now" CTA
 * (which fires the mutation in a loop, one per stuck row).
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { postJson } from "@/lib/mutations";
import { obsKeys } from "@/lib/queries/observability";

export function useRedispatchOrphan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (callId: string) =>
      postJson(`/api/observability/orphans/${encodeURIComponent(callId)}/redispatch`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: obsKeys.runs() });
      qc.invalidateQueries({ queryKey: obsKeys.stuck() });
      toast.success("Re-dispatched", { description: "Pipeline event re-emitted." });
    },
    onError: (err) => {
      toast.error("Could not re-dispatch", {
        description: err instanceof Error ? err.message : "Unknown error",
      });
    },
  });
}
