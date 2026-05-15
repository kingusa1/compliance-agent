"use client";

/**
 * SegmentCards — per-segment verdict stack with nested checkpoints.
 *
 * 2026-05-14 redesign per Aly's feedback:
 *   - Each card is an EXPANDABLE container; click anywhere on the
 *     header to toggle. First card with non-zero score auto-expands.
 *   - Header strip is clean + enterprise-grade: stage band on the
 *     left, score + bucket pill on the right, breach counts as
 *     proper pills underneath.
 *   - Source-of-truth badge ('what rubric grades this?') is always
 *     visible — emerald 88-rule pack, blue pre-sales 88, amber verbal
 *     script, violet LOA script, neutral V1 fallback.
 *   - Expanded body shows: classifier reasoning + every CheckpointCard
 *     that belongs to this segment (passed/partial/non-compliant grouped
 *     by the page-level filter pill).
 *   - The flat CheckpointCards list at the bottom of /calls/[id] is gone
 *     — every checkpoint is now NESTED under its segment.
 */
import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

import { CheckpointCard, type CheckpointVerdict } from "./CheckpointCard";
import { RubricBadge, type RubricKind } from "./RubricBadge";
import type { ScriptCheckpoint } from "@/lib/queries/reviewer";
import type { WordToken } from "@/lib/queries/reviewer";

type SegmentCheckpoint = {
  name?: string;
  status?: string;
  evidence?: string | null;
  notes?: string | null;
  severity?: string;
  rubric_kind?: RubricKind | null;
  rubric_label?: string | null;
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
  rubric_kind: RubricKind | null;
  rubric_label: string | null;
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

const STAGE_TONE: Record<string, { band: string; fg: string; soft: string }> = {
  lead_gen: { band: "var(--emerald)", fg: "var(--emerald-400)", soft: "rgba(16,185,129,0.10)" },
  pre_sales: { band: "var(--blue)", fg: "var(--blue)", soft: "rgba(59,130,246,0.10)" },
  verbal: { band: "var(--amber)", fg: "var(--amber-400)", soft: "rgba(245,158,11,0.10)" },
  loa: { band: "var(--violet)", fg: "var(--violet)", soft: "rgba(167,139,250,0.10)" },
};

const BUCKET_VISUAL: Record<
  string,
  { label: string; bg: string; fg: string; border: string; dot: string }
> = {
  pass: {
    label: "Pass",
    bg: "var(--emerald-bg)",
    fg: "var(--emerald-400)",
    border: "var(--emerald-border)",
    dot: "var(--emerald)",
  },
  coaching: {
    label: "Coaching",
    bg: "var(--amber-bg)",
    fg: "var(--amber-400)",
    border: "var(--amber-border)",
    dot: "var(--amber)",
  },
  review: {
    label: "Needs Review",
    bg: "var(--amber-bg)",
    fg: "var(--amber-400)",
    border: "var(--amber-border)",
    dot: "var(--amber)",
  },
  blocked: {
    label: "Non-Compliant",
    bg: "var(--red-bg)",
    fg: "var(--red)",
    border: "var(--red-border)",
    dot: "var(--red)",
  },
  pending: {
    label: "Pending",
    bg: "var(--bg-elev3)",
    fg: "var(--text-muted)",
    border: "var(--border-subtle)",
    dot: "var(--text-dim)",
  },
};

function fmtSecs(secs: number | null): string {
  if (secs == null || !Number.isFinite(secs)) return "—";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function ConfidenceDial({ value }: { value: number | null }) {
  // 2026-05-15: confidence is the classifier's belief in the segment's
  // STAGE tag, NOT the pass rate. Was rendered next to the score string
  // which made "82% · 0/11 · Coaching" look like a math contradiction
  // (you'd read 82% as the pass rate). Dropped the bare percent number;
  // dots-only + hover-title preserves the signal without confusing users.
  if (value == null) return null;
  const pct = Math.max(0, Math.min(1, value));
  const stops = 5;
  const filled = Math.round(pct * stops);
  return (
    <span
      title={`Classifier confidence ${Math.round(pct * 100)}%`}
      aria-label={`Classifier confidence ${Math.round(pct * 100)} percent`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 2,
        fontSize: 10,
        color: "var(--text-faint)",
      }}
    >
      {Array.from({ length: stops }).map((_, i) => (
        <span
          key={i}
          style={{
            width: 6,
            height: 6,
            borderRadius: 1.5,
            background:
              i < filled
                ? pct >= 0.85
                  ? "var(--emerald)"
                  : pct >= 0.6
                    ? "var(--amber)"
                    : "var(--red)"
                : "var(--bg-elev3)",
          }}
        />
      ))}
    </span>
  );
}

// Compute pass-rate% from the "passed/total" score string. Returns null
// when the score is missing or malformed. Used to show a percentage that
// MATCHES the score (so reviewers don't see "82% · 0/11" again).
function passRatePct(score: string | null | undefined): number | null {
  if (!score) return null;
  const m = /^(\d+)\s*\/\s*(\d+)$/.exec(score.trim());
  if (!m) return null;
  const passed = Number(m[1]);
  const total = Number(m[2]);
  if (!Number.isFinite(passed) || !Number.isFinite(total) || total <= 0) return null;
  return Math.round((passed / total) * 100);
}

function BreachPill({
  count,
  label,
  tone,
}: {
  count: number;
  label: string;
  tone: "red" | "amber" | "neutral";
}) {
  if (count <= 0) return null;
  const styles: Record<string, { fg: string; bg: string; border: string }> = {
    red: { fg: "var(--red)", bg: "var(--red-bg)", border: "var(--red-border)" },
    amber: { fg: "var(--amber-400)", bg: "var(--amber-bg)", border: "var(--amber-border)" },
    neutral: { fg: "var(--text-muted)", bg: "var(--bg-elev3)", border: "var(--border-subtle)" },
  };
  const s = styles[tone];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 10.5,
        fontWeight: 600,
        padding: "2px 8px",
        borderRadius: 999,
        color: s.fg,
        background: s.bg,
        border: `1px solid ${s.border}`,
        whiteSpace: "nowrap",
      }}
    >
      <span style={{ fontFamily: "var(--font-mono)", fontVariantNumeric: "tabular-nums" }}>
        {count}
      </span>
      <span>{label}</span>
    </span>
  );
}

