"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  obsKeys,
  redispatchOrphan,
  useStuckQuery,
} from "@/lib/queries/observability";

/**
 * StuckBanner — only renders when the /api/observability/stuck endpoint
 * returns a non-empty list. Each row is a call_id whose pipeline never
 * progressed past intake. Clicking Re-dispatch fires a POST that re-emits
 * the Inngest "call.uploaded" event.
 */
export function StuckBanner() {
  const qc = useQueryClient();
  const { data } = useStuckQuery();
  const mutation = useMutation({
    mutationFn: (callId: string) => redispatchOrphan(callId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: obsKeys.stuck() });
      qc.invalidateQueries({ queryKey: obsKeys.runs() });
      toast.success("Re-dispatched", { description: "Pipeline event re-emitted." });
    },
    onError: (err) => {
      toast.error("Could not re-dispatch", {
        description: err instanceof Error ? err.message : "Unknown error",
      });
    },
  });

  const stuck = data?.stuck ?? [];
  if (stuck.length === 0) return null;

  return (
    <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-[14px] font-semibold text-red-400">
          {stuck.length} stuck {stuck.length === 1 ? "call" : "calls"}
        </h3>
        <span className="text-[12px] text-[var(--text-dim)]">
          Pipeline never progressed past intake
        </span>
      </div>
      <ul className="space-y-1.5">
        {stuck.slice(0, 5).map((s) => (
          <li
            key={s.call_id}
            className="flex items-center justify-between rounded-md bg-[var(--bg-elev1)] px-3 py-2 text-[12px]"
          >
            <div className="flex items-center gap-3">
              <span className="font-mono text-[var(--text-primary)]">
                {s.call_id.slice(0, 12)}
              </span>
              {s.reason && (
                <span className="text-[var(--text-muted)]">{s.reason}</span>
              )}
              {s.age_minutes != null && (
                <span className="text-[var(--text-dim)]">
                  {Math.round(s.age_minutes)}m old
                </span>
              )}
            </div>
            <Button
              size="xs"
              variant="outline"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate(s.call_id)}
            >
              Re-dispatch
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
