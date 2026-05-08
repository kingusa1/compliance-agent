"use client";

import { useRouter } from "next/navigation";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import type { AgentLeaderboardRow } from "@/lib/queries/aggregator";

/**
 * AgentsTable — leaderboard rows for the /agents listing. Mirrors the
 * R3 hi-fi 9-row layout (design/extracted/screens/ops.jsx). Click a
 * row to drop into /agents/[name] for the 4-tab drilldown.
 *
 * The status pill is a derived signal: backend returns
 * `needs_escalation: true` when the recent non-compliant rate or
 * open directive count crosses ops thresholds.
 */

export type AgentsTableProps = {
  agents: AgentLeaderboardRow[];
};

export function AgentsTable({ agents }: AgentsTableProps) {
  const router = useRouter();

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)]">
      <Table>
        <TableHeader>
          <TableRow className="border-[var(--border-subtle)] hover:bg-transparent">
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Agent
            </TableHead>
            <TableHead className="text-right text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Total Calls
            </TableHead>
            <TableHead className="text-right text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Compliant
            </TableHead>
            <TableHead className="text-right text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Non-compliant
            </TableHead>
            <TableHead className="text-right text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Recent Flags
            </TableHead>
            <TableHead className="text-right text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Open Directives
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Last Call
            </TableHead>
            <TableHead className="text-[12px] uppercase tracking-wide text-[var(--text-muted)]">
              Status
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {agents.map((a) => (
            <TableRow
              key={a.agent_name}
              data-testid="agent-row"
              data-agent-name={a.agent_name}
              onClick={() =>
                router.push(`/agents/${encodeURIComponent(a.agent_name)}`)
              }
              className="cursor-pointer border-[var(--border-subtle)] hover:bg-[var(--bg-elev2)]"
            >
              <TableCell className="text-[13px] font-medium text-[var(--text-primary)]">
                {a.agent_name}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums text-[13px] text-[var(--text-primary)]">
                {a.total_calls}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums text-[13px] text-emerald-400">
                {a.compliant}
              </TableCell>
              <TableCell
                className={
                  "text-right font-mono tabular-nums text-[13px] " +
                  (a.non_compliant > 10
                    ? "text-red-400"
                    : "text-[var(--text-muted)]")
                }
              >
                {a.non_compliant}
              </TableCell>
              <TableCell
                className={
                  "text-right font-mono tabular-nums text-[13px] " +
                  (a.recent_non_compliant_30d > 3
                    ? "text-amber-400"
                    : "text-[var(--text-muted)]")
                }
              >
                {a.recent_non_compliant_30d || "—"}
              </TableCell>
              <TableCell
                className={
                  "text-right font-mono tabular-nums text-[13px] " +
                  (a.open_directives > 0
                    ? "text-amber-400"
                    : "text-[var(--text-dim)]")
                }
              >
                {a.open_directives || "—"}
              </TableCell>
              <TableCell className="text-[13px] text-[var(--text-muted)]">
                {formatRelative(a.last_call_at)}
              </TableCell>
              <TableCell>
                <StatusPill needsEscalation={a.needs_escalation} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function StatusPill({
  needsEscalation,
}: {
  needsEscalation: boolean;
}) {
  if (needsEscalation)
    return (
      <Badge className="border-red-500/30 bg-red-500/10 text-red-400">
        ● ESCALATE
      </Badge>
    );
  return (
    <Badge className="border-emerald-500/30 bg-emerald-500/10 text-emerald-400">
      ● OK
    </Badge>
  );
}

function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  try {
    const then = new Date(iso).getTime();
    const now = Date.now();
    const m = Math.round((now - then) / 60_000);
    if (m < 1) return "just now";
    if (m < 60) return `${m}m ago`;
    const h = Math.round(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.round(h / 24);
    if (d === 1) return "Yest.";
    if (d < 7) return `${d}d ago`;
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}
