"use client";

/**
 * Inline AI-detected segments chip strip for the call detail header.
 * Renders one tiny color-coded pill per CallSegment so the reviewer
 * sees "Lead Gen · Verbal · LOA" at a glance without opening the
 * SegmentCards stack on the Checkpoints tab.
 *
 * Falls back to a silent null when there are no segments (e.g. mid-
 * pipeline or v1 fallback path).
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

type Segment = {
  id: string;
  idx: number;
  stage: string;
  start_s: number | null;
  end_s: number | null;
};

type Resp = { call_id: string; segments: Segment[] };

const STAGE_LABEL: Record<string, string> = {
  lead_gen: "Lead Gen",
  pre_sales: "Pre-Sales",
  verbal: "Verbal",
  loa: "LOA",
};

const STAGE_TONE: Record<string, { fg: string; bg: string; border: string }> = {
  lead_gen: { fg: "var(--emerald-400)", bg: "var(--emerald-bg)", border: "var(--emerald-border)" },
  pre_sales: { fg: "var(--blue)", bg: "var(--blue-bg)", border: "var(--blue-border)" },
  verbal: { fg: "var(--amber-400)", bg: "var(--amber-bg)", border: "var(--amber-border)" },
  loa: { fg: "var(--violet)", bg: "var(--violet-bg)", border: "var(--violet-border)" },
};

function fmtSecs(secs: number | null): string {
  if (secs == null || !Number.isFinite(secs)) return "—";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function SegmentChips({ callId }: { callId: string }) {
  const q = useQuery({
    queryKey: ["call", callId, "segments"] as const,
    queryFn: () => apiFetch<Resp>(`/api/calls/${encodeURIComponent(callId)}/segments`),
    enabled: !!callId,
    staleTime: 30_000,
  });
  const segments = q.data?.segments ?? [];
  if (segments.length === 0) return null;

  return (
    <div
      data-testid="segment-chips"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        flexWrap: "wrap",
      }}
    >
      {segments.map((s) => {
        const tone = STAGE_TONE[s.stage] ?? {
          fg: "var(--text-muted)",
          bg: "var(--bg-elev3)",
          border: "var(--border-subtle)",
        };
        const label = STAGE_LABEL[s.stage] ?? s.stage;
        return (
          <span
            key={s.id}
            title={`${label} segment · ${fmtSecs(s.start_s)} → ${fmtSecs(s.end_s)}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "2px 7px",
              fontSize: 10.5,
              fontWeight: 600,
              letterSpacing: "0.02em",
              borderRadius: 999,
              background: tone.bg,
              color: tone.fg,
              border: `1px solid ${tone.border}`,
              whiteSpace: "nowrap",
            }}
          >
            {label}
          </span>
        );
      })}
    </div>
  );
}
