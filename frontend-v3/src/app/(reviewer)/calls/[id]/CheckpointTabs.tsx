"use client";

import { useState } from "react";
import { Check, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useCallCheckpointsQuery } from "@/lib/queries/reviewer";
import { useReviewCheckpoint } from "@/lib/mutations/reviewer";

/**
 * CheckpointTabs — left list of script checkpoints with pass/fail toggle
 * and an inline reviewer note.
 *
 * Each card mirrors the call.jsx mock:
 *   CP01  Identity verification
 *   Required: Confirm full name and supply address
 *   [✓ Pass]  [✕ Fail]            ← toggle group
 *   <reviewer note textarea — collapsible>
 *
 * The toggle PUTs to /api/calls/{id}/checkpoint/{idx}/review immediately
 * (optimistic; if the mutation fails the toast surfaces the error and the
 * cache invalidation pulls the truth back).
 *
 * Initial passed/fail state is derived from `aiResults` (from the call's
 * checkpoint_results JSON, parsed by the parent).
 */
export type CheckpointAIResult = {
  rule_text: string;
  passed: boolean;
  excerpt: string | null;
};

export function CheckpointTabs({
  callId,
  aiResults,
}: {
  callId: string;
  /** Parsed `checkpoint_results` from /api/calls/{id} — same length as
   * the script-checkpoints list (1:1 by index). May be empty if the
   * pipeline hasn't run yet. */
  aiResults: CheckpointAIResult[];
}) {
  const checkpoints = useCallCheckpointsQuery(callId);
  const review = useReviewCheckpoint();

  if (checkpoints.isLoading) return <CheckpointSkeleton />;
  if (checkpoints.isError || !checkpoints.data) {
    return (
      <div className="p-5 text-[13px] text-[var(--red-fail)]">
        Couldn’t load checkpoints
      </div>
    );
  }

  const cps = checkpoints.data.checkpoints ?? [];
  if (cps.length === 0) {
    return (
      <div className="p-5 text-[13px] text-[var(--text-muted)]">
        No script checkpoints attached to this call.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 p-5">
      {cps.map((cp, i) => (
        <CheckpointCard
          key={`${cp.section}-${cp.name}`}
          callId={callId}
          index={i}
          name={cp.name}
          required={cp.required}
          aiPassed={aiResults[i]?.passed ?? null}
          excerpt={aiResults[i]?.excerpt ?? null}
          onSubmit={(verdict, notes) =>
            review.mutate({ callId, index: i, verdict, notes })
          }
          submitting={review.isPending}
        />
      ))}
    </div>
  );
}

function CheckpointCard({
  callId: _callId,
  index,
  name,
  required,
  aiPassed,
  excerpt,
  onSubmit,
  submitting,
}: {
  callId: string;
  index: number;
  name: string;
  required: string;
  aiPassed: boolean | null;
  excerpt: string | null;
  onSubmit: (verdict: "pass" | "fail", notes?: string) => void;
  submitting: boolean;
}) {
  const [verdict, setVerdict] = useState<"pass" | "fail" | null>(
    aiPassed === true ? "pass" : aiPassed === false ? "fail" : null,
  );
  const [notesOpen, setNotesOpen] = useState(false);
  const [notes, setNotes] = useState("");

  const isPass = verdict === "pass";
  const isFail = verdict === "fail";

  const borderTone = isPass
    ? "var(--emerald-pass)"
    : isFail
      ? "var(--red-fail)"
      : "var(--border-strong)";

  return (
    <div
      data-testid="checkpoint-card"
      data-cp-index={index}
      className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3.5"
      style={{ borderLeft: `2px solid ${borderTone}` }}
    >
      <div className="mb-1 flex items-center gap-2">
        <span className="font-mono text-[11px] text-[var(--text-dim)]">
          CP{String(index + 1).padStart(2, "0")}
        </span>
        <span className="text-[14px] font-medium text-[var(--text-primary)]">{name}</span>
      </div>
      <div className="mb-2.5 text-[13px] text-[var(--text-muted)]">{required}</div>

      <div className="inline-flex overflow-hidden rounded-md border border-[var(--border-subtle)]">
        <button
          type="button"
          aria-pressed={isPass}
          data-testid="cp-pass"
          onClick={() => {
            setVerdict("pass");
            onSubmit("pass", notes || undefined);
          }}
          disabled={submitting}
          className="flex items-center gap-1.5 border-r border-[var(--border-subtle)] px-3 py-1 text-[12px] font-medium"
          style={{
            background: isPass
              ? "color-mix(in oklab, var(--emerald-pass) 10%, transparent)"
              : "transparent",
            color: isPass ? "var(--emerald-pass)" : "var(--text-muted)",
          }}
        >
          <Check className="h-3 w-3" />
          Pass
        </button>
        <button
          type="button"
          aria-pressed={isFail}
          data-testid="cp-fail"
          onClick={() => {
            setVerdict("fail");
            setNotesOpen(true);
            onSubmit("fail", notes || undefined);
          }}
          disabled={submitting}
          className="flex items-center gap-1.5 px-3 py-1 text-[12px] font-medium"
          style={{
            background: isFail
              ? "color-mix(in oklab, var(--red-fail) 10%, transparent)"
              : "transparent",
            color: isFail ? "var(--red-fail)" : "var(--text-muted)",
          }}
        >
          <X className="h-3 w-3" />
          Fail
        </button>
      </div>

      <Button
        variant="link"
        size="sm"
        className="ml-2 h-7 text-[12px]"
        onClick={() => setNotesOpen((o) => !o)}
      >
        {notesOpen ? "Hide note" : "Add note"}
      </Button>

      {notesOpen && (
        <div className="mt-3 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-2.5">
          <div className="mb-1.5 text-[11px] uppercase tracking-wide text-[var(--text-dim)]">
            Reviewer note
          </div>
          <Textarea
            placeholder="Why pass / fail? (optional)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="min-h-[60px] text-[13px]"
          />
          <div className="mt-2 flex justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setNotesOpen(false)}
              disabled={submitting}
            >
              Done
            </Button>
            <Button
              size="sm"
              onClick={() => {
                if (verdict) onSubmit(verdict, notes || undefined);
              }}
              disabled={!verdict || submitting}
            >
              Save note
            </Button>
          </div>
        </div>
      )}

      {excerpt ? (
        <div className="mt-3 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-2.5 text-[12px] text-[var(--text-muted)]">
          <span className="text-[var(--text-dim)]">Excerpt: </span>
          {excerpt}
        </div>
      ) : null}
    </div>
  );
}

function CheckpointSkeleton() {
  return (
    <div className="flex flex-col gap-2 p-5">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3.5"
        >
          <Skeleton className="mb-2 h-4 w-1/3" />
          <Skeleton className="mb-3 h-3 w-2/3" />
          <Skeleton className="h-7 w-32" />
        </div>
      ))}
    </div>
  );
}
