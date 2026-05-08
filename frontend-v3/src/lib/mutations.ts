/**
 * Generic POST/PUT/PATCH/DELETE wrappers for TanStack Query mutations.
 *
 * Lane-specific mutations (claim, verdict, upload, retraining toggle)
 * live in `src/lib/mutations/<lane>.ts`. This file holds the wire-level
 * helpers so each lane writes thin domain wrappers around them.
 *
 * Invalidation rules: each lane's mutation is responsible for calling
 * `queryClient.invalidateQueries` with the right key. Common pairs:
 *   - claim → invalidate ["queue", *] + ["call", id]
 *   - verdict → invalidate ["call", id] + ["queue", *] + ["findings", *]
 *   - upload → invalidate ["calls", *] + ["customers", *]
 *   - retraining toggle → invalidate ["agents"] + ["agent", name]
 */
import { apiFetch } from "@/lib/api";

export type MutationOpts = {
  /** Optional `If-Match: <revision>` header for optimistic locking. */
  revision?: number | null;
};

function _ifMatchHeader(revision?: number | null): Record<string, string> {
  return revision != null ? { "If-Match": String(revision) } : {};
}

export async function postJson<TResp = unknown, TBody = unknown>(
  path: string,
  body?: TBody,
  opts: MutationOpts = {},
): Promise<TResp> {
  return apiFetch<TResp>(path, {
    method: "POST",
    body: body !== undefined ? JSON.stringify(body) : undefined,
    headers: _ifMatchHeader(opts.revision),
  });
}

export async function putJson<TResp = unknown, TBody = unknown>(
  path: string,
  body?: TBody,
  opts: MutationOpts = {},
): Promise<TResp> {
  return apiFetch<TResp>(path, {
    method: "PUT",
    body: body !== undefined ? JSON.stringify(body) : undefined,
    headers: _ifMatchHeader(opts.revision),
  });
}

export async function patchJson<TResp = unknown, TBody = unknown>(
  path: string,
  body?: TBody,
  opts: MutationOpts = {},
): Promise<TResp> {
  return apiFetch<TResp>(path, {
    method: "PATCH",
    body: body !== undefined ? JSON.stringify(body) : undefined,
    headers: _ifMatchHeader(opts.revision),
  });
}

export async function deleteJson<TResp = unknown>(
  path: string,
  opts: MutationOpts = {},
): Promise<TResp> {
  return apiFetch<TResp>(path, {
    method: "DELETE",
    headers: _ifMatchHeader(opts.revision),
  });
}

/**
 * Multipart upload helper — bypasses apiFetch's JSON content-type and
 * still injects the Supabase Bearer JWT. Used by /calls/upload (L7
 * IntakeForm). Returns the parsed JSON body or throws on non-2xx.
 */
export async function uploadMultipart<TResp = unknown>(
  path: string,
  formData: FormData,
): Promise<TResp> {
  const { getAccessToken } = await import("@/lib/supabase");
  const token = await getAccessToken();
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || ""}${path}`, {
    method: "POST",
    body: formData,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail || `Upload failed: ${res.status}`);
  }
  return res.json() as Promise<TResp>;
}
