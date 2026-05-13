"use client";

/**
 * CheckpointCard — 4-section per-checkpoint card.
 *
 *   1. Script         — the rule text + key phrases + strictness pill
 *   2. AI Verdict     — model's reasoning + W4.7 AI suggestion chip
 *                       (rejection bucket + remediation, FAIL/PARTIAL only)
 *   3. Actual Call    — evidence quote + 3-tier timestamp fallback
 *                       (start_ms → client word match → proportional)
 *                       OR explicit "Agent never said this" empty state
 *                       when status is FAIL with no evidence
 *   4. Human Review   — 2-step verdict commit (idle → pending → reviewed)
 *                       + retry button + "How to judge this" expander
 *
 * Ported from `frontend/src/components/CheckpointCard.tsx` (main branch).
 * Tracker additions kept in place:
 *   - W1.6 script line-number badge `[L17]` next to checkpoint name
 *   - W4.7 AI suggestion chip after the AI verdict reasoning
 *   - `suggested_category` / `suggested_fix_required` / `category_confidence`
 *     fields on `CheckpointVerdict`
 *
 * Section 4's reviewer-side fields (`reviewer_verdict`, `reviewer_notes`,
 * `reviewer_reasoning`, `reviewer_id`) are read from the verdict if the
 * backend serializer happens to expose them, with empty defaults when null.
 *
 * TODO(backend): the v3 `/api/calls/{id}/script-checkpoints` + checkpoint_results
 * serializer doesn't yet emit reviewer_verdict / reviewer_reasoning / reviewer_id —
 * the section renders empty until that's plumbed through. Tracked as a
 * follow-up to this port.
 */

import { useState } from "react";
import { Play, RotateCcw } from "lucide-react";

import {
  deriveDisplayState,
  displayStateAccent,
  displayStateLabel,
  type DisplayState,
} from "@/lib/checkpoint-state";
import { findWordRangeMs } from "@/lib/word-match";
import type { ScriptCheckpoint, WordToken } from "@/lib/queries/reviewer";

import { CheckpointGuidelines } from "./CheckpointGuidelines";
import { RubricBadge } from "./RubricBadge";

// ── Type ──────────────────────────────────────────────────────────

export type CheckpointVerdict = {
  section: number;
  name: string;
  status: "pass" | "fail" | "partial" | string;
  evidence: string | null;
  notes: string | null;
  confidence: string | null;
  needs_review: boolean;
  start_ms: number | null;
  end_ms: number | null;
  similarity?: number | null;
  verified?: boolean | null;
  // W4.7 (v3-watt-coverage): AI-suggested rejection bucket + remediation
  // surfaced on FAIL/PARTIAL checkpoints. Reviewer-side read-only — the
  // rejection itself is overridable via the rejections page.
  suggested_category?: string | null;
  suggested_fix_required?: string | null;
  category_confidence?: number | null;
  // Reviewer-side fields (port from main). Optional — backend serializer
  // may not surface them yet; the Human Review section renders idle when
  // they're absent.
  reviewer_verdict?: "pass" | "fail" | null;
  reviewer_notes?: string | null;
  reviewer_reasoning?: string | null;
  reviewer_id?: string | null;
};

