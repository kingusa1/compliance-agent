"use client";

/**
 * /queue — calls flagged by the AI as needing reviewer attention.
 *
 * Master-detail 60/40. Top bar: H1 + count chip + filter chips +
 * search input + saved-views dropdown. Left = comfortable-density
 * 6-col table (When | Customer/filename | Supplier | Agent | Score
 * | Status pill). Right = preview panel with mini waveform, snippet
 * and "Open & review" CTA.
 *
 * Claim/unclaim flow was removed 2026-05-10 — every reviewer can open
 * any pending call directly. Per-user lock no longer required.
 */
import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import {
  Search,
  AlertCircle,
  Play,
  Pause,
  Download,
  Inbox as InboxIcon,
} from "lucide-react";

import {
  useQueueQuery,
  useCallDetailQuery,
  useCallWordsQuery,
  useCallAudioUrlQuery,
  type QueueFilter,
} from "@/lib/queries/reviewer";
import { ApiError } from "@/lib/api";
import { useDebouncedValue } from "@/lib/hooks/useDebouncedValue";
import { useUrlState } from "@/lib/hooks/useUrlState";
import { useRealtimeInvalidate } from "@/lib/hooks/useRealtimeInvalidate";
import { Pill } from "@/components/design/Pill";
import { FilterChip } from "@/components/design/FilterChip";
import { EmptyState } from "@/components/design/EmptyState";
import { Waveform } from "@/components/design/Waveform";
import { SavedViewsBar } from "./SavedViewsBar";

import type { QueueCall } from "@/lib/api";

// Visible filter values: {all, unclaimed, in_review, today}.
// The backend understands the same set via /api/queue?filter=…
// (with `today` → `reviewed_today` mapped in lib/api.ts).
//
// 2026-05-18: added "in_review" so the Reviewing tab can actually
// filter to mid-review calls instead of falling back to "unclaimed"
// every time parseQueueFilter runs against the URL.
const QUEUE_FILTERS: readonly QueueFilter[] = [
  "all",
  "unclaimed",  // surfaced as "Pending"
  "in_review",  // surfaced as "Reviewing"
  "today",      // surfaced as "Reviewed"
] as const;

function parseQueueFilter(raw: string): QueueFilter {
  return (QUEUE_FILTERS as readonly string[]).includes(raw)
    ? (raw as QueueFilter)
    : "unclaimed";
}

function statusPill(status: string) {
  const s = (status || "").toLowerCase();
  if (s === "reviewed")
    return <Pill tone="emerald" dot>Reviewed</Pill>;
  // Everything else (unclaimed, in_review legacy) renders as Pending.
  return <Pill tone="neutral" dot>Pending</Pill>;
}

// Plan §5a: the second pill on each queue row is the AI's verdict (X/N
// from the per-segment aggregator) plus a Coaching/Review/Block marker.
// 2026-05-14: user removed the "AI:" prefix — the column already implies
// "AI score" so the prefix was duplicating signal. Just score + marker.
function aiVerdictPill(row: QueueCall) {
  const score = (row as QueueCall & { score?: string | null }).score;
  if (!score) {
    return <Pill tone="neutral" mono>…</Pill>;
  }
  const bucket = (row.bucket || row.compliance_status || "").toLowerCase();
  let tone: "emerald" | "amber" | "red" | "neutral" = "neutral";
  let marker = "";
  if (bucket === "pass" || bucket === "compliant") {
    tone = "emerald";
    marker = "✓";
  } else if (bucket === "coaching") {
    tone = "amber";
    marker = "⚠";
  } else if (bucket === "review" || bucket === "pending") {
    tone = "amber";
    marker = "⚠";
  } else if (bucket === "blocked" || bucket === "non_compliant") {
    tone = "red";
    marker = "✗";
  }
  return (
    <Pill tone={tone} mono>
      {score} {marker}
    </Pill>
  );
}

// Map call_type / stage codes to a short human label for the segments column.
const STAGE_LABEL: Record<string, string> = {
  lead_gen: "Lead Gen",
  pre_sales: "Pre-Sales",
  verbal: "Verbal",
  loa: "LOA",
};

/**
 * Format seconds as `MM:SS` with both minute and second floored.
 * Fixes audit 2026-05-16 P1 #10 — the previous inline formatter omitted
 * `Math.floor` on the modulo, causing display like `1:9.911999999999992`
 * instead of `01:09` whenever `seconds` was a float.
 */
