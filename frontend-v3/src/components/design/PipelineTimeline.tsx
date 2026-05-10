"use client";

/**
 * PipelineTimeline — visualises every step the AI took for a single call.
 *
 * Renders a 5-stage stepper showing what each layer of the pipeline did:
 *   1. Deepgram Nova-3 transcription + diarisation
 *   2. Speaker-label resolution (signal-based agent vs customer)
 *   3. Supplier auto-detection + canonicalisation
 *   4. Script auto-match (supplier × call_type → script row)
 *   5. Opus 4.7 checkpoint analysis + scoring
 *
 * Each step shows: status (done / running / failed), the AI's output for
 * that step, and a one-line "why" so a reviewer can see exactly what
 * the AI decided AND what evidence it used.
 *
 * No backend changes needed — every value is already on the call detail
 * payload (transcript, agent_name, customer_name, detected_supplier,
 * script_id, score, deepgram_metadata).
 */
import { CheckCircle2, Circle, AlertTriangle, Mic, Users, Building2, FileText, Sparkles } from "lucide-react";

type Step = {
  icon: typeof Mic;
  label: string;
  detail: string | null;
  status: "ok" | "missing" | "warn";
  hint: string;
};

interface CallLite {
  transcript?: string | null;
  agent_name?: string | null;
  customer_name?: string | null;
  detected_supplier?: string | null;
  script_id?: string | null;
  score?: string | null;
  duration_seconds?: number | null;
  deepgram_metadata?: unknown;
  call_type?: string | null;
  status?: string | null;
}

function durationLabel(s?: number | null): string {
  if (typeof s !== "number" || s <= 0) return "—";
  const m = Math.floor(s / 60);
  const ss = Math.floor(s % 60).toString().padStart(2, "0");
  return `${m}:${ss}`;
}

function callTypeLabel(t?: string | null): string {
  if (!t || t === "full") return "Auto-detected";
  return t
    .split("_")
    .map((w) => w[0]?.toUpperCase() + w.slice(1))
    .join(" ");
}

export function PipelineTimeline({ call }: { call: CallLite }) {
  const dur = durationLabel(call.duration_seconds);
  const transcriptLen = (call.transcript ?? "").length;
  const supplier = call.detected_supplier ?? "Unknown";
  const supplierKnown = supplier && supplier !== "Unknown" && supplier !== "";

  const steps: Step[] = [
    {
      icon: Mic,
      label: "Deepgram Nova-3 transcription",
      detail: transcriptLen > 0 ? `${dur} audio · ${transcriptLen} chars` : "Not yet transcribed",
      status: transcriptLen > 0 ? "ok" : "missing",
      hint: "Speaker-diarised, PII-redacted (UK NI, phone numbers), en-GB locale.",
    },
    {
      icon: Users,
      label: "Speaker labels — Agent / Customer",
      detail:
        call.agent_name && call.customer_name
          ? `Agent: ${call.agent_name} · Customer: ${call.customer_name}`
          : call.customer_name
            ? `Customer: ${call.customer_name}`
            : call.agent_name
              ? `Agent: ${call.agent_name}`
              : "Names not detected",
      status: call.agent_name || call.customer_name ? "ok" : "warn",
      hint: "Speaker turn picked by broker-language signals (\"my name is\", \"your electricity\", supplier mentions).",
    },
    {
      icon: Building2,
      label: "Supplier auto-detection",
      detail: supplierKnown ? supplier : "Unknown — could not identify supplier",
      status: supplierKnown ? "ok" : "warn",
      hint: "LLM scans the transcript for supplier name + tariff cues, canonicalises to one of 6 known suppliers, inherits from sibling calls when needed.",
    },
    {
      icon: FileText,
      label: "Script auto-match",
      detail: call.script_id
        ? `Matched (${callTypeLabel(call.call_type)})`
        : "No script matched — using Third-Party Disclosure rule",
      status: call.script_id ? "ok" : "warn",
      hint: "Joins supplier × call_type → picks the right verbal-contract or LOA script from the supplier catalogue.",
    },
    {
      icon: Sparkles,
      label: "Opus 4.7 checkpoint analysis",
      detail: call.score ? `Score ${call.score}` : "Pending analysis",
      status: call.score ? "ok" : "missing",
      hint: "Per-checkpoint AI reasoning + evidence quote, with regex pre-pass for high-precision Critical hits.",
    },
  ];

  return (
    <div
      className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-4"
      data-testid="pipeline-timeline"
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <div className="text-[12px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
            What the AI did
          </div>
          <div className="text-[14px] text-[var(--text-primary)]">
            5-stage pipeline · Deepgram → Speaker labels → Supplier → Script → Opus 4.7
          </div>
        </div>
        <span className="rounded-md bg-[var(--bg-elev3)] px-2 py-1 text-[11px] font-medium text-[var(--text-muted)]">
          {call.status === "completed" ? "Complete" : call.status ?? "—"}
        </span>
      </div>
      <ol className="flex flex-col gap-2">
        {steps.map((s, i) => {
          const StepIcon = s.icon;
          const StatusIcon =
            s.status === "ok" ? CheckCircle2 : s.status === "warn" ? AlertTriangle : Circle;
          const statusColor =
            s.status === "ok" ? "#10b981" : s.status === "warn" ? "#f59e0b" : "#6b7280";
          return (
            <li
              key={i}
              className="flex items-start gap-3 rounded-md border border-transparent px-2 py-2 hover:border-[var(--border-subtle)] hover:bg-[var(--bg-elev2)]"
              title={s.hint}
            >
              <StepIcon className="mt-0.5 size-4 shrink-0 text-[var(--text-faint)]" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-medium text-[var(--text-primary)]">
                    {i + 1}. {s.label}
                  </span>
                  <StatusIcon className="size-3.5" style={{ color: statusColor }} />
                </div>
                <div className="mt-0.5 text-[12px] text-[var(--text-muted)]">
                  {s.detail}
                </div>
                <div className="mt-1 text-[11px] italic text-[var(--text-faint)]">
                  {s.hint}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
