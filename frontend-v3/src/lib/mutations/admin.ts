/**
 * Admin-lane mutation hooks.
 *
 *   useUploadCall()   — POST /api/calls/upload (multipart)
 *                      Surfaces METADATA_MISMATCH (HTTP 409) as a typed
 *                      error the form can recover from.
 *   useAddCustomer()  — POST /api/customers
 *   useCleanupCalls() — POST /api/calls/cleanup
 *
 * Each mutation invalidates the query keys it might affect; pages don't
 * need to invalidate manually unless they want to sequence side-effects.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { postJson, uploadMultipart } from "@/lib/mutations";
import { adminKeys } from "@/lib/queries/admin";

// ── Upload call ───────────────────────────────────────────────────
//
// METADATA_MISMATCH error — backend returns 409 with body
// `{ code: "METADATA_MISMATCH", manual: "...", auto: "...", field: "supplier" }`
// when the reviewer's manual supplier disagrees with the pipeline's auto
// detection. The form catches this and renders MetadataMismatchBanner so
// the reviewer can pick one and re-submit.
export class MetadataMismatchError extends Error {
  field: string;
  manual: string;
  auto: string;
  constructor(field: string, manual: string, auto: string) {
    super(`METADATA_MISMATCH (${field}): manual=${manual}, auto=${auto}`);
    this.name = "MetadataMismatchError";
    this.field = field;
    this.manual = manual;
    this.auto = auto;
  }
}

export type UploadCallResponse = {
  call_id?: string;
  id?: string;
  status?: string;
  [k: string]: unknown;
};

export type UploadCallVars = {
  formData: FormData;
};

export async function uploadCall(formData: FormData): Promise<UploadCallResponse> {
  try {
    return await uploadMultipart<UploadCallResponse>("/api/calls/upload", formData);
  } catch (err) {
    // uploadMultipart wraps non-2xx in a generic Error w/ JSON.detail.
    // Try to detect METADATA_MISMATCH from the message body.
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("METADATA_MISMATCH")) {
      // Body shape: "Upload failed: 409" or detail w/ JSON. Extract best-
      // effort. Tests stub this directly to assert banner renders.
      const m = msg.match(/manual=([^,\s]+).*auto=([^,\s)]+)/);
      const manual = m?.[1] ?? "unknown";
      const auto = m?.[2] ?? "unknown";
      throw new MetadataMismatchError("supplier", manual, auto);
    }
    throw err;
  }
}

export function useUploadCall() {
  const qc = useQueryClient();
  return useMutation<UploadCallResponse, Error, UploadCallVars>({
    mutationFn: ({ formData }) => uploadCall(formData),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "calls"] });
      qc.invalidateQueries({ queryKey: ["admin", "customers"] });
    },
  });
}

// ── Add customer ──────────────────────────────────────────────────
export type AddCustomerPayload = {
  legal_name: string;
  trading_as?: string | null;
  business_type?: string | null;
  address_postcode?: string | null;
  company_number?: string | null;
  charity_number?: string | null;
  vulnerable_customer_flag?: boolean;
  // Free-form extras the form sends through unchanged. Backend ignores
  // unknown keys today; spec migration will pick them up.
  [k: string]: unknown;
};

export type AddCustomerResponse = {
  customer: { id: string; slug: string; legal_name: string };
  slug: string;
};

export function useAddCustomer() {
  const qc = useQueryClient();
  return useMutation<AddCustomerResponse, Error, AddCustomerPayload>({
    mutationFn: (body) => postJson<AddCustomerResponse, AddCustomerPayload>("/api/customers", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "customers"] });
    },
  });
}

// ── Cleanup stuck calls ──────────────────────────────────────────
export type CleanupResponse = {
  cleaned?: number;
  [k: string]: unknown;
};

export function useCleanupCalls() {
  const qc = useQueryClient();
  return useMutation<CleanupResponse, Error, void>({
    mutationFn: () => postJson<CleanupResponse>("/api/calls/cleanup"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "calls"] });
    },
  });
}

// ── Edit call metadata (reviewer override of auto-detect) ─────────
//
// PATCH /api/calls/{id}/metadata. Reviewer fills/corrects fields the
// auto-detect pipeline missed; backend writes the overrides into Call,
// parent CustomerDeal, and parent Customer rows so /tracker reflects
// the new value next refresh.
export type EditCallMetadataPayload = {
  customer_name?: string;
  agent_name?: string;
  mpan_or_mprn?: string;
  expected_live_date?: string;
  deal_value_gbp?: number;
  supplier?: string;
  contract_length_months?: number;
  notes?: string;
};

export type EditCallMetadataResponse = {
  call: { id: string; customer_name: string | null; agent_name: string | null; deal_id: string | null };
  deal: { id: string; supplier: string | null; mpan_or_mprn: string | null; expected_live_date: string | null; deal_value_gbp: number | null } | null;
  customer: { id: string; legal_name: string | null } | null;
};

export function useEditCallMetadata(callId: string) {
  const qc = useQueryClient();
  return useMutation<EditCallMetadataResponse, Error, EditCallMetadataPayload>({
    mutationFn: (body) =>
      apiFetch<EditCallMetadataResponse>(`/api/calls/${encodeURIComponent(callId)}/metadata`, {
        method: "PATCH",
        body: JSON.stringify(body),
        headers: { "Content-Type": "application/json" },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "calls"] });
      qc.invalidateQueries({ queryKey: ["reviewer", "callDetail", callId] });
      qc.invalidateQueries({ queryKey: ["admin", "tracker"] });
    },
  });
}

// Helper so unit tests + form code can both build the FormData identically.
export function buildUploadFormData(input: {
  customer: { name: string; [k: string]: unknown };
  deal: { supplier: string; [k: string]: unknown };
  call: { call_type: string | null; audio_file: File; [k: string]: unknown };
  customer_slug?: string;
  supplier_override?: "manual" | "auto";
  dev_auto_detect?: boolean;
}): FormData {
  const fd = new FormData();
  fd.append("file", input.call.audio_file);
  if (input.call.call_type) {
    fd.append("call_type", input.call.call_type);
  }
  fd.append("customer_name", input.customer.name);
  if (input.customer_slug) fd.append("customer_slug", input.customer_slug);
  if (input.supplier_override) fd.append("supplier_override", input.supplier_override);
  // Stash the rest of the structured intake as a JSON `metadata` blob;
  // backend already accepts this string-encoded field.
  const metadata = {
    customer: input.customer,
    deal: input.deal,
    call: { ...input.call, audio_file: undefined }, // strip File from JSON
    dev_auto_detect: input.dev_auto_detect ?? false,
  };
  fd.append("metadata", JSON.stringify(metadata));
  return fd;
}
