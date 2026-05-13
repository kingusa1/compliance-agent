"use client";

/**
 * SegmentCards — per-segment verdict view (Plan §5b).
 *
 * The 2026-05-12 pipeline rewrite emits 1-4 CallSegment rows per recording
 * (lead_gen / pre_sales / verbal / loa), each graded against its own rubric.
 * This card stack surfaces the per-segment breakdown so the reviewer can
 * see "verbal scored 22/26 ⚠, LOA scored 11/11 ✓" rather than just the
 * aggregate.
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { Pill } from "@/components/design/Pill";

type SegmentCheckpoint = {
  name?: string;
  status?: string;
  evidence?: string | null;
  notes?: string | null;
  severity?: string;
};

type Segment = {
  id: string;
  idx: number;
  stage: string;
  confidence: number | null;
  start_s: number | null;
  end_s: number | null;
  start_word_idx: number | null;
  end_word_idx: number | null;
  transcript_excerpt: string | null;
  classifier_reasoning: string | null;
  score: string | null;
  bucket: string | null;
  compliant: boolean | null;
  reason: string | null;
  critical_breaches: number;
  high_breaches: number;
  medium_breaches: number;
  checkpoints: SegmentCheckpoint[];
};

type Resp = {
  call_id: string;
  segments: Segment[];
};

const STAGE_LABEL: Record<string, string> = {
  lead_gen: "Lead Gen",
  pre_sales: "Pre-Sales",
  verbal: "Verbal",
  loa: "LOA",
};

const STAGE_TONE: Record<string, string> = {
  lead_gen: "var(--emerald)",
  pre_sales: "var(--blue)",
  verbal: "var(--amber)",
  loa: "var(--violet)",
};

function fmtSecs(secs: number | null): string {
  if (secs == null || !Number.isFinite(secs)) return "—";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function bucketPill(bucket: string | null | undefined): React.ReactNode {
  const b = (bucket || "").toLowerCase();
  if (b === "pass")
    return (
      <Pill tone="emerald" dot>
        Pass
      </Pill>
    );
  if (b === "coaching")
    return (
      <Pill tone="amber" dot>
        Coaching
      </Pill>
    );
  if (b === "review")
    return (
      <Pill tone="amber" dot>
        Needs Review
      </Pill>
    );
  if (b === "blocked")
    return (
      <Pill tone="red" dot>
        Non-Compliant
      </Pill>
    );
  return (
    <Pill tone="neutral" dot>
      Pending
    </Pill>
  );
}

export function SegmentCards({ callId }: { callId: string }) {
  const q = useQuery({
    queryKey: ["call", callId, "segments"] as const,
    queryFn: () => apiFetch<Resp>(`/api/calls/${encodeURIComponent(callId)}/segments`),
    enabled: !!callId,
    refetchInterval: (query) => {
      // Same poll cadence as call detail — segments arrive as the pipeline
      // finishes. Stop once we have ≥1 segment with a bucket set.
      const data = query.state.data as Resp | undefined;
      if (data && data.segments.some((s) => s.bucket)) return false;
      return 3000;
    },
  });

  if (q.isLoading) {
    return (
      <div style={{ color: "var(--text-faint)", fontSize: 12, padding: 12 }}>
        Loading segments…
      </div>
    );
  }

  const segments = q.data?.segments ?? [];
  if (!segments.length) {
    return (
      <div
        style={{
          color: "var(--text-faint)",
          fontSize: 12,
          padding: 12,
          fontStyle: "italic",
        }}
      >
        No segments classified yet — the content classifier hasn't run, or
        this call halted at <code>needs_classification</code>.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {segments.map((s) => {
        const label = STAGE_LABEL[s.stage] ?? s.stage;
        const tone = STAGE_TONE[s.stage] ?? "var(--text-muted)";
        return (
          <div
            key={s.id}
            data-testid={`segment-card-${s.idx}`}
            style={{
              background: "var(--bg-elev2)",
              border: "1px solid var(--border-subtle)",
              borderLeft: `3px solid ${tone}`,
              borderRadius: 8,
              padding: 14,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                marginBottom: 8,
              }}
            >
              <div
                style={{
                  fontWeight: 600,
                  letterSpacing: "-0.005em",
                  color: "var(--text-primary)",
                  fontSize: 14,
                }}
              >
                {label}
              </div>
              <span
                style={{
                  fontSize: 11,
                  color: "var(--text-faint)",
                  fontFamily: "var(--font-mono)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                Segment {s.idx + 1}
              </span>
              <span
                style={{
                  fontSize: 11,
                  color: "var(--text-faint)",
                  fontFamily: "var(--font-mono)",
                  fontVariantNumeric: "tabular-nums",
                }}
                title={`Segment ranges from ${fmtSecs(s.start_s)} to ${fmtSecs(s.end_s)}`}
              >
                · {fmtSecs(s.start_s)} → {fmtSecs(s.end_s)}
              </span>
              {s.confidence != null && (
                <span
                  style={{
                    fontSize: 11,
                    color: "var(--text-faint)",
                    fontFamily: "var(--font-mono)",
                  }}
                  title={`Classifier confidence ${Math.round((s.confidence ?? 0) * 100)}%`}
                >
                  · conf {Math.round((s.confidence ?? 0) * 100)}%
                </span>
              )}
              <div style={{ flex: 1 }} />
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  color: "var(--text-primary)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {s.score ?? "—"}
              </span>
              {bucketPill(s.bucket)}
            </div>
            {/* Why did the AI call this segment <stage>? — surfaces the
                classifier_reasoning so the reviewer can audit at a glance. */}
            {s.classifier_reasoning && (
              <div
                style={{
                  fontSize: 12,
                  color: "var(--text-muted)",
                  marginBottom: 6,
                  fontStyle: "italic",
                  borderLeft: "2px solid var(--border-subtle)",
                  paddingLeft: 8,
                }}
                title="Why the AI classified this segment as the chosen stage"
              >
                <span style={{ color: "var(--text-faint)", fontStyle: "normal" }}>
                  AI&apos;s reasoning · why {label}:{" "}
                </span>
                {s.classifier_reasoning}
              </div>
            )}
            {s.reason && (
              <div
                style={{
                  fontSize: 12,
                  color: "var(--text-muted)",
                  marginBottom: 6,
                }}
              >
                {s.reason}
              </div>
            )}
            {(s.critical_breaches > 0 || s.high_breaches > 0 || s.medium_breaches > 0) && (
              <div
                style={{
                  display: "flex",
                  gap: 8,
                  fontSize: 11,
                  color: "var(--text-faint)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {s.critical_breaches > 0 && (
                  <span style={{ color: "var(--red)" }}>
                    {s.critical_breaches} Critical
                  </span>
                )}
                {s.high_breaches > 0 && (
                  <span style={{ color: "var(--amber)" }}>
                    {s.high_breaches} High
                  </span>
                )}
                {s.medium_breaches > 0 && (
                  <span>{s.medium_breaches} Medium</span>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
