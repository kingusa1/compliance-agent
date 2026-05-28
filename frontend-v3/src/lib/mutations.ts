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
/** FastAPI validation error item — shape returned in `detail` on 422. */
interface PydanticValidationError {
  type?: string;
  loc?: (string | number)[];
  msg?: string;
  input?: unknown;
}

/**
 * Format a FastAPI error body into a human-readable string for toast UI.
 *
 * FastAPI returns `detail` in three different shapes depending on the
 * error path:
 *   - string             — HTTPException(400, "message"): take verbatim
 *   - array of objects   — 422 ValidationError: list of {loc, msg, type}
 *                          (this is the path that produced "[object Object]"
 *                          when stringified naively — wave-40 root cause)
 *   - object             — domain shape (e.g. {METADATA_MISMATCH, manual, auto})
 *                          preserve full JSON so callers can parse it
 *
 * For the array shape we surface the FIRST validation error's
 * `<loc>: <msg>` so the reviewer immediately sees which field is wrong,
 * with a "(+N more)" suffix when there are additional errors. Test by
 * blanking a required field in manual upload mode.
 */
export function formatErrorDetail(err: unknown, fallback: string): string {
  if (err && typeof err === "object" && "detail" in err) {
    const detail = (err as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as PydanticValidationError;
      const path = Array.isArray(first?.loc)
        ? first.loc
            .filter((p) => p !== "body" && p !== "form")
            .join(".")
        : "";
      const msg = first?.msg || first?.type || "validation failed";
      const head = path ? `${path}: ${msg}` : msg;
      return detail.length > 1 ? `${head} (+${detail.length - 1} more)` : head;
    }
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      // Domain-shaped errors like METADATA_MISMATCH — keep the raw JSON
      // so admin.ts:uploadCall can pattern-match on it. Array case is
      // already handled above; empty arrays fall through to fallback.
      try {
        return JSON.stringify(detail);
      } catch {
        /* fall through */
      }
    }
  }
  return fallback;
}

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
    throw new Error(formatErrorDetail(err, `Upload failed: ${res.status}`));
  }
  return res.json() as Promise<TResp>;
}
