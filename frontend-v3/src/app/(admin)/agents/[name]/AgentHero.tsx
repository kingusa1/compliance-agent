"use client";

import { useState } from "react";
import { toast } from "sonner";

import {
  patchAgentRetraining,
  type AgentDrilldown,
} from "@/lib/queries/aggregator";
import { ApiError } from "@/lib/api";

/**
 * AgentHero — 4-stat hero strip + retraining toggle. Stats wired from
 * the /agents/{name}/drilldown endpoint:
 *   - Total flagged (critical_count_7d) — red
 *   - Pass rate (pass_rate_30d) — amber when <80
 *   - Open directives — amber when >0
 *   - Failed at risk £ (open_rejections_value_gbp) — red when >0
 *
 * Retraining toggle is the source-of-truth for HR follow-up; PATCHes
 * /api/agents/{name} on flip.
 */

export type AgentHeroProps = {
  data: AgentDrilldown;
};

export function AgentHero({ data }: AgentHeroProps) {
  const [retraining, setRetraining] = useState<boolean>(
    data.retraining_assigned,
  );
  const [pending, setPending] = useState(false);

  async function toggleRetraining(next: boolean) {
    setPending(true);
    const prev = retraining;
    setRetraining(next);
    try {
      await patchAgentRetraining(data.agent_name, {
        retraining_assigned: next,
      });
      toast.success(
        next
          ? `Retraining assigned to ${data.agent_name}`
          : `Retraining cleared for ${data.agent_name}`,
      );
    } catch (e) {
      setRetraining(prev);
      const msg =
        e instanceof ApiError
          ? `${e.status} ${e.body || e.message}`
          : e instanceof Error
            ? e.message
            : "Unknown error";
      toast.error("Couldn't update retraining flag", { description: msg });
    } finally {
      setPending(false);
    }
  }

  const passRatePct =
    data.pass_rate_30d != null ? Math.round(data.pass_rate_30d * 100) : null;
  const passRateTone =
    passRatePct == null
      ? "var(--text-muted)"
      : passRatePct < 80
        ? "var(--amber-review)"
        : "var(--emerald-pass)";

  return (
    <div className="border-b border-[var(--border-subtle)] px-6 py-6">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Stat
          label="Total flagged"
          value={String(data.critical_count_7d)}
          sub="critical · last 7d"
          tone="var(--red-fail)"
        />
        <Stat
          label="Pass rate"
          value={passRatePct == null ? "—" : `${passRatePct}%`}
          sub="rolling 30d"
          tone={passRateTone}
        />
        <Stat
          label="Open directives"
          value={String(data.open_directives)}
          sub={data.open_directives > 0 ? "needs follow-up" : "none open"}
          tone={
            data.open_directives > 0
              ? "var(--amber-review)"
              : "var(--text-muted)"
          }
        />
        <Stat
          label="Failed at risk"
          value={
            data.open_rejections_value_gbp != null
              ? `£${formatGBP(data.open_rejections_value_gbp)}`
              : "—"
          }
          sub="across open dirs"
          tone={
            data.open_rejections_value_gbp != null &&
            data.open_rejections_value_gbp > 0
              ? "var(--red-fail)"
              : "var(--text-muted)"
          }
        />

        <label
          className="flex items-center justify-between rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] px-4 py-4 sm:col-span-2 lg:col-span-1"
          htmlFor="retraining-toggle"
        >
          <div>
            <div className="mb-1.5 text-[11px] uppercase tracking-[0.06em] text-[var(--text-dim)]">
              Retraining
            </div>
            <div className="text-[13px] text-[var(--text-primary)]">
              {retraining ? "Assigned" : "Not assigned"}
            </div>
            {data.retraining_reason && (
              <div className="mt-1 text-[11px] text-[var(--text-muted)]">
                {data.retraining_reason}
              </div>
            )}
          </div>
          <button
            type="button"
            id="retraining-toggle"
            role="switch"
            aria-checked={retraining}
            disabled={pending}
            onClick={() => toggleRetraining(!retraining)}
            data-testid="retraining-toggle"
            className={
              "relative inline-flex h-6 w-10 shrink-0 cursor-pointer rounded-full border border-transparent transition-colors disabled:opacity-50 " +
              (retraining
                ? "bg-emerald-500/40"
                : "bg-[var(--bg-elev3)]")
            }
          >
            <span
              className={
                "inline-block size-5 translate-y-px rounded-full bg-white shadow transition-transform " +
                (retraining ? "translate-x-4" : "translate-x-px")
              }
            />
          </button>
        </label>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub: string;
  tone: string;
}) {
  return (
    <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-4">
      <div className="mb-1.5 text-[11px] uppercase tracking-[0.06em] text-[var(--text-dim)]">
        {label}
      </div>
      <div className="flex items-baseline gap-2">
        <div
          className="font-mono text-[26px] font-semibold tabular-nums"
          style={{ color: tone, letterSpacing: "-0.01em" }}
        >
          {value}
        </div>
      </div>
      <div className="mt-1 text-[12px] text-[var(--text-muted)]">{sub}</div>
    </div>
  );
}

function formatGBP(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}m`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}k`;
  return Math.round(v).toLocaleString();
}
