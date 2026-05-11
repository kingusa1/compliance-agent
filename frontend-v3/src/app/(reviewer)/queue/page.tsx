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
  Bookmark,
  ChevronDown,
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
import { Pill } from "@/components/design/Pill";
import { FilterChip } from "@/components/design/FilterChip";
import { EmptyState } from "@/components/design/EmptyState";
import { Waveform } from "@/components/design/Waveform";
import { HelpBanner } from "@/components/design/HelpBanner";

import type { QueueCall } from "@/lib/api";

// Visible filter values are simplified to {all, pending, reviewed}.
// The backend still understands the legacy "unclaimed/in_review/today"
// values; we map at the boundary so the API contract stays unchanged.
const QUEUE_FILTERS: readonly QueueFilter[] = [
  "all",
  "unclaimed", // surfaced as "Pending"
  "today",     // surfaced as "Reviewed"
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

const COL_TEMPLATE = "84px 1.4fr 1fr 1fr 110px 110px";

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
            color: "var(--text-primary)",
            fontWeight: 500,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {row.filename}
        </div>
        <div
          style={{
            color: "var(--text-faint)",
            fontSize: 12,
            fontFamily: "var(--font-mono)",
            marginTop: 2,
          }}
        >
          {row.id.slice(0, 8)}
        </div>
      </div>
      <div style={{ color: "var(--text-primary)" }}>{row.supplier ?? "—"}</div>
      <div style={{ color: "var(--text-muted)" }}>—</div>
      <ScoreBar pct={pct} />
      <div>{statusPill(row.review_status)}</div>
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
          {row.duration ? `${Math.floor((row.duration ?? 0) / 60)}:${String((row.duration ?? 0) % 60).padStart(2, "0")} · ` : ""}
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
            {`${Math.floor(currentTime / 60)}:${String(Math.floor(currentTime % 60)).padStart(2, "0")}`} / {row.duration ? `${Math.floor((row.duration ?? 0) / 60)}:${String((row.duration ?? 0) % 60).padStart(2, "0")}` : "—:—"}
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
        <button
          type="button"
          style={{
            height: 38,
            padding: "0 12px",
            borderRadius: 8,
            background: "var(--bg-elev2)",
            color: "var(--text-primary)",
            border: "1px solid var(--border-subtle)",
            display: "inline-flex",
            alignItems: "center",
            cursor: "pointer",
          }}
        >
          <Download size={14} />
        </button>
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
  const queue = useQueueQuery(filter);

  const filteredCalls: QueueCall[] = useMemo(() => {
    const calls = queue.data?.calls ?? [];
    if (!debouncedSearch.trim()) return calls;
    const q = debouncedSearch.toLowerCase();
    return calls.filter(
      (c) =>
        (c.filename ?? "").toLowerCase().includes(q) ||
        (c.supplier ?? "").toLowerCase().includes(q),
    );
  }, [queue.data?.calls, debouncedSearch]);

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
          Review Queue
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
            count={reviewedTodayCount + inReviewCount}
          >
            Reviewed
          </FilterChip>
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
        <button
          type="button"
          style={{
            height: 32,
            padding: "0 12px",
            fontSize: 13,
            fontWeight: 500,
            background: "var(--bg-elev2)",
            color: "var(--text-primary)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            cursor: "pointer",
          }}
        >
          <Bookmark size={14} />
          Saved views
          <ChevronDown size={14} style={{ color: "var(--text-muted)" }} />
        </button>
      </div>

      <HelpBanner id="queue" title="How to work the Queue" href="/guide#review-queue">
        Each card is a call the AI thinks needs reviewer attention. Open a call to see the AI verdict, listen to the audio, then accept or override. Your verdict is the audit-of-record — it overrides the AI on conflict. No claim/lock step — anyone can pick up any pending call.
      </HelpBanner>

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
              <HeaderCell>Agent</HeaderCell>
              <HeaderCell>Score</HeaderCell>
              <HeaderCell>Status</HeaderCell>
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