function BucketPill({ bucket }: { bucket: string | null | undefined }) {
  const b = (bucket || "pending").toLowerCase();
  const v = BUCKET_VISUAL[b] ?? BUCKET_VISUAL.pending;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 11,
        fontWeight: 600,
        padding: "4px 10px",
        borderRadius: 999,
        background: v.bg,
        color: v.fg,
        border: `1px solid ${v.border}`,
        whiteSpace: "nowrap",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: v.dot,
        }}
      />
      {v.label}
    </span>
  );
}

export type SegmentCardsProps = {
  callId: string;
  /** Optional: when present, render the matched CheckpointCard rows from
   *  the parent page INSIDE each segment's expanded body. The parent owns
   *  the words, mutations and seek handlers; this component just routes
   *  the right subset of cpCards to each segment by checkpoint name. */
  cpCards?: Array<{
    key: string;
    script?: ScriptCheckpoint;
    verdict?: CheckpointVerdict;
    startSec: number | null;
    startSecEnd: number | null;
  }>;
  /** Status filter from the page-level filter pills (All / Passed / Partial
   *  / Non-Compliant). Falls back to "all". */
  cpFilter?: "all" | "passed" | "partial" | "fail";
  /** Optional shared props for the inner CheckpointCard rows. */
  innerProps?: {
    callDurationSec: number;
    words: WordToken[];
    seekAndPlay: (sec: number) => void;
    onReviewVerdict?: (origIndex: number, v: "pass" | "fail", notes: string) => Promise<void>;
    onRetry?: (origIndex: number) => Promise<void>;
    activeCheckpointKey?: string | null;
    totalSections: number;
  };
};