/** Parse `c.checkpoint_results` (JSON-encoded) into a typed list. */
export function parseCheckpointResults(blob: string | null | undefined): CheckpointVerdict[] {
  if (!blob) return [];
  try {
    const parsed = JSON.parse(blob);
    if (!Array.isArray(parsed)) return [];
    return parsed.map((row): CheckpointVerdict => ({
      section: Number(row?.section ?? 0),
      name: String(row?.name ?? ""),
      status: String(row?.status ?? "fail"),
      evidence: row?.evidence ?? null,
      notes: row?.notes ?? null,
      confidence: row?.confidence ?? null,
      needs_review: Boolean(row?.needs_review),
      start_ms: typeof row?.start_ms === "number" ? row.start_ms : null,
      end_ms: typeof row?.end_ms === "number" ? row.end_ms : null,
      similarity: typeof row?.similarity === "number" ? row.similarity : null,
      verified: typeof row?.verified === "boolean" ? row.verified : null,
      suggested_category:
        typeof row?.suggested_category === "string" ? row.suggested_category : null,
      suggested_fix_required:
        typeof row?.suggested_fix_required === "string" ? row.suggested_fix_required : null,
      category_confidence:
        typeof row?.category_confidence === "number" ? row.category_confidence : null,
      reviewer_verdict:
        row?.reviewer_verdict === "pass" || row?.reviewer_verdict === "fail"
          ? row.reviewer_verdict
          : null,
      reviewer_notes: typeof row?.reviewer_notes === "string" ? row.reviewer_notes : null,
      reviewer_reasoning:
        typeof row?.reviewer_reasoning === "string" ? row.reviewer_reasoning : null,
      reviewer_id: typeof row?.reviewer_id === "string" ? row.reviewer_id : null,
    }));
  } catch {
    return [];
  }
}

// ── Helpers ───────────────────────────────────────────────────────

