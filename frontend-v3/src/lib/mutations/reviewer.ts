/**
 * Reviewer-lane TanStack Mutation hooks.
 *
 * Every mutation follows the same shape:
 *   - call the wire helper from `lib/mutations.ts` (postJson/putJson)
 *   - on success, invalidate the relevant cache keys from
 *     `lib/queries/reviewer.ts`
 *   - on success/error, surface a sonner toast so the reviewer always
 *     gets explicit feedback (UX-D07 — sonner + banner pattern)
 *
 * Components opt in: each hook returns the standard `useMutation` result
 * (mutate, mutateAsync, isPending, ...) — pages choose whether to await.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { deleteJson, patchJson, postJson, putJson } from "@/lib/mutations";
import { ApiError, type WordEditResponse } from "@/lib/api";
import { reviewerKeys, type ChatMessage } from "@/lib/queries/reviewer";

// ── Helpers ───────────────────────────────────────────────────────

function _errMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    // Try to surface the backend's "detail" string if present.
    try {
      const parsed = JSON.parse(err.body) as { detail?: string };
      if (parsed.detail) return parsed.detail;
    } catch {
      /* body wasn't JSON */
    }
    return `${err.status} ${err.body || err.message}`;
  }
  if (err instanceof Error) return err.message;
  return fallback;
}

// ── Claim / release ───────────────────────────────────────────────

export type ClaimResponse = {
  /** Backend returns review_session_id (not session_id). Both aliases accepted. */
  review_session_id: string;
  /** Legacy alias kept for callers that already destructure session_id. */
  session_id?: string;
  expires_at?: string;
  status?: string;
};

// silent=true suppresses success/error toasts — used by the page-mount
// auto-claim path so navigating to a call detail page doesn't pop two
// toasts on every view. User-initiated claim/release sites omit it and
// get the standard toast UX.
export type ClaimArgs = { callId: string; silent?: boolean };
export type ReleaseArgs = { sessionId: string; silent?: boolean };

export function useClaimCall() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ callId }: ClaimArgs): Promise<ClaimResponse> =>
      postJson<ClaimResponse>(`/api/calls/${encodeURIComponent(callId)}/claim`),
    onSuccess: (_data, { callId, silent }) => {
      qc.invalidateQueries({ queryKey: ["queue"] });
      qc.invalidateQueries({ queryKey: reviewerKeys.callDetail(callId) });
      if (!silent) {
        toast.success("Call claimed", { description: "30-min review lock acquired." });
      }
    },
    onError: (err, { silent }) => {
      if (!silent) {
        toast.error("Couldn’t claim call", { description: _errMessage(err, "Try again.") });
      }
    },
  });
}

export function useReleaseCall() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId }: ReleaseArgs) =>
      postJson(`/api/review-sessions/${encodeURIComponent(sessionId)}/release`),
    onSuccess: (_data, { silent }) => {
      qc.invalidateQueries({ queryKey: ["queue"] });
      if (!silent) toast("Released review session");
    },
    onError: (err, { silent }) => {
      if (!silent) {
        toast.error("Couldn’t release session", { description: _errMessage(err, "Try again.") });
      }
    },
  });
}

// ── Checkpoint review (PUT, query params) ─────────────────────────

export type CheckpointReviewArgs = {
  callId: string;
  index: number; // cp_index from /script-checkpoints (back-compat fallback)
  /** 2026-05-27 — owner-reported "Pass button doesn't work for some
   *  checkpoints". `cpCards` reorders script-defined CPs vs verdicts so
   *  the position N at the UI no longer matches `call.checkpoint_results[N]`.
   *  Backend now resolves by NAME first when this field is present (route
   *  query param `?name=...`); the int `index` remains as a back-compat
   *  fallback. Always set this when caller knows the CP name (which is
   *  always — both the script def and the verdict carry `.name`). */
  name?: string;
  verdict: "pass" | "fail";
  notes?: string;
};

