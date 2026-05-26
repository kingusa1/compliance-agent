"use client";

/**
 * /calls/[id] — ported pixel-perfect from
 * design/handoff-bundle/project/screens/call.jsx +
 * design/handoff-bundle/project/hifi/karaoke.jsx.
 *
 * 60/40 master/detail. Left = audio waveform + chat-bubble transcript
 * with PII pills + word-level karaoke (when wordsQuery has data).
 * Right = Checkpoints | Verdict | Chat tabs.
 */
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Download,
  RefreshCw,
  Play,
  Pause,
  CheckCircle2,
  AlertTriangle,
  GraduationCap,
  XCircle,
  Ban,
  Send,
  Mail,
  ChevronDown,
} from "lucide-react";

import {
  useCallDetailQuery,
  useCallFlagsQuery,
  useCallWordsQuery,
  useCallCheckpointsQuery,
  useCallAudioUrlQuery,
  reviewerKeys,
  type WordToken,
  type ScriptCheckpoint,
} from "@/lib/queries/reviewer";
import {
  useSubmitVerdict,
  useAgentChat,
  useReviewCheckpoint,
  useRetryCheckpoint,
  useClaimCall,
  type VerdictAction,
} from "@/lib/mutations/reviewer";
import { ApiError, apiFetch } from "@/lib/api";
import { useMe } from "@/lib/auth";
import { formatScorePercent } from "@/lib/score";
import { formatCustomerName, isPlaceholderCustomerName } from "@/lib/customer";
import { Pill } from "@/components/design/Pill";
import { WorkflowTypePill } from "@/components/design/WorkflowTypePill";
import { Waveform } from "@/components/design/Waveform";
import { useCallEvents } from "@/lib/hooks/useCallEvents";
import { CheckpointCard, parseCheckpointResults, type CheckpointVerdict } from "./CheckpointCard";
import { TranscriptPlayer, type WordData } from "./TranscriptPlayer";
// 2026-05-18: TranscriptAgreementChip + diarization-fallback chip removed
// per user request. The two chips ("Transcripts agree (90%)" and "Speakers
// from <engine>") were visual noise; the underlying two-layer DG/AAI
// cross-validation still runs and is observable via the admin endpoints
// (`/api/admin/transcript-agreement-stats`).
import { PricingMismatchBanner } from "./PricingMismatchBanner";
import { ProcessingStepper } from "./ProcessingStepper";
import { VerdictTab } from "./VerdictTab";
import { SegmentCards } from "./SegmentCards";
import { SegmentChips } from "./SegmentChips";
import { VulnerabilityBanner } from "./VulnerabilityBanner";
import { EditMetadataDialog } from "./EditMetadataDialog";
import { ReanalyzeButton } from "./ReanalyzeButton";
import { PipelineTimeline } from "@/components/design/PipelineTimeline";
import type { ChatMessage } from "@/lib/queries/reviewer";

type TranscriptLine = {
  who: string; // e.g. "Speaker 1", "Speaker 2"
  speakerIdx: number; // 1-based index for color/avatar
  startSec: number;
  endSec: number;
  timestamp: string;
  text: string;
  words: WordToken[];
  flagged: boolean;
  checkpoint: string | null;
};

function fmtTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/**
 * Map AssemblyAI speaker letter (A/B/C/D) or backend "AGENT"/"CUSTOMER"
 * to a stable 1-based index. Audio diarisation can't reliably tell who is
 * the agent vs customer (per user feedback after E2E walk), so we keep the
 * UI neutral as "Speaker 1 / Speaker 2 / …" rather than guessing roles.
 */
function letterToIdx(speaker: string | number | null | undefined): number {
  const sp = String(speaker ?? "").toUpperCase().trim();
  if (sp === "A" || sp === "AGENT" || sp === "1" || sp === "0") return 1;
  if (sp === "B" || sp === "CUSTOMER" || sp === "2") return 2;
  if (sp === "C" || sp === "3") return 3;
  if (sp === "D" || sp === "4") return 4;
  return 1;
}

const SPEAKER_BG = ["var(--emerald)", "var(--amber)", "var(--blue)", "var(--violet)"];
const SPEAKER_FG = ["#04201a", "#1a1100", "#04162a", "#100a1f"];
const SPEAKER_ACCENT = ["var(--emerald-400)", "var(--amber-400)", "var(--blue)", "var(--violet)"];

function speakerColor(idx: number, kind: "bg" | "fg" | "accent"): string {
  const i = Math.max(0, Math.min(3, idx - 1));
  if (kind === "bg") return SPEAKER_BG[i];
  if (kind === "fg") return SPEAKER_FG[i];
  return SPEAKER_ACCENT[i];
}

/**
 * Resolve a checkpoint's audio start (and optional end) in seconds.
 *
 * Tier 1: backend-computed `start_ms` from `checkpoint_results` (precise).
 * Tier 2: client fuzzy-match — first 3-5 words of evidence against words[].
 *         Lowercased, punctuation stripped, sliding window scan.
 * Tier 3: null — caller renders "—" + disables play button.
 *
 * The /flags endpoint returns null word_start/word_end on this dataset so
 * we don't even attempt that path; if backend later populates it, we'd
 * add a tier between 1 and 2.
 */
function computeCheckpointStart(
  verdict: CheckpointVerdict | undefined,
  words: WordToken[],
): { startSec: number | null; endSec: number | null } {
  if (!verdict) return { startSec: null, endSec: null };
  if (verdict.start_ms != null) {
    return {
      startSec: verdict.start_ms / 1000,
      endSec: verdict.end_ms != null ? verdict.end_ms / 1000 : null,
    };
  }
  const evidence = verdict.evidence;
  if (!evidence || evidence === "NOT FOUND IN TRANSCRIPT" || words.length === 0) {
    return { startSec: null, endSec: null };
  }
  const normalize = (s: string) =>
    s.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
  const evWords = normalize(evidence).split(" ").filter(Boolean).slice(0, 5);
  if (evWords.length < 2) return { startSec: null, endSec: null };
  const target = evWords.slice(0, Math.min(3, evWords.length)).join(" ");
  for (let i = 0; i + evWords.length <= words.length; i++) {
    const window = normalize(
      words.slice(i, i + Math.min(3, evWords.length)).map((w) => w.word).join(" "),
    );
    if (window === target) {
      const startSec = words[i]?.start ?? null;
      const lastIdx = Math.min(words.length - 1, i + evWords.length - 1);
      const endSec = words[lastIdx]?.end ?? null;
      return { startSec, endSec };
    }
  }
  return { startSec: null, endSec: null };
}

function _speakerKeyOf(w: WordToken): string {
  // Plan §5b: prefer backend-resolved role (AGENT/CUSTOMER) over the raw
  // diarisation speaker id so transcript turns are labelled "AGENT" /
  // "CUSTOMER" loudly. Falls back to the raw speaker only when role is
  // missing (older calls predating the role-tagging pass).
  const r = (w.role ? String(w.role) : "").toUpperCase().trim();
  if (r === "AGENT" || r === "CUSTOMER") return r;
  return String(w.speaker ?? "AGENT").toUpperCase();
}

function buildLines(words: WordToken[], flaggedRanges: Array<[number, number]>): TranscriptLine[] {
  if (!words.length) return [];
  const lines: TranscriptLine[] = [];
  let curr: WordToken[] = [];
  let currSpeaker: string | null = null;
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    const sp = _speakerKeyOf(w);
    if (currSpeaker == null) currSpeaker = sp;
    if (sp !== currSpeaker || curr.length >= 35) {
      if (curr.length > 0) {
        lines.push(makeLine(curr, currSpeaker, flaggedRanges, i - curr.length));
      }
      curr = [];
      currSpeaker = sp;
    }
    curr.push(w);
  }
  if (curr.length > 0) lines.push(makeLine(curr, currSpeaker || "AGENT", flaggedRanges, words.length - curr.length));
  return lines;
}