function formatTs(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ── Component ─────────────────────────────────────────────────────

export interface CheckpointCardProps {
  index: number;
  script?: ScriptCheckpoint;
  verdict?: CheckpointVerdict;
  startSec: number | null;
  isActive: boolean;
  onPlay: (sec: number) => void;
  // Optional props to enable the 4-section layout's Section 3 fallback +
  // Section 4 reviewer flow. When omitted the card still renders the
  // first three sections; Section 4 only renders if `callId` is provided.
  callId?: string;
  callDurationSec?: number;
  totalSections?: number;
  words?: WordToken[];
  origIndex?: number;
  onReviewVerdict?: (origIndex: number, verdict: "pass" | "fail", notes: string) => Promise<void>;
  onRetry?: (origIndex: number) => Promise<void>;
  // Rubric provenance — surfaced as a small badge in the header strip so
  // the reviewer sees "what is this graded against?" per checkpoint.
  // Set by the SegmentCards parent (which fetches /api/calls/{id}/segments
  // and inherits the segment's rubric source).
  rubricKind?: string;
  rubricLabel?: string;
}

export function CheckpointCard(props: CheckpointCardProps) {
  const {
    index,
    script,
    verdict,
    startSec,
    isActive,
    onPlay,
    callId,
    callDurationSec = 0,
    totalSections = 0,
    words,
    origIndex,
    onReviewVerdict,
    onRetry,
    rubricKind,
    rubricLabel,
  } = props;

  const cpId = `CP${String(index + 1).padStart(2, "0")}`;
  const lineNumber =
    typeof script?.line_number === "number" && Number.isFinite(script.line_number)
      ? script.line_number
      : null;

  // Section 3 anchor timestamp — three-tier fallback. Tier 1 = backend's
  // start_ms; tier 2 = client-side fuzzy match against the word stream;
  // tier 3 = proportional (section N of total × call duration). Tier 3
  // only kicks in when we can't pin a word; if we do, we always use it.
  let computedStartMs: number | null =
    verdict?.start_ms ?? (startSec != null ? Math.round(startSec * 1000) : null);
  if (computedStartMs == null && verdict?.evidence && words && words.length > 0) {
    const [ms] = findWordRangeMs(verdict.evidence, words);
    computedStartMs = ms;
  }

  // Display-state derivation (5 director-facing buckets) — uses needs_review
  // + status + evidence emptiness. Falls through to "unverified" when no
  // verdict has been scored yet.
  const state: DisplayState = verdict
    ? deriveDisplayState({
        status: verdict.status,
        evidence: verdict.evidence,
        needs_review: verdict.needs_review,
      })
    : "unverified";
  const accent = verdict ? displayStateAccent(state) : "#8a857e";
  const label = verdict ? displayStateLabel(state) : "Not yet scored";

  const isApproximate = state === "not_said" || computedStartMs == null;
  const targetMs =
    computedStartMs != null
      ? computedStartMs
      : callDurationSec > 0 && totalSections > 0 && verdict
        ? Math.min(
            callDurationSec * 1000,
            callDurationSec * 1000 * (verdict.section / totalSections),
          )
        : 0;
  const targetSec = targetMs / 1000;

  const handleCardClick = () => {
    if (targetSec > 0 || (computedStartMs != null && computedStartMs >= 0)) {
      onPlay(targetSec);
    }
  };
  const stop = (e: React.MouseEvent) => e.stopPropagation();

  return (
    <div
      data-cp-id={cpId}
      data-cp-origindex={origIndex ?? index}
      role="button"
      tabIndex={0}
      aria-label={`Play from checkpoint: ${script?.name || verdict?.name || "checkpoint"} at ${formatTs(targetMs)}`}
      onClick={handleCardClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleCardClick();
        }
      }}
      style={{
        background: "var(--bg-elev2)",
        border: "1px solid var(--border-subtle)",
        borderLeft: `3px solid ${accent}`,
        borderRadius: 8,
        marginBottom: 0,
        cursor: "pointer",
        overflow: "hidden",
        boxShadow: isActive ? "inset 0 0 0 1px rgba(16,185,129,0.18)" : undefined,
        transition: "box-shadow 0.15s",
      }}
    >
      {/* Header — cp id + L<line> badge + name + strictness + ts + verdict pill */}
      <Header
        cpId={cpId}
        name={script?.name || verdict?.name || "Unnamed checkpoint"}
        lineNumber={lineNumber}
        strictness={script?.strictness}
        accent={accent}
        label={label}
        targetMs={targetMs}
        isApproximate={isApproximate}
        hasVerdict={!!verdict}
        rubricKind={rubricKind}
        rubricLabel={rubricLabel}
      />

      {/* Section 1 — Script */}
      <Section label="Script" accent="var(--text-faint, #8a857e)">
        <SectionBody>
          {script?.required ? (
            <span>{script.required}</span>
          ) : (
            <span style={{ color: "var(--text-faint, #8a857e)", fontStyle: "italic" }}>
              (Script text unavailable — script not matched to this call.)
            </span>
          )}
          {script?.customer_response_required && (
            <span style={{ color: "var(--text-faint, #8a857e)", marginLeft: 6 }}>
              (customer must answer yes)
            </span>
          )}
        </SectionBody>
        {script?.key_phrases && script.key_phrases.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }}>
            {script.key_phrases.map((p, i) => (
              <span
                key={i}
                style={{
                  fontSize: 10,
                  padding: "2px 6px",
                  borderRadius: 3,
                  background: "rgba(245,158,11,0.15)",
                  color: "#f59e0b",
                }}
              >
                {p}
              </span>
            ))}
          </div>
        )}
      </Section>

      {/* Section 2 — AI Verdict (+ W4.7 AI suggestion chip).
          Falls back to evidence when the analyzer didn't populate notes —
          the older V1 path emits descriptive evidence text but no notes. */}
      <Section label="AI Verdict" accent={accent}>
        <SectionBody>
          {verdict?.notes && verdict.notes.trim() ? (
            verdict.notes
          ) : verdict?.evidence && verdict.evidence.trim() ? (
            <span>{verdict.evidence}</span>
          ) : verdict ? (
            <span style={{ color: "var(--text-faint, #8a857e)", fontStyle: "italic" }}>
              (No reasoning available — analyzer produced no notes.)
            </span>
          ) : (
            <span style={{ color: "var(--text-faint, #8a857e)", fontStyle: "italic" }}>
              Not yet scored
            </span>
          )}
        </SectionBody>
        <AiSuggestionChip verdict={verdict} />
      </Section>

      {/* Section 3 — Actual Call */}
      <ActualCallSection
        verdict={verdict}
        state={state}
        accent={accent}
        targetMs={targetMs}
        canPlay={computedStartMs != null && computedStartMs >= 0}
        onPlay={onPlay}
        targetSec={targetSec}
      />

      {/* Section 4 — Human Review (only when callId + onReviewVerdict provided) */}
      {callId && onReviewVerdict && verdict && (
        <ReviewerSection
          verdict={verdict}
          callId={callId}
          origIndex={origIndex ?? index}
          onReviewVerdict={onReviewVerdict}
          onRetry={onRetry}
          stop={stop}
        />
      )}
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────