export function useReviewCheckpoint() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ callId, index, verdict, notes, name }: CheckpointReviewArgs) => {
      const qs = new URLSearchParams({ verdict });
      if (notes) qs.set("notes", notes);
      // 2026-05-27 code-reviewer MEDIUM — only forward name when it's
      // non-empty after trim. Whitespace would match any malformed row
      // whose .name is also blank, patching the wrong CP.
      const trimmedName = name?.trim();
      if (trimmedName) qs.set("name", trimmedName);
      return putJson(
        `/api/calls/${encodeURIComponent(callId)}/checkpoint/${index}/review?${qs.toString()}`,
      );
    },
    // 2026-05-27 — owner-reported "Pass / Override → Fail is so slow".
    // Optimistic update so the verdict chip flips IMMEDIATELY when the
    // reviewer clicks; the server confirmation arrives in background.
    // Backend `abstract_and_store_review` was made fire-and-forget the
    // same day (routes.py:review_checkpoint_verdict) so server response
    // is now sub-second, but optimistic update + sub-second server
    // together feel instant. Rollback on error.
    onMutate: async ({ callId, index, verdict, notes, name }) => {
      const detailKey = reviewerKeys.callDetail(callId);
      // Cancel any in-flight detail refetch so our optimistic patch
      // isn't immediately overwritten by stale server data.
      await qc.cancelQueries({ queryKey: detailKey });
      const prev = qc.getQueryData<{ checkpoint_results?: string | null } | undefined>(detailKey);
      // The checkpoint verdicts live in a JSON string on Call.checkpoint_results.
      // Parse, resolve target by NAME (preferred, order-independent) or
      // fall back to int index, patch, re-serialize.
      if (prev && typeof prev === "object" && "checkpoint_results" in prev && prev.checkpoint_results) {
        try {
          const parsed = JSON.parse(prev.checkpoint_results as string);
          if (Array.isArray(parsed)) {
            let resolvedIndex = -1;
            // 2026-05-27 code-reviewer HIGH — checkpoint_results MAY contain
            // duplicate `.name` rows (the JSON column has no uniqueness
            // constraint, and the frontend cpCards union can produce
            // duplicates when a script-defined CP also has an unmatched
            // verdict). When the caller's `index` lands on a row whose
            // name matches the supplied `name`, prefer THAT row over the
            // first-match — it disambiguates the duplicate by position.
            // Only fall back to the broad first-match when no
            // position-aligned name match exists.
            const target = name?.trim().toLowerCase();
            if (target) {
              // 1. Position-anchored: caller's index also has the right name.
              if (
                index >= 0 && index < parsed.length &&
                typeof parsed[index] === "object" && parsed[index] !== null &&
                typeof (parsed[index] as { name?: unknown }).name === "string" &&
                ((parsed[index] as { name: string }).name).trim().toLowerCase() === target
              ) {
                resolvedIndex = index;
              } else {
                // 2. First-match-by-name fallback.
                resolvedIndex = parsed.findIndex(
                  (r: unknown) =>
                    typeof r === "object" && r !== null &&
                    typeof (r as { name?: unknown }).name === "string" &&
                    ((r as { name: string }).name).trim().toLowerCase() === target,
                );
              }
            }
            if (resolvedIndex < 0 && index >= 0 && index < parsed.length) {
              resolvedIndex = index;
            }
            if (resolvedIndex >= 0 && resolvedIndex < parsed.length) {
              const next = parsed.slice();
              next[resolvedIndex] = {
                ...next[resolvedIndex],
                reviewer_verdict: verdict,
                reviewer_notes: notes ?? "",
                needs_review: false,
              };
              qc.setQueryData(detailKey, {
                ...prev,
                checkpoint_results: JSON.stringify(next),
              });
            }
          }
        } catch {
          // Malformed JSON — leave the cache untouched; the server's
          // response will repaint via onSuccess.
        }
      }
      return { prev };
    },
    onError: (err, _args, ctx) => {
      // Rollback the optimistic patch so the UI matches server reality.
      if (ctx?.prev !== undefined) {
        qc.setQueryData(reviewerKeys.callDetail(_args.callId), ctx.prev);
      }
      toast.error("Couldn't save checkpoint", { description: _errMessage(err, "Try again.") });
    },
    onSuccess: (_data, { callId }) => {
      // Confirm the optimistic patch by refetching detail + dependent
      // queries. The cache patch already drove the UI; these refetches
      // sync any derived state (score pill, segment cards) without
      // making the user wait.
      qc.invalidateQueries({ queryKey: reviewerKeys.callDetail(callId) });
      qc.invalidateQueries({ queryKey: reviewerKeys.callCheckpoints(callId) });
      // SegmentCards on the call-detail page reads from a separate key
      // (`["call", id, "segments"]` — see calls/[id]/page.tsx). Without
      // this invalidation, overriding a single CP updated the top-bar
      // score (63/88 → 62/88) but left the per-segment cards still
      // showing 63/88 and the filter pills stale. Audit 2026-05-16 P1 #8.
      qc.invalidateQueries({ queryKey: ["call", callId, "segments"] });
      // Toasts on every checkpoint click would be noisy — stay silent on success.
    },
  });
}

