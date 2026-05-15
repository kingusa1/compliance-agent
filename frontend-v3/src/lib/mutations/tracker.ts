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

export function useEditTrackerRow() {
  const qc = useQueryClient();
  return useMutation<EditTrackerResponse, Error, EditTrackerVars>({
    mutationFn: patchTrackerRow,
    onSuccess: () => {
      // useTrackerRowsQuery uses ["admin", "tracker", filters] — prefix match.
      qc.invalidateQueries({ queryKey: ["admin", "tracker"] });
    },
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
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "tracker"] }),
  });
}

export function useOverrideVerdict() {
  const qc = useQueryClient();
  return useMutation<void, Error, { rejectionId: string; body: OverridePayload }>({
    mutationFn: ({ rejectionId, body }) => overrideRejection(rejectionId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "tracker"] }),
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
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "tracker"] }),
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
  return apiFetch(`/api/calls/${encodeURIComponent(callId)}/meta`, {
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
    onSuccess: () => {
      // Invalidate both the tracker (so the awaiting-review row refreshes)
      // and the underlying calls view, since the same edits surface on
      // /calls/[id] header chips.
      qc.invalidateQueries({ queryKey: ["admin", "tracker"] });
      qc.invalidateQueries({ queryKey: ["calls"] });
    },
  });
}
