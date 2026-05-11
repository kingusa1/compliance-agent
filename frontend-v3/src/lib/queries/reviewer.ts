/**
 * Reviewer-lane TanStack Query keys + queryFns.
 *
 * Lives alongside the reviewer pages so R1's root `lib/queries.ts` doesn't
 * grow unbounded. Re-exports `apiFetch` so consumers don't need a separate
 * import. Mutation wrappers + invalidation rules live in
 * `lib/mutations/reviewer.ts`.
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch, getCall, getQueue, type Call, type QueueResponse } from "@/lib/api";

// ── Domain types ──────────────────────────────────────────────────
//
// Most of these mirror the backend's openapi shapes loosely — strict openapi
// types break when the backend evolves faster than codegen, so we keep these
// permissive at the boundary and let the page narrow on render.

export type QueueFilter = "all" | "unclaimed" | "in_review" | "today";

export type WordToken = {
  word: string;
  start: number; // seconds
  end: number; // seconds
  /** Raw Deepgram speaker id (number-as-string or number). Numeric and
   *  meaningless on its own — use `role` for display. */
  speaker?: string | number | null;
  /** Resolved AGENT / CUSTOMER label assigned by the backend's
   *  `_detect_agent_speaker` heuristic. */
  role?: "AGENT" | "CUSTOMER" | string | null;
  confidence?: number | null;
};

export type WordsResponse = {
  words: WordToken[];
};

export type ScriptCheckpoint = {
  section: number;
  name: string;
  required: string;
  key_phrases: string[];
  customer_response_required?: boolean;
  strictness?: string;
  // W1.6 (v3-watt-coverage): script-line number when known. Watt reviewers
  // operate on "amendment for line 11-14" syntax. May be null (older
  // scripts) or an integer.
  line_number?: number | null;
};

export type ScriptCheckpointsResponse = {
  checkpoints: ScriptCheckpoint[];
};

export type Flag = {
  id: string;
  call_id: string;
  rule_id: string;
  severity: "HIGH" | "MEDIUM" | "LOW" | string;
  reason: string;
  word_start: number;
  word_end: number;
  evidence?: string | null;
  risk_tag?: string | null;
  created_at?: string | null;
};

export type Directive = {
  id: string;
  call_id: string;
  agent_name?: string | null;
  text: string;
  status?: string | null;
  created_at?: string | null;
};

export type Finding = {
  id: string;
  call_id: string;
  call_filename?: string | null;
  agent_name?: string | null;
  rule_id: string;
  severity: string;
  reason: string;
  status?: string | null;
  created_at: string;
};

export type FindingsResponse = {
  findings: Finding[];
  total?: number;
  next_cursor?: string | null;
};

export type FindingsParams = {
  severity?: string;
  rule_id?: string;
  agent?: string;
  cursor?: string;
  limit?: number;
};

export type SavedView = {
  id: string;
  name: string;
  endpoint: string;
  filters: Record<string, unknown>;
  is_shared?: boolean;
  created_at?: string | null;
};

export type SavedViewsResponse = {
  views: SavedView[];
};

export type ChatCitation = {
  id: string; // e.g. "T1" or "S5"
  kind: "transcript" | "source" | string;
  word_start?: number;
  word_end?: number;
  quote?: string;
  timestamp?: string;
};

export type ChatMessage = {
  role: "user" | "assistant" | "system";
  content: string;
  citations?: ChatCitation[];
};

export type CallAudioUrlResponse = {
  url: string;
};

// ── Query keys ────────────────────────────────────────────────────

export const reviewerKeys = {
  queue: (filter?: QueueFilter) => ["queue", filter ?? "all"] as const,
  callDetail: (id: string) => ["call", id, "detail"] as const,
  callWords: (id: string) => ["call", id, "words"] as const,
  callCheckpoints: (id: string) => ["call", id, "checkpoints"] as const,
  callFlags: (id: string) => ["call", id, "flags"] as const,
  callDirectives: (id: string) => ["call", id, "directives"] as const,
  callAudioUrl: (id: string) => ["call", id, "audio-url"] as const,
  findings: (params?: FindingsParams) => ["findings", params ?? {}] as const,
  savedViews: () => ["saved-views"] as const,
};

// ── Fetchers ──────────────────────────────────────────────────────

function _callPath(id: string, suffix = ""): string {
  return `/api/calls/${encodeURIComponent(id)}${suffix}`;
}

export function fetchQueue(filter: QueueFilter = "all"): Promise<QueueResponse> {
  // Map UI filters to backend filter names. The backend accepts "all" /
  // "unclaimed" / "in_review" / "today"; the type alias mirrors that.
  return getQueue(filter);
}