// ── Retry checkpoint (POST) ───────────────────────────────────────
// Re-runs analysis for a single checkpoint against the call's current
// transcript. Used by the per-card retry button when reviewers want to
// re-trigger the LLM after a script tweak or transcript edit.

export type RetryCheckpointArgs = {
  callId: string;
  index: number;
};

export function useRetryCheckpoint() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ callId, index }: RetryCheckpointArgs) =>
      postJson(`/api/calls/${encodeURIComponent(callId)}/checkpoint/${index}/retry`),
    onSuccess: (_data, { callId }) => {
      qc.invalidateQueries({ queryKey: reviewerKeys.callDetail(callId) });
      qc.invalidateQueries({ queryKey: reviewerKeys.callCheckpoints(callId) });
      toast.success("Checkpoint re-analyzed");
    },
    onError: (err) => {
      toast.error("Couldn’t re-analyze checkpoint", { description: _errMessage(err, "Try again.") });
    },
  });
}

// ── Word edit (POST) ──────────────────────────────────────────────
// Reviewer corrects a misheard transcript word. Used by TranscriptPlayer's
// alt-click / double-click editor. The optional `checkpoint_id` triggers a
// single-checkpoint re-run on the backend; if the rerun flips the verdict
// the response carries the updated checkpoint row.
//
// 409 surfaces as ApiError; the caller (TranscriptPlayer) inspects
// err.status to invoke its `onConflict` callback for parent refetch.

export type WordEditArgs = {
  callId: string;
  word_index: number;
  old_text: string;
  new_text: string;
  checkpoint_id?: string | null;
  revision?: number | null;
};

export function useEditWord(callId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ word_index, old_text, new_text, checkpoint_id, revision }: Omit<WordEditArgs, "callId">) =>
      postJson<WordEditResponse>(
        `/api/calls/${encodeURIComponent(callId)}/edit-word`,
        { word_index, old_text, new_text, checkpoint_id: checkpoint_id ?? null },
        { revision },
      ),
    onSuccess: (data) => {
      // Invalidate detail + words so karaoke re-syncs with the patched word
      // stream. Skip toast on every keystroke commit — the inline tooltip
      // already shows the edit succeeded.
      qc.invalidateQueries({ queryKey: reviewerKeys.callWords(callId) });
      qc.invalidateQueries({ queryKey: reviewerKeys.callDetail(callId) });
      if (data?.verdict_changed) {
        qc.invalidateQueries({ queryKey: reviewerKeys.callCheckpoints(callId) });
      }
    },
    onError: (err) => {
      // 409 (concurrent write) is handled by the caller via onConflict —
      // don't double up with a toast.
      if (err instanceof ApiError && err.status === 409) return;
      toast.error("Couldn’t save word edit", { description: _errMessage(err, "Try again.") });
    },
  });
}

// ── Verdict ───────────────────────────────────────────────────────

export type VerdictAction = "PASS" | "REVIEW" | "COACHING" | "FAIL" | "BLOCK";

export type VerdictArgs = {
  callId: string;
  checkpoint_id?: string; // backend's VerdictPayload uses checkpoint_id; the
                          // overall-call verdict is allowed to be empty
                          // string in current backend tests.
  action: VerdictAction;
  reason: string;
  sendEmail?: boolean;
};

export type VerdictResponse = {
  ok?: boolean;
  history_id?: string;
  send_email?: boolean;
  // W2 (v3-watt-coverage): backend returns this UUID when the verdict was
  // FAIL or REVIEW and a rejection was auto-created (Stage 4 of Watt's
  // 41-step flow). Null/missing on PASS/COACHING/BLOCK.
  auto_rejection_id?: string | null;
};

