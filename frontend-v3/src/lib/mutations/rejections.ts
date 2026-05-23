/**
 * W2 (v3-watt-coverage): rejection mutation hooks.
 *
 * Pattern matches lib/mutations/reviewer.ts: each hook calls a wire helper
 * from lib/mutations.ts, invalidates the right rejectionsKeys entries on
 * success, surfaces a sonner toast for the user.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import { deleteJson, patchJson, postJson } from "@/lib/mutations";
import { rejectionsKeys } from "@/lib/queries/rejections";
import type {
  Rejection,
  RejectionCreateValues,
  RejectionStatus,
} from "@/lib/schemas/rejections";

function _errMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    try {
      const parsed = JSON.parse(err.body) as { detail?: string };
      if (parsed.detail) return parsed.detail;
    } catch {
      /* not JSON */
    }
    return `${err.status} ${err.body || err.message}`;
  }
  if (err instanceof Error) return err.message;
  return fallback;
}

function _invalidate(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: rejectionsKeys.all() });
}

// ── Create ─────────────────────────────────────────────────────────

export function useCreateRejection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RejectionCreateValues) =>
      postJson<Rejection, RejectionCreateValues>(`/api/rejections`, body),
    onSuccess: () => {
      _invalidate(qc);
      toast.success("Rejection created");
    },
    onError: (err) => {
      toast.error("Couldn’t create rejection", {
        description: _errMessage(err, "Try again."),
      });
    },
  });
}

// ── Patch ──────────────────────────────────────────────────────────

export type PatchRejectionArgs = {
  id: string;
  body: Partial<{
    customer_slug: string | null;
    supplier: string | null;
    sales_agent: string | null;
    category: string;
    rejection_reason: string;
    fix_required: string | null;
    fix_assignee_id: string | null;
    status: string;
    outcome: string | null;
    outcome_narrative: string | null;
    /** W4.6 — one of DEAD_REASONS keys; only meaningful when status=DEAD. */
    dead_reason: string | null;
  }>;
};

export function usePatchRejection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: PatchRejectionArgs) =>
      patchJson<Rejection>(`/api/rejections/${encodeURIComponent(id)}`, body),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: rejectionsKeys.detail(id) });
      qc.invalidateQueries({ queryKey: rejectionsKeys.auditLog(id) });
      qc.invalidateQueries({ queryKey: ["rejections", "list"] });
    },
    onError: (err) => {
      toast.error("Couldn’t save rejection", {
        description: _errMessage(err, "Try again."),
      });
    },
  });
}

// ── Transition ─────────────────────────────────────────────────────

export type TransitionArgs = {
  id: string;
  to_status: RejectionStatus;
  notes?: string;
};

export function useTransitionRejection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, to_status, notes }: TransitionArgs) =>
      postJson<Rejection>(`/api/rejections/${encodeURIComponent(id)}/transition`, {
        to_status,
        notes,
      }),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: rejectionsKeys.detail(id) });
      qc.invalidateQueries({ queryKey: rejectionsKeys.auditLog(id) });
      qc.invalidateQueries({ queryKey: ["rejections", "list"] });
      toast.success("Status updated");
    },
    onError: (err) => {
      toast.error("Couldn’t update status", {
        description: _errMessage(err, "Try again."),
      });
    },
  });
}

// ── Delete ─────────────────────────────────────────────────────────

export function useDeleteRejection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      deleteJson(`/api/rejections/${encodeURIComponent(id)}`),
    onSuccess: () => {
      _invalidate(qc);
      toast.success("Rejection deleted");
    },
    onError: (err) => {
      toast.error("Couldn’t delete rejection", {
        description: _errMessage(err, "Try again."),
      });
    },
  });
}

// ── 2026-05-24 — bulk transition ──────────────────────────────────────

export type BulkTransitionArgs = {
  rejection_ids: string[];
  to_status: RejectionStatus;
  notes?: string;
};

export type BulkTransitionResponse = {
  updated: number;
  skipped_already_in_state: number;
  not_found: number;
  ids_updated: string[];
  ids_skipped: string[];
  ids_not_found: string[];
  to_status: string;
};

/**
 * Powers the /rejections bulk-action bar and the per-group "Mark all
 * fixed" button on RejectionGroupCard. Idempotent server-side: a retry
 * with the same payload reports skipped_already_in_state instead of
 * double-writing audit rows. Safe to fire on user impatience.
 */
export function useBulkTransitionRejections() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: BulkTransitionArgs) =>
      postJson<BulkTransitionResponse, BulkTransitionArgs>(
        `/api/rejections/bulk-transition`,
        args,
      ),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: rejectionsKeys.all() });
      const moved = data.updated;
      const skipped = data.skipped_already_in_state;
      const missing = data.not_found;
      const target = data.to_status.replace(/_/g, " ").toLowerCase();
      if (moved > 0) {
        const tail =
          skipped > 0 || missing > 0
            ? ` (${[
                skipped > 0 ? `${skipped} already ${target}` : null,
                missing > 0 ? `${missing} not found` : null,
              ]
                .filter(Boolean)
                .join(", ")})`
            : "";
        toast.success(
          `Marked ${moved} rejection${moved === 1 ? "" : "s"} → ${target}${tail}`,
        );
      } else if (skipped > 0) {
        toast.info(`Already ${target} — no change`);
      } else if (missing > 0) {
        toast.error(`Nothing matched — ${missing} id${missing === 1 ? "" : "s"} not found`);
      }
    },
    onError: (err) => {
      toast.error("Couldn’t bulk-update rejections", {
        description: _errMessage(err, "Try again."),
      });
    },
  });
}

// ── W4.5 — portal-batches submit ──────────────────────────────────────

export type SubmitPortalBatchArgs = {
  supplier: string;
  rejection_ids: string[];
};

export type SubmitPortalBatchResponse = {
  submitted: number;
  supplier: string;
  rejection_ids: string[];
};

export function useSubmitPortalBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: SubmitPortalBatchArgs) =>
      postJson<SubmitPortalBatchResponse, SubmitPortalBatchArgs>(
        `/api/portal-batches/submit`,
        args,
      ),
    onSuccess: (data) => {
      // Invalidate both the portal-batches query and the rejections list
      // (each row's status flipped to SUBMITTED_TO_PORTAL).
      qc.invalidateQueries({ queryKey: ["portal-batches"] });
      qc.invalidateQueries({ queryKey: rejectionsKeys.all() });
      toast.success(
        `Submitted ${data.submitted} rejection${data.submitted === 1 ? "" : "s"} to ${data.supplier}`,
      );
    },
    onError: (err) => {
      toast.error("Couldn’t submit batch", {
        description: _errMessage(err, "Try again."),
      });
    },
  });
}