function StrictnessChip({ strictness }: { strictness?: string }) {
  if (!strictness) return null;
  const style: { label: string; fg: string; bg: string } =
    strictness === "verbatim"
      ? { label: "Word for Word", fg: "#f97316", bg: "rgba(249,115,22,0.15)" }
      : strictness === "customer_yes"
        ? { label: "+ Customer ✓", fg: "#2dd4bf", bg: "rgba(45,212,191,0.15)" }
        : { label: "Meaning", fg: "#8a857e", bg: "rgba(148,163,184,0.1)" };
  return (
    <span
      style={{
        fontSize: 9,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        padding: "2px 7px",
        borderRadius: 3,
        background: style.bg,
        color: style.fg,
        whiteSpace: "nowrap",
      }}
    >
      {style.label}
    </span>
  );
}

function Header({
  cpId,
  name,
  lineNumber,
  strictness,
  accent,
  label,
  targetMs,
  isApproximate,
  hasVerdict,
  rubricKind,
  rubricLabel,
}: {
  cpId: string;
  name: string;
  lineNumber: number | null;
  strictness?: string;
  accent: string;
  label: string;
  targetMs: number;
  isApproximate: boolean;
  hasVerdict: boolean;
  rubricKind?: string;
  rubricLabel?: string;
}) {
  // 2026-05-14 redesign: two-row header so the checkpoint name never gets
  // squeezed into a one-word-per-line column when the metadata chips
  // (rubric badge, strictness, timestamp, status pill) compete for the
  // same flex row. Row 1 = title strip (id / line / dot / name / status).
  // Row 2 = metadata strip (strictness / rubric / timestamp / play).
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "12px 16px 10px",
        borderBottom: "1px solid var(--border-subtle)",
      }}
    >
      {/* ── Row 1: identifier strip + name + final status pill ── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          minWidth: 0,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--text-faint)",
            flexShrink: 0,
          }}
        >
          {cpId}
        </span>
        {lineNumber !== null && (
          <span
            data-slot="cp-line-number"
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "var(--text-faint)",
              padding: "1px 4px",
              borderRadius: 3,
              border: "1px solid var(--border-subtle)",
              background: "var(--bg-elev3)",
              flexShrink: 0,
            }}
            title={`Script line ${lineNumber}`}
          >
            [L{lineNumber}]
          </span>
        )}
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: accent,
            flexShrink: 0,
          }}
        />
        <h4
          style={{
            fontSize: 14,
            fontWeight: 600,
            color: "var(--text-primary)",
            margin: 0,
            flex: 1,
            minWidth: 0,
            lineHeight: 1.35,
            // Allow long checkpoint names to wrap on word boundaries
            // (e.g. "Confirm full registered company name and address")
            // without the surrounding flex row squeezing them into a
            // vertical column of single words.
            whiteSpace: "normal",
            overflowWrap: "anywhere",
          }}
        >
          {name}
        </h4>
        <span
          style={{
            fontSize: 9,
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            padding: "3px 9px",
            borderRadius: 4,
            background: hasVerdict ? `${accent}26` : "var(--bg-elev3)",
            color: hasVerdict ? accent : "var(--text-faint)",
            border: hasVerdict ? `1px solid ${accent}55` : "1px solid var(--border-subtle)",
            flexShrink: 0,
            whiteSpace: "nowrap",
          }}
        >
          {label}
        </span>
      </div>

      {/* ── Row 2: metadata strip (strictness · rubric · ts · play) ── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          // Indent under the dot so the row 1 hierarchy is preserved.
          paddingLeft: 26,
        }}
      >
        <StrictnessChip strictness={strictness} />
        {rubricKind && rubricLabel ? (
          <RubricBadge kind={rubricKind} label={rubricLabel} compact />
        ) : null}
        <span style={{ flex: 1 }} />
        {hasVerdict && (
          <span
            style={{
              fontSize: 11,
              fontFamily: "var(--font-mono)",
              color: isApproximate ? "#ef4444" : "var(--text-faint)",
              fontVariantNumeric: "tabular-nums",
              whiteSpace: "nowrap",
            }}
          >
            {isApproximate ? "~" : ""}
            {formatTs(targetMs)}
            {isApproximate ? " (approx)" : ""}
          </span>
        )}
        <span
          className="cp-play-chip"
          style={{
            fontSize: 10,
            color: "var(--text-faint)",
            whiteSpace: "nowrap",
          }}
        >
          ▶ play
        </span>
      </div>
    </div>
  );
}

function Section({
  label,
  accent,
  children,
}: {
  label: string;
  accent: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        padding: "12px 16px",
        borderBottom: "1px solid var(--border-subtle)",
        borderLeft: `3px solid ${accent}`,
        marginLeft: 0,
      }}
    >
      <div
        style={{
          fontSize: 9,
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          marginBottom: 6,
          color: accent,
        }}
      >
        {label}
      </div>
      {children}
    </div>
  );
}

function SectionBody({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 13, lineHeight: 1.55, color: "var(--text-primary)" }}>
      {children}
    </div>
  );
}

function ActualCallSection({
  verdict,
  state,
  accent,
  targetMs,
  canPlay,
  onPlay,
  targetSec,
}: {
  verdict: CheckpointVerdict | undefined;
  state: DisplayState;
  accent: string;
  targetMs: number;
  canPlay: boolean;
  onPlay: (sec: number) => void;
  targetSec: number;
}) {
  const labelSuffix =
    state === "passed"
      ? ` (${formatTs(targetMs)})`
      : state === "partial"
        ? ` — partial match (${formatTs(targetMs)})`
        : state === "said_wrong"
          ? ` — doesn't match (${formatTs(targetMs)})`
          : state === "unverified"
            ? ` — low-confidence match (${formatTs(targetMs)})`
            : "";

  // Not-said: explicit empty state. No quote; LLM reasoning already in
  // Section 2. Section 3 just declares the omission + seek affordance.
  if (state === "not_said") {
    return (
      <div
        style={{
          padding: "14px 16px",
          borderBottom: "1px solid var(--border-subtle)",
          borderLeft: `3px solid ${accent}`,
        }}
      >
        <div
          style={{
            fontSize: 9,
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            marginBottom: 8,
            color: accent,
          }}
        >
          Actual Call
        </div>
        <div
          style={{
            padding: "12px 14px",
            background: "rgba(239,68,68,0.08)",
            border: "2px dashed #ef4444",
            borderRadius: 6,
            display: "flex",
            gap: 12,
            alignItems: "flex-start",
          }}
        >
          <div style={{ fontSize: 22, color: "#ef4444", lineHeight: 1, flexShrink: 0 }}>⚠</div>
          <div style={{ flex: 1 }}>
            <div
              style={{
                fontSize: 13,
                color: "#ef4444",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                marginBottom: 4,
              }}
            >
              Agent never said this
            </div>
            <div style={{ fontSize: 12, color: "var(--text-faint)", marginTop: 4 }}>
              — Click the card to jump to the approximate moment this should have been said
              (~{formatTs(targetMs)}).
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <Section label={`Actual Call${labelSuffix}`} accent={accent}>
      {verdict?.evidence && verdict.evidence.trim() ? (
        <blockquote
          style={{
            margin: 0,
            fontSize: 13,
            lineHeight: 1.55,
            fontStyle: "italic",
            color: "var(--text-primary)",
            padding: "8px 12px",
            borderRadius: 4,
            background: `${accent}0F`,
          }}
        >
          {verdict.evidence}
        </blockquote>
      ) : (
        <span style={{ color: "var(--text-faint)", fontStyle: "italic" }}>
          (No quote from transcript.)
        </span>
      )}
      {state === "partial" && (
        <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 4, fontStyle: "italic" }}>
          Partial match — see AI Verdict above for what's missing.
        </div>
      )}
      {state === "unverified" && verdict && (
        <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 4, fontStyle: "italic" }}>
          Couldn't confirm — needs a human to listen.
        </div>
      )}
      {/* Per-checkpoint Play button preserved from tracker — reviewers asked
          for an explicit play target rather than only the whole-card click. */}
      {canPlay && (
        <div style={{ marginTop: 8 }}>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onPlay(targetSec);
            }}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              height: 24,
              padding: "0 10px",
              fontSize: 11,
              fontFamily: "var(--font-mono)",
              background: "var(--bg-elev3)",
              color: "var(--text-primary)",
              border: "1px solid var(--border-subtle)",
              borderRadius: 4,
              cursor: "pointer",
            }}
            aria-label={`Play from ${formatTs(targetMs)}`}
          >
            <Play size={11} /> {formatTs(targetMs)}
          </button>
        </div>
      )}
    </Section>
  );
}

