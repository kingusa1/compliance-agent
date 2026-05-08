/**
 * Aggregator/listing mutation hooks.
 *
 *   usePatchAgent() — PATCH /api/agents/{name} body { retraining_assigned: bool }
 *
 * Used by AgentHero retraining toggle. AgentHero today calls
 * `patchAgentRetraining` directly (with its own toast + rollback); this
 * hook wraps the same call in a useMutation so cache invalidation is
 * handled once.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  aggregatorKeys,
  patchAgentRetraining,
  type AgentRetrainingPatch,
} from "@/lib/queries/aggregator";

export type PatchAgentArgs = {
  name: string;
  payload: AgentRetrainingPatch;
};

export function usePatchAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, payload }: PatchAgentArgs) =>
      patchAgentRetraining(name, payload),
    onSuccess: (_data, { name, payload }) => {
      qc.invalidateQueries({ queryKey: aggregatorKeys.agentDrilldown(name) });
      qc.invalidateQueries({ queryKey: aggregatorKeys.agents() });
      toast.success(
        payload.retraining_assigned
          ? `Retraining assigned to ${name}`
          : `Retraining cleared for ${name}`,
      );
    },
    onError: (err) => {
      toast.error("Could not update retraining flag", {
        description: err instanceof Error ? err.message : "Unknown error",
      });
    },
  });
}