export function SegmentCards({ callId, cpCards = [], cpFilter = "all", innerProps }: SegmentCardsProps) {
  const q = useQuery({
    queryKey: ["call", callId, "segments"] as const,
    queryFn: () => apiFetch<Resp>(`/api/calls/${encodeURIComponent(callId)}/segments`),
    enabled: !!callId,
    refetchInterval: (query) => {
      const data = query.state.data as Resp | undefined;
      if (data && data.segments.some((s) => s.bucket)) return false;
      return 3000;
    },
  });

  const segments = q.data?.segments ?? [];

  // Open state per segment idx. First segment with score auto-opens.
  const [openIdx, setOpenIdx] = useState<Set<number>>(new Set());
  useEffect(() => {
    if (segments.length === 0 || openIdx.size > 0) return;
    const first = segments.find((s) => s.score) ?? segments[0];
    if (first) setOpenIdx(new Set([first.idx]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segments.length]);

  const toggle = (idx: number) =>
    setOpenIdx((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });

  // Bucket cpCards by which segment they belong to. Uses the segment's
  // `checkpoints[]` server-side list (which is the JSON the analyzer wrote
  // per segment). Name-match is case-insensitive + trim-normalised.
  const cpsBySegmentIdx = useMemo(() => {
    const norm = (s: string) => (s || "").trim().toLowerCase();
    const result: Record<number, typeof cpCards> = {};
    if (cpCards.length === 0 || segments.length === 0) return result;
    // Build name → segment idx lookup.
    const nameToSegIdx = new Map<string, number>();
    for (const s of segments) {
      for (const cp of s.checkpoints ?? []) {
        const k = norm(cp.name ?? "");
        if (k && !nameToSegIdx.has(k)) nameToSegIdx.set(k, s.idx);
      }
    }
    for (const cpc of cpCards) {
      const k = norm(cpc.script?.name || cpc.verdict?.name || "");
      const segIdx = nameToSegIdx.get(k);
      if (segIdx == null) continue;
      (result[segIdx] ??= []).push(cpc);
    }
    return result;
  }, [cpCards, segments]);

  // Apply the page-level filter to each segment's cpCards subset.
  const applyFilter = (rows: typeof cpCards) => {
    if (cpFilter === "all") return rows;
    return rows.filter((r) => {
      const s = (r.verdict?.status ?? "").toLowerCase();
      if (cpFilter === "passed") return s === "pass";
      if (cpFilter === "partial") return s === "partial";
      if (cpFilter === "fail") return s === "fail";
      return true;
    });
  };

  if (q.isLoading) {
    return (
      <div
        style={{
          color: "var(--text-faint)",
          fontSize: 12,
          padding: 16,
          textAlign: "center",
        }}
      >
        Loading segments…
      </div>
    );
  }

  if (segments.length === 0) {
    return (
      <div
        style={{
          color: "var(--text-faint)",
          fontSize: 12,
          padding: 16,
          fontStyle: "italic",
          textAlign: "center",
        }}
      >
        No segments classified yet — the content classifier hasn&apos;t run, or
        this call halted at <code>needs_classification</code>.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {segments.map((s) => {
        const label = STAGE_LABEL[s.stage] ?? s.stage;
        const tone = STAGE_TONE[s.stage] ?? STAGE_TONE.lead_gen;
        const open = openIdx.has(s.idx);
        const subset = applyFilter(cpsBySegmentIdx[s.idx] ?? []);
        const allForSegment = (cpsBySegmentIdx[s.idx] ?? []).length;
        return (
          <article
            key={s.id}
            data-testid={`segment-card-${s.idx}`}
            data-stage={s.stage}
            data-open={open ? "1" : "0"}
            style={{
              background: "var(--bg-elev1)",
              border: "1px solid var(--border-subtle)",
              borderLeft: `3px solid ${tone.band}`,
              borderRadius: 12,
              overflow: "hidden",
              boxShadow: open
                ? "0 8px 24px -12px rgba(0,0,0,0.4)"
                : "0 1px 0 rgba(255,255,255,0.02) inset",
              transition: "box-shadow 140ms ease",
            }}
          >
            {/* ─── HEADER — clickable to toggle ─────────────────────── */}
            <button
              type="button"
              data-testid={`segment-card-toggle-${s.idx}`}
              onClick={() => toggle(s.idx)}
              aria-expanded={open}
              style={{
                all: "unset",
                cursor: "pointer",
                display: "block",
                width: "100%",
                padding: "14px 16px",
                boxSizing: "border-box",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  flexWrap: "wrap",
                }}
              >
                <span style={{ color: tone.fg, flexShrink: 0 }}>
                  {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </span>
                <div
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "4px 10px",
                    borderRadius: 8,
                    background: tone.soft,
                    color: tone.fg,
                    fontWeight: 700,
                    fontSize: 12,
                    letterSpacing: "0.06em",
                    textTransform: "uppercase",
                  }}
                >
                  {label}
                </div>
                <span
                  style={{
                    fontSize: 11,
                    color: "var(--text-muted)",
                    fontFamily: "var(--font-mono)",
                    fontVariantNumeric: "tabular-nums",
                  }}
                  title={`Segment ${s.idx + 1} · audio range`}
                >
                  Segment {s.idx + 1} · {fmtSecs(s.start_s)} → {fmtSecs(s.end_s)}
                </span>
                <ConfidenceDial value={s.confidence} />
                <div style={{ flex: 1 }} />
                {/* Pass-rate% derived from the score string so the
                    percentage matches what the reviewer expects.
                    Previously a bare classifier-confidence % shown here
                    made "82% · 0/11 · Coaching" look broken. */}
                {(() => {
                  const pct = passRatePct(s.score);
                  if (pct == null) return null;
                  return (
                    <span
                      title={`Pass rate ${pct}% (${s.score})`}
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        fontWeight: 500,
                        color:
                          pct >= 90
                            ? "var(--emerald-400, #34d399)"
                            : pct >= 60
                              ? "var(--amber-400, #fbbf24)"
                              : "var(--red-400, #f87171)",
                        fontVariantNumeric: "tabular-nums",
                        padding: "1px 6px",
                        borderRadius: 3,
                        background: "var(--bg-elev2)",
                      }}
                    >
                      {pct}%
                    </span>
                  );
                })()}
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 14,
                    fontWeight: 600,
                    color: "var(--text-primary)",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {s.score ?? "—"}
                </span>
                <BucketPill bucket={s.bucket} />
              </div>

              {/* SECOND LINE — rubric source + breach pills */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  flexWrap: "wrap",
                  marginTop: 10,
                  paddingLeft: 28,
                }}
              >
                <RubricBadge kind={s.rubric_kind} label={s.rubric_label} />
                <span style={{ flex: 1 }} />
                <BreachPill count={s.critical_breaches} label="Critical" tone="red" />
                <BreachPill count={s.high_breaches} label="High" tone="amber" />
                <BreachPill count={s.medium_breaches} label="Medium" tone="neutral" />
              </div>
            </button>

            {/* ─── EXPANDED BODY ─────────────────────────────────────── */}
            {open ? (
              <div
                style={{
                  borderTop: "1px solid var(--border-subtle)",
                  padding: "14px 16px 16px",
                  background: "var(--bg-elev2)",
                }}
              >
                {/* AI reasoning */}
                {s.classifier_reasoning ? (
                  <div
                    style={{
                      fontSize: 12,
                      lineHeight: 1.55,
                      color: "var(--text-muted)",
                      marginBottom: 14,
                      padding: "10px 12px",
                      borderRadius: 8,
                      background: "var(--bg-elev1)",
                      border: "1px solid var(--border-subtle)",
                    }}
                  >
                    <div
                      style={{
                        fontSize: 10,
                        fontWeight: 700,
                        textTransform: "uppercase",
                        letterSpacing: "0.08em",
                        color: tone.fg,
                        marginBottom: 4,
                      }}
                    >
                      AI&apos;s reasoning · why {label}
                    </div>
                    <div style={{ color: "var(--text-primary)" }}>
                      {s.classifier_reasoning}
                    </div>
                  </div>
                ) : null}

                {/* Score reason */}
                {s.reason ? (
                  <div
                    style={{
                      fontSize: 12,
                      color: "var(--text-muted)",
                      marginBottom: 14,
                    }}
                  >
                    {s.reason}
                  </div>
                ) : null}

                {/* Nested checkpoints */}
                <div
                  style={{
                    fontSize: 10.5,
                    fontWeight: 700,
                    textTransform: "uppercase",
                    letterSpacing: "0.08em",
                    color: "var(--text-faint)",
                    marginBottom: 8,
                  }}
                >
                  Checkpoints in this segment
                  {cpFilter !== "all" && allForSegment > 0
                    ? ` · ${subset.length} of ${allForSegment} match the ${cpFilter} filter`
                    : ` · ${allForSegment}`}
                </div>

                {subset.length === 0 ? (
                  <div
                    style={{
                      fontSize: 12,
                      color: "var(--text-faint)",
                      fontStyle: "italic",
                      padding: "12px 0",
                    }}
                  >
                    {allForSegment === 0
                      ? "No checkpoint detail available for this segment."
                      : `No ${cpFilter} checkpoints in this segment.`}
                  </div>
                ) : (
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 10,
                    }}
                  >
                    {innerProps
                      ? subset.map((cpc, i) => (
                          <CheckpointCard
                            key={`${cpc.key}-${i}`}
                            index={i}
                            script={cpc.script}
                            verdict={cpc.verdict}
                            startSec={cpc.startSec}
                            isActive={innerProps.activeCheckpointKey === cpc.key}
                            onPlay={innerProps.seekAndPlay}
                            callId={callId}
                            callDurationSec={innerProps.callDurationSec}
                            totalSections={innerProps.totalSections}
                            words={innerProps.words}
                            origIndex={cpCards.indexOf(cpc)}
                            rubricKind={s.rubric_kind ?? undefined}
                            rubricLabel={s.rubric_label ?? undefined}
                            onReviewVerdict={innerProps.onReviewVerdict}
                            onRetry={innerProps.onRetry}
                          />
                        ))
                      : subset.map((cpc) => (
                          <div
                            key={cpc.key}
                            style={{
                              fontSize: 12,
                              padding: 8,
                              border: "1px solid var(--border-subtle)",
                              borderRadius: 6,
                              color: "var(--text-muted)",
                            }}
                          >
                            {cpc.script?.name ?? cpc.verdict?.name}
                          </div>
                        ))}
                  </div>
                )}
              </div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}