/**
 * W4.7 — read-only AI category/remediation chip.
 *
 * Renders below the AI verdict reasoning when (a) the checkpoint failed
 * or partially failed (or is unverified), and (b) Claude returned a
 * valid category. PASS checkpoints and pre-W4.7 results render nothing.
 *
 * Pure read-only — reviewer overrides the bucket via the rejections page
 * (PATCH /api/rejections/{id}), not from inside this card.
 */
function AiSuggestionChip({ verdict }: { verdict?: CheckpointVerdict }) {
  if (!verdict) return null;
  const status = verdict.status?.toLowerCase?.() ?? "";
  if (status !== "fail" && status !== "partial" && status !== "unverified") return null;
  const cat = verdict.suggested_category;
  if (!cat) return null;
  const fix = verdict.suggested_fix_required;
  const conf =
    typeof verdict.category_confidence === "number" ? verdict.category_confidence : null;
  return (
    <div
      data-slot="ai-suggestion-chip"
      className="bg-slate-100 text-slate-700 text-xs"
      style={{
        marginTop: 8,
        padding: "4px 8px",
        borderRadius: 4,
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        lineHeight: 1.3,
      }}
      title="AI-suggested rejection bucket. Reviewer can override on the rejections page."
    >
      <span style={{ fontWeight: 600, letterSpacing: "0.02em" }}>AI suggests:</span>
      <span style={{ fontFamily: "var(--font-mono)" }}>{cat}</span>
      {fix && (
        <>
          <span aria-hidden>+</span>
          <span style={{ fontFamily: "var(--font-mono)" }}>{fix}</span>
        </>
      )}
      {conf !== null && (
        <>
          <span aria-hidden>·</span>
          <span>{conf.toFixed(2)} confidence</span>
        </>
      )}
    </div>
  );
}

