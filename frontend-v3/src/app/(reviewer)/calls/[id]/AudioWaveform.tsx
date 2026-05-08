"use client";

import { forwardRef, useImperativeHandle, useRef, useState } from "react";
import { Pause, Play, Rewind, FastForward } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * AudioWaveform — sine-modulated 120-bar waveform with a karaoke-style
 * progress fill, driven by an underlying <audio> element.
 *
 * Bars are computed deterministically from index so the visualization is
 * stable across renders (vs. random heights). Played bars use
 * --emerald-pass; unplayed bars use --border-strong.
 *
 * Controls below the bar:
 *   - Play / Pause (big primary)
 *   - ±10s skip
 *   - Speed dropdown (0.5/0.75/1/1.25/1.5/2x)
 *   - Volume slider
 *   - "Show only flagged" toggle (delegated to parent via prop)
 *
 * `onTimeUpdate` is forwarded so the TranscriptTimeline can sync karaoke.
 * The audio source URL is opt-in — when absent, the component renders a
 * "no audio" placeholder so the rest of the page still functions.
 */
export type AudioWaveformHandle = {
  seek: (seconds: number) => void;
  play: () => void;
  pause: () => void;
};

export type AudioWaveformProps = {
  src?: string | null;
  duration: number; // seconds — best-known guess from backend
  onTimeUpdate?: (currentSeconds: number) => void;
  /** Number of bars; the queue.jsx mock uses 120. */
  bars?: number;
  /** Bound to the "Show only flagged" toggle; the parent owns the value. */
  showOnlyFlagged?: boolean;
  onShowOnlyFlaggedChange?: (next: boolean) => void;
  /** Flag positions as percent 0-100 along the waveform (red ticks). */
  flagMarkers?: number[];
};

export const AudioWaveform = forwardRef<AudioWaveformHandle, AudioWaveformProps>(function AudioWaveform(
  {
    src,
    duration,
    onTimeUpdate,
    bars = 120,
    showOnlyFlagged = false,
    onShowOnlyFlaggedChange,
    flagMarkers = [],
  },
  ref,
) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [volume, setVolume] = useState(1);

  useImperativeHandle(ref, () => ({
    seek(seconds: number) {
      const a = audioRef.current;
      if (a) {
        a.currentTime = Math.max(0, seconds);
      }
      setCurrent(seconds);
      onTimeUpdate?.(seconds);
    },
    play() {
      audioRef.current?.play();
    },
    pause() {
      audioRef.current?.pause();
    },
  }));

  const safeDuration = duration > 0 && Number.isFinite(duration) ? duration : 1;
  const playedFrac = Math.max(0, Math.min(1, current / safeDuration));
  const playedBars = Math.floor(bars * playedFrac);

  function seekToBar(idx: number) {
    const t = (idx / bars) * safeDuration;
    const a = audioRef.current;
    if (a) a.currentTime = t;
    setCurrent(t);
    onTimeUpdate?.(t);
  }

  return (
    <div className="flex flex-col gap-2 px-5 py-4">
      <div
        role="slider"
        aria-label="Audio progress"
        aria-valuemin={0}
        aria-valuemax={Math.round(safeDuration)}
        aria-valuenow={Math.round(current)}
        data-testid="waveform"
        className="relative flex h-10 cursor-pointer items-center gap-[1px]"
        onClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const x = e.clientX - rect.left;
          const idx = Math.round((x / rect.width) * bars);
          seekToBar(idx);
        }}
      >
        {Array.from({ length: bars }).map((_, i) => {
          const h =
            4 +
            Math.abs(
              Math.sin(i * 0.43) * Math.cos(i * 0.21) + Math.sin(i * 0.11),
            ) *
              16;
          const isPlayed = i < playedBars;
          return (
            <div
              key={i}
              className="flex-1 rounded-[1px]"
              style={{
                height: h,
                minWidth: 1.5,
                background: isPlayed ? "var(--emerald-pass)" : "var(--border-strong)",
              }}
            />
          );
        })}
        {/* Flag markers — red 2×8 ticks above waveform at flagged percentages */}
        {flagMarkers.map((pct, i) => (
          <div
            key={`flag-${i}`}
            className="pointer-events-none absolute"
            style={{
              left: `${Math.max(0, Math.min(100, pct))}%`,
              top: -2,
              width: 2,
              height: 8,
              background: "var(--red-fail)",
              borderRadius: 1,
            }}
          />
        ))}
        {/* Playhead overlay — 2px emerald line */}
        <div
          className="pointer-events-none absolute top-0 bottom-0 w-[2px]"
          style={{
            left: `${playedFrac * 100}%`,
            background: "var(--emerald-pass)",
          }}
        />
      </div>

      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          size="sm"
          aria-label="Rewind 10 seconds"
          data-testid="waveform-rewind"
          onClick={() => {
            const a = audioRef.current;
            const next = Math.max(0, current - 10);
            if (a) a.currentTime = next;
            setCurrent(next);
            onTimeUpdate?.(next);
          }}
        >
          <Rewind className="h-3.5 w-3.5" />
        </Button>
        <Button
          size="sm"
          data-testid="waveform-play"
          onClick={() => {
            const a = audioRef.current;
            if (!a) {
              // No real audio, just toggle internal flag for visual feedback in tests.
              setPlaying((p) => !p);
              return;
            }
            if (a.paused) {
              void a.play();
            } else {
              a.pause();
            }
          }}
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
        </Button>
        <Button
          variant="outline"
          size="sm"
          aria-label="Forward 10 seconds"
          data-testid="waveform-forward"
          onClick={() => {
            const a = audioRef.current;
            const next = Math.min(safeDuration, current + 10);
            if (a) a.currentTime = next;
            setCurrent(next);
            onTimeUpdate?.(next);
          }}
        >
          <FastForward className="h-3.5 w-3.5" />
        </Button>

        <div className="font-mono text-[11px] text-[var(--text-muted)] tabular-nums">
          {formatT(current)} / {formatT(safeDuration)}
        </div>

        <div className="flex-1" />

        <select
          value={speed}
          onChange={(e) => {
            const n = Number(e.target.value);
            setSpeed(n);
            const a = audioRef.current;
            if (a) a.playbackRate = n;
          }}
          className="h-8 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] px-2 text-[12px]"
          aria-label="Playback speed"
        >
          {[0.5, 0.75, 1, 1.25, 1.5, 2].map((s) => (
            <option key={s} value={s}>
              {s}×
            </option>
          ))}
        </select>

        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={volume}
          aria-label="Volume"
          onChange={(e) => {
            const v = Number(e.target.value);
            setVolume(v);
            const a = audioRef.current;
            if (a) a.volume = v;
          }}
          className="w-[80px]"
        />

        <label className="flex items-center gap-1.5 text-[12px] text-[var(--text-muted)]">
          <input
            type="checkbox"
            checked={showOnlyFlagged}
            onChange={(e) => onShowOnlyFlaggedChange?.(e.target.checked)}
            className="h-3.5 w-3.5 accent-[var(--amber-review)]"
          />
          Flagged only
        </label>
      </div>

      {src ? (
        <audio
          ref={audioRef}
          src={src}
          preload="metadata"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onTimeUpdate={(e) => {
            const t = (e.target as HTMLAudioElement).currentTime;
            setCurrent(t);
            onTimeUpdate?.(t);
          }}
        />
      ) : null}
    </div>
  );
});

function formatT(secs: number): string {
  if (!Number.isFinite(secs)) return "0:00";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
