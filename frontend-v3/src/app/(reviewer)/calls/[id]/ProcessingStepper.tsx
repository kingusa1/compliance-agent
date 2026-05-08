/**
 * ProcessingStepper — pipeline progress indicator that mirrors the
 * Claude Design mockup. Replaces the bare "No transcript yet" placeholder
 * while the backend is still working through the 6-step pipeline:
 *
 *   1. download_audio       — pull audio from Storage
 *   2. transcribe           — STT + diarisation
 *   3. detect_metadata      — names, supplier, script variant
 *   4. analyze_checkpoints  — LLM batch analysis (N of M)
 *   5. score                — derive call.score / compliant
 *   6. finalize             — commit + HITL routing
 *
 * Step state is derived from the call detail fields the page already
 * fetches; no new endpoint required. The detail query auto-polls every
 * 3s while the call is processing (see useCallDetailQuery), so this
 * component repaints as each step lands.
 */
"use client";

import { Loader2, Check, AlertTriangle } from "lucide-react";

type Call = {
  status?: string | null;
  reason?: string | null;
  transcript?: string | null;
  detected_supplier?: string | null;
  script_id?: string | null;
  agent_name?: string | null;
  customer_name?: string | null;
  // backend returns either a numeric score or a stringified one — accept both
  score?: number | string | null;
};

type StepState = "done" | "active" | "pending" | "failed";

interface Step {
  key: string;
  label: string;
  hint?: string;
  state: StepState;
}

function deriveSteps(call: Call | undefined, checkpointsCount: number): Step[] {
  const failed = call?.status === "failed";
  const completed = call?.status === "completed";
  const hasTranscript = !!call?.transcript;
  const hasMetadata =
    !!call?.detected_supplier || !!call?.agent_name || !!call?.customer_name;
  const hasScript = !!call?.script_id;
  const hasCheckpoints = checkpointsCount > 0;
  const hasScore = call?.score !== undefined && call?.score !== null;

  // Mark the first incomplete step "active"; everything before it "done";
  // everything after it "pending". If the call failed, mark the active
  // step "failed".
  const stages: { key: string; label: string; hint?: string; isDone: boolean }[] = [
    { key: "upload", label: "Audio uploaded", hint: "File received and stored", isDone: true },
    {
      key: "transcribe",
      label: "Transcribing audio",
      hint: hasTranscript ? "Speech-to-text complete" : "AssemblyAI Universal-3 Pro running",
      isDone: hasTranscript,
    },
    {
      key: "detect",
      label: "Detecting metadata",
      hint: hasMetadata
        ? `Agent: ${call?.agent_name ?? "?"}, Supplier: ${call?.detected_supplier ?? "?"}`
        : "Names, supplier, script variant",
      isDone: hasMetadata,
    },
    {
      key: "match-script",
      label: "Matching script",
      hint: hasScript ? "Script picked" : "Choosing supplier-specific script",
      isDone: hasScript,
    },
    {
      key: "analyze",
      label: hasCheckpoints
        ? `Analyzing checkpoints (${checkpointsCount})`
        : "Analyzing checkpoints",
      hint: hasCheckpoints
        ? "Claude scoring each script line"
        : "Waiting for analyzer batch",
      isDone: completed,
    },
    {
      key: "score",
      label: "Final score",
      hint: hasScore ? `Score: ${call?.score}` : "Computing compliance + HITL routing",
      isDone: completed && hasScore,
    },
  ];

  const firstIncomplete = stages.findIndex((s) => !s.isDone);

  return stages.map((s, i) => {
    if (firstIncomplete === -1) return { ...s, state: "done" as const };
    if (i < firstIncomplete) return { ...s, state: "done" as const };
    if (i === firstIncomplete) {
      return { ...s, state: failed ? ("failed" as const) : ("active" as const) };
    }
    return { ...s, state: "pending" as const };
  });
}

export function ProcessingStepper({
  call,
  checkpointsCount,
}: {
  call: Call | undefined;
  checkpointsCount: number;
}) {
  const steps = deriveSteps(call, checkpointsCount);
  const failed = call?.status === "failed";
  const completed = call?.status === "completed";

  return (
    <div className="flex flex-col items-stretch gap-4 px-8 py-10">
      <div className="text-center">
        <div className="text-sm font-medium text-[var(--text-default)]">
          {completed
            ? "Pipeline complete"
            : failed
              ? "Pipeline failed"
              : "Processing your call…"}
        </div>
        <div className="mt-1 text-[12px] text-[var(--text-muted)]">
          {failed
            ? call?.reason ?? "Run interrupted"
            : completed
              ? "Refresh if checkpoints don't appear immediately."
              : "This page auto-refreshes every 3 seconds."}
        </div>
      </div>

      <ol className="mx-auto w-full max-w-md space-y-3">
        {steps.map((s) => {
          const iconBg =
            s.state === "done"
              ? "bg-emerald-100 text-emerald-700"
              : s.state === "active"
                ? "bg-blue-100 text-blue-700"
                : s.state === "failed"
                  ? "bg-red-100 text-red-700"
                  : "bg-slate-100 text-slate-400";
          const titleColor =
            s.state === "done" || s.state === "active" || s.state === "failed"
              ? "text-[var(--text-default)]"
              : "text-[var(--text-muted)]";
          return (
            <li key={s.key} className="flex items-start gap-3">
              <span
                className={`mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-full ${iconBg}`}
              >
                {s.state === "done" ? (
                  <Check className="h-4 w-4" />
                ) : s.state === "active" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : s.state === "failed" ? (
                  <AlertTriangle className="h-4 w-4" />
                ) : (
                  <span className="block h-2 w-2 rounded-full bg-slate-300" />
                )}
              </span>
              <div className="flex-1">
                <div className={`text-sm ${titleColor}`}>{s.label}</div>
                {s.hint ? (
                  <div className="text-[12px] text-[var(--text-muted)]">{s.hint}</div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
