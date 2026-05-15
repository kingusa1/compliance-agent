// View-layer derivation: collapse the backend's checkpoint status fields
// into the five director-facing display states. Pure function, no
// schema change on the backend.
//
// The distinction that matters to a director:
//   - `said_wrong` — the agent said something but it contradicts the script
//   - `not_said`   — the agent never raised the topic at all (evidence empty)
//
// Ported from `frontend/src/lib/checkpoint-state.ts` (main branch). Kept
// duck-typed (`CheckpointStateInput`) so it consumes either v1's
// `CheckpointResult` or v3's `CheckpointVerdict` without a circular import.

export type DisplayState =
  | "passed"
  | "partial"
  | "said_wrong"
  | "not_said"
  | "unverified"
  | "not_scored";

export interface CheckpointStateInput {
  status: string;
  evidence: string | null;
  needs_review?: boolean;
}

export function deriveDisplayState(cp: CheckpointStateInput): DisplayState {
  // Plan §5b: per-checkpoint statuses are reduced to ONLY pass / partial /
  // non-compliant. The yellow "needs_review" tile was confusing reviewers
  // ("review what?"). needs_review checkpoints are folded into "partial" so
  // they're surfaced as amber but inside the canonical 3-state model.
  //
  // 2026-05-15: ``not_scored`` is a synthetic backend status emitted by
  // pipeline._normalize_checkpoint_results when the analyzer's per-segment
  // slice missed a rule. The reviewer needs to see it as a muted grey
  // placeholder (not "Partial"), so it's its own display state.
  if (cp.status === "not_scored") return "not_scored";
  if (cp.needs_review) return "partial";
  if (cp.status === "unverified") return "partial";
  if (cp.status === "pass") return "passed";
  if (cp.status === "partial") return "partial";
  if (cp.status === "fail") {
    return cp.evidence && cp.evidence.trim() ? "said_wrong" : "not_said";
  }
  return "partial";
}

export function displayStateLabel(s: DisplayState): string {
  switch (s) {
    case "passed": return "Passed";
    case "partial": return "Partial";
    case "said_wrong": return "Non-Compliant";
    case "not_said": return "Non-Compliant";
    case "unverified": return "Partial";
    case "not_scored": return "Not Scored";
  }
}

export function displayStateAccent(s: DisplayState): string {
  switch (s) {
    case "passed": return "#22c55e";   // emerald
    case "partial": return "#f59e0b";  // amber
    case "said_wrong": return "#ef4444"; // red
    case "not_said": return "#ef4444";   // red
    case "unverified": return "#f59e0b"; // amber (fallback only)
    case "not_scored": return "#94a3b8"; // slate — muted, not alarming
  }
}