function formatMmSs(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const mm = Math.floor(s / 60).toString().padStart(2, "0");
  const ss = (s % 60).toString().padStart(2, "0");
  return `${mm}:${ss}`;
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diffSec = Math.floor((Date.now() - t) / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

function scorePctOf(score: string | null | undefined): number {
  if (!score) return 0;
  const m = score.match(/(\d+)\s*\/\s*(\d+)/);
  if (m) return Math.round((Number(m[1]) / Number(m[2])) * 100);
  const num = Number(score);
  return Number.isFinite(num) ? Math.round(num * (num <= 1 ? 100 : 1)) : 0;
}

function ScoreBar({ pct }: { pct: number }) {
  const tone =
    pct >= 85
      ? "var(--emerald)"
      : pct >= 70
        ? "var(--amber)"
        : "var(--red)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          color: "var(--text-primary)",
          minWidth: 38,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {pct}%
      </div>
      <div
        style={{
          width: 48,
          height: 4,
          background: "var(--bg-elev3)",
          borderRadius: 2,
          overflow: "hidden",
        }}
      >
        <div style={{ height: "100%", width: `${pct}%`, background: tone }} />
      </div>
    </div>
  );
}

// Plan §5a columns: When · Customer · Supplier · Segments · Score · AI · Review
const COL_TEMPLATE = "76px 1.3fr 1fr 1fr 100px 110px 110px";

function QueueRow({
  row,
  selected,
  onClick,
}: {
  row: QueueCall;
  selected: boolean;
  onClick: () => void;
}) {
  const pct = scorePctOf((row as QueueCall & { score?: string }).score ?? null);
  const segs = row.segments ?? [];
  const segLabel =
    segs.length > 0
      ? segs.map((s) => STAGE_LABEL[s.stage] ?? s.stage).join(" · ")
      : "—";
  const reviewed = (row.review_status || "").toLowerCase() === "reviewed";
  return (
    <div
      onClick={onClick}
      style={{
        display: "grid",
        gridTemplateColumns: COL_TEMPLATE,
        gap: 12,
        alignItems: "center",
        padding: "12px 24px",
        borderBottom: "1px solid var(--border-subtle)",
        background: selected ? "var(--bg-elev2)" : "transparent",
        borderLeft: `2px solid ${selected ? "var(--emerald)" : "transparent"}`,
        cursor: "pointer",
        fontSize: 13,
      }}
    >
      <div style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
        {formatRelative(row.created_at)}
      </div>
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            color: row.customer_name ? "var(--text-primary)" : "var(--text-faint)",
            fontWeight: 500,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={row.customer_name ?? "Customer not yet extracted"}
        >
          {row.customer_name ?? "—"}
        </div>
        <div
          style={{
            color: "var(--text-faint)",
            fontSize: 11.5,
            fontFamily: "var(--font-mono)",
            marginTop: 2,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={row.filename}
        >
          {row.filename}
        </div>
      </div>
      <div style={{ color: "var(--text-primary)" }}>{row.supplier ?? "—"}</div>
      <div
        style={{
          color: "var(--text-muted)",
          fontSize: 12,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={segLabel}
      >
        {segLabel}
      </div>
      <ScoreBar pct={pct} />
      <div>{aiVerdictPill(row)}</div>
      <div>{reviewed ? statusPill(row.review_status) : <Pill tone="amber" dot>To Review</Pill>}</div>
    </div>
  );
}

function HeaderCell({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 11,
        fontWeight: 500,
        color: "var(--text-faint)",
        textTransform: "uppercase",
        letterSpacing: "0.06em",
      }}
    >
      {children}
    </div>
  );
}

function PreviewPanel({ row }: { row: QueueCall | null }) {
  const detail = useCallDetailQuery(row?.id ?? "");
  const wordsQuery = useCallWordsQuery(row?.id ?? "");
  const audioUrlQuery = useCallAudioUrlQuery(row?.id ?? "");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);

  if (!row) {
    return (
      <div
        style={{
          flex: 1,
          display: "grid",
          placeItems: "center",
          color: "var(--text-faint)",
          fontSize: 13,
        }}
      >
        Select a call to preview
      </div>
    );
  }

  // Build a 3-line transcript snippet from real backend data.
  // Prefer word-level data (preserves speaker + timestamps); fall back to
  // splitting raw transcript text on speaker prefixes; otherwise empty.
  // Speaker labels are neutral ("Speaker 1" / "Speaker 2") to mirror the
  // /calls/[id] detail page — diarisation can't reliably tell agent from
  // customer, so we don't pretend.
  type SnippetLine = { t: string; who: string; speakerIdx: number; text: string };
  const words = wordsQuery.data?.words ?? [];
  const queueLetterToIdx = (sp: string | number | null | undefined): number => {
    const s = String(sp ?? "").toUpperCase().trim();
    if (s === "A" || s === "AGENT" || s === "1" || s === "0") return 1;
    if (s === "B" || s === "CUSTOMER" || s === "2") return 2;
    if (s === "C" || s === "3") return 3;
    if (s === "D" || s === "4") return 4;
    return 1;
  };
  const snippet: SnippetLine[] = (() => {
    if (words.length > 0) {
      const lines: SnippetLine[] = [];
      let curr: typeof words = [];
      let currIdx = 0;
      for (let i = 0; i < words.length && lines.length < 3; i++) {
        const w = words[i];
        const idx = queueLetterToIdx(w.speaker);
        if (idx !== currIdx && curr.length > 0) {
          const startSec = Math.floor(curr[0]?.start ?? 0);
          lines.push({
            t: `${String(Math.floor(startSec / 60)).padStart(2, "0")}:${String(startSec % 60).padStart(2, "0")}`,
            who: `Speaker ${currIdx}`,
            speakerIdx: currIdx,
            text: curr.map((x) => x.word || "").join(" ").trim().slice(0, 140),
          });
          curr = [];
        }
        currIdx = idx;
        curr.push(w);
      }
      if (curr.length > 0 && lines.length < 3) {
        const startSec = Math.floor(curr[0]?.start ?? 0);
        lines.push({
          t: `${String(Math.floor(startSec / 60)).padStart(2, "0")}:${String(startSec % 60).padStart(2, "0")}`,
          who: `Speaker ${currIdx}`,
          speakerIdx: currIdx,
          text: curr.map((x) => x.word || "").join(" ").trim().slice(0, 140),
        });
      }
      return lines;
    }
    const txt = (detail.data?.transcript ?? "").trim();
    if (!txt) return [];
    // Split on common speaker labels and take first 3 chunks.
    const chunks = txt.split(/\n+/).filter(Boolean).slice(0, 3);
    return chunks.map((c, i) => {
      const idx = (i % 2) + 1;
      return {
        t: `00:${String(i * 10).padStart(2, "0")}`,
        who: `Speaker ${idx}`,
        speakerIdx: idx,
        text: c.replace(/^(AGENT|CUSTOMER|SPEAKER\s*\d+)[: ]+/i, "").slice(0, 140),
      };
    });
  })();

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* header */}
      <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border-subtle)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <Pill tone="neutral" mono>
            {row.filename}
          </Pill>
          {statusPill(row.review_status)}
        </div>
        <div
          style={{
            fontSize: 18,
            fontWeight: 600,
            letterSpacing: "-0.014em",
            marginTop: 4,
            color: "var(--text-primary)",
          }}
        >
          {row.supplier || "Unknown supplier"}
        </div>
        <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 4 }}>
          {row.duration ? `${formatMmSs(row.duration)} · ` : ""}
          {formatRelative(row.created_at)}
        </div>
      </div>

      {/* mini waveform */}
      <div
        style={{
          padding: "16px 20px",
          borderBottom: "1px solid var(--border-subtle)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button
            type="button"
            disabled={!audioUrlQuery.data?.url}
            onClick={() => {
              const a = audioRef.current;
              if (!a) return;
              if (a.paused) void a.play().catch(() => {});
              else a.pause();
            }}
            style={{
              width: 28,
              height: 28,
              borderRadius: 14,
              background: "var(--bg-elev3)",
              border: "1px solid var(--border-subtle)",
              display: "grid",
              placeItems: "center",
              color: "var(--text-primary)",
              cursor: audioUrlQuery.data?.url ? "pointer" : "not-allowed",
              opacity: audioUrlQuery.data?.url ? 1 : 0.4,
            }}
            aria-label={playing ? "Pause" : "Play"}
          >
            {playing ? <Pause size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}
          </button>
          <div style={{ flex: 1 }}>
            <Waveform
              played={Math.floor((currentTime / Math.max(1, row.duration ?? 1)) * 64)}
              total={64}
              height={28}
            />
          </div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--text-muted)",
            }}
          >
            {formatMmSs(currentTime)} / {row.duration ? formatMmSs(row.duration) : "—:—"}
          </div>
          {audioUrlQuery.data?.url && (
            <audio
              ref={audioRef}
              src={audioUrlQuery.data.url}
              preload="metadata"
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onTimeUpdate={(e) => setCurrentTime((e.target as HTMLAudioElement).currentTime)}
            />
          )}
        </div>
      </div>

      {/* snippet */}
      <div style={{ padding: "16px 20px", flex: 1, overflowY: "auto" }} className="ca-scroll">
        <div
          style={{
            fontSize: 11,
            fontWeight: 500,
            color: "var(--text-faint)",
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            marginBottom: 10,
          }}
        >
          Transcript snippet
        </div>
        {snippet.length === 0 ? (
          <div
            style={{
              fontSize: 12,
              color: "var(--text-faint)",
              fontStyle: "italic",
            }}
          >
            {detail.isLoading || wordsQuery.isLoading
              ? "Loading transcript…"
              : "No transcript yet — call may still be processing."}
          </div>
        ) : (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 10,
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              lineHeight: 1.6,
            }}
          >
            {snippet.map((ln, i) => (
              <div key={`${ln.t}-${i}`}>
                <span style={{ color: "var(--text-faint)" }}>[{ln.t}]</span>{" "}
                <span
                  style={{
                    color:
                      ln.speakerIdx === 1
                        ? "var(--emerald-400)"
                        : ln.speakerIdx === 2
                          ? "var(--amber-400)"
                          : ln.speakerIdx === 3
                            ? "var(--blue)"
                            : "var(--violet)",
                  }}
                >
                  {ln.who}
                </span>{" "}
                <span style={{ color: "var(--text-primary)" }}>
                  {ln.text.split(/(\[[A-Z_]+\])/g).map((part, j) =>
                    /^\[[A-Z_]+\]$/.test(part) ? (
                      <Pill key={j} tone="violet" mono>
                        {part}
                      </Pill>
                    ) : (
                      <span key={j}>{part}</span>
                    ),
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div
        style={{
          padding: 16,
          borderTop: "1px solid var(--border-subtle)",
          display: "flex",
          gap: 8,
        }}
      >
        <Link
          href={`/calls/${encodeURIComponent(row.id)}`}
          style={{
            flex: 1,
            height: 38,
            borderRadius: 8,
            background: "var(--emerald)",
            color: "#04201a",
            fontSize: 14,
            fontWeight: 500,
            border: "1px solid var(--emerald)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            textDecoration: "none",
            cursor: "pointer",
            boxShadow: "var(--shadow-sm)",
          }}
        >
          Open &amp; review
        </Link>
        {/* 2026-05-14 audit fix: was a silent no-op. Now downloads the
            audio file via the already-loaded audioUrlQuery — same source
            the inline player uses. Disabled until the URL resolves. */}
        <a
          href={audioUrlQuery.data?.url ?? "#"}
          download={row.filename ?? "call.mp3"}
          target="_blank"
          rel="noopener noreferrer"
          aria-disabled={!audioUrlQuery.data?.url}
          onClick={(e) => {
            if (!audioUrlQuery.data?.url) {
              e.preventDefault();
            }
          }}
          aria-label="Download audio"
          title={audioUrlQuery.data?.url ? "Download audio" : "Audio not yet available"}
          style={{
            height: 38,
            padding: "0 12px",
            borderRadius: 8,
            background: "var(--bg-elev2)",
            color: "var(--text-primary)",
            border: "1px solid var(--border-subtle)",
            display: "inline-flex",
            alignItems: "center",
            cursor: audioUrlQuery.data?.url ? "pointer" : "not-allowed",
            opacity: audioUrlQuery.data?.url ? 1 : 0.5,
            textDecoration: "none",
          }}
        >
          <Download size={14} />
        </a>
      </div>
    </div>
  );
}

function SkeletonRow() {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: COL_TEMPLATE,
        gap: 12,
        alignItems: "center",
        padding: "14px 24px",
        borderBottom: "1px solid var(--border-subtle)",
      }}
    >
      {[60, 180, 110, 100, 80, 90].map((w, i) => (
        <div
          key={i}
          style={{
            height: 10,
            width: w,
            background: "var(--bg-elev3)",
            borderRadius: 3,
            animation: "ca-pulse 1.5s ease-in-out infinite",
          }}
        />
      ))}
    </div>
  );
}