function makeLine(ws: WordToken[], speaker: string, flaggedRanges: Array<[number, number]>, startIdx: number): TranscriptLine {
  const text = ws.map((w) => w.word).join(" ");
  const startSec = ws[0]?.start ?? 0;
  const endSec = ws[ws.length - 1]?.end ?? startSec;
  const endIdx = startIdx + ws.length - 1;
  const flagged = flaggedRanges.some(([fs, fe]) => fs <= endIdx && fe >= startIdx);
  // Plan §5b: label transcript turns AGENT / CUSTOMER (loud) when the
  // backend's role-tagging populated the role; fall back to the legacy
  // "Speaker N" only when role is missing. ``speaker`` here is already the
  // resolved key from _speakerKeyOf (AGENT/CUSTOMER or a raw id).
  const upper = (speaker || "").toUpperCase().trim();
  const isRoleLabel = upper === "AGENT" || upper === "CUSTOMER";
  const speakerIdx = isRoleLabel ? (upper === "AGENT" ? 1 : 2) : letterToIdx(speaker);
  const who = isRoleLabel ? upper : `Speaker ${speakerIdx}`;
  return {
    who,
    speakerIdx,
    startSec,
    endSec,
    timestamp: fmtTime(startSec),
    text,
    words: ws,
    flagged,
    checkpoint: null,
  };
}

/**
 * Parse the unredacted `transcript` field (format: "[MM:SS] Agent: text\n[MM:SS] Customer: text\n...")
 * into TranscriptLine[]. This source preserves real person names from the
 * audio (the AssemblyAI words[] endpoint returns redacted [PERSON_NAME] tokens
 * which is not what reviewers want to read).
 *
 * Speaker labels in the transcript text ("Agent" / "Customer") are mapped to
 * neutral Speaker 1 / Speaker 2 to match the rest of the UI.
 */
function parseTranscriptText(transcript: string): TranscriptLine[] {
  if (!transcript) return [];
  const linePattern = /\[(\d{1,2}):(\d{2})\]\s*(Agent|Customer|Speaker\s*\d+)?:?\s*(.*)/i;
  const out: TranscriptLine[] = [];
  const rawLines = transcript.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  for (let i = 0; i < rawLines.length; i++) {
    const m = rawLines[i].match(linePattern);
    if (!m) continue;
    const mm = Number(m[1]);
    const ss = Number(m[2]);
    const startSec = mm * 60 + ss;
    const speakerLabel = (m[3] || "").toLowerCase();
    let speakerIdx = 1;
    if (speakerLabel.startsWith("customer")) speakerIdx = 2;
    else if (speakerLabel.startsWith("agent")) speakerIdx = 1;
    else if (/speaker\s*(\d+)/i.test(speakerLabel)) {
      speakerIdx = Math.max(1, Math.min(4, Number(speakerLabel.match(/(\d+)/)?.[1] ?? 1)));
    } else {
      speakerIdx = (i % 2) + 1;
    }
    const text = (m[4] || "").trim();
    if (!text) continue;
    // endSec: peek next line's timestamp; fall back to start + 10s
    const next = rawLines[i + 1]?.match(linePattern);
    const endSec = next ? Number(next[1]) * 60 + Number(next[2]) : startSec + 10;
    out.push({
      who: `Speaker ${speakerIdx}`,
      speakerIdx,
      startSec,
      endSec,
      timestamp: fmtTime(startSec),
      text,
      words: [],
      flagged: false,
      checkpoint: null,
    });
  }
  // If parsing failed entirely (transcript not in [MM:SS] format), fall back
  // to a naive paragraph split with alternating speakers.
  if (out.length === 0) {
    const blocks = transcript.split(/\n\n+/).slice(0, 30);
    return blocks.map((block, i) => {
      const speakerIdx = (i % 2) + 1;
      return {
        who: `Speaker ${speakerIdx}`,
        speakerIdx,
        startSec: i * 10,
        endSec: (i + 1) * 10,
        timestamp: fmtTime(i * 10),
        text: block.trim(),
        words: [],
        flagged: false,
        checkpoint: null,
      } as TranscriptLine;
    });
  }
  return out;
}

// Backwards-compat alias: callers used `fallbackLines` previously.
const fallbackLines = parseTranscriptText;

// TranscriptLineRow removed — replaced by `TranscriptPlayer` (per-word
// click-to-seek + double-click edit + karaoke). Speaker turn grouping is
// now owned by the new component. Line-level state on this page (`lines`,
// `visibleLines`, `flagMarkerPercents`, the "Flagged only" toggle, and
// `spotlightLine`) is still used by the audio waveform tickers and the
// chat citation chip lookup, so the line-derivation memos remain in place.


