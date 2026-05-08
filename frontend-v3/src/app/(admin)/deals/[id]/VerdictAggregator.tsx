"use client";

import { ScoreGauge } from "@/components/shared/ScoreGauge";
import { Badge } from "@/components/ui/badge";
import { LifecyclePill } from "../DealsTable";
import type { DealVerdict } from "@/lib/queries/aggregator";

/**
 * VerdictAggregator (UX-D17) — centered radial gauge showing the
 * deal-level composite percent + worst-action chip + lifecycle pill.
 * Mirrors the customer-deal aggregator screen in design/extracted/
 * screens/lifecycle.jsx but uses our pure-SVG ScoreGauge (Tremor was
 * dropped at R1 due to a React 19 peer-dep cap).
 */

export type VerdictAggregatorProps = {
  verdict: DealVerdict;
  /** Total expected calls (used to render the "X of N" caption). */
  callsExpected?: number;
};

export function VerdictAggregator({
  verdict,
  callsExpected,
}: VerdictAggregatorProps) {
  const composite = verdict.composite_score;
  const completed = verdict.call_breakdown.filter((c) => !!c.completed_at).length;
  const total = callsExpected ?? completed + verdict.missing_calls.length;
  const pending = composite == null;

  return (
    <section
      className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-8"
      data-testid="verdict-aggregator"
      data-worst-action={verdict.worst_action}
      data-lifecycle={verdict.lifecycle_status}
    >
      <div className="flex flex-col items-center gap-6 md:flex-row md:gap-10">
        <div className="shrink-0">
          {pending ? (
            <PendingGauge />
          ) : (
            <ScoreGauge
              value={composite ?? 0}
              size={250}
              caption="Composite"
            />
          )}
        </div>

        <div className="flex flex-1 flex-col gap-5">
          <Stat label="Worst action">
            <WorstActionPill action={verdict.worst_action} />
          </Stat>

          <Stat label="Calls scored">
            <div className="text-[22px] font-semibold tabular-nums text-[var(--text-primary)]">
              {completed}{" "}
              <span className="text-[16px] font-medium text-[var(--text-muted)]">
                / {total || "—"}
              </span>
            </div>
          </Stat>

          <Stat label="Lifecycle">
            <LifecyclePill status={verdict.lifecycle_status} />
          </Stat>

          <Stat label="Threshold">
            {composite == null ? (
              <span className="text-[13px] text-[var(--amber-review)]">
                ≥ 80% · pending all calls
              </span>
            ) : composite >= 80 ? (
              <span className="text-[13px]">
                ≥ 80% ·{" "}
                <span className="text-[var(--emerald-pass)]">met</span>
              </span>
            ) : (
              <span className="text-[13px]">
                ≥ 80% ·{" "}
                <span className="text-[var(--red-fail)]">below threshold</span>
              </span>
            )}
          </Stat>
        </div>
      </div>
    </section>
  );
}

function Stat({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1.5 text-[11px] uppercase tracking-[0.06em] text-[var(--text-dim)]">
        {label}
      </div>
      {children}
    </div>
  );
}

function PendingGauge() {
  return (
    <div
      className="flex flex-col items-center justify-center rounded-full border border-dashed border-[var(--border-strong)]"
      style={{ width: 250, height: 250 }}
      role="img"
      aria-label="Composite score pending"
      data-testid="score-gauge-pending"
    >
      <div className="text-[40px] font-semibold text-[var(--text-muted)]">—</div>
      <div className="mt-2 text-[11px] uppercase tracking-[0.08em] text-[var(--text-dim)]">
        Pending
      </div>
    </div>
  );
}

export function WorstActionPill({ action }: { action: string }) {
  const a = (action || "").toUpperCase();
  if (a === "PASS")
    return (
      <Badge className="border-emerald-500/30 bg-emerald-500/10 text-emerald-400">
        ● PASS
      </Badge>
    );
  if (a === "REVIEW")
    return (
      <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-400">
        ● REVIEW
      </Badge>
    );
  if (a === "COACHING")
    return (
      <Badge className="border-blue-500/30 bg-blue-500/10 text-blue-400">
        ● COACHING
      </Badge>
    );
  if (a === "FAIL")
    return (
      <Badge className="border-red-500/30 bg-red-500/10 text-red-400">
        ● FAIL
      </Badge>
    );
  if (a === "BLOCK")
    return (
      <Badge className="border-violet-500/30 bg-violet-500/10 text-violet-400">
        ● BLOCK
      </Badge>
    );
  return <Badge variant="outline">● {a || "—"}</Badge>;
}
