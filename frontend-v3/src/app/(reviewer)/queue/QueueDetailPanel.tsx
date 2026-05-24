"use client";

import Link from "next/link";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Headphones,
  Inbox,
  Pause,
  Play,
  User,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useCallDetailQuery } from "@/lib/queries/reviewer";

/**
 * QueueDetailPanel — right-rail (40%) triage preview for a selected
 * queue row.
 *
 * 2026-05-24 redesign — the prior layout buried the things a reviewer
 * needs to triage (customer / agent / call type / score / compliance
 * state) in a single grey subtitle, and made the filename a big
 * monospace chip up top. The new layout puts:
 *   1. Customer name as the hero title + supplier + "X ago"
 *   2. Stage / status / score pills on one row — all triage signals
 *      in one glance
 *   3. Agent + call type + duration as a metadata strip
 *   4. AI verdict card (when populated) — the AI's reasoning is now
 *      a first-class block, not hidden in the transcript area
 *   5. Inline audio player — Play button actually plays (was decorative)
 *   6. Transcript snippet
 *   7. Filename footer chip — useful but de-emphasised
 *   8. Full-width "Open & review" CTA
 */
export function QueueDetailPanel({ callId }: { callId: string | null }) {
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
        <Skeleton className="mt-2 h-7 w-full" />
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
  const scoreNum = parseScorePct(c.score);
  const scoreBreakdown = parseScoreBreakdown(c.score);
  const callTypeLabel = CALL_TYPE_LABEL[c.call_type ?? ""] ?? c.call_type ?? null;
  const customerName = (c.customer_name || "").trim();
  const agentName = (c.agent_name || "").trim();

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* ── Hero header — customer + supplier + age ─────────────────── */}
      <div className="border-b border-[var(--border-subtle)] px-5 pb-3 pt-4">
        <div
          className="truncate text-[17px] font-semibold tracking-tight text-[var(--text-primary)]"
          title={customerName || c.filename}
        >
          {customerName || c.filename}
        </div>
        <div className="mt-1 flex items-center gap-2 text-[12px] text-[var(--text-muted)]">
          {c.detected_supplier ? (
            <span>{c.detected_supplier}</span>
          ) : (
            <span className="italic">supplier pending</span>
          )}
          <span className="text-[var(--text-faint)]">·</span>
          <Clock className="h-3 w-3" aria-hidden />
          <span>{formatAge(c.created_at)}</span>
          {c.duration_seconds != null && (
            <>
              <span className="text-[var(--text-faint)]">·</span>
              <span className="font-mono tabular-nums">
                {formatDuration(c.duration_seconds)}
              </span>
            </>
          )}
        </div>

        {/* Triage pills — stage / status / score */}
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          {callTypeLabel && <StagePill label={callTypeLabel} stage={c.call_type ?? ""} />}
          <StatusPill
            status={c.review_status ?? c.status}
            complianceStatus={c.compliance_status}
            compliant={c.compliant}
          />
          {scoreNum != null && (
            <ScorePill pct={scoreNum} breakdown={scoreBreakdown} />
          )}
        </div>
      </div>

      {/* ── Metadata strip — agent / call type ─────────────────────── */}
      <div className="grid grid-cols-2 gap-px border-b border-[var(--border-subtle)] bg-[var(--border-subtle)]">
        <KvCell
          icon={<User className="h-3.5 w-3.5" aria-hidden />}
          label="Agent"
          value={agentName || "—"}
          dim={!agentName}
        />
        <KvCell
          icon={<Headphones className="h-3.5 w-3.5" aria-hidden />}
          label="Call type"
          value={callTypeLabel || "—"}
          dim={!callTypeLabel}
        />
      </div>

      {/* ── AI verdict (when populated) ─────────────────────────────── */}
      {(c.reason || c.excerpt) && (
        <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-elev1)] px-5 py-3">
          <div className="mb-1 flex items-center gap-1.5 text-[10.5px] uppercase tracking-wide text-[var(--text-faint)]">
            <AiVerdictIcon compliant={c.compliant} />
            AI verdict
          </div>
          <div className="text-[12.5px] leading-[1.5] text-[var(--text-primary)]">
            {c.reason ?? c.excerpt}
          </div>
        </div>
      )}

      {/* ── Audio player (functional) ───────────────────────────────── */}
      <div className="border-b border-[var(--border-subtle)] px-5 py-3">
        <AudioBar src={c.audio_url ?? null} />
      </div>

      {/* ── Transcript snippet ──────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto px-5 py-4">
        <div className="mb-2 text-[10.5px] uppercase tracking-wide text-[var(--text-faint)]">
          Transcript snippet
        </div>
        <div className="font-mono text-[12px] leading-[1.6] text-[var(--text-primary)]">
          {c.transcript
            ? c.transcript.slice(0, 320).trim() + (c.transcript.length > 320 ? "…" : "")
            : <span className="italic text-[var(--text-muted)]">No transcript yet.</span>}
        </div>
        {/* Filename footer chip — useful for the audio file but not the hero anymore */}
        {c.filename && (
          <div
            className="mt-4 truncate rounded border border-[var(--border-subtle)] bg-[var(--bg-elev1)] px-2 py-1 font-mono text-[10.5px] text-[var(--text-muted)]"
            title={c.filename}
          >
            {c.filename}
          </div>
        )}
      </div>

      {/* ── Primary CTA ─────────────────────────────────────────────── */}
      <div className="flex gap-2 border-t border-[var(--border-subtle)] p-4">
        <Link
          href={`/calls/${callId}`}
          className="flex h-10 flex-1 items-center justify-center rounded-md bg-[var(--emerald)] px-3 text-[13.5px] font-medium text-[#04201a] no-underline shadow-sm hover:opacity-90"
        >
          Open &amp; review
        </Link>
      </div>
    </div>
  );
}