export function useSubmitVerdict() {
  const qc = useQueryClient();
  const router = useRouter();
  return useMutation({
    mutationFn: ({ callId, checkpoint_id, action, reason }: VerdictArgs) =>
      postJson<VerdictResponse>(`/api/calls/${encodeURIComponent(callId)}/verdict`, {
        // backend VerdictPayload: { checkpoint_id, verdict, reasoning }
        checkpoint_id: checkpoint_id ?? "",
        verdict: action,
        reasoning: reason,
      }),
    onSuccess: (data, { callId }) => {
      // 2026-05-27 — split invalidations by visibility. The 3 detail-page
      // queries refetch immediately (the user is staring at them). The 5
      // off-page queries (queue/findings/tracker/admin-calls/rejections)
      // mark stale via `refetchType: "none"` so they refresh lazily on
      // navigation. Owner-reported "buttons take time" was partly this
      // chain triggering 8 concurrent refetches that contended with the
      // visible page's queries.
      qc.invalidateQueries({ queryKey: reviewerKeys.callDetail(callId) });
      qc.invalidateQueries({ queryKey: reviewerKeys.callCheckpoints(callId) });
      qc.invalidateQueries({ queryKey: ["call", callId, "segments"] });
      // Off-page — mark stale, do not refetch immediately.
      qc.invalidateQueries({ queryKey: ["queue"], refetchType: "none" });
      qc.invalidateQueries({ queryKey: reviewerKeys.findings(), refetchType: "none" });
      qc.invalidateQueries({ queryKey: ["admin", "tracker"], refetchType: "none" });
      qc.invalidateQueries({ queryKey: ["admin-calls"], refetchType: "none" });
      // 2026-05-16 audit Bug 7 fix: invalidate ["rejections"] UNCONDITIONALLY.
      // Previously gated on data?.auto_rejection_id being truthy, but a FAIL
      // verdict with no failing CallCheckpoint rows (e.g. reviewer override
      // on a partial-status call) returns auto_rejection_id=null and the
      // /rejections page stays stale even though the verdict shipped. Also
      // covers the case where confirmed_by was updated on a pre-existing
      // rejection row (which doesn't return an id but DOES change list data).
      qc.invalidateQueries({ queryKey: ["rejections"], refetchType: "none" });
      // W2: surface the new rejection in the toast only when one was created.
      if (data?.auto_rejection_id) {
        toast.success("Verdict committed + rejection created", {
          description: `Tracked at /rejections/${data.auto_rejection_id.slice(0, 8)}…`,
          action: {
            label: "Open",
            // router.push keeps the SPA shell; window.location.href forced
            // a full reload that wiped the verdict-submit's query cache and
            // flashed the login gate. Audit 2026-05-16 P1-11.
            onClick: () => router.push(`/rejections?focus=${encodeURIComponent(data.auto_rejection_id!)}`),
          },
        });
      } else {
        toast.success("Verdict submitted", { description: "Queue updated." });
      }
    },
    onError: (err) => {
      toast.error("Couldn’t submit verdict", { description: _errMessage(err, "Try again.") });
    },
  });
}

// ── Customer confirmation email (W3.B v3-watt-coverage) ──────────

export type CustomerEmailArgs = {
  callId: string;
  /** Optional override; backend defaults to the customer record's email. */
  to?: string;
  cc?: string[];
};

export type CustomerEmailResponse = {
  sent: boolean;
  message_id: string;
  preview_html: string;
  to: string | null;
  cc: string[];
  /**
   * Names of template fields the backend couldn't fill (e.g. unit_rate
   * missing because W3.A pricing extraction hasn't landed yet). The
   * preview HTML renders these as visible `{{ MISSING: <key> }}` tokens
   * so the reviewer can spot the gap before sending.
   */
  missing_fields: string[];
};

/**
 * Send the post-call customer confirmation email (compliance manual §8).
 * Backed by ``POST /api/calls/{id}/customer-email``. Distinct from
 * This hook targets the *customer*, not the internal sales agent —
 * the two endpoints log separate SEND events for clarity.
 */
export function useCustomerEmail() {
  return useMutation({
    mutationFn: ({ callId, to, cc }: CustomerEmailArgs) =>
      postJson<CustomerEmailResponse>(
        `/api/calls/${encodeURIComponent(callId)}/customer-email`,
        { to, cc: cc ?? [] },
      ),
    onSuccess: (data) => {
      if (data.sent) {
        toast.success("Customer confirmation sent", {
          description: `To ${data.to ?? "(no recipient)"} — ref ${data.message_id}.`,
        });
      } else {
        // No recipient supplied + none on file → preview-only outcome.
        toast.warning("Preview only — no recipient", {
          description: "Add a customer email address before sending.",
        });
      }
      if (data.missing_fields.length > 0) {
        toast.warning(
          `${data.missing_fields.length} field${data.missing_fields.length === 1 ? "" : "s"} missing in template`,
          {
            description: data.missing_fields.slice(0, 4).join(", "),
          },
        );
      }
    },
    onError: (err) => {
      toast.error("Couldn’t send customer email", { description: _errMessage(err, "Try again.") });
    },
  });
}

