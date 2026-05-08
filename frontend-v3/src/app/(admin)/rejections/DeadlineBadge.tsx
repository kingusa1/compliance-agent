"use client";

/**
 * DeadlineBadge — ticking countdown to the rejection's auto-deadline
 * (rejected_at + 2 days, computed in PG via GENERATED column).
 *
 *   > 48h        — gray "in 3 days" / "in 2d 4h"
 *   24-48h       — yellow "in 1d 2h"
 *   < 24h ahead  — amber "12h left"
 *   < 1h ahead   — amber "47m left"
 *   overdue      — red "overdue 2h" (with alert-triangle icon)
 *   terminal     — gray "—" (FIXED_AND_APPROVED / DEAD)
 *
 * Port of DeadlineCell from rejections-hifi.jsx, generalized to handle the
 * full window (the hi-fi prototype only modelled past/<=24h/future).
 */
import { AlertTriangle } from "lucide-react";

import type { RejectionStatus } from "@/lib/schemas/rejections";

export type DeadlineBadgeProps = {
  /** ISO timestamp from server. */
  deadline: string | null | undefined;
  /** When set to FIXED_AND_APPROVED or DEAD, render an em-dash (no SLA). */
  status?: RejectionStatus | string | null;
  /** Override for tests — defaults to `Date.now()`. */
  now?: Date;
};

const TERMINAL: ReadonlySet<string> = new Set([
  "FIXED_AND_APPROVED",
  "DEAD",
]);

export function formatDeadlineLabel(
  deadline: Date,
  now: Date = new Date(),
): { label: string; tone: "gray" | "yellow" | "amber" | "red"; overdue: boolean } {
  const diffMs = deadline.getTime() - now.getTime();
  const overdue = diffMs < 0;
  const absMs = Math.abs(diffMs);
  const totalHours = Math.floor(absMs / (60 * 60 * 1000));
  const totalMinutes = Math.floor(absMs / (60 * 1000));

  if (overdue) {
    if (totalHours < 1) return { label: `overdue ${totalMinutes}m`, tone: "red", overdue: true };
    if (totalHours < 24) return { label: `overdue ${totalHours}h`, tone: "red", overdue: true };
    const days = Math.floor(totalHours / 24);
    const rem = totalHours % 24;
    return {
      label: rem ? `overdue ${days}d ${rem}h` : `overdue ${days}d`,
      tone: "red",
      overdue: true,
    };
  }

  if (totalHours < 1) {
    return { label: `${totalMinutes}m left`, tone: "amber", overdue: false };
  }
  if (totalHours < 24) {
    return { label: `${totalHours}h left`, tone: "amber", overdue: false };
  }
  if (totalHours < 48) {
    const days = Math.floor(totalHours / 24);
    const rem = totalHours % 24;
    return {
      label: rem ? `in ${days}d ${rem}h` : `in ${days}d`,
      tone: "yellow",
      overdue: false,
    };
  }
  const days = Math.floor(totalHours / 24);
  const rem = totalHours % 24;
  return {
    label: rem && days < 7 ? `in ${days}d ${rem}h` : `in ${days}d`,
    tone: "gray",
    overdue: false,
  };
}

const TONE_COLORS: Record<"gray" | "yellow" | "amber" | "red", string> = {
  gray: "var(--text-muted)",
  yellow: "#facc15",
  amber: "var(--amber)",
  red: "var(--red)",
};

export function DeadlineBadge({ deadline, status, now }: DeadlineBadgeProps) {
  if (status && TERMINAL.has(String(status))) {
    return (
      <span
        data-slot="deadline-badge"
        data-tone="terminal"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11.5,
          color: "var(--text-dim)",
          fontWeight: 600,
        }}
      >
        —
      </span>
    );
  }

  if (!deadline) {
    return (
      <span
        data-slot="deadline-badge"
        data-tone="missing"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11.5,
          color: "var(--text-dim)",
          fontWeight: 600,
        }}
      >
        no deadline
      </span>
    );
  }

  const dt = new Date(deadline);
  const { label, tone, overdue } = formatDeadlineLabel(dt, now);
  const color = TONE_COLORS[tone];
  return (
    <span
      data-slot="deadline-badge"
      data-tone={tone}
      data-overdue={overdue ? "1" : "0"}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        fontFamily: "var(--font-mono)",
        fontSize: 11.5,
        color,
        fontWeight: 600,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {overdue && <AlertTriangle size={11} strokeWidth={1.75} />}
      {label}
    </span>
  );
}
