"use client";

/**
 * Inline AI-detected segments chip strip for the call detail header.
 *
 * 2026-05-14 redesign: groups inner segments under the 2 top-level
 * deal stages (Opener / Closer). Each top-level stage gets a parent
 * pill, with the AI-detected inner segments rendered as small sub-pills
 * inside or alongside.
 *
 *   Opener   → Lead Gen
 *   Closer   → Pre-Sales · Verbal           (non-E.ON)
 *   Closer   → Pre-Sales · Verbal · LOA     (E.ON, LOA bundled)
 *
 * Falls back to a silent null when there are no segments (e.g. mid-
 * pipeline or v1 fallback path).
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import {
  SEGMENT_LABEL,
  SEGMENT_PARENT,
  TOPLEVEL_LABEL,
  type SegmentStage,
  type TopLevelStage,
} from "@/lib/workflow";

type Segment = {
  id: string;
  idx: number;
  stage: string;
  start_s: number | null;
  end_s: number | null;
};

type Resp = { call_id: string; segments: Segment[] };

const SEG_TONE: Record<SegmentStage, { fg: string; bg: string; border: string }> = {
  lead_gen: { fg: "var(--emerald-400)", bg: "var(--emerald-bg)", border: "var(--emerald-border)" },
  pre_sales: { fg: "var(--blue)", bg: "var(--blue-bg)", border: "var(--blue-border)" },
  verbal: { fg: "var(--amber-400)", bg: "var(--amber-bg)", border: "var(--amber-border)" },
  loa: { fg: "var(--violet)", bg: "var(--violet-bg)", border: "var(--violet-border)" },
};

const TOP_TONE: Record<TopLevelStage, { fg: string; border: string }> = {
  opener: { fg: "var(--emerald-400)", border: "var(--emerald-border)" },
  closer: { fg: "var(--amber-400)", border: "var(--amber-border)" },
};

function fmtSecs(secs: number | null): string {
  if (secs == null || !Number.isFinite(secs)) return "—";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function isSegmentStage(s: string): s is SegmentStage {
  return s === "lead_gen" || s === "pre_sales" || s === "verbal" || s === "loa";
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

  // Group inner segments by their top-level parent stage.
  const groups: Record<TopLevelStage, Segment[]> = { opener: [], closer: [] };
  for (const s of segments) {
    if (!isSegmentStage(s.stage)) continue;
    groups[SEGMENT_PARENT[s.stage]].push(s);
  }

  return (
    <div
      data-testid="segment-chips"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        flexWrap: "wrap",
      }}
    >
      {(Object.keys(groups) as TopLevelStage[]).map((top) => {
        const inner = groups[top];
        if (inner.length === 0) return null;
        const topTone = TOP_TONE[top];
        return (
          <span
            key={top}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "2px 4px 2px 8px",
              fontSize: 10.5,
              fontWeight: 600,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              borderRadius: 999,
              border: `1px solid ${topTone.border}`,
              color: topTone.fg,
              background: "var(--bg-elev1)",
            }}
            title={`${TOPLEVEL_LABEL[top]} stage — ${inner
              .map((s) => SEGMENT_LABEL[s.stage as SegmentStage] ?? s.stage)
              .join(" · ")}`}
          >
            <span>{TOPLEVEL_LABEL[top]}</span>
            <span style={{ display: "inline-flex", gap: 3 }}>
              {inner.map((s) => {
                const tone = SEG_TONE[s.stage as SegmentStage];
                const label = SEGMENT_LABEL[s.stage as SegmentStage] ?? s.stage;
                return (
                  <span
                    key={s.id}
                    title={`${label} segment · ${fmtSecs(s.start_s)} → ${fmtSecs(s.end_s)}`}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      padding: "1px 6px",
                      fontSize: 10,
                      fontWeight: 600,
                      letterSpacing: "0.02em",
                      textTransform: "none",
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
            </span>
          </span>
        );
      })}
    </div>
  );
}
