"use client";

import { useEffect, useRef } from "react";

import type { WordToken } from "@/lib/queries/reviewer";

/**
 * TranscriptTimeline — chat-bubble transcript with word-level karaoke.
 *
 * Inputs:
 *   - words: WordToken[] from /api/calls/{id}/words (per-word ts + speaker)
 *   - currentSeconds: playhead position from AudioWaveform
 *   - onSeek: clicking a line/timestamp seeks the audio
 *   - flaggedLineKeys: optional set of "speaker:start" keys whose lines
 *     get the flagged-line treatment (amber border + tinted bg)
 *   - showOnlyFlagged: when true, render only flagged lines
 *
 * Lines are computed by grouping consecutive words sharing a speaker
 * label (or pause > 750ms). The currently-spoken word gets an emerald
 * pill background; played words full color, upcoming words dim. PII
 * markers ([PERSON_NAME], [PHONE_NUMBER], etc.) are rendered as violet
 * inline pills.
 *
 * On `currentSeconds` change, auto-scrolls the active line into view (if
 * not already visible) — kept gentle so the user can scroll freely while
 * still being nudged back to the playhead.
 */
export type TranscriptLine = {
  key: string; // stable key for React + selection
  speaker: string;
  start: number;
  end: number;
  words: WordToken[];
};

export function groupWordsIntoLines(words: WordToken[]): TranscriptLine[] {
  const out: TranscriptLine[] = [];
  let buf: WordToken[] = [];
  let speaker = "";

  function flush() {
    if (buf.length === 0) return;
    const start = buf[0].start;
    const end = buf[buf.length - 1].end;
    out.push({
      key: `${speaker}:${start.toFixed(3)}`,
      speaker: speaker || "AGENT",
      start,
      end,
      words: buf,
    });
    buf = [];
  }

  for (const w of words) {
    const sp = w.speaker || speaker || "AGENT";
    const longPause = buf.length > 0 && w.start - buf[buf.length - 1].end > 0.75;
    if (sp !== speaker || longPause) {
      flush();
      speaker = sp;
    }
    buf.push(w);
  }
  flush();
  return out;
}

const PII_RE = /\[(PERSON_NAME|PHONE_NUMBER|EMAIL_ADDRESS|ADDRESS|DATE_OF_BIRTH|POSTCODE|ID_NUMBER)\]/;

export function TranscriptTimeline({
  words,
  currentSeconds,
  onSeek,
  flaggedLineKeys,
  showOnlyFlagged = false,
}: {
  words: WordToken[];
  currentSeconds: number;
  onSeek: (seconds: number) => void;
  flaggedLineKeys?: Set<string>;
  showOnlyFlagged?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const activeLineRef = useRef<HTMLDivElement | null>(null);

  const lines = groupWordsIntoLines(words);
  const visibleLines = showOnlyFlagged && flaggedLineKeys
    ? lines.filter((l) => flaggedLineKeys.has(l.key))
    : lines;

  // Auto-scroll the active line into view.
  useEffect(() => {
    if (!activeLineRef.current || !containerRef.current) return;
    const line = activeLineRef.current;
    const container = containerRef.current;
    const lineRect = line.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    const visible =
      lineRect.top >= containerRect.top && lineRect.bottom <= containerRect.bottom;
    if (!visible) {
      line.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [currentSeconds]);

  if (lines.length === 0) {
    return (
      <div className="px-5 py-10 text-center text-[13px] text-[var(--text-muted)]">
        No transcript words available.
      </div>
    );
  }

  return (
    <div ref={containerRef} className="flex flex-col gap-3 px-5 py-4" data-testid="transcript">
      <div className="text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
        Transcript
      </div>
      {visibleLines.map((line) => {
        const isActive = currentSeconds >= line.start && currentSeconds <= line.end;
        const isFlagged = flaggedLineKeys?.has(line.key);
        return (
          <div
            key={line.key}
            ref={isActive ? activeLineRef : undefined}
            data-testid="transcript-line"
            data-active={isActive || undefined}
            data-flagged={isFlagged || undefined}
            onClick={() => onSeek(line.start)}
            className="grid cursor-pointer grid-cols-[64px_72px_1fr] gap-3 rounded-md font-mono text-[13px] leading-[1.6]"
            style={{
              background: isActive
                ? "color-mix(in oklab, var(--emerald-pass) 6%, transparent)"
                : isFlagged
                  ? "color-mix(in oklab, var(--amber-review) 6%, transparent)"
                  : "transparent",
              borderLeft: isFlagged
                ? `2px solid var(--amber-review)`
                : isActive
                  ? `2px solid var(--emerald-pass)`
                  : "2px solid transparent",
              padding: isActive || isFlagged ? "8px 10px" : "0 10px",
              margin: isActive || isFlagged ? "0 -10px" : "0 -10px",
            }}
          >
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onSeek(line.start);
              }}
              className="text-left tabular-nums text-[var(--text-dim)] hover:text-[var(--emerald-pass)]"
              data-testid="transcript-timestamp"
            >
              {formatT(line.start)}
            </button>
            <div
              className={`font-medium ${
                line.speaker.toUpperCase() === "AGENT"
                  ? "text-[var(--blue-coaching)]"
                  : "text-[var(--amber-review)]"
              }`}
            >
              {line.speaker.toUpperCase()}
            </div>
            <div className="text-[var(--text-primary)]">
              {line.words.map((w, i) => {
                const piiMatch = w.word && PII_RE.test(w.word);
                if (piiMatch) {
                  return (
                    <span
                      key={i}
                      data-testid="pii-pill"
                      className="mx-0.5 inline-flex items-center rounded-[3px] px-1.5 py-[1px] text-[11px]"
                      style={{
                        background: "color-mix(in oklab, var(--violet-block) 10%, transparent)",
                        color: "var(--violet-block)",
                        border: "1px solid color-mix(in oklab, var(--violet-block) 30%, transparent)",
                      }}
                    >
                      {w.word}
                    </span>
                  );
                }
                const playedFully = currentSeconds >= w.end;
                const playing = currentSeconds >= w.start && currentSeconds < w.end;
                const upcoming = currentSeconds < w.start;
                return (
                  <span
                    key={i}
                    data-testid="transcript-word"
                    data-playing={playing || undefined}
                    style={{
                      color: playing
                        ? "var(--emerald-pass)"
                        : upcoming
                          ? "var(--text-dim)"
                          : "var(--text-primary)",
                      background: playing
                        ? "color-mix(in oklab, var(--emerald-pass) 14%, transparent)"
                        : "transparent",
                      padding: playing ? "0 2px" : "0",
                      borderRadius: 2,
                      fontWeight: playing ? 500 : 400,
                      transition: "background 80ms linear",
                    }}
                  >
                    {playedFully || playing || upcoming ? w.word : w.word}{" "}
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

function formatT(secs: number): string {
  if (!Number.isFinite(secs)) return "0:00";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
