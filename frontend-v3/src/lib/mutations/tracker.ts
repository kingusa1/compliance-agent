/**
 * /tracker inline-edit mutation.
 *
 * PATCH /api/tracker/rows/{rejection_id} — accepts {field: value} body and
 * returns the updated row id + per-field source map. Uses the auth-aware
 * `apiFetch` (Supabase Bearer JWT, one-shot refresh-and-retry on 401).
 *
 * On success, invalidates the `["admin", "tracker", ...]` query key prefix
 * so the table refetches with the new value + flipped `source: "human"`
 * provenance badges.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { TrackerFieldSource } from "@/lib/queries/tracker";

export type EditTrackerVars = {
  rejectionId: string;
  fields: Record<string, string | number | null>;
};

export type EditTrackerResponse = {
  id: string;
  field_sources: Record<string, TrackerFieldSource>;
};

async function patchTrackerRow({ rejectionId, fields }: EditTrackerVars): Promise<EditTrackerResponse> {
  return apiFetch<EditTrackerResponse>(`/api/tracker/rows/${encodeURIComponent(rejectionId)}`, {
    method: "PATCH",
    body: JSON.stringify(fields),
    headers: { "Content-Type": "application/json" },
  });
}

// 2026-05-24 audit — every tracker mutation now invalidates the SAME set
// of dependent caches so a reviewer edit on /tracker is visible across
// every page that re-renders the same row. Previously only
// `["admin","tracker"]` dropped → /rejections, /dashboard, /calls/[id],
// /deals/[id], /agents/[name], /compliant, /non-compliant all showed
// stale data until the user hard-refreshed.
function _invalidateTrackerDependents(qc: ReturnType<typeof useQueryClient>): void {
  qc.invalidateQueries({ queryKey: ["admin", "tracker"] });
  qc.invalidateQueries({ queryKey: ["rejections"] });
  qc.invalidateQueries({ queryKey: ["admin", "dashboard"] });
  qc.invalidateQueries({ queryKey: ["calls"] });
  qc.invalidateQueries({ queryKey: ["call"] });          // /calls/[id] detail
  qc.invalidateQueries({ queryKey: ["deals"] });          // /deals list
  qc.invalidateQueries({ queryKey: ["deal"] });           // /deals/[id]
  qc.invalidateQueries({ queryKey: ["admin", "deal"] });  // composite-verdict
  qc.invalidateQueries({ queryKey: ["admin", "customers"] });
  qc.invalidateQueries({ queryKey: ["agents"] });         // leaderboard
  qc.invalidateQueries({ queryKey: ["agent"] });          // drilldown
  qc.invalidateQueries({ queryKey: ["compliant"] });
  qc.invalidateQueries({ queryKey: ["non-compliant"] });
}

export function useEditTrackerRow() {
  const qc = useQueryClient();
  return useMutation<EditTrackerResponse, Error, EditTrackerVars>({
    mutationFn: patchTrackerRow,
    onSuccess: () => _invalidateTrackerDependents(qc),
  });
}


// ── AI/HUMAN verdict gate mutations ──────────────────────────────────────
// confirm  → POST /api/rejections/{id}/confirm  → HUMAN_CONFIRMED
// override → POST /api/rejections/{id}/override → HUMAN_OVERRIDDEN
// Side-panel uses confirm when no field changed; override when reviewer
// edited any of {category, fix_required, fix_narrative, rejection_reason}.

export type OverridePayload = {
  category?: string | null;
  fix_required?: string | null;
  fix_narrative?: string | null;
  rejection_reason?: string | null;
  outcome_narrative?: string | null;
};

async function confirmRejection(rejectionId: string): Promise<void> {
  await apiFetch(`/api/rejections/${encodeURIComponent(rejectionId)}/confirm`, {
    method: "POST",
  });
}

async function overrideRejection(
  rejectionId: string,
  body: OverridePayload,
): Promise<void> {
  await apiFetch(`/api/rejections/${encodeURIComponent(rejectionId)}/override`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
}

export function useConfirmVerdict() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (rejectionId) => confirmRejection(rejectionId),
    onSuccess: () => _invalidateTrackerDependents(qc),
  });
}

export function useOverrideVerdict() {
  const qc = useQueryClient();
  return useMutation<void, Error, { rejectionId: string; body: OverridePayload }>({
    mutationFn: ({ rejectionId, body }) => overrideRejection(rejectionId, body),
    onSuccess: () => _invalidateTrackerDependents(qc),
  });
}

// ── Assignee mutation (2026-05-15) ──────────────────────────────────────
// POST /api/tracker/rows/{rejection_id}/assignee — body {assignee_id}.
// Null clears the assignment. Backend validates assignee_id is a known
// profile so a stale dropdown can't poison the row.

export type SetAssigneeVars = {
  rejectionId: string;
  assigneeId: string | null;
};

async function setAssignee({ rejectionId, assigneeId }: SetAssigneeVars): Promise<{
  id: string;
  fix_assignee_id: string | null;
}> {
  return apiFetch(`/api/tracker/rows/${encodeURIComponent(rejectionId)}/assignee`, {
    method: "POST",
    body: JSON.stringify({ assignee_id: assigneeId }),
    headers: { "Content-Type": "application/json" },
  });
}

export function useSetAssignee() {
  const qc = useQueryClient();
  return useMutation<{ id: string; fix_assignee_id: string | null }, Error, SetAssigneeVars>({
    mutationFn: setAssignee,
    onSuccess: () => _invalidateTrackerDependents(qc),
  });
}


// ── Call-level meta PATCH (2026-05-15) ──────────────────────────────────
// PATCH /api/calls/{call_id}/meta — used by the tracker side panel on
// AWAITING_REVIEW rows that don't yet have a Rejection. Accepts the same
// field keys as the rejection-row PATCH plus call-level keys (agent_name,
// customer_name, detected_supplier). Routing happens server-side.

export type EditCallMetaVars = {
  callId: string;
  fields: Record<string, string | number | null>;
};

async function patchCallMeta({ callId, fields }: EditCallMetaVars): Promise<{
  call_id: string;
  deal_id: string | null;
  deal_field_sources: Record<string, string> | null;
  applied_keys: string[];
}> {
  // Path is namespaced under /api/tracker/ on purpose — the earlier
  // /api/calls/{id}/meta endpoint 404'd in a previous deploy, causing
  // Chrome to cache a 600-second negative-CORS preflight result that
  // poisoned subsequent in-tab requests. The tracker-namespaced path
  // is fresh per-tab so reviewers don't hit the cached failure.
  return apiFetch(`/api/tracker/calls/${encodeURIComponent(callId)}/meta`, {
    method: "PATCH",
    body: JSON.stringify(fields),
    headers: { "Content-Type": "application/json" },
  });
}

export function useEditCallMeta() {
  const qc = useQueryClient();
  return useMutation<
    Awaited<ReturnType<typeof patchCallMeta>>,
    Error,
    EditCallMetaVars
  >({
    mutationFn: patchCallMeta,
    onSuccess: () => _invalidateTrackerDependents(qc),
  });
}
