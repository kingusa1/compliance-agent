"use client";

// Per-word, click-to-seek + double-click / alt-click-to-edit transcript
// player. Ported from `frontend/src/components/TranscriptPlayer.tsx`
// (main branch) with two adaptations for v3:
//   1. WordData type is defined locally — main imports from @/lib/api
//      with a richer shape (punctuated_word, numeric speaker, mandatory
//      confidence). v3's WordToken uses string speakers (A/B/AGENT/...)
//      and optional confidence, so the page-level caller adapts the
//      shape before passing it in. See page.tsx mapping.
//   2. Word-edit mutation comes from `useEditWord(callId)` hook instead
//      of a bare `editWord` call — gives us TanStack invalidation +
//      toast plumbing for free.

import { useEffect, useRef, useState, useCallback, useMemo } from "react";

import { ApiError } from "@/lib/api";
import { getCurrentUser } from "@/lib/supabase";
import { useEditWord } from "@/lib/mutations/reviewer";

export interface WordData {
  word: string;
  punctuated_word: string;
  start: number; // seconds
  end: number;   // seconds
  speaker: number; // 0 = Agent, 1 = Customer, ...
  confidence: number;
}

interface WordEditRecord {
  old: string;
  new: string;
  by: string;
  at: string;
}

interface TranscriptPlayerProps {
  words: WordData[];
  currentTime: number;
  onWordClick: (timestamp: number) => void;
  highlightedEvidence?: string | null;
  callId?: string;
  selectedCheckpointId?: string | null;
  onCheckpointUpdate?: (cp: unknown) => void;
  getRevision?: () => number | undefined;
  onConflict?: () => void;
}

// Plan §5b: AGENT / CUSTOMER labels are LOUD — uppercase, bold, strong
// contrasting colours and tinted backgrounds so the reviewer can tell who
// is speaking at a glance while scrubbing through the karaoke transcript.
const SPEAKER_STYLES: Record<number, { label: string; color: string; bg: string }> = {
  0: { label: "AGENT", color: "#22c55e", bg: "rgba(34,197,94,0.18)" },
  1: { label: "CUSTOMER", color: "#f59e0b", bg: "rgba(245,158,11,0.18)" },
};

function getSpeakerStyle(speaker: number) {
  return SPEAKER_STYLES[speaker] || { label: `Speaker ${speaker}`, color: "var(--dim, #8a857e)", bg: "rgba(138,133,126,0.08)" };
}

function formatTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/** Group words into speaker turns (consecutive words from same speaker, or gap > 2s). */
function groupIntoTurns(words: WordData[]): { speaker: number; startTime: number; startIdx: number; words: WordData[] }[] {
  if (words.length === 0) return [];
  const turns: { speaker: number; startTime: number; startIdx: number; words: WordData[] }[] = [];
  let current = { speaker: words[0].speaker, startTime: words[0].start, startIdx: 0, words: [words[0]] };

  for (let i = 1; i < words.length; i++) {
    const w = words[i];
    if (w.speaker !== current.speaker || w.start - current.words[current.words.length - 1].end > 2) {
      turns.push(current);
      current = { speaker: w.speaker, startTime: w.start, startIdx: i, words: [w] };
    } else {
      current.words.push(w);
    }
  }
  turns.push(current);
  return turns;
}

