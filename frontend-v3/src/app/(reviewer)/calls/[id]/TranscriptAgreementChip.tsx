"use client";
/**
 * Two-layer transcript validation chip + expandable comparison drawer.
 *
 * Reads `call.transcript_agreement` (populated by the backend cross-
 * validation module). When the two STT engines agree within the floor,
 * renders a small green confirmation chip. When they diverge, renders
 * an amber warning chip that expands to a side-by-side comparison of
 * up to 8 disagreement windows.
 *
 * The reviewer can manually verify each disagreement against the
 * audio player. This is the user-visible artefact of the doctrine
 * §2 "zero accuracy degradation" requirement: both engines run, both
 * are visible, divergence is flagged loudly.
 */
import { useState } from "react";

export type DisagreementSample = {
  tag: string;
  deepgram: string | null;
  assemblyai: string | null;
  deepgram_only: string | null;
  assemblyai_only: string | null;
};

export type TranscriptAgreement = {
  agreement: number | null;
  agreement_full: number | null;
  deepgram_word_count: number;
  assemblyai_word_count: number;
  below_floor: boolean;
  floor: number;
  disagreement_samples: DisagreementSample[];
  skipped_reason: string | null;
};

export type DiarizationInfo = {
  source: string;
  deepgram_speakers: number;
  assemblyai_speakers: number;
  fallback: boolean;
};

interface Props {
  agreement: TranscriptAgreement | null | undefined;
  diarization?: DiarizationInfo | null;
}

function DiarizationChip({ diarization }: { diarization: DiarizationInfo }) {
  const isFallback = diarization.fallback;
  const bg = isFallback ? "rgba(245,158,11,0.15)" : "rgba(99,102,241,0.12)";
  const fg = isFallback ? "#b45309" : "#4338ca";
  const border = isFallback ? "rgba(245,158,11,0.4)" : "rgba(99,102,241,0.35)";
  const label = isFallback
    ? `⚠ Diarization fallback — DG ${diarization.deepgram_speakers} · AAI ${diarization.assemblyai_speakers} speakers (transcript may show one turn)`
    : `🗣 Speakers from ${diarization.source} (DG ${diarization.deepgram_speakers} · AAI ${diarization.assemblyai_speakers})`;
  return (
    <div
      style={{
        fontSize: 11,
        padding: "4px 10px",
        borderRadius: 999,
        background: bg,
        color: fg,
        border: `1px solid ${border}`,
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        width: "fit-content",
      }}
      title={
        isFallback
          ? "Both Deepgram and AssemblyAI failed to split speakers. Click Reanalyze to retry, or check audio is stereo."
          : `Speaker labels are driven by ${diarization.source}`
      }
      data-testid="diarization-chip"
      data-fallback={isFallback ? "true" : "false"}
    >
      {label}
    </div>
  );
}

export function TranscriptAgreementChip({ agreement, diarization }: Props) {
  const [open, setOpen] = useState(false);

  if (!agreement && !diarization) return null;

  if (!agreement && diarization) {
    return <DiarizationChip diarization={diarization} />;
  }

  if (!agreement) {
    // Type narrow — covered by the guards above, but TS doesn't track it.
    return null;
  }

  if (agreement.skipped_reason) {
    const reasonLabel =
      agreement.skipped_reason === "deepgram_missing"
        ? "Deepgram transcript missing"
        : agreement.skipped_reason === "assemblyai_missing"
          ? "AssemblyAI transcript missing"
          : "Cross-validation skipped";
    return (
      <div
        style={{
          fontSize: 11,
          padding: "4px 10px",
          borderRadius: 999,
          background: "rgba(120,120,120,0.18)",
          color: "var(--text-muted)",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
        title="Cross-validation needs both Deepgram and AssemblyAI transcripts"
        data-testid="transcript-agreement-skipped"
      >
        <span>ℹ</span>
        <span>{reasonLabel}</span>
      </div>
    );
  }

  const pct =
    typeof agreement.agreement === "number"
      ? Math.round(agreement.agreement * 100)
      : null;
  const floorPct = Math.round(agreement.floor * 100);
  const samples = agreement.disagreement_samples ?? [];
  const isAmber = agreement.below_floor;

  const chipBg = isAmber ? "rgba(245,158,11,0.15)" : "rgba(16,185,129,0.15)";
  const chipFg = isAmber ? "#b45309" : "#047857";
  const chipBorder = isAmber ? "rgba(245,158,11,0.4)" : "rgba(16,185,129,0.4)";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{
          fontSize: 11,
          fontWeight: 500,
          padding: "4px 10px",
          borderRadius: 999,
          background: chipBg,
          color: chipFg,
          border: `1px solid ${chipBorder}`,
          cursor: samples.length > 0 ? "pointer" : "default",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          width: "fit-content",
        }}
        title={
          isAmber
            ? `Deepgram and AssemblyAI disagree on ${samples.length} window${
                samples.length === 1 ? "" : "s"
              }. Click to inspect.`
            : `Both engines agree above ${floorPct}% threshold.`
        }
        data-testid="transcript-agreement-chip"
        data-below-floor={isAmber ? "true" : "false"}
      >
        <span>{isAmber ? "⚠" : "✓"}</span>
        <span>
          {isAmber
            ? `Transcription divergence: ${pct}% agreement (floor ${floorPct}%)`
            : `Transcripts agree (${pct}%)`}
        </span>
        <span style={{ opacity: 0.7 }}>
          DG {agreement.deepgram_word_count} · AAI {agreement.assemblyai_word_count}
        </span>
        {samples.length > 0 ? (
          <span style={{ opacity: 0.7 }}>{open ? "▲" : "▼"}</span>
        ) : null}
      </button>

      {diarization ? <DiarizationChip diarization={diarization} /> : null}

      {open && samples.length > 0 ? (
        <div
          style={{
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
            background: "var(--surface-1)",
            padding: 8,
            display: "flex",
            flexDirection: "column",
            gap: 6,
            maxHeight: 320,
            overflowY: "auto",
          }}
          data-testid="transcript-agreement-drawer"
        >
          <div
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              paddingBottom: 4,
              borderBottom: "1px dashed var(--border-subtle)",
            }}
          >
            Showing the top {samples.length} disagreement window
            {samples.length === 1 ? "" : "s"} (longest spans first). »…« marks
            the divergent tokens.
          </div>
          {samples.map((s, i) => (
            <div
              key={i}
              style={{
                fontSize: 11,
                lineHeight: 1.5,
                display: "grid",
                gridTemplateColumns: "70px 1fr",
                gap: 6,
                padding: "4px 0",
                borderBottom:
                  i === samples.length - 1
                    ? "none"
                    : "1px dashed var(--border-subtle)",
              }}
            >
              <div
                style={{
                  color: "#b45309",
                  fontWeight: 500,
                  textTransform: "uppercase",
                  fontSize: 10,
                  letterSpacing: 0.4,
                }}
              >
                Deepgram
              </div>
              <div style={{ color: "var(--text-default)" }}>
                {s.deepgram || <em style={{ opacity: 0.6 }}>(empty span)</em>}
              </div>
              <div
                style={{
                  color: "#047857",
                  fontWeight: 500,
                  textTransform: "uppercase",
                  fontSize: 10,
                  letterSpacing: 0.4,
                }}
              >
                AssemblyAI
              </div>
              <div style={{ color: "var(--text-default)" }}>
                {s.assemblyai || <em style={{ opacity: 0.6 }}>(empty span)</em>}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