function ReviewerSection({
  verdict,
  callId,
  origIndex,
  onReviewVerdict,
  onRetry,
  stop,
}: {
  verdict: CheckpointVerdict;
  callId: string;
  origIndex: number;
  onReviewVerdict: (i: number, v: "pass" | "fail", notes: string) => Promise<void>;
  onRetry?: (i: number) => Promise<void>;
  stop: (e: React.MouseEvent) => void;
}) {
  // 2-step confirmation flow: pick pass/fail → pending → Commit. Cancel
  // returns to idle. Once committed, the chip shows reviewer + Edit link.
  const alreadyReviewed =
    verdict.reviewer_verdict === "pass" || verdict.reviewer_verdict === "fail";
  const [pending, setPending] = useState<"pass" | "fail" | null>(null);
  const [notes, setNotes] = useState<string>(verdict.reviewer_reasoning ?? "");
  const [submitting, setSubmitting] = useState(false);

  const openEdit = () => {
    setPending((verdict.reviewer_verdict as "pass" | "fail" | null) ?? null);
    setNotes(verdict.reviewer_reasoning ?? "");
  };

  const cancel = () => {
    setPending(null);
    setNotes(verdict.reviewer_reasoning ?? "");
  };

  const commit = async () => {
    if (!pending || submitting) return;
    setSubmitting(true);
    try {
      await onReviewVerdict(origIndex, pending, notes.trim());
      setPending(null);
    } finally {
      setSubmitting(false);
    }
  };

  // PENDING STATE
  if (pending !== null) {
    const verdictColor = pending === "pass" ? "#22c55e" : "#ef4444";
    return (
      <div
        onClick={stop}
        className="cp-reviewer-row"
        style={{
          padding: "12px 16px",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          background: "var(--bg-elev1)",
          borderTop: `1px solid ${verdictColor}33`,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div
            style={{
              fontSize: 9,
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--text-faint)",
            }}
          >
            Confirming
          </div>
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              padding: "3px 10px",
              borderRadius: 10,
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              background: `${verdictColor}26`,
              color: verdictColor,
            }}
          >
            {pending === "pass" ? "✓ Pass" : "✗ Fail"}
          </span>
          <span style={{ flex: 1 }} />
          <button
            onClick={(e) => {
              stop(e);
              cancel();
            }}
            disabled={submitting}
            style={{
              background: "transparent",
              border: "none",
              cursor: submitting ? "default" : "pointer",
              color: "var(--text-faint)",
              fontSize: 11,
              padding: "2px 6px",
            }}
          >
            Cancel
          </button>
        </div>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onClick={stop}
          placeholder={
            pending === "pass"
              ? "Anything worth noting? (optional)"
              : "What did the agent miss? (helps future reviews — optional)"
          }
          rows={2}
          style={{
            width: "100%",
            fontSize: 12,
            fontFamily: "inherit",
            padding: "6px 8px",
            background: "var(--bg-elev2)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 4,
            color: "var(--text-primary)",
            resize: "vertical",
          }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            onClick={(e) => {
              stop(e);
              commit();
            }}
            disabled={submitting}
            style={{
              padding: "5px 14px",
              fontSize: 11,
              fontWeight: 700,
              border: `1px solid ${verdictColor}`,
              borderRadius: 4,
              background: verdictColor,
              color: "#0b0b0d",
              cursor: submitting ? "default" : "pointer",
              opacity: submitting ? 0.6 : 1,
            }}
          >
            {submitting ? "Saving…" : `Commit ${pending === "pass" ? "Pass" : "Fail"}`}
          </button>
          <span style={{ fontSize: 10, color: "var(--text-faint)" }}>
            Will be saved with your reviewer identity + timestamp.
          </span>
        </div>
      </div>
    );
  }

  // REVIEWED STATE
  if (alreadyReviewed) {
    const verdictColor = verdict.reviewer_verdict === "pass" ? "#22c55e" : "#ef4444";
    return (
      <div
        onClick={stop}
        className="cp-reviewer-row"
        style={{
          padding: "10px 16px",
          display: "flex",
          gap: 8,
          alignItems: "flex-start",
          flexWrap: "wrap",
          background: "var(--bg-elev1)",
        }}
      >
        <div
          style={{
            fontSize: 9,
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            color: "var(--text-faint)",
            marginRight: 4,
            marginTop: 4,
          }}
        >
          Human Review
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                padding: "3px 10px",
                borderRadius: 10,
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                background: `${verdictColor}26`,
                color: verdictColor,
              }}
            >
              {verdict.reviewer_verdict === "pass" ? "✓ Reviewed · Pass" : "✗ Reviewed · Fail"}
            </span>
            <button
              onClick={(e) => {
                stop(e);
                openEdit();
              }}
              style={{
                background: "transparent",
                border: "1px solid var(--border-subtle)",
                color: "var(--text-faint)",
                fontSize: 10,
                padding: "2px 8px",
                borderRadius: 3,
                cursor: "pointer",
              }}
            >
              Edit
            </button>
          </div>
          {verdict.reviewer_reasoning && verdict.reviewer_reasoning.trim() && (
            <div
              style={{
                fontSize: 11,
                color: "var(--text-primary)",
                fontStyle: "italic",
                lineHeight: 1.5,
                paddingLeft: 2,
              }}
            >
              “{verdict.reviewer_reasoning}”
            </div>
          )}
        </div>
        <span style={{ flex: 1 }} />
        <div onClick={stop}>
          <CheckpointGuidelines callId={callId} checkpointName={verdict.name} />
        </div>
        {onRetry && (
          <button
            onClick={async (e) => {
              stop(e);
              await onRetry(origIndex);
            }}
            title="Re-analyze this checkpoint"
            className="cp-retry-btn"
            style={{
              background: "transparent",
              border: "none",
              cursor: "pointer",
              color: "var(--text-faint)",
              padding: "2px 4px",
              display: "flex",
              alignItems: "center",
            }}
          >
            <RotateCcw size={12} />
          </button>
        )}
      </div>
    );
  }

  // IDLE STATE
  return (
    <div
      onClick={stop}
      className="cp-reviewer-row"
      style={{
        padding: "10px 16px",
        display: "flex",
        gap: 8,
        alignItems: "center",
        flexWrap: "wrap",
        background: "var(--bg-elev1)",
      }}
    >
      <div
        style={{
          fontSize: 9,
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: "var(--text-faint)",
          marginRight: 4,
        }}
      >
        Human Review
      </div>
      <button
        // Plan §5b: 1-click pass — bypass the confirmation modal and commit
        // immediately (reviewers were ignoring the optional-notes textbox
        // anyway). Reject still routes through the modal so the reviewer
        // has to articulate why.
        onClick={async (e) => {
          stop(e);
          if (submitting) return;
          setSubmitting(true);
          try {
            await onReviewVerdict(origIndex, "pass", "");
          } finally {
            setSubmitting(false);
          }
        }}
        disabled={submitting}
        style={{
          padding: "4px 12px",
          fontSize: 11,
          fontWeight: 600,
          border: "1px solid rgba(34,197,94,0.3)",
          borderRadius: 4,
          background: "rgba(34,197,94,0.1)",
          color: "#22c55e",
          cursor: submitting ? "default" : "pointer",
          opacity: submitting ? 0.6 : 1,
        }}
      >
        {submitting ? "Saving…" : "✓ Pass"}
      </button>
      <button
        onClick={(e) => {
          stop(e);
          setPending("fail");
        }}
        style={{
          padding: "4px 12px",
          fontSize: 11,
          fontWeight: 600,
          border: "1px solid rgba(239,68,68,0.3)",
          borderRadius: 4,
          background: "rgba(239,68,68,0.1)",
          color: "#ef4444",
          cursor: "pointer",
        }}
      >
        Override → Fail
      </button>

      <span style={{ flex: 1 }} />

      <div onClick={stop}>
        <CheckpointGuidelines callId={callId} checkpointName={verdict.name} />
      </div>

      {onRetry && (
        <button
          onClick={async (e) => {
            stop(e);
            await onRetry(origIndex);
          }}
          title="Re-analyze this checkpoint"
          className="cp-retry-btn"
          style={{
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--text-faint)",
            padding: "2px 4px",
            display: "flex",
            alignItems: "center",
          }}
        >
          <RotateCcw size={12} />
        </button>
      )}
    </div>
  );
}
