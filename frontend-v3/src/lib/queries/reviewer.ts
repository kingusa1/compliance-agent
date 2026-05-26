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
    // 2026-05-23 — /queue subscribes to Supabase Realtime on calls +
    // review_sessions (see app/(reviewer)/queue/page.tsx). Every
    // postgres_changes event invalidates this key, so polling is
    // pure waste + caused the visible refresh flicker the owner
    // called out. refetchOnReconnect (inherited from QueryProvider)
    // covers the rare case of a websocket drop.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
  });
}

// 2026-05-26 — In-flight statuses for which the call detail must auto-
// refresh even when the SSE feed is silent. The SSE path is the primary
// invalidation mechanism, but production observation shows ~15% of
// per-call subscriptions never receive the matching events (a per-call
// queue is created but the publish loop never lands an event into it —
// likely a fan-out gap between worker threads and the asyncio loop).
// Without a safety-net poll, the reviewer sees a stuck "Processing your
// call…" pane long after the pipeline has finalized. The poll only runs
// while the call is in-flight and turns itself OFF the moment the API
// returns a final status, so completed calls remain SSE-driven.
const _IN_FLIGHT_STATUSES = new Set([
  "processing",
  "queued",
  "transcribing",
  "analyzing",
  "scoring",
  "pending",
  "needs_classification",
]);

function _isInFlight(call: Call | undefined): boolean {
  const s = (call?.status ?? "").toString().toLowerCase();
  return _IN_FLIGHT_STATUSES.has(s);
}

export function useCallDetailQuery(id: string) {
  return useQuery({
    queryKey: reviewerKeys.callDetail(id),
    queryFn: () => fetchCallDetail(id),
    enabled: !!id,
    // 2026-05-23 — primary invalidation is the per-call SSE feed via
    // useCallEvents(id). 2026-05-26 — added a 3 s safety-net poll
    // ONLY while ``data.status`` is in-flight. Stops automatically once
    // the call reaches a terminal status, so completed reviews are still
    // free of polling churn. ``Infinity`` staleTime is preserved so SSE
    // invalidation continues to win when it does fire.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    refetchInterval: (query) => (_isInFlight(query.state.data) ? 3000 : false),
  });
}

export function useCallWordsQuery(id: string, callStatus?: string) {
  return useQuery({
    queryKey: reviewerKeys.callWords(id),
    queryFn: () => fetchCallWords(id),
    enabled: !!id,
    // Word data is immutable per call revision; refresh only via explicit
    // invalidation when the pipeline writes new word_data.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    // 2026-05-26 — Words endpoint 404s mid-pipeline (the file is written
    // at finalize). Single-shot retry stops the query forever, so opening
    // a call right after upload leaves the transcript blank even after
    // the pipeline finishes. Safety-net poll while the call is in-flight
    // mirrors the callDetail / callCheckpoints policy and turns off
    // automatically once a terminal status arrives.
    retry: (count) => count < 1,
    refetchInterval: callStatus && _IN_FLIGHT_STATUSES.has(callStatus.toLowerCase()) ? 3000 : false,
  });
}

export function useCallCheckpointsQuery(id: string, callStatus?: string) {
  return useQuery({
    queryKey: reviewerKeys.callCheckpoints(id),
    queryFn: () => fetchCallCheckpoints(id),
    enabled: !!id,
    // 2026-05-23 — SSE drives invalidation on every pipeline-step
    // transition. 2026-05-26 — safety-net poll while the parent call is
    // in-flight so checkpoint cards fill in even when SSE drops events.
    // ``callStatus`` is hoisted in by the page-level hook so the query
    // can decide without an extra round-trip to its own cache.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    refetchInterval: callStatus && _IN_FLIGHT_STATUSES.has(callStatus.toLowerCase()) ? 3000 : false,
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