export function TranscriptPlayer({
  words,
  currentTime,
  onWordClick,
  highlightedEvidence,
  callId,
  selectedCheckpointId,
  onCheckpointUpdate,
  getRevision,
  onConflict,
}: TranscriptPlayerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const activeWordRef = useRef<HTMLSpanElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const scrollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Word-edit state. Click-to-seek stays on single click; editing opens
  // via alt-click or double-click so we don't break the existing audio
  // seek UX. Only activates when `callId` is supplied.
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [edits, setEdits] = useState<Record<number, WordEditRecord>>({});
  const [currentUserEmail, setCurrentUserEmail] = useState<string>("you");

  const editWordMutation = useEditWord(callId ?? "");

  useEffect(() => {
    if (!callId) return;
    getCurrentUser()
      .then((u) => {
        if (u?.email) setCurrentUserEmail(u.email);
      })
      .catch(() => {
        /* tooltip falls back to "you" */
      });
  }, [callId]);

  const turns = useMemo(() => groupIntoTurns(words), [words]);

  // Binary search for active word index (performant for large transcripts).
  const activeWordIndex = useMemo(() => {
    if (words.length === 0 || currentTime < words[0].start) return -1;
    let lo = 0,
      hi = words.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (words[mid].start <= currentTime) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }, [words, currentTime]);

  // Auto-scroll to active word.
  useEffect(() => {
    if (autoScroll && activeWordRef.current) {
      activeWordRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [activeWordIndex, autoScroll]);

  // Detect manual scroll — pause auto-scroll for 3 seconds.
  const handleScroll = useCallback(() => {
    setAutoScroll(false);
    if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
    scrollTimeoutRef.current = setTimeout(() => setAutoScroll(true), 3000);
  }, []);

  // Cleanup timeout on unmount.
  useEffect(() => {
    return () => {
      if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
    };
  }, []);

  // Build a set of evidence words for fast lookup.
  const evidenceWords = useMemo(() => {
    if (!highlightedEvidence) return new Set<string>();
    return new Set(highlightedEvidence.toLowerCase().split(/\s+/).filter((w) => w.length > 2));
  }, [highlightedEvidence]);

  const openEditor = useCallback(
    (globalIdx: number, currentText: string) => {
      if (!callId) return;
      setEditingIdx(globalIdx);
      setDraft(currentText);
    },
    [callId],
  );

  const cancelEdit = useCallback(() => {
    setEditingIdx(null);
    setDraft("");
  }, []);

  const commitEdit = useCallback(
    async (globalIdx: number) => {
      if (!callId) return;
      const original = words[globalIdx];
      if (!original) {
        cancelEdit();
        return;
      }
      const newText = draft.trim();
      const oldText = original.punctuated_word || original.word;
      if (!newText || newText === oldText) {
        cancelEdit();
        return;
      }
      // Optimistic — render highlight + tooltip immediately so the
      // reviewer's click feels instant even if reanalysis takes a few seconds.
      const at = new Date().toISOString();
      setEdits((prev) => ({
        ...prev,
        [globalIdx]: { old: oldText, new: newText, by: currentUserEmail, at },
      }));
      setEditingIdx(null);
      setDraft("");
      try {
        const resp = await editWordMutation.mutateAsync({
          word_index: globalIdx,
          old_text: oldText,
          new_text: newText,
          checkpoint_id: selectedCheckpointId ?? null,
          revision: getRevision?.() ?? null,
        });
        if (resp.verdict_changed && resp.checkpoint && onCheckpointUpdate) {
          onCheckpointUpdate(resp.checkpoint);
        }
      } catch (err) {
        // Roll back the optimistic edit so the UI doesn't lie.
        setEdits((prev) => {
          const next = { ...prev };
          delete next[globalIdx];
          return next;
        });
        // 409 → concurrent write; tell the parent to refetch.
        if (err instanceof ApiError && err.status === 409 && onConflict) {
          onConflict();
        }
        // Other errors are surfaced via the mutation's onError toast.
      }
    },
    [
      callId,
      draft,
      words,
      currentUserEmail,
      selectedCheckpointId,
      onCheckpointUpdate,
      cancelEdit,
      getRevision,
      onConflict,
      editWordMutation,
    ],
  );

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      style={{
        // Parent container already scrolls (see call-detail page transcript
        // panel, maxHeight calc(100vh - 280px)); nesting a second scroll
        // here produced a cramped 400px inner window with a scrollbar that
        // floated over the last character of each line. Let the parent
        // own scrolling.
        padding: "12px 0",
        fontSize: 14,
        lineHeight: 1.8,
        scrollBehavior: "smooth",
      }}
    >
      {turns.map((turn, turnIdx) => {
        const style = getSpeakerStyle(turn.speaker);
        const turnContainsActive =
          activeWordIndex >= turn.startIdx &&
          activeWordIndex < turn.startIdx + turn.words.length;

        return (
          <div
            key={turnIdx}
            style={{
              display: "flex",
              gap: 10,
              padding: "6px 12px",
              borderRadius: 6,
              marginBottom: 2,
              background: turnContainsActive ? style.bg : "transparent",
            }}
          >
            {/* Speaker label + timestamp — Plan §5b: LOUD AGENT/CUSTOMER */}
            <div style={{ minWidth: 100, flexShrink: 0, paddingTop: 2 }}>
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 800,
                  color: style.color,
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                  padding: "2px 8px",
                  borderRadius: 4,
                  background: style.bg,
                  display: "inline-block",
                }}
              >
                {style.label}
              </span>
              <br />
              <span
                style={{
                  fontSize: 10,
                  color: "var(--dim, #524f4a)",
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                {formatTimestamp(turn.startTime)}
              </span>
            </div>

            {/* Words */}
            <div style={{ flex: 1 }}>
              {turn.words.map((w, wIdx) => {
                const globalIdx = turn.startIdx + wIdx;
                const isActive = globalIdx === activeWordIndex;
                const isPast = globalIdx < activeWordIndex;
                const isLowConf = w.confidence < 0.7;
                const isEvidence = evidenceWords.has(w.word.toLowerCase());
                const edit = edits[globalIdx];
                const isEdited = Boolean(edit);
                const isEditing = editingIdx === globalIdx;
                const displayText = edit?.new ?? (w.punctuated_word || w.word);

                if (isEditing) {
                  return (
                    <input
                      key={globalIdx}
                      autoFocus
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          commitEdit(globalIdx);
                        } else if (e.key === "Escape") {
                          e.preventDefault();
                          cancelEdit();
                        }
                      }}
                      onBlur={cancelEdit}
                      onFocus={(e) => e.currentTarget.select()}
                      style={{
                        display: "inline-block",
                        width: `${Math.max(draft.length, 4) + 1}ch`,
                        padding: "1px 4px",
                        margin: "0 2px",
                        borderRadius: 3,
                        border: "1px solid var(--teal, #22c55e)",
                        background: "var(--bg, #faf8f4)",
                        color: "var(--ink, #1a1815)",
                        fontSize: 14,
                        fontFamily: "inherit",
                        outline: "none",
                      }}
                    />
                  );
                }

                return (
                  <span
                    key={globalIdx}
                    ref={isActive ? activeWordRef : undefined}
                    onClick={(e) => {
                      // alt/option-click on any word opens the editor when
                      // callId is supplied; plain click still seeks audio.
                      if (callId && e.altKey) {
                        e.preventDefault();
                        openEditor(globalIdx, displayText.trim());
                        return;
                      }
                      onWordClick(w.start);
                    }}
                    onDoubleClick={(e) => {
                      // Double-click is the fallback affordance — also opens
                      // the editor, without requiring the modifier key.
                      if (!callId) return;
                      e.preventDefault();
                      openEditor(globalIdx, displayText.trim());
                    }}
                    title={
                      isEdited
                        ? `Was: "${edit!.old}" · edited by ${edit!.by} · ${new Date(edit!.at).toLocaleTimeString()}`
                        : `${formatTimestamp(w.start)} — confidence: ${(w.confidence * 100).toFixed(0)}%${callId ? " · alt-click or double-click to edit" : ""}`
                    }
                    style={{
                      cursor: "pointer",
                      padding: "1px 2px",
                      borderRadius: 3,
                      transition: "all 0.15s ease",
                      color: isActive ? "#fff" : isPast ? "var(--dim, #c4c0bb)" : "var(--text-muted, #a1a1aa)",
                      background: isActive
                        ? "var(--teal, #22c55e)"
                        : isEdited
                          ? "rgba(34,197,94,0.18)"
                          : isEvidence
                            ? "rgba(245,158,11,0.15)"
                            : "transparent",
                      fontWeight: isActive ? 600 : 400,
                      textDecoration: isEdited
                        ? "underline"
                        : isLowConf
                          ? "wavy underline"
                          : "none",
                      textDecorationColor: isEdited
                        ? "var(--teal, #22c55e)"
                        : isLowConf
                          ? "rgba(239,68,68,0.5)"
                          : "transparent",
                      textUnderlineOffset: "3px",
                    }}
                  >
                    {displayText}{" "}
                  </span>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