export default function QueuePage() {
  const { get, set } = useUrlState();
  const filter = parseQueueFilter(get("filter") || "unclaimed");
  const setFilter = (next: QueueFilter) =>
    set("filter", next === "unclaimed" ? null : next);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Plan §5a: hide rows still mid-pipeline (score=null OR 0%) by default —
  // they confuse reviewers ("0% means nothing"). Toggle reveals them.
  const [showProcessing, setShowProcessing] = useState(false);
  // 2026-05-18: when the user is searching, query the broader "all" set so
  // a match in the Reviewed tab still surfaces while the user is on Pending.
  // The visible tab still controls the active chip; only the dataset
  // expands. Cleared the moment search empties.
  const effectiveFilter: QueueFilter = debouncedSearch.trim() ? "all" : filter;
  const queue = useQueueQuery(effectiveFilter);

  // Supabase Realtime — any INSERT/UPDATE/DELETE on `calls` or `review_sessions`
  // invalidates the queue + checkpoint queries. Feature-flagged on
  // NEXT_PUBLIC_USE_REALTIME=1. Path 3 of the 2026-05-16 realtime overhaul.
  useRealtimeInvalidate("calls", [["queue"]]);
  useRealtimeInvalidate("review_sessions", [["queue"]]);

  const filteredCalls: QueueCall[] = useMemo(() => {
    let calls = queue.data?.calls ?? [];
    const hasSearch = !!debouncedSearch.trim();
    // 2026-05-18: when searching, surface processing rows too — otherwise a
    // call the reviewer is looking for stays hidden because the score isn't
    // computed yet.
    if (!showProcessing && !hasSearch) {
      calls = calls.filter((c) => {
        const sc = (c as QueueCall & { score?: string | null }).score;
        if (!sc) return false;
        const m = sc.match(/(\d+)\s*\/\s*(\d+)/);
        if (m && Number(m[2]) === 0) return false;
        if (m && Number(m[1]) === 0 && Number(m[2]) > 0) return false;
        return true;
      });
    }
    if (!hasSearch) return calls;
    const q = debouncedSearch.toLowerCase();
    return calls.filter(
      (c) =>
        (c.filename ?? "").toLowerCase().includes(q) ||
        (c.supplier ?? "").toLowerCase().includes(q) ||
        (c.customer_name ?? "").toLowerCase().includes(q) ||
        (c.agent_name ?? "").toLowerCase().includes(q) ||
        // Also search the stage / segment labels so reviewers can type
        // "Verbal" / "LOA" to narrow by call type.
        (c.segments ?? []).some((s) =>
          (STAGE_LABEL[s.stage] ?? s.stage).toLowerCase().includes(q),
        ),
    );
  }, [queue.data?.calls, debouncedSearch, showProcessing]);

  const processingCount = (queue.data?.calls ?? []).filter((c) => {
    const sc = (c as QueueCall & { score?: string | null }).score;
    if (!sc) return true;
    const m = sc.match(/(\d+)\s*\/\s*(\d+)/);
    if (m && Number(m[2]) === 0) return true;
    if (m && Number(m[1]) === 0 && Number(m[2]) > 0) return true;
    return false;
  }).length;

  const effectiveSelected =
    selectedId && filteredCalls.some((c) => c.id === selectedId)
      ? selectedId
      : (filteredCalls[0]?.id ?? null);

  const selectedRow = filteredCalls.find((c) => c.id === effectiveSelected) ?? null;

  // Real counts: backend metrics are computed over the WHOLE table (not the
  // visible page). All three counts come from /api/queue metrics directly.
  const unclaimedCount = queue.data?.metrics?.backlog ?? 0;
  const inReviewCount = queue.data?.metrics?.in_review ?? 0;
  const reviewedTodayCount = queue.data?.metrics?.reviewed_today ?? 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", minWidth: 0 }}>
      {/* Top bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "14px 24px",
          borderBottom: "1px solid var(--border-subtle)",
          flexShrink: 0,
        }}
      >
        <h1
          style={{
            fontSize: 19,
            fontWeight: 600,
            letterSpacing: "-0.018em",
            margin: 0,
            color: "var(--text-primary)",
          }}
          title="Calls flagged by the AI as needing reviewer attention. Open a call to read the AI verdict and override if needed — your decision is the audit-of-record."
        >
          Human Review Queue
        </h1>
        <Pill tone="emerald" mono>
          {unclaimedCount} pending
        </Pill>
        <div
          style={{
            width: 1,
            height: 18,
            background: "var(--border-subtle)",
            margin: "0 4px",
          }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <FilterChip active={filter === "all"} onClick={() => setFilter("all")}>
            All
          </FilterChip>
          <FilterChip
            active={filter === "unclaimed"}
            onClick={() => setFilter("unclaimed")}
            count={unclaimedCount}
          >
            Pending
          </FilterChip>
          <FilterChip
            active={filter === "today"}
            onClick={() => setFilter("today")}
            count={reviewedTodayCount}
          >
            Reviewed
          </FilterChip>
          {inReviewCount > 0 ? (
            <FilterChip
              active={filter === "in_review"}
              onClick={() => setFilter("in_review")}
              count={inReviewCount}
              title="Currently being reviewed (claimed but not yet submitted)."
            >
              Reviewing
            </FilterChip>
          ) : null}
        </div>
        <div style={{ flex: 1 }} />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            height: 32,
            padding: "0 10px",
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
            width: 240,
          }}
        >
          <Search size={14} style={{ color: "var(--text-dim)" }} />
          <input
            type="text"
            placeholder="Search calls…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              background: "transparent",
              border: "none",
              outline: "none",
              color: "var(--text-primary)",
              fontSize: 13,
              flex: 1,
              fontFamily: "inherit",
            }}
          />
        </div>
        {/* 2026-05-16 audit fix — was a "Coming soon" placeholder even
            though SavedViewsBar.tsx already implements the full CRUD UI
            against the existing backend endpoints (useSavedViewsQuery /
            useSaveView / useDeleteView). Wire it in. */}
        <SavedViewsBar
          current={{ filter, q: search }}
          onApply={({ filter: nextFilter, q: nextQ }) => {
            if (nextFilter) setFilter(nextFilter);
            if (nextQ !== undefined) setSearch(nextQ);
          }}
        />
      </div>

      {/* Error banner */}
      {queue.isError ? (
        <div
          style={{
            padding: "10px 24px",
            background: "var(--red-bg)",
            borderBottom: "1px solid var(--red-border)",
            display: "flex",
            alignItems: "center",
            gap: 10,
            color: "var(--red)",
            fontSize: 13,
          }}
        >
          <AlertCircle size={14} />
          <span>
            Failed to load queue
            {queue.error instanceof ApiError ? ` — ${queue.error.status}` : ""}.{" "}
            <span
              onClick={() => queue.refetch()}
              style={{ textDecoration: "underline", cursor: "pointer" }}
            >
              Retry
            </span>
          </span>
        </div>
      ) : null}

      {/* Master / detail */}
      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateColumns: "60% 40%",
          overflow: "hidden",
          minHeight: 0,
        }}
      >
        {/* left: table */}
        <div
          style={{
            borderRight: "1px solid var(--border-subtle)",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          {!queue.isLoading && filteredCalls.length > 0 && (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: COL_TEMPLATE,
                gap: 12,
                padding: "10px 24px",
                borderBottom: "1px solid var(--border-subtle)",
                background: "var(--bg-elev1)",
                position: "sticky",
                top: 0,
                zIndex: 1,
              }}
            >
              <HeaderCell>When</HeaderCell>
              <HeaderCell>Customer</HeaderCell>
              <HeaderCell>Supplier</HeaderCell>
              <HeaderCell>Segments</HeaderCell>
              <HeaderCell>Score</HeaderCell>
              <HeaderCell>AI Verdict</HeaderCell>
              <HeaderCell>Human Review</HeaderCell>
            </div>
          )}
          <div style={{ flex: 1, overflowY: "auto" }} className="ca-scroll">
            {queue.isLoading ? (
              Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} />)
            ) : filteredCalls.length === 0 ? (
              <EmptyState
                icon={<InboxIcon size={20} />}
                iconTone="emerald"
                title={
                  filter === "unclaimed"
                    ? "Nothing to review — nice work"
                    : "No matching calls"
                }
                body={
                  filter === "unclaimed"
                    ? "The queue is clear. New calls will appear here as they're transcribed."
                    : "Try a different filter or saved view."
                }
              />
            ) : (
              filteredCalls.map((row) => (
                <QueueRow
                  key={row.id}
                  row={row}
                  selected={row.id === effectiveSelected}
                  onClick={() => setSelectedId(row.id)}
                />
              ))
            )}
          </div>
        </div>

        {/* right: preview */}
        <div
          style={{
            background: "var(--bg-elev1)",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <PreviewPanel row={selectedRow} />
        </div>
      </div>
    </div>
  );
}
