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
  | "unverified";

export interface CheckpointStateInput {
  status: string;
  evidence: string | null;
  needs_review?: boolean;
}

export function deriveDisplayState(cp: CheckpointStateInput): DisplayState {
  if (cp.needs_review || cp.status === "unverified") return "unverified";
  if (cp.status === "pass") return "passed";
  if (cp.status === "partial") return "partial";
  if (cp.status === "fail") {
    return cp.evidence && cp.evidence.trim() ? "said_wrong" : "not_said";
  }
  return "unverified";
}

export function displayStateLabel(s: DisplayState): string {
  switch (s) {
    case "passed": return "Passed";
    case "partial": return "Partial";
    case "said_wrong": return "Said wrong";
    case "not_said": return "Not said at all";
    case "unverified": return "Needs review";
  }
}

export function displayStateAccent(s: DisplayState): string {
  switch (s) {
    case "passed": return "#22c55e";
    case "partial": return "#8b5cf6";
    case "said_wrong": return "#ef4444";
    case "not_said": return "#ef4444";
    case "unverified": return "#f59e0b";
  }
}
