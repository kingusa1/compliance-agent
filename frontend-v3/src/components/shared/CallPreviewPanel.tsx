"use client";

/**
 * Shared right-rail (40%) preview for a selected call row.
 *
 * Used by /queue, /compliant, /non-compliant. Mirrors the layout of the
 * original reviewer QueueDetailPanel so the master-detail view feels
 * consistent across roles. Pages decide what the primary action is:
 *
 *   - /queue                 → "Claim & review" (POST /api/calls/{id}/claim)
 *   - /compliant + /non-compliant → "Open in queue" (router.push)
 *
 * That action is wired by the parent via the `primaryAction` prop. Empty
 * state, loading state and error state are owned by this component so each
 * page doesn't reinvent the wheel.
 */

import Link from "next/link";
import { Inbox, Play } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useCallDetailQuery } from "@/lib/queries/reviewer";
import { ScoreBar } from "@/components/reviewer/ScoreBar";
import { shortFilename } from "@/lib/filename";

export type CallPreviewPanelProps = {
  callId: string | null;
  /**
   * Optional footer slot. If omitted the panel renders an "Open in queue"
   * link to /calls/{id}, which is the right default for the
   * compliant/non-compliant pages where the call is already triaged.
   */
  primaryAction?: React.ReactNode;
};

export function CallPreviewPanel({ callId, primaryAction }: CallPreviewPanelProps) {
  const detail = useCallDetailQuery(callId ?? "");

  if (!callId) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <Inbox className="h-7 w-7 text-[var(--text-dim)]" />
        <div className="text-[13px] text-[var(--text-muted)]">
          Select a call to preview
        </div>
      </div>
    );
  }

  if (detail.isLoading) {
    return (
      <div className="flex flex-col gap-3 p-6">
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="mt-4 h-16 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="mt-auto h-10 w-full" />
      </div>
    );
  }

  if (detail.isError || !detail.data) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-[13px] text-[var(--red-fail)]">
        Couldn’t load call details
      </div>
    );
  }

  const c = detail.data;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-[var(--border-subtle)] px-5 py-4">
        <div className="mb-2 flex items-center gap-2">
          <Badge
            variant="outline"
            className="border-[var(--border-strong)] font-mono text-[11px] text-[var(--text-muted)] max-w-[260px] truncate"
            title={c.filename ?? undefined}
          >
            {shortFilename(c.filename)}
          </Badge>
          <StatusPill status={c.review_status ?? c.status} />
        </div>
        <div className="text-[18px] font-semibold text-[var(--text-primary)]">
          {c.customer_name ?? c.filename}
        </div>
        <div className="mt-1 text-[12px] text-[var(--text-muted)]">
          {[c.detected_supplier, c.agent_name && `agent ${c.agent_name}`]
            .filter(Boolean)
            .join(" · ") || "—"}
        </div>
      </div>

      <div className="border-b border-[var(--border-subtle)] px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-7 w-7 items-center justify-center rounded-full border border-[var(--border-subtle)] bg-[var(--bg-elev2)]">
            <Play className="h-3.5 w-3.5" />
          </div>
          <div className="flex-1 font-mono text-[11px] text-[var(--text-muted)]">
            {formatDuration(c.duration_seconds)}
          </div>
          <ScoreBar score={c.score} width={64} />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        <div className="mb-2 text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
          Transcript snippet
        </div>
        <div className="font-mono text-[12px] leading-[1.6] text-[var(--text-primary)]">
          {c.transcript
            ? c.transcript.slice(0, 320).trim() +
              (c.transcript.length > 320 ? "…" : "")
            : "No transcript yet."}
        </div>
        {c.reason ? (
          <div className="mt-4 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3">
            <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
              AI reason
            </div>
            <div className="text-[12px] text-[var(--text-primary)]">{c.reason}</div>
          </div>
        ) : null}
      </div>

      <div className="flex gap-2 border-t border-[var(--border-subtle)] p-4">
        {primaryAction ?? (
          <Link
            href={`/calls/${encodeURIComponent(callId)}`}
            className="flex h-9 flex-1 items-center justify-center rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev2)] text-[13px] font-medium text-[var(--text-primary)] hover:bg-[var(--bg-elev3)]"
          >
            Open call detail
          </Link>
        )}
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string | null | undefined }) {
  const s = (status || "").toLowerCase();
  if (s === "reviewed" || s === "completed")
    return (
      <Badge className="border-emerald-500/30 bg-emerald-500/10 text-[var(--emerald-pass)]">
        ● Reviewed
      </Badge>
    );
  // unclaimed / in_review legacy values collapse to "Pending".
  return (
    <Badge variant="outline" className="border-[var(--border-strong)]">
      ● Pending
    </Badge>
  );
}

function formatDuration(secs: number | null): string {
  if (secs == null || !Number.isFinite(secs)) return "—";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