// ── Flags ─────────────────────────────────────────────────────────

export type AddFlagArgs = {
  callId: string;
  rule_id: string;
  severity: string;
  reason: string;
  word_start: number;
  word_end: number;
  evidence?: string;
  risk_tag?: string;
};

export function useAddFlag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ callId, ...body }: AddFlagArgs) =>
      postJson(`/api/calls/${encodeURIComponent(callId)}/flags`, body),
    onSuccess: (_data, { callId }) => {
      qc.invalidateQueries({ queryKey: reviewerKeys.callFlags(callId) });
      qc.invalidateQueries({ queryKey: reviewerKeys.findings() });
      toast.success("Flag added");
    },
    onError: (err) => {
      toast.error("Couldn’t add flag", { description: _errMessage(err, "Try again.") });
    },
  });
}

// ── Directives ────────────────────────────────────────────────────

export type AddDirectiveArgs = {
  callId: string;
  text: string;
  agent_name?: string;
};

export function useAddDirective() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ callId, ...body }: AddDirectiveArgs) =>
      postJson(`/api/calls/${encodeURIComponent(callId)}/directives`, body),
    onSuccess: (_data, { callId }) => {
      qc.invalidateQueries({ queryKey: reviewerKeys.callDirectives(callId) });
      toast.success("Directive added");
    },
    onError: (err) => {
      toast.error("Couldn’t add directive", { description: _errMessage(err, "Try again.") });
    },
  });
}

// ── Risk tags (W1.5 — v3-watt-coverage) ───────────────────────────

/**
 * Closed enum the backend accepts on PATCH /api/calls/{id}/risk-tags.
 * Order matches the chip layout on VerdictTab top.
 */
export const RISK_TAGS = [
  "Ombudsman",
  "Mis-selling",
  "Complaint",
  "Cancellation",
  "Vulnerable",
] as const;
export type RiskTag = (typeof RISK_TAGS)[number];

export type SetRiskTagsArgs = {
  callId: string;
  tags: RiskTag[];
};

export function useSetCallRiskTags() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ callId, tags }: SetRiskTagsArgs) =>
      patchJson<{ call_id: string; risk_tags: string[] }>(
        `/api/calls/${encodeURIComponent(callId)}/risk-tags`,
        { tags },
      ),
    onSuccess: (_data, { callId }) => {
      qc.invalidateQueries({ queryKey: reviewerKeys.callDetail(callId) });
    },
    onError: (err) => {
      toast.error("Couldn’t save risk tags", { description: _errMessage(err, "Try again.") });
    },
  });
}

// ── Agent chat ────────────────────────────────────────────────────

export type AgentChatArgs = {
  call_id?: string;
  messages: ChatMessage[];
};

export type AgentChatResponse = {
  message: ChatMessage;
};

export function useAgentChat() {
  return useMutation({
    mutationFn: (args: AgentChatArgs) => postJson<AgentChatResponse>(`/api/agent/chat`, args),
    onError: (err) => {
      toast.error("Chat failed", { description: _errMessage(err, "Try again.") });
    },
  });
}

// ── Saved views ───────────────────────────────────────────────────

export type SaveViewArgs = {
  name: string;
  endpoint: string;
  filters: Record<string, unknown>;
};

export function useSaveView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: SaveViewArgs) => postJson(`/api/saved-views`, args),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: reviewerKeys.savedViews() });
      toast.success("Saved view created");
    },
    onError: (err) => {
      toast.error("Couldn’t save view", { description: _errMessage(err, "Try again.") });
    },
  });
}

export type UpdateViewArgs = {
  id: string;
  name?: string;
  filters?: Record<string, unknown>;
};

export function useUpdateView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: UpdateViewArgs) =>
      patchJson(`/api/saved-views/${encodeURIComponent(id)}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: reviewerKeys.savedViews() });
      toast.success("Saved view updated");
    },
    onError: (err) => {
      toast.error("Couldn’t update view", { description: _errMessage(err, "Try again.") });
    },
  });
}

export function useDeleteView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteJson(`/api/saved-views/${encodeURIComponent(id)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: reviewerKeys.savedViews() });
      toast.success("Saved view deleted");
    },
    onError: (err) => {
      toast.error("Couldn’t delete view", { description: _errMessage(err, "Try again.") });
    },
  });
}