export default function CallDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const router = useRouter();
  const { id } = use(params);

  // 2026-05-16 — subscribe to per-call SSE feed. Replaces the 3s in-flight
  // refetchInterval on useCallDetailQuery / useCallCheckpointsQuery so audio
  // stays mounted across pipeline-step transitions (no re-mount, no reset).
  useCallEvents(id);

  const meQ = useMe();
  const detail = useCallDetailQuery(id);
  // 2026-05-26 — pass call.status down so dependent queries can safety-
  // net poll while the pipeline is in-flight. Without this, useCallWords
  // returns 404 once mid-pipeline (file not written yet), retries 0
  // times per its retry policy, and the cache stays empty forever —
  // leaving the ProcessingStepper visible after the call finalized.
  const wordsQuery = useCallWordsQuery(id, detail.data?.status);
  const flagsQuery = useCallFlagsQuery(id);
  const checkpointsQuery = useCallCheckpointsQuery(id, detail.data?.status);
  const audioUrlQuery = useCallAudioUrlQuery(id);
  const submitVerdict = useSubmitVerdict();
  const agentChat = useAgentChat();
  const reviewCheckpoint = useReviewCheckpoint();
  const retryCheckpoint = useRetryCheckpoint();
  const claimCall = useClaimCall();
  // useReleaseCall intentionally NOT used here — we fire release via a
  // direct fetch({ keepalive: true }) in the effect cleanup below. See
  // the comment on releaseClaim below for the smoke-test evidence (2026-
  // 05-16 T2) showing the mutation-hook path drops requests on unmount.

  // Claim lifecycle (2026-05-16 audit P0 #2). The page opens in
  // "Reviewing" mode visually — back this with a real claim so two
  // reviewers can't double-work the same call. 409 → read-only banner,
  // reviewer can take over or back out.
  const [claimSessionId, setClaimSessionId] = useState<string | null>(null);
  const [claimReadOnly, setClaimReadOnly] = useState(false);
  const [claimConflictBy, setClaimConflictBy] = useState<string | null>(null);
  // claimedRef gates ONE in-flight or settled claim per page mount.
  // claimSessionRef captures the session_id as soon as onSuccess fires so the
  // unmount cleanup can release it even if React 18 strict-mode tore down the
  // component between mutate() and onSuccess.
  const claimedRef = useRef<boolean>(false);
  const claimSessionRef = useRef<string | null>(null);

  const [tab, setTab] = useState<"checkpoints" | "verdict" | "chat">("checkpoints");

  // Plan §5b extension (2026-05-14): fetch segments at page level so the
  // TranscriptPlayer can draw segment dividers; SegmentChips + SegmentCards
  // share the cache via the same query key.
  type _SegRow = {
    id: string;
    idx: number;
    stage: string;
    start_s: number | null;
    end_s: number | null;
  };
  const segmentsQuery = useQuery({
    queryKey: ["call", id, "segments"] as const,
    queryFn: () =>
      apiFetch<{ segments: _SegRow[] }>(
        `/api/calls/${encodeURIComponent(id)}/segments`,
      ),
    enabled: !!id,
    staleTime: 30_000,
  });

  // 2026-05-24 audit — `EditMetadataDialog` was rendered with `deal={null}`
  // so every supplier / MPAN / value / live-date / term / docusign edit
  // landed only on the Call row; the backend then silently no-op'd the
  // deal-side fields. Fetch the deal here so the dialog receives the
  // canonical values and the change-only payload picks up real diffs.
  const dealId: string | null = (detail.data as { deal_id?: string | null } | undefined)?.deal_id ?? null;
  type _DealSeed = {
    customer_name?: string | null;
    supplier?: string | null;
    mpan_or_mprn?: string | null;
    expected_live_date?: string | null;
    deal_value_gbp?: number | null;
    term_months?: number | null;
    notes?: string | null;
  };
  const dealQuery = useQuery({
    queryKey: ["deal", dealId ?? "none"] as const,
    queryFn: () => apiFetch<_DealSeed>(`/api/deals/${encodeURIComponent(dealId ?? "")}`),
    enabled: !!dealId,
    staleTime: 30_000,
  });
  // Plan §5b: top-row pill filter restored (Pass / Partial / Non-Compliant)
  // with counts. Click a chip → narrow the checkpoint list to that status.
  const [cpFilter, setCpFilter] = useState<"all" | "passed" | "partial" | "fail" | "na">("all");
  const [chosen, setChosen] = useState<VerdictAction | null>(null);
  const [committed, setCommitted] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([]);
  const [paused, setPaused] = useState(true);
  const [currentSec, setCurrentSec] = useState(0);
  const [showOnlyFlagged, setShowOnlyFlagged] = useState(false);
  const [speed, setSpeed] = useState<number>(1);
  const [editOpen, setEditOpen] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const transcriptScrollRef = useRef<HTMLDivElement | null>(null);
  const lineRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  // Throttle auto-scroll to ~5fps so the playhead doesn't constantly fight
  // the user's manual scrolling. lastAutoScrollRef tracks both the line key
  // we last scrolled to and the timestamp to debounce subsequent calls.
  const lastAutoScrollRef = useRef<{ key: string | null; at: number }>({
    key: null,
    at: 0,
  });

  const c = detail.data;
  const qc = useQueryClient();

  // Words endpoint 404s until pipeline writes word_data. wordsQuery has
  // staleTime=30min + retry<1 + refetchOnMount=false, so the cached 404
  // sticks even after pipeline completes → ProcessingStepper never hides.
  // Invalidate words query once status flips to completed.
  useEffect(() => {
    if (c?.status === "completed" && wordsQuery.data === undefined) {
      qc.invalidateQueries({ queryKey: reviewerKeys.callWords(id) });
    }
  }, [c?.status, wordsQuery.data, qc, id]);

  // Claim the call on mount, release on unmount.
  //
  // Lifecycle invariants:
  // 1. claimedRef.current flips to true ONLY inside onSuccess — so a transient
  //    network failure does NOT permanently block retries (C2). On non-409
  //    error we leave claimedRef=false so the next effect run can try again
  //    if the component remounts. The mutation hook itself surfaces the toast.
  // 2. The session_id is mirrored into claimSessionRef as soon as onSuccess
  //    fires. Cleanup reads from the ref, NOT from a captured `let`, so it
  //    releases the lock even if React 18 strict-mode tore down the component
  //    between mutate() and onSuccess (C1).
  // 3. We skip the claim entirely for terminal-state calls (committed /
  //    compliant / non_compliant) — claiming makes no sense there and would
  //    cause spurious 4xx + a read-only banner (H6).
  // 4. The auto-claim path uses the mutation hooks' { silent: true } option so
  //    the page-mount doesn't pop "Call claimed" + "Released review session"
  //    toasts on every navigation (H5).
  const terminalStatus =
    c?.status === "committed" ||
    c?.compliance_status === "compliant" ||
    c?.compliance_status === "non_compliant";

  // releaseClaim fires a fire-and-forget release POST. We deliberately do
  // NOT use the useReleaseCall mutation hook here because calling
  // `mutate(...)` from inside a useEffect cleanup or a pagehide listener
  // can drop the request — the mutation observer is torn down faster than
  // TanStack Query queues the fetch. Direct fetch with `keepalive: true`
  // lets the browser deliver the POST even after the document unloads
  // (hard tab close, Next.js router navigation, page reload).
  //
  // The Authorization header is supplied via cookie (credentials: "include").
  // No response handling — backend logs the release on its side; the UI
  // doesn't need to wait for it.
  const releaseClaim = useCallback((sessionId: string) => {
    if (!sessionId) return;
    const base = process.env.NEXT_PUBLIC_API_URL || "";
    const url = `${base}/api/review-sessions/${encodeURIComponent(sessionId)}/release`;
    try {
      void fetch(url, {
        method: "POST",
        credentials: "include",
        keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
    } catch {
      // sendBeacon as ultimate fallback (no body, but the URL is enough
      // for the backend to release the session).
      if (typeof navigator !== "undefined" && navigator.sendBeacon) {
        navigator.sendBeacon(url);
      }
    }
  }, []);

  useEffect(() => {
    if (!id || claimedRef.current || terminalStatus) return;
    claimCall.mutate(
      { callId: id, silent: true },
      {
        onSuccess: (data) => {
          claimedRef.current = true;
          // Backend returns review_session_id; fall back to session_id for safety.
          const sid = data.review_session_id ?? data.session_id ?? null;
          claimSessionRef.current = sid;
          setClaimSessionId(sid);
          setClaimReadOnly(false);
          setClaimConflictBy(null);
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 409) {
            // 409 = another reviewer holds the lock → page is read-only;
            // flip the ref so we don't keep retrying.
            claimedRef.current = true;
            let by: string | null = null;
            try {
              const parsed = JSON.parse(err.body) as { detail?: string; claimed_by?: string };
              by = parsed.claimed_by ?? parsed.detail ?? null;
            } catch {
              /* body not JSON — leave by=null */
            }
            setClaimConflictBy(by);
            setClaimReadOnly(true);
          }
          // For non-409 (network blip, 5xx) leave claimedRef=false so a
          // remount can retry. The mutation hook surfaces an error toast.
        },
      },
    );

    // pagehide handler: covers hard browser close + cross-origin nav.
    // The useEffect cleanup below covers within-app router.push() nav.
    const onPageHide = () => {
      const sid = claimSessionRef.current;
      if (sid) {
        releaseClaim(sid);
        claimSessionRef.current = null;
      }
    };
    window.addEventListener("pagehide", onPageHide);

    return () => {
      window.removeEventListener("pagehide", onPageHide);
      const sid = claimSessionRef.current;
      if (sid) {
        // Direct fetch (not the mutation hook) so the request fires reliably
        // when the cleanup runs during a router.push navigation — the smoke
        // test 2026-05-16 T2 found that releaseCall.mutate() was being torn
        // down before the fetch queued, leaving 30-min orphan locks on every
        // call viewed by a reviewer who navigates away.
        releaseClaim(sid);
        claimSessionRef.current = null;
      }
    };
    // We intentionally depend on id + terminalStatus only — claimCall
    // identity changes every render and we do NOT want to re-claim on
    // every render. releaseClaim is stable (useCallback with empty deps).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, terminalStatus]);

  const words = wordsQuery.data?.words ?? [];
  const flags = flagsQuery.data?.flags ?? [];
  const checkpoints = checkpointsQuery.data?.checkpoints ?? [];

  // Adapt v3's WordToken[] (string speaker, optional confidence) to the
  // WordData[] shape main's TranscriptPlayer expects (numeric speaker,
  // mandatory punctuated_word + confidence). letterToIdx returns 1-based
  // indices; subtract 1 so Agent → 0, Customer → 1 (TranscriptPlayer's
  // SPEAKER_STYLES uses 0 = Agent, 1 = Customer).
  const wordsForPlayer = useMemo<WordData[]>(
    () =>
      // Plan §5b: prefer backend-resolved role so the TranscriptPlayer's
      // SPEAKER_STYLES (0 = AGENT, 1 = CUSTOMER) is keyed off the AI's
      // identification instead of raw diarisation. Falls back to the
      // letter-derived index when role is missing.
      words.map((w) => {
        const r = (w.role ? String(w.role) : "").toUpperCase().trim();
        const speakerIdx0 =
          r === "AGENT"
            ? 0
            : r === "CUSTOMER"
              ? 1
              : Math.max(0, letterToIdx(w.speaker) - 1);
        return {
          word: w.word,
          punctuated_word: w.word,
          start: w.start,
          end: w.end,
          speaker: speakerIdx0,
          confidence: w.confidence ?? 1.0,
        };
      }),
    [words],
  );
  const flaggedRanges = useMemo<Array<[number, number]>>(
    () =>
      flags
        .filter((f) => typeof f.word_start === "number" && typeof f.word_end === "number")
        .map((f) => [f.word_start, f.word_end] as [number, number]),
    [flags],
  );
  const lines = useMemo(() => {
    // Backend returns the unredacted transcript on `c.transcript` (real person
    // names preserved) but the AssemblyAI words[] endpoint applies PII
    // redaction so word.word is "[PERSON_NAME]" wherever a name was spoken.
    // Reviewers want to read what was actually said, so we prefer the
    // unredacted transcript text. Word-level data still drives audio karaoke
    // sync via activeLineIdx + activeWordIdx, but the rendered text comes
    // from the transcript-parsed lines.
    const transcriptText = c?.transcript ?? "";
    if (transcriptText) return parseTranscriptText(transcriptText);
    if (words.length > 0) return buildLines(words, flaggedRanges);
    return [];
  }, [words, flaggedRanges, c?.transcript]);

  // Locate active line + active word for karaoke.
  const { activeLineIdx, activeWordIdx } = useMemo(() => {
    if (!words.length) return { activeLineIdx: -1, activeWordIdx: -1 };
    let li = -1;
    let wi = -1;
    for (let i = 0; i < lines.length; i++) {
      if (currentSec >= lines[i].startSec && currentSec <= lines[i].endSec) {
        li = i;
        const ws = lines[i].words;
        for (let j = 0; j < ws.length; j++) {
          if (currentSec >= ws[j].start && currentSec <= ws[j].end) {
            wi = j;
            break;
          }
        }
        break;
      }
    }
    return { activeLineIdx: li, activeWordIdx: wi };
  }, [currentSec, lines, words.length]);

  // Resolve duration in priority order:
  //   1) call.duration_seconds (populated from Deepgram's container probe)
  //   2) Deepgram metadata duration (older calls before that field was wired)
  //   3) end timestamp of the last transcribed word (when we have word_data)
  //   4) 0 — explicit "unknown" rather than a misleading fixed fallback
  const duration: number = (() => {
    if (typeof c?.duration_seconds === "number" && c.duration_seconds > 0) {
      return c.duration_seconds;
    }
    const dgMeta = (c as unknown as { deepgram_metadata?: { metadata?: { duration?: number } } } | undefined)?.deepgram_metadata;
    const dgDur = dgMeta?.metadata?.duration;
    if (typeof dgDur === "number" && dgDur > 0) return dgDur;
    if (words.length > 0) {
      const last = words[words.length - 1];
      if (typeof last?.end === "number" && last.end > 0) return last.end;
    }
    return 0;
  })();
  const playedPct = Math.min(100, Math.max(0, (currentSec / Math.max(1, duration)) * 100));

  // Flag tick percentages on the audio waveform (red 2×8 markers).
  // Derived from flagged lines' midpoint time / duration.
  const flagMarkerPercents = useMemo<number[]>(() => {
    const safeDur = Math.max(1, duration);
    return lines
      .filter((ln) => ln.flagged && Number.isFinite(ln.startSec) && Number.isFinite(ln.endSec))
      .map((ln) => Math.round(((ln.startSec + ln.endSec) / 2 / safeDur) * 100));
  }, [lines, duration]);

  // Visible transcript lines: when "Flagged only" toggle is on, hide
  // unflagged. A line is "flagged" if EITHER the backend emitted a Flag
  // covering its words (Ombudsman/Mis-selling/Vulnerable/etc) OR a
  // FAIL/PARTIAL checkpoint verdict quotes a substring of the line as
  // evidence. Without the checkpoint-excerpt fallback the toggle reads
  // "0 of N" on calls where the rule-based flags didn't fire but the AI
  // per-checkpoint verdict did. Lazy-init: verdicts are computed below
  // (line ~891) so we read them via a ref through a closure when needed.
  // (Forward-declared placeholder; actual values wired into the same memo
  // graph after `verdicts` exists.)

  function seekTo(sec: number) {
    setCurrentSec(sec);
    if (audioRef.current) audioRef.current.currentTime = sec;
  }

  /**
   * Seek audio + start playback. Used by the per-checkpoint Play button so
   * one click jumps to the excerpt's timestamp AND plays — reviewers asked
   * for the v1/v2 behaviour where the play button means "play it for me",
   * not just "scrub here".
   */
  function seekAndPlay(sec: number) {
    const safe = Math.max(0, sec);
    setCurrentSec(safe);
    if (!audioRef.current) return;
    audioRef.current.currentTime = safe;
    void audioRef.current.play().catch(() => {});
  }

  // ── Drag-to-scrub on the call-detail waveform ──────────────────────
  // Reviewer ask 2026-05-14: pointer-down anywhere on the waveform bar
  // → live drag the playhead to any timestamp. Releases commit the seek
  // and resume playback if audio was already playing when the drag began.
  const waveformWrapRef = useRef<HTMLDivElement | null>(null);
  const [scrubbing, setScrubbing] = useState(false);
  const wasPlayingDuringScrubRef = useRef(false);

  const scrubToClientX = useCallback(
    (clientX: number, commitAudio: boolean) => {
      const el = waveformWrapRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0) return;
      const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
      const frac = x / rect.width;
      const t = frac * (duration > 0 ? duration : 1);
      setCurrentSec(t);
      if (commitAudio && audioRef.current) {
        audioRef.current.currentTime = t;
      }
    },
    [duration],
  );

  useEffect(() => {
    if (!scrubbing) return;
    const onMove = (e: PointerEvent) => scrubToClientX(e.clientX, false);
    const onUp = (e: PointerEvent) => {
      scrubToClientX(e.clientX, true);
      setScrubbing(false);
      if (wasPlayingDuringScrubRef.current) {
        audioRef.current?.play().catch(() => {
          /* user gesture lost — leave paused */
        });
        wasPlayingDuringScrubRef.current = false;
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [scrubbing, scrubToClientX]);

  const startScrub = useCallback(
    (clientX: number) => {
      const a = audioRef.current;
      wasPlayingDuringScrubRef.current = !!a && !a.paused;
      if (a && !a.paused) a.pause();
      setScrubbing(true);
      scrubToClientX(clientX, true);
    },
    [scrubToClientX],
  );

  // ── Checkpoint card data merge ─────────────────────────────────────
  //
  // Three sources combined per checkpoint, matched by name (case-insensitive
  // trim):
  //   - Script defs: `useCallCheckpointsQuery` → rule_text + key_phrases +
  //     strictness + customer_response_required.
  //   - Verdict + reasoning: `c.checkpoint_results` JSON parsed once. This
  //     blob is the gold source — it has start_ms/end_ms (for seeking),
  //     status, evidence, and notes (the LLM's reasoning).
  //   - Word-level fallback: when start_ms is missing we fuzzy-match the
  //     first 3-5 words of the excerpt against `words[]`.
  //
  // Note: the dedicated /flags endpoint returns null word_start/word_end
  // for this dataset, so we don't rely on it for seeking — we go directly
  // to checkpoint_results.start_ms.
  const verdicts = useMemo(
    () => parseCheckpointResults(c?.checkpoint_results ?? null),
    [c?.checkpoint_results],
  );

  // ── Visible lines (depends on `verdicts` for the FAIL/PARTIAL excerpt
  // fallback) — see the long block-comment above `seekTo`.
  const flaggedExcerpts = useMemo<string[]>(() => {
    return verdicts
      .filter((v) => v.status !== "pass")
      .map((v) => (v.evidence ?? "").trim().toLowerCase())
      .filter((s) => s.length >= 8 && s !== "not found in transcript");
  }, [verdicts]);
  const visibleLines = useMemo(() => {
    if (!showOnlyFlagged) return lines;
    return lines.filter((ln) => {
      if (ln.flagged) return true;
      const hay = ln.text.toLowerCase();
      return flaggedExcerpts.some((needle) => hay.includes(needle));
    });
  }, [lines, showOnlyFlagged, flaggedExcerpts]);

  const cpCards = useMemo(() => {
    // Build the union of scripts + verdicts (in case verdicts have entries
    // not in the active script def, which can happen for legacy calls).
    type Merged = {
      key: string;
      script?: ScriptCheckpoint;
      verdict?: CheckpointVerdict;
      startSec: number | null;
      startSecEnd: number | null;
    };
    const norm = (s: string) => s.trim().toLowerCase();
    const verdictByName = new Map<string, CheckpointVerdict>();
    for (const v of verdicts) verdictByName.set(norm(v.name), v);

    const seen = new Set<string>();
    const merged: Merged[] = [];

    for (const sc of checkpoints) {
      const k = norm(sc.name);
      seen.add(k);
      const v = verdictByName.get(k);
      const { startSec, endSec } = computeCheckpointStart(v, words);
      merged.push({ key: k, script: sc, verdict: v, startSec, startSecEnd: endSec });
    }
    // Verdicts without a matching script (e.g. legacy schema) — append at end
    for (const v of verdicts) {
      const k = norm(v.name);
      if (seen.has(k)) continue;
      const { startSec, endSec } = computeCheckpointStart(v, words);
      merged.push({ key: k, verdict: v, startSec, startSecEnd: endSec });
    }
    return merged;
  }, [checkpoints, verdicts, words]);

  /**
   * Active checkpoint — the one whose [startSec, endSec] window contains
   * the current playhead. Used to highlight that card while audio plays.
   * `endSec` falls back to startSec + 8s when only the start is known so
   * a single point gets a small active window rather than zero.
   */
  const activeCheckpointKey = useMemo<string | null>(() => {
    for (const m of cpCards) {
      if (m.startSec == null) continue;
      const end = m.startSecEnd ?? m.startSec + 8;
      if (currentSec >= m.startSec && currentSec <= end) return m.key;
    }
    return null;
  }, [cpCards, currentSec]);

  function registerLineRef(key: string, el: HTMLDivElement | null) {
    const map = lineRefs.current;
    if (el) map.set(key, el);
    else map.delete(key);
  }

  /**
   * Apply the active-line spotlight + scroll the matching transcript line
   * into view. Used by both karaoke (auto-follow) and the chat citation
   * chips. Throttled by `key` so we don't fight a user who scrolls away.
   */
  function spotlightLine(lineId: string, opts: { smooth?: boolean } = {}) {
    const el = lineRefs.current.get(lineId);
    if (!el) return;
    const map = lineRefs.current;
    map.forEach((node) => node.removeAttribute("data-call-line-active"));
    el.setAttribute("data-call-line-active", "true");
    el.scrollIntoView({
      behavior: opts.smooth === false ? "auto" : "smooth",
      block: "center",
    });
  }

  // Karaoke auto-follow — when the active line changes (~once per spoken
  // line, not per word), nudge it into view. ~5fps cap so a fast playback
  // doesn't trigger a cascade of scrollIntoView calls.
  useEffect(() => {
    if (activeLineIdx < 0) return;
    const lineId = `T${activeLineIdx + 1}`;
    const last = lastAutoScrollRef.current;
    const now = performance.now();
    if (last.key === lineId && now - last.at < 200) return;
    lastAutoScrollRef.current = { key: lineId, at: now };
    spotlightLine(lineId, { smooth: true });
  }, [activeLineIdx]);

  if (detail.isError) {
    const errMsg =
      detail.error instanceof ApiError
        ? `${detail.error.status} ${detail.error.body?.slice(0, 200) ?? ""}`
        : (detail.error as Error)?.message ?? "Unknown error";
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          fontSize: 13,
          color: "var(--red)",
          gap: 8,
          padding: 24,
          textAlign: "center",
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 600 }}>Failed to load call</div>
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--text-muted)",
            maxWidth: 640,
            wordBreak: "break-word",
          }}
        >
          {errMsg}
        </div>
        <button
          type="button"
          onClick={() => detail.refetch()}
          style={{
            marginTop: 8,
            padding: "6px 14px",
            fontSize: 12,
            background: "var(--bg-elev3)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
            color: "var(--text-primary)",
            cursor: "pointer",
          }}
        >
          Retry
        </button>
      </div>
    );
  }
  // Initial mount: wait for first fetch instead of falsely flagging "no data"
  if (detail.isPending || !c) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          fontSize: 13,
          color: "var(--text-muted)",
        }}
      >
        Loading call…
      </div>
    );
  }

  const score = c?.score ?? "—";
  const pct = formatScorePercent(c?.score ?? null);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", minWidth: 0 }}>
      {/* Top bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "12px 20px",
          borderBottom: "1px solid var(--border-subtle)",
          flexShrink: 0,
        }}
      >
        <button
          onClick={() => router.push("/queue")}
          style={{
            height: 28,
            padding: "0 10px",
            background: "transparent",
            border: "none",
            color: "var(--text-muted)",
            fontSize: 12,
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            fontFamily: "inherit",
          }}
        >
          <ArrowLeft size={14} />
          Queue
        </button>
        <div style={{ width: 1, height: 18, background: "var(--border-subtle)" }} />
        <div style={{ display: "flex", flexDirection: "column", minWidth: 0, maxWidth: 360 }}>
          <span
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: "var(--text-primary)",
              letterSpacing: "-0.01em",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
            title={c?.filename ?? id}
          >
            {formatCustomerName(c?.customer_name)}
            {isPlaceholderCustomerName(c?.customer_name) && (
              <span
                style={{
                  marginLeft: 6,
                  padding: "1px 5px",
                  fontSize: 9.5,
                  fontWeight: 500,
                  textTransform: "uppercase",
                  color: "#92400e",
                  background: "#fef3c7",
                  borderRadius: 3,
                  verticalAlign: "middle",
                }}
                title="AI couldn't read the customer name from this audio. Edit it via the Edit metadata dialog."
              >
                AI couldn&apos;t read
              </span>
            )}
          </span>
          <span
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {[
              c?.detected_supplier ?? "supplier pending",
              `agent ${c?.agent_name ?? "(no agent detected)"}`,
              c?.filename,
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </div>
        <WorkflowTypePill supplier={c?.detected_supplier ?? null} compact />
        {/* Plan §5b extension (2026-05-14): inline AI-detected segment chips. */}
        <SegmentChips callId={id} />
        {committed ? (
          <Pill tone="emerald" dot>
            Committed
          </Pill>
        ) : claimReadOnly ? (
          <Pill tone="red" dot>
            Read-only · claimed by {claimConflictBy ?? "another reviewer"}
          </Pill>
        ) : claimSessionId ? (
          <Pill tone="amber" dot>
            Reviewing
          </Pill>
        ) : (
          <Pill tone="amber">Claiming…</Pill>
        )}
        <button
          type="button"
          onClick={() => setEditOpen(true)}
          className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-2.5 py-1 text-[11px] hover:bg-[var(--bg-elev2)]"
          title="Edit customer name, agent, MPAN, supplier, and other metadata"
        >
          ✎ Edit metadata
        </button>
        <ReanalyzeButton callId={id} />
        <div style={{ flex: 1 }} />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "5px 12px",
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
          }}
        >
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--text-primary)" }}>
            {score}
          </span>
          <div
            style={{
              width: 60,
              height: 4,
              background: "var(--bg-elev4)",
              borderRadius: 2,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: pct,
                height: "100%",
                background: parseInt(pct) >= 80 ? "var(--emerald)" : "var(--amber)",
                borderRadius: 2,
              }}
            />
          </div>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-muted)" }}>
            {pct}
          </span>
        </div>
        <button
          type="button"
          onClick={() => {
            detail.refetch();
            checkpointsQuery.refetch();
            qc.invalidateQueries({ queryKey: ["call", id, "segments"] });
          }}
          title="Refresh call detail + checkpoints"
          style={{
            height: 28,
            padding: "0 10px",
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            color: "var(--text-primary)",
            borderRadius: 6,
            fontSize: 12,
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            cursor: "pointer",
            fontFamily: "inherit",
          }}
        >
          <RefreshCw size={12} />
          Retry
        </button>
        <button
          type="button"
          disabled
          title="Export — coming soon"
          aria-disabled
          style={{
            height: 28,
            padding: "0 10px",
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            color: "var(--text-muted)",
            borderRadius: 6,
            fontSize: 12,
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            cursor: "not-allowed",
            opacity: 0.6,
            fontFamily: "inherit",
          }}
        >
          <Download size={12} />
          Export
        </button>
      </div>

      {/* W3.A — pricing mismatch banner stacks above the master/detail grid */}
      <PricingMismatchBanner
        flags={flags}
        onSeek={(f) => {
          // word_start is an index into the words[] array. Look up the
          // matching token's start time; fall back to t=0 if missing.
          const idx = typeof f.word_start === "number" ? f.word_start : -1;
          const word = idx >= 0 && idx < words.length ? words[idx] : null;
          const sec = word && Number.isFinite(word.start) ? word.start : 0;
          seekAndPlay(sec);
        }}
      />

      {/* W3.C — vulnerable customer banner. Renders only when the
          extraction pipeline emitted a VULNERABLE_CUSTOMER flag.
          Stacks below the W3.A pricing-mismatch banner when both fire. */}
      <VulnerabilityBanner flags={flags} />

      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateColumns: "60% 40%",
          overflow: "hidden",
          minHeight: 0,
        }}
      >
        {/* LEFT: audio + transcript */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            borderRight: "1px solid var(--border-subtle)",
            minWidth: 0,
          }}
        >
          {/* Audio player */}
          <div
            style={{
              padding: "16px 20px",
              background: "var(--bg-elev2)",
              borderBottom: "1px solid var(--border-subtle)",
              display: "flex",
              flexDirection: "column",
              gap: 12,
              flexShrink: 0,
            }}
          >
            <div
              ref={waveformWrapRef}
              role="slider"
              aria-label="Audio playhead"
              aria-valuemin={0}
              aria-valuemax={Math.round(duration)}
              aria-valuenow={Math.round(currentSec)}
              tabIndex={0}
              data-testid="call-waveform"
              style={{
                position: "relative",
                height: 48,
                cursor: scrubbing ? "grabbing" : "grab",
                touchAction: "none",
                userSelect: "none",
              }}
              onPointerDown={(e) => {
                // 2026-05-14: reviewer-requested drag scrubbing. Click and
                // drag anywhere on the waveform bar to move the playhead.
                // Pointer capture lets the drag continue even if the cursor
                // exits the bar's bounds.
                e.currentTarget.setPointerCapture?.(e.pointerId);
                startScrub(e.clientX);
              }}
              onKeyDown={(e) => {
                const step = e.shiftKey ? 15 : 5;
                if (e.key === "ArrowRight") {
                  e.preventDefault();
                  seekAndPlay(Math.min(duration, currentSec + step));
                } else if (e.key === "ArrowLeft") {
                  e.preventDefault();
                  seekTo(Math.max(0, currentSec - step));
                }
              }}
            >
              <Waveform
                played={Math.floor((playedPct / 100) * 140)}
                total={140}
                height={40}
                showPlayhead
                playedPct={playedPct}
              />
              {/* Flag markers — red 2×8 ticks at flagged percent positions */}
              {flagMarkerPercents.map((pct, i) => (
                <div
                  key={`flag-${i}-${pct}`}
                  aria-label="flagged segment"
                  className="pointer-events-none absolute"
                  style={{
                    left: `${Math.max(0, Math.min(100, pct))}%`,
                    top: 0,
                    width: 2,
                    height: 8,
                    background: "var(--red, #ef4444)",
                    borderRadius: 1,
                  }}
                />
              ))}
            </div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "0 2px",
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                color: "var(--text-faint)",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              <span>0:00</span>
              <span>{fmtTime(duration / 4)}</span>
              <span>{fmtTime(duration / 2)}</span>
              <span>{fmtTime((duration * 3) / 4)}</span>
              <span>{fmtTime(duration)}</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <button
                type="button"
                onClick={() => seekTo(Math.max(0, currentSec - 10))}
                style={{
                  height: 32,
                  padding: "0 10px",
                  background: "var(--bg-elev3)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: 6,
                  color: "var(--text-primary)",
                  fontSize: 11,
                  fontFamily: "var(--font-mono)",
                  cursor: "pointer",
                }}
              >
                −10s
              </button>
              <button
                type="button"
                onClick={() => {
                  setPaused((p) => !p);
                  if (audioRef.current) {
                    if (paused) void audioRef.current.play().catch(() => {});
                    else audioRef.current.pause();
                  }
                }}
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: "50%",
                  background: paused ? "var(--bg-elev3)" : "var(--emerald)",
                  border: paused ? "1px solid var(--emerald-border)" : "none",
                  color: paused ? "var(--emerald)" : "#04201a",
                  display: "grid",
                  placeItems: "center",
                  cursor: "pointer",
                  boxShadow: paused
                    ? "0 0 0 3px rgba(16,185,129,0.18), var(--shadow-md)"
                    : "var(--shadow-md), inset 0 1px 0 rgba(255,255,255,0.2)",
                }}
              >
                {paused ? <Play size={18} fill="currentColor" /> : <Pause size={18} fill="currentColor" />}
              </button>
              <button
                type="button"
                onClick={() => seekTo(Math.min(duration, currentSec + 10))}
                style={{
                  height: 32,
                  padding: "0 10px",
                  background: "var(--bg-elev3)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: 6,
                  color: "var(--text-primary)",
                  fontSize: 11,
                  fontFamily: "var(--font-mono)",
                  cursor: "pointer",
                }}
              >
                +10s
              </button>
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 13,
                  color: "var(--text-primary)",
                  marginLeft: 4,
                }}
              >
                {fmtTime(currentSec)}{" "}
                <span style={{ color: "var(--text-faint)" }}>/ {fmtTime(duration)}</span>
              </div>
              <div style={{ flex: 1 }} />
              <select
                value={speed}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  setSpeed(n);
                  if (audioRef.current) audioRef.current.playbackRate = n;
                }}
                aria-label="Playback speed"
                style={{
                  height: 28,
                  padding: "0 24px 0 8px",
                  background: "var(--bg-elev3)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: 6,
                  color: "var(--text-primary)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  cursor: "pointer",
                  appearance: "none",
                  WebkitAppearance: "none",
                  backgroundImage:
                    "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2371717a' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>\")",
                  backgroundRepeat: "no-repeat",
                  backgroundPosition: "right 8px center",
                }}
              >
                {[0.5, 0.75, 1, 1.25, 1.5, 2].map((s) => (
                  <option key={s} value={s} style={{ background: "var(--bg-elev2)" }}>
                    {s.toFixed(s === 1 ? 1 : 2).replace(/\.0+$/, ".0").replace(/\.([1-9])0$/, ".$1")}×
                  </option>
                ))}
              </select>
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: 12,
                  color: "var(--text-muted)",
                  cursor: "pointer",
                  userSelect: "none",
                }}
              >
                <input
                  type="checkbox"
                  checked={showOnlyFlagged}
                  onChange={(e) => setShowOnlyFlagged(e.target.checked)}
                  style={{
                    width: 14,
                    height: 14,
                    accentColor: "var(--amber-review, #f59e0b)",
                    cursor: "pointer",
                  }}
                  aria-label="Show only flagged transcript lines"
                />
                Flagged only
                {showOnlyFlagged && (
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                      color: "var(--text-faint)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    ({visibleLines.length} of {lines.length})
                  </span>
                )}
              </label>
            </div>
            <audio
              ref={audioRef}
              // 2026-05-16 perf — prefer the audio_url baked into the
              // detail response so we don't wait on the second RTT to
              // /audio-url. Falls back to the dedicated endpoint for
              // legacy callers or when the inline URL expires (50min
              // staleTime on the dedicated endpoint re-issues a fresh
              // signed URL well before the 1hr Supabase TTL).
              src={c?.audio_url ?? audioUrlQuery.data?.url ?? undefined}
              preload="metadata"
              onTimeUpdate={() => audioRef.current && setCurrentSec(audioRef.current.currentTime)}
              onPause={() => setPaused(true)}
              onPlay={() => setPaused(false)}
            />
            {!c?.audio_url && audioUrlQuery.isError && (
              <div
                style={{
                  fontSize: 11,
                  color: "var(--text-faint)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                No audio for this call (legacy upload).
              </div>
            )}
          </div>

          {/* Transcript */}
          <div
            ref={transcriptScrollRef}
            style={{
              flex: 1,
              overflowY: "auto",
              padding: "12px 16px",
              display: "flex",
              flexDirection: "column",
              gap: 4,
              minHeight: 0,
            }}
            className="ca-scroll"
          >
            {wordsForPlayer.length === 0 ? (
              <ProcessingStepper call={c} checkpointsCount={checkpoints.length} />
            ) : null}
            {c ? (
              <div style={{ padding: "0 16px 12px" }}>
                <PipelineTimeline call={c as never} />
              </div>
            ) : null}
            {/* 2026-05-18: transcript-agreement + diarization chips removed
                from the reviewer surface per user request. */}
            {wordsForPlayer.length > 0 ? (
              <TranscriptPlayer
                words={wordsForPlayer}
                segments={segmentsQuery.data?.segments ?? []}
                currentTime={currentSec}
                onWordClick={seekTo}
                callId={id}
                agentName={c?.agent_name ?? null}
                customerName={c?.customer_name ?? null}
                onConflict={() => {
                  // 409 from edit-word — refetch words + detail so the
                  // optimistic edit is replaced with the server-side truth.
                  void wordsQuery.refetch();
                  void detail.refetch();
                }}
              />
            ) : null}
          </div>
        </div>

        {/* RIGHT: tabs */}
        <div
          style={{
            background: "var(--bg-elev1)",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          {/* Tab strip */}
          <div
            style={{
              display: "flex",
              borderBottom: "1px solid var(--border-subtle)",
              paddingLeft: 8,
              flexShrink: 0,
            }}
          >
            {(
              [
                { key: "checkpoints", label: "Checkpoints", count: cpCards.length, disabled: false },
                { key: "verdict", label: "Verdict", count: null, disabled: false },
                { key: "chat", label: "Chat", count: null, disabled: true },
              ] as const
            ).map((t) => {
              const active = tab === t.key;
              const disabled = t.disabled;
              return (
                <button
                  key={t.key}
                  onClick={() => {
                    // 2026-05-14: Chat is gated behind "Coming soon" until the
                    // backend RAG endpoint is wired. Click is a no-op so the
                    // empty/unbuilt tab body never renders.
                    if (disabled) return;
                    setTab(t.key);
                  }}
                  disabled={disabled}
                  aria-disabled={disabled}
                  title={disabled ? "Coming soon" : undefined}
                  style={{
                    padding: "12px 14px",
                    fontSize: 13,
                    fontWeight: 500,
                    color: disabled
                      ? "var(--text-faint)"
                      : active
                        ? "var(--text-primary)"
                        : "var(--text-muted)",
                    borderBottom: `2px solid ${active && !disabled ? "var(--emerald)" : "transparent"}`,
                    marginBottom: -1,
                    cursor: disabled ? "not-allowed" : "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    background: "transparent",
                    border: "none",
                    fontFamily: "inherit",
                    opacity: disabled ? 0.55 : 1,
                  }}
                >
                  {t.label}
                  {t.count != null && (
                    <span
                      style={{
                        fontSize: 11,
                        color: "var(--text-faint)",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {t.count}
                    </span>
                  )}
                  {disabled && (
                    <span
                      style={{
                        fontSize: 9,
                        fontWeight: 700,
                        padding: "1px 6px",
                        borderRadius: 999,
                        background: "var(--bg-elev3)",
                        color: "var(--text-faint)",
                        textTransform: "uppercase",
                        letterSpacing: "0.06em",
                        marginLeft: 2,
                      }}
                    >
                      Coming soon
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }} className="ca-scroll">
            {tab === "checkpoints" && (
              <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
                {(() => {
                  // Plan §5b: status filter pills with counts. The pills now
                  // SCOPE the nested CheckpointCards inside each SegmentCard.
                  const counts = cpCards.reduce(
                    (acc, m) => {
                      const s = (m.verdict?.status ?? "").toLowerCase();
                      if (s === "pass") acc.passed++;
                      else if (s === "partial") acc.partial++;
                      else if (s === "fail") acc.fail++;
                      else if (s === "" || s === "na" || s === "skipped" || s === "unscored" || s === "not_scored") acc.na++;
                      // Unknown statuses (error / pending / future enums) are
                      // intentionally NOT counted — they should surface as a
                      // missing-row total instead of silently inflating N/A.
                      return acc;
                    },
                    { passed: 0, partial: 0, fail: 0, na: 0 },
                  );
                  // Audit 2026-05-16 P1 #9: previously `All` = cpCards.length
                  // while Passed+Partial+Fail summed only scored CPs, so the
                  // pills didn't add up (e.g. All 113 / 63+6+19=88, 25 hidden).
                  // Expose an explicit N/A pill so reviewers can see the
                  // skipped/unscored category instead of silently dropping it.
                  const chips: { key: typeof cpFilter; label: string; n: number; tone: string }[] = [
                    { key: "all", label: "All", n: cpCards.length, tone: "var(--text-primary)" },
                    { key: "passed", label: "Passed", n: counts.passed, tone: "var(--emerald)" },
                    { key: "partial", label: "Partial", n: counts.partial, tone: "var(--amber)" },
                    { key: "fail", label: "Non-Compliant", n: counts.fail, tone: "var(--red)" },
                    ...(counts.na > 0
                      ? [{ key: "na" as const, label: "N/A", n: counts.na, tone: "var(--text-faint)" }]
                      : []),
                  ];
                  return (
                    <div style={{ display: "flex", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
                      {chips.map((c) => {
                        const active = cpFilter === c.key;
                        return (
                          <button
                            key={c.key}
                            type="button"
                            onClick={() => setCpFilter(c.key)}
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 6,
                              height: 28,
                              padding: "0 10px",
                              fontSize: 12,
                              fontWeight: 500,
                              background: active ? "var(--bg-elev3)" : "var(--bg-elev2)",
                              color: active ? c.tone : "var(--text-muted)",
                              border: `1px solid ${active ? c.tone : "var(--border-subtle)"}`,
                              borderRadius: 999,
                              cursor: "pointer",
                            }}
                          >
                            <span>{c.label}</span>
                            <span
                              style={{
                                fontFamily: "var(--font-mono)",
                                fontSize: 11,
                                fontVariantNumeric: "tabular-nums",
                                color: active ? c.tone : "var(--text-faint)",
                              }}
                            >
                              {c.n}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  );
                })()}

                {/* Per-segment expandable cards with nested CheckpointCards.
                    Replaces the old flat checkpoint list — every checkpoint
                    now lives INSIDE its parent segment. (2026-05-14) */}
                {cpCards.length === 0 ? (
                  <div style={{ color: "var(--text-faint)", fontSize: 13, padding: 20, textAlign: "center" }}>
                    No checkpoints scored yet.
                  </div>
                ) : (
                  <SegmentCards
                    callId={id}
                    cpCards={cpCards}
                    cpFilter={cpFilter}
                    innerProps={{
                      callDurationSec: duration,
                      words,
                      seekAndPlay,
                      activeCheckpointKey,
                      totalSections: cpCards.length,
                      onReviewVerdict: async (origIndex, verdict, notes) => {
                        await reviewCheckpoint.mutateAsync({
                          callId: id,
                          index: origIndex,
                          verdict,
                          notes,
                        });
                      },
                      onRetry: async (origIndex) => {
                        await retryCheckpoint.mutateAsync({
                          callId: id,
                          index: origIndex,
                        });
                      },
                    }}
                  />
                )}
              </div>
            )}
            {tab === "verdict" && (
              <>
                {committed ? (
                  <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 16 }}>
                    <div
                      style={{
                        padding: 16,
                        background: "var(--emerald-bg)",
                        border: "1px solid var(--emerald-border)",
                        borderRadius: 8,
                        display: "flex",
                        alignItems: "center",
                        gap: 12,
                      }}
                    >
                      <div
                        style={{
                          width: 32,
                          height: 32,
                          borderRadius: 16,
                          background: "var(--emerald-bg-strong)",
                          display: "grid",
                          placeItems: "center",
                          color: "var(--emerald)",
                        }}
                      >
                        <CheckCircle2 size={16} />
                      </div>
                      <div style={{ flex: 1 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <Pill tone="emerald">{chosen ?? "PASS"}</Pill>
                          <span style={{ fontSize: 13, color: "var(--text-primary)" }}>
                            committed
                          </span>
                        </div>
                        <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
                          just now
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <VerdictTab
                    callId={id}
                    agentName={c?.agent_name ?? null}
                    customerName={c?.customer_name ?? null}
                    filename={c?.filename ?? null}
                    score={c?.score ?? null}
                    reviewerEmail={meQ.data?.email ?? null}
                    cpCards={cpCards.map((m) => ({
                      key: m.key,
                      script: m.script,
                      verdict: m.verdict,
                    }))}
                    flags={flags}
                    initialRiskTags={
                      ((c as { risk_tags?: string[] } | null | undefined)?.risk_tags ?? []).filter(
                        (t): t is "Ombudsman" | "Mis-selling" | "Complaint" | "Cancellation" | "Vulnerable" =>
                          t === "Ombudsman" ||
                          t === "Mis-selling" ||
                          t === "Complaint" ||
                          t === "Cancellation" ||
                          t === "Vulnerable",
                      )
                    }
                    onSubmitted={() => {
                      // PROTOTYPE: VerdictTab handles its own toast +
                      // payload logging. We don't flip `committed` so
                      // the reviewer stays on the form and can iterate.
                      // Once backend is wired, switch to setCommitted(true)
                      // + router.push("/queue").
                    }}
                  />
                )}
              </>
            )}
            {tab === "chat" && (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  height: "100%",
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    flex: 1,
                    overflowY: "auto",
                    padding: 20,
                    display: "flex",
                    flexDirection: "column",
                    gap: 14,
                    minHeight: 0,
                  }}
                  className="ca-scroll"
                >
                  {chatHistory.length === 0 && (
                    <div style={{ color: "var(--text-faint)", fontSize: 13, textAlign: "center", padding: 20 }}>
                      Ask anything about this call. Citations like [T1] or [S2] will be linked.
                    </div>
                  )}
                  {chatHistory.map((msg, i) => {
                    const isYou = msg.role === "user";
                    const parts = msg.content.split(/(\[T\d+\]|\[S\d+\])/g);
                    return (
                      <div
                        key={i}
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          alignItems: isYou ? "flex-end" : "flex-start",
                        }}
                      >
                        <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 4 }}>
                          {isYou ? "You" : "Compliance Agent"}
                        </div>
                        <div
                          style={{
                            maxWidth: "85%",
                            padding: "10px 12px",
                            background: isYou ? "var(--bg-elev3)" : "var(--bg-elev2)",
                            border: "1px solid var(--border-subtle)",
                            borderRadius: 8,
                            fontSize: 13,
                            color: "var(--text-primary)",
                            lineHeight: 1.55,
                          }}
                        >
                          {parts.map((p, j) => {
                            const match = p.match(/^\[((?:T|S)\d+)\]$/);
                            if (!match) return <span key={j}>{p}</span>;
                            const citeId = match[1];
                            const cite = msg.citations?.find(
                              (c) => c.id === citeId,
                            );
                            const quote = cite?.quote ?? "Click to scroll transcript";
                            return (
                              <button
                                key={j}
                                type="button"
                                data-cite-id={citeId}
                                title={quote}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  // Citation IDs of form T<n> map directly
                                  // to data-line-id; S<n> source citations
                                  // don't have a transcript line yet, but
                                  // we still attempt the lookup so future
                                  // source-line registrations Just Work.
                                  spotlightLine(citeId);
                                }}
                                style={{
                                  display: "inline-flex",
                                  alignItems: "center",
                                  padding: "1px 6px",
                                  fontSize: 11,
                                  fontFamily: "var(--font-mono)",
                                  background: "var(--blue-bg)",
                                  color: "var(--blue)",
                                  border: "1px solid var(--blue-border)",
                                  borderRadius: 3,
                                  cursor: "pointer",
                                  margin: "0 2px",
                                  fontWeight: 500,
                                }}
                              >
                                {citeId}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div
                  style={{
                    padding: 16,
                    borderTop: "1px solid var(--border-subtle)",
                    display: "flex",
                    gap: 8,
                  }}
                >
                  <input
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                    placeholder="Ask about this call…"
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && chatInput.trim()) {
                        const next: ChatMessage[] = [
                          ...chatHistory,
                          { role: "user", content: chatInput.trim() },
                        ];
                        setChatHistory(next);
                        const sent = chatInput.trim();
                        setChatInput("");
                        agentChat.mutate(
                          { call_id: id, messages: next },
                          {
                            onSuccess: (data) => {
                              setChatHistory((h) => [...h, data.message]);
                            },
                          },
                        );
                        void sent;
                      }
                    }}
                    style={{
                      flex: 1,
                      height: 32,
                      padding: "0 10px",
                      background: "var(--bg-elev2)",
                      border: "1px solid var(--border-subtle)",
                      borderRadius: 6,
                      color: "var(--text-primary)",
                      fontSize: 13,
                      outline: "none",
                      fontFamily: "inherit",
                    }}
                  />
                  <button
                    type="button"
                    style={{
                      height: 32,
                      padding: "0 12px",
                      background: "var(--emerald)",
                      color: "#04201a",
                      border: "1px solid var(--emerald)",
                      borderRadius: 6,
                      fontSize: 13,
                      cursor: "pointer",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      fontFamily: "inherit",
                    }}
                  >
                    <Send size={14} />
                    Send
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {c && (
        <EditMetadataDialog
          call={{ id: c.id, customer_name: c.customer_name, agent_name: c.agent_name }}
          deal={dealQuery.data ?? null}
          open={editOpen}
          onClose={() => setEditOpen(false)}
        />
      )}
    </div>
  );
}