// ─── Helpers + sub-components ────────────────────────────────────────

const CALL_TYPE_LABEL: Record<string, string> = {
  lead_gen: "Lead Gen",
  pre_sales: "Pre-Sales",
  verbal: "Verbal",
  loa: "LOA",
};

const STAGE_BG: Record<string, string> = {
  lead_gen: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  pre_sales: "bg-blue-500/15 text-blue-300 border-blue-500/30",
  verbal: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  loa: "bg-violet-500/15 text-violet-300 border-violet-500/30",
};

function StagePill({ label, stage }: { label: string; stage: string }) {
  const cls = STAGE_BG[stage] ?? "bg-zinc-500/15 text-zinc-300 border-zinc-500/30";
  return (
    <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      {label}
    </span>
  );
}

function StatusPill({
  status,
  complianceStatus,
  compliant,
}: {
  status: string | null | undefined;
  complianceStatus: string | null | undefined;
  compliant: boolean | null | undefined;
}) {
  const r = (status || "").toLowerCase();
  const c = (complianceStatus || "").toLowerCase();
  // Reviewed-tab states first (terminal)
  if (r === "reviewed" || r === "completed") {
    return (
      <Badge className="border-emerald-500/30 bg-emerald-500/15 text-[var(--emerald-pass)]">
        <CheckCircle2 className="mr-1 h-3 w-3" aria-hidden /> Reviewed
      </Badge>
    );
  }
  if (c === "non_compliant" || compliant === false) {
    return (
      <Badge className="border-red-500/30 bg-red-500/15 text-red-300">
        <AlertTriangle className="mr-1 h-3 w-3" aria-hidden /> Non-compliant
      </Badge>
    );
  }
  if (c === "compliant" || compliant === true) {
    return (
      <Badge className="border-emerald-500/30 bg-emerald-500/15 text-[var(--emerald-pass)]">
        <CheckCircle2 className="mr-1 h-3 w-3" aria-hidden /> Compliant
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="border-[var(--border-strong)] text-[var(--text-muted)]">
      ● Pending
    </Badge>
  );
}

function ScorePill({ pct, breakdown }: { pct: number; breakdown: string | null }) {
  const tone =
    pct >= 80
      ? "border-emerald-500/30 bg-emerald-500/15 text-[var(--emerald-pass)]"
      : pct >= 50
        ? "border-amber-500/30 bg-amber-500/15 text-amber-300"
        : "border-red-500/30 bg-red-500/15 text-red-300";
  return (
    <span
      className={`inline-flex items-baseline gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium tabular-nums ${tone}`}
      title={breakdown ?? undefined}
    >
      <span className="font-semibold">{pct}%</span>
      {breakdown && (
        <span className="text-[10px] font-normal opacity-80">{breakdown}</span>
      )}
    </span>
  );
}

function KvCell({
  icon,
  label,
  value,
  dim = false,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  dim?: boolean;
}) {
  return (
    <div className="bg-[var(--bg-elev1)] px-5 py-2.5">
      <div className="mb-0.5 flex items-center gap-1 text-[10px] uppercase tracking-wide text-[var(--text-faint)]">
        {icon}
        {label}
      </div>
      <div
        className={`truncate text-[12.5px] ${dim ? "text-[var(--text-muted)] italic" : "font-medium text-[var(--text-primary)]"}`}
        title={value}
      >
        {value}
      </div>
    </div>
  );
}

function AiVerdictIcon({ compliant }: { compliant: boolean | null | undefined }) {
  if (compliant === true) return <CheckCircle2 className="h-3 w-3 text-emerald-400" aria-hidden />;
  if (compliant === false) return <AlertTriangle className="h-3 w-3 text-red-400" aria-hidden />;
  return <Clock className="h-3 w-3 text-[var(--text-muted)]" aria-hidden />;
}

function AudioBar({ src }: { src: string | null }) {
  const ref = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);
  // 2026-05-24 — dep array must include `src` because the <audio>
  // element is conditionally rendered. Without it: first mount with
  // src=null skips the effect (ref.current is null), and when src
  // later populates the effect never re-runs → listeners never attach
  // → progress bar stays at 0%. Reset playback state on src change.
  useEffect(() => {
    setT(0);
    setDur(0);
    setPlaying(false);
    const a = ref.current;
    if (!a) return;
    const onTime = () => setT(a.currentTime);
    const onDur = () => setDur(a.duration || 0);
    const onEnd = () => setPlaying(false);
    a.addEventListener("timeupdate", onTime);
    a.addEventListener("loadedmetadata", onDur);
    a.addEventListener("ended", onEnd);
    return () => {
      a.removeEventListener("timeupdate", onTime);
      a.removeEventListener("loadedmetadata", onDur);
      a.removeEventListener("ended", onEnd);
    };
  }, [src]);
  function toggle() {
    const a = ref.current;
    if (!a) return;
    if (a.paused) {
      void a.play();
      setPlaying(true);
    } else {
      a.pause();
      setPlaying(false);
    }
  }
  const pct = dur > 0 ? (t / dur) * 100 : 0;
  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        onClick={toggle}
        disabled={!src}
        aria-label={playing ? "Pause" : "Play"}
        className="grid h-9 w-9 place-items-center rounded-full border border-[var(--border-subtle)] bg-[var(--bg-elev2)] text-[var(--text-primary)] transition hover:bg-[var(--bg-elev3)] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="ml-0.5 h-3.5 w-3.5" />}
      </button>
      <div className="flex flex-1 items-center gap-2">
        <div className="relative h-1 flex-1 overflow-hidden rounded-full bg-[var(--bg-elev3)]">
          <div
            className="absolute inset-y-0 left-0 bg-emerald-500"
            style={{ width: `${pct}%` }}
            aria-hidden
          />
        </div>
        <div className="font-mono text-[11px] tabular-nums text-[var(--text-muted)]">
          {formatDuration(t)} / {formatDuration(dur || null)}
        </div>
      </div>
      {src && (
        <audio ref={ref} src={src} preload="metadata">
          <track kind="captions" />
        </audio>
      )}
    </div>
  );
}

function parseScorePct(score: string | null): number | null {
  if (!score) return null;
  // Common shapes: "21/25" → 84; "84%" → 84; "0.84" → 84
  const slash = score.match(/^\s*(\d+)\s*\/\s*(\d+)\s*$/);
  if (slash) {
    const n = parseInt(slash[1], 10);
    const d = parseInt(slash[2], 10);
    if (d > 0) return Math.round((n / d) * 100);
  }
  const pct = score.match(/^\s*(\d+(?:\.\d+)?)\s*%\s*$/);
  if (pct) return Math.round(parseFloat(pct[1]));
  const flt = score.match(/^\s*(0\.\d+|1(?:\.0+)?)\s*$/);
  if (flt) return Math.round(parseFloat(flt[1]) * 100);
  return null;
}

function parseScoreBreakdown(score: string | null): string | null {
  if (!score) return null;
  const m = score.match(/^\s*(\d+)\s*\/\s*(\d+)\s*$/);
  return m ? `${m[1]}/${m[2]}` : null;
}

function formatDuration(secs: number | null): string {
  if (secs == null || !Number.isFinite(secs)) return "—";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatAge(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "—";
  const diff = Math.floor((Date.now() - t) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
}