export function fetchCallDetail(id: string): Promise<Call> {
  return getCall(id);
}

export function fetchCallWords(id: string): Promise<WordsResponse> {
  return apiFetch<WordsResponse>(_callPath(id, "/words"));
}

export function fetchCallCheckpoints(id: string): Promise<ScriptCheckpointsResponse> {
  return apiFetch<ScriptCheckpointsResponse>(_callPath(id, "/script-checkpoints"));
}

export function fetchCallFlags(id: string): Promise<{ flags: Flag[] }> {
  return apiFetch<{ flags: Flag[] }>(_callPath(id, "/flags"));
}

export function fetchCallDirectives(id: string): Promise<{ directives: Directive[] }> {
  return apiFetch<{ directives: Directive[] }>(_callPath(id, "/directives"));
}

export function fetchCallAudioUrl(id: string): Promise<CallAudioUrlResponse> {
  return apiFetch<CallAudioUrlResponse>(_callPath(id, "/audio-url"));
}

export function fetchFindings(params: FindingsParams = {}): Promise<FindingsResponse> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const tail = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<FindingsResponse>(`/api/findings${tail}`);
}

export function fetchSavedViews(): Promise<SavedViewsResponse> {
  return apiFetch<SavedViewsResponse>(`/api/saved-views`);
}

// ── Hook helpers ──────────────────────────────────────────────────
//
// Page components prefer these over raw `useQuery(...)` calls so the cache
// key + queryFn live in one place. They do NOT pass `enabled` — callers
// can wrap with `useQuery({...useQueueQuery(filter), enabled: ...})` if
// they need conditional fetch.

export function useQueueQuery(filter: QueueFilter = "all") {
  return useQuery({
    queryKey: reviewerKeys.queue(filter),
    queryFn: () => fetchQueue(filter),
    // Queue should feel live — refetch on focus + every 30s while open.
    staleTime: 5_000,
    refetchInterval: 30_000,
  });
}

export function useCallDetailQuery(id: string) {
  return useQuery({
    queryKey: reviewerKeys.callDetail(id),
    queryFn: () => fetchCallDetail(id),
    enabled: !!id,
    // Auto-refresh while the pipeline is still working — matches the v1
    // SSE-on-stream behaviour without re-wiring the stream endpoint.
    // Stops polling once the call is in a terminal state.
    refetchInterval: (q) => {
      const status = (q.state.data as { status?: string } | undefined)?.status;
      if (status === "completed" || status === "failed") return false;
      return 3000;
    },
  });
}

export function useCallWordsQuery(id: string) {
  return useQuery({
    queryKey: reviewerKeys.callWords(id),
    queryFn: () => fetchCallWords(id),
    enabled: !!id,
    staleTime: 5 * 60 * 1000, // word data is immutable per call revision
    retry: (count) => count < 1, // word file is 404 until pipeline completes
  });
}

export function useCallCheckpointsQuery(id: string) {
  return useQuery({
    queryKey: reviewerKeys.callCheckpoints(id),
    queryFn: () => fetchCallCheckpoints(id),
    enabled: !!id,
    // Mirror useCallDetailQuery polling so checkpoint cards appear as
    // soon as the analyzer writes them.
    refetchInterval: 3000,
  });
}

export function useCallFlagsQuery(id: string) {
  return useQuery({
    queryKey: reviewerKeys.callFlags(id),
    queryFn: () => fetchCallFlags(id),
    enabled: !!id,
  });
}

export function useCallDirectivesQuery(id: string) {
  return useQuery({
    queryKey: reviewerKeys.callDirectives(id),
    queryFn: () => fetchCallDirectives(id),
    enabled: !!id,
  });
}

/**
 * Signed URL for a call's audio in Supabase Storage. Backend returns a
 * 1-hour TTL — we set staleTime to 50min so we proactively refetch before
 * the URL expires. 404 is expected for legacy pre-Storage uploads; callers
 * should treat the absence of `data.url` as "no audio available".
 */
export function useCallAudioUrlQuery(id: string) {
  return useQuery({
    queryKey: reviewerKeys.callAudioUrl(id),
    queryFn: () => fetchCallAudioUrl(id),
    enabled: !!id,
    staleTime: 50 * 60 * 1000,
    retry: false,
  });
}

export function useFindingsQuery(params: FindingsParams = {}) {
  return useQuery({
    queryKey: reviewerKeys.findings(params),
    queryFn: () => fetchFindings(params),
  });
}

export function useSavedViewsQuery() {
  return useQuery({
    queryKey: reviewerKeys.savedViews(),
    queryFn: () => fetchSavedViews(),
    staleTime: 60_000,
  });
}
