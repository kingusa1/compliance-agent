import { getAccessToken, supabase } from "@/lib/supabase";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

/**
 * Typed error for apiFetch. Callers can narrow via `e instanceof ApiError`
 * and branch on `e.status` (e.g. 401 → force re-login, 403 → role mismatch).
 */
export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, statusText: string, body: string) {
    super(`${status} ${statusText}: ${body}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function _fetch(path: string, init: RequestInit, token: string | null): Promise<Response> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string>) || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  return fetch(`${API_URL}${path}`, { ...init, headers });
}

/**
 * Fetch helper that injects a Supabase Bearer JWT when a session exists.
 * On 401 with a token present, performs one refresh-and-retry so expired
 * JWTs don't surface as transient errors. Throws ApiError on any other
 * non-2xx; callers can branch on `err.status`.
 */
export async function apiFetch<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  let token = await getAccessToken();
  let r = await _fetch(path, init, token);

  // One-shot refresh-and-retry on 401 (handles silent token expiry).
  if (r.status === 401 && token) {
    const { data, error } = await supabase.auth.refreshSession();
    if (!error && data.session?.access_token) {
      token = data.session.access_token;
      r = await _fetch(path, init, token);
    }
  }

  if (!r.ok) {
    const text = await r.text();
    throw new ApiError(r.status, r.statusText, text);
  }
  return r.json() as Promise<T>;
}

// ── User / role ───────────────────────────────────────────────────
export type Me = {
  id: string;
  email: string;
  name: string;
  role: "reviewer" | "lead" | "admin";
};

export async function getMe(): Promise<Me> {
  return apiFetch<Me>("/api/me");
}

// ── Calls ─────────────────────────────────────────────────────────
export interface Call {
  id: string;
  filename: string;
  file_size: number | null;
  duration_seconds: number | null;
  status: string;
  transcript: string | null;
  compliant: boolean | null;
  reason: string | null;
  excerpt: string | null;
  agent_name: string | null;
  customer_name: string | null;
  rule_id: string;
  created_at: string;
  completed_at: string | null;
  script_id: string | null;
  checkpoint_results: string | null;
  score: string | null;
  detected_supplier: string | null;
  compliance_status?: string | null;
  review_status?: string | null;
  revision?: number;
  call_ref?: string | null;
  slug?: string | null;
}

export interface CallListResponse {
  calls: Call[];
  total: number;
}

export type CallsListParams = {
  limit?: number;
  offset?: number;
};

export async function getCalls(params: CallsListParams = {}): Promise<CallListResponse> {
  const qs = new URLSearchParams();
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  const tail = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<CallListResponse>(`/api/calls${tail}`);
}

export async function getCall(id: string): Promise<Call> {
  return apiFetch<Call>(`/api/calls/${encodeURIComponent(id)}`);
}

// ── Queue ─────────────────────────────────────────────────────────
export type QueueMetrics = {
  backlog: number;
  in_review: number;
  avg_turnaround_min: number;
  reviewed_today: number;
  leaderboard: { reviewer_id: string; name: string; count: number }[];
};

export type QueueCall = {
  id: string;
  filename: string;
  customer_name: string | null;
  agent_name: string | null;
  score: string | null;
  supplier: string | null;
  duration: number | null;
  created_at: string | null;
  review_status: string;
  compliance_status: string;
  flagged_count: number;
  claimed_by: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
};

export type QueueResponse = {
  metrics: QueueMetrics;
  calls: QueueCall[];
};

export async function getQueue(filter: string = "all"): Promise<QueueResponse> {
  return apiFetch<QueueResponse>(`/api/queue?filter=${encodeURIComponent(filter)}`);
}

// ── Customers ─────────────────────────────────────────────────────
export type CustomerListItem = {
  id: string;
  slug: string;
  legal_name: string;
  trading_as?: string | null;
  total_deals?: number;
  total_calls?: number;
  last_activity_at?: string | null;
};

export type CustomerListResponse = {
  customers: CustomerListItem[];
  total: number;
};

export async function getCustomers(params: { q?: string; limit?: number; offset?: number } = {}): Promise<CustomerListResponse> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const tail = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<CustomerListResponse>(`/api/customers${tail}`);
}

export async function getCustomer(slug: string): Promise<unknown> {
  return apiFetch(`/api/customers/${encodeURIComponent(slug)}`);
}

// ── Deals ─────────────────────────────────────────────────────────
export type DealListItem = {
  id: string;
  customer_name: string;
  supplier: string | null;
  status: string;
  created_at: string;
};

export type DealListResponse = {
  deals: DealListItem[];
  total?: number;
};

export async function getDeals(): Promise<DealListResponse> {
  return apiFetch<DealListResponse>(`/api/deals`);
}

export async function getDeal(id: string): Promise<unknown> {
  return apiFetch(`/api/deals/${encodeURIComponent(id)}`);
}

// ── Agents ────────────────────────────────────────────────────────
export type AgentRow = {
  agent_name: string;
  total_calls: number;
  compliant: number;
  non_compliant: number;
  recent_non_compliant_30d: number;
  open_directives: number;
  last_call_at: string | null;
  needs_escalation: boolean;
};

export async function getAgents(): Promise<{ agents: AgentRow[] }> {
  return apiFetch(`/api/agents`);
}

// ── Checkpoint guidelines (HITL Task 23) ─────────────────────────
// Renders inside the per-checkpoint card's "How to judge this" expander.
// Returns the script excerpt, strictness mode, and up to 5 past human
// reviewer decisions for the same checkpoint+supplier — used as anti-bias
// training context for the reviewer.
export type GuidelinesExample = {
  pattern: string;
  agent_verdict: string;
  human_verdict: string;
  lesson: string;
};

export type CheckpointGuidelinesResponse = {
  checkpoint_name: string;
  script_excerpt: string;
  strictness: string;
  examples: GuidelinesExample[];
};

export async function getCheckpointGuidelines(
  callId: string,
  checkpointName: string,
): Promise<CheckpointGuidelinesResponse> {
  return apiFetch<CheckpointGuidelinesResponse>(
    `/api/calls/${encodeURIComponent(callId)}/checkpoint-guidelines?checkpoint_name=${encodeURIComponent(checkpointName)}`,
  );
}

// ── Word edit (HITL) ──────────────────────────────────────────────
// Reviewer corrects a misheard word in the transcript. Backend patches
// `call.word_data`, persists a TranscriptEdit audit row, and (when a
// `checkpoint_id` is supplied) re-runs that single checkpoint against
// the corrected text. If the rerun flips the verdict, `verdict_changed`
// is true and `checkpoint` carries the updated row so the UI can
// optimistically swap it in without a full refetch.
export type WordEditResponse = {
  saved: boolean;
  edit_id: string;
  verdict_changed: boolean;
  new_verdict: string | null;
  checkpoint: unknown | null;
};
