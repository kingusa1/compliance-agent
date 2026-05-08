/**
 * Scripts TanStack Query keys + queryFns.
 *
 * Backs /scripts list + /scripts/[id] detail. The list endpoint is
 * shared with IntakeForm; keeping a separate `scriptsKeys` namespace
 * because the page-detail keys (markdown, versions) don't apply there.
 *
 * Backend response shapes (probed against FastAPI :8001):
 *   GET /api/scripts
 *     → { scripts: [{ id, supplier_name, script_name, version, mode,
 *                     checkpoints (JSON STRING), active, created_at,
 *                     updated_at }], total }
 *   GET /api/scripts/{id}
 *     → same fields as the list item (single object, not wrapped)
 *   GET /api/scripts/{id}/markdown
 *     → text/markdown plain text body (NOT json) — we expose it via a
 *       small fetch helper that bypasses apiFetch.
 *   GET /api/scripts/{id}/versions
 *     → { versions: [{ id, script_id, version_number, checkpoints_snapshot,
 *                      mode_snapshot, created_at }], total }
 *   POST /api/scripts/upload (multipart "file")
 *     → { filename, checkpoints, checkpoint_count }
 *       NOTE: parser does NOT extract supplier_name/script_name/mode —
 *       the user fills those in the preview before saving.
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { getAccessToken } from "@/lib/supabase";

/** Strictness levels supported by the backend script schema. */
export type CheckpointStrictness =
  | "mandatory"
  | "customer_yes"
  | "meaning_for_meaning";

/** Single checkpoint shape (matches backend ScriptCheckpoint pydantic model). */
export type Checkpoint = {
  section?: number | null;
  name: string;
  required: string;
  key_phrases: string[];
  customer_response_required?: boolean;
  strictness: CheckpointStrictness;
};

export type Script = {
  id: string;
  supplier_name?: string | null;
  script_name?: string | null;
  /** v2 fallback. Some legacy callers reference `name`. */
  name?: string | null;
  version?: string | number | null;
  mode?: string | null;
  /** Backend persists checkpoints as a JSON-encoded string. */
  checkpoints?: string | null;
  active?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  [k: string]: unknown;
};

export type ScriptsResponse = {
  scripts: Script[];
  total?: number;
};

export type ScriptVersion = {
  id?: string;
  script_id?: string;
  version_number?: number;
  /** Snapshot of `checkpoints` at the point of update (JSON string). */
  checkpoints_snapshot?: string | null;
  mode_snapshot?: string | null;
  created_at?: string | null;
  /** Legacy alias used by some renderers. */
  version?: string | number;
  [k: string]: unknown;
};

export const scriptsKeys = {
  list: () => ["scripts"] as const,
  one: (id: string) => ["scripts", id] as const,
  markdown: (id: string) => ["scripts", id, "markdown"] as const,
  versions: (id: string) => ["scripts", id, "versions"] as const,
};

export function fetchScripts(): Promise<ScriptsResponse> {
  return apiFetch<ScriptsResponse>(`/api/scripts`);
}

export function fetchScript(id: string): Promise<Script> {
  return apiFetch<Script>(`/api/scripts/${encodeURIComponent(id)}`);
}

/**
 * /markdown returns text/plain — apiFetch.json() would throw. Use a
 * minimal raw fetch here that still injects the Supabase Bearer token.
 */
export async function fetchScriptMarkdown(id: string): Promise<{ markdown: string }> {
  const token = await getAccessToken();
  const res = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL || ""}/api/scripts/${encodeURIComponent(id)}/markdown`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  if (!res.ok) throw new Error(`Markdown fetch failed: ${res.status}`);
  return { markdown: await res.text() };
}

export function fetchScriptVersions(id: string): Promise<{ versions: ScriptVersion[]; total?: number }> {
  return apiFetch<{ versions: ScriptVersion[]; total?: number }>(
    `/api/scripts/${encodeURIComponent(id)}/versions`,
  );
}

export function useScriptsListQuery() {
  return useQuery({
    queryKey: scriptsKeys.list(),
    queryFn: () => fetchScripts(),
    staleTime: 60_000,
  });
}

export function useScriptQuery(id: string) {
  return useQuery({
    queryKey: scriptsKeys.one(id),
    queryFn: () => fetchScript(id),
    enabled: !!id,
  });
}

export function useScriptMarkdownQuery(id: string) {
  return useQuery({
    queryKey: scriptsKeys.markdown(id),
    queryFn: () => fetchScriptMarkdown(id),
    enabled: !!id,
  });
}

export function useScriptVersionsQuery(id: string) {
  return useQuery({
    queryKey: scriptsKeys.versions(id),
    queryFn: () => fetchScriptVersions(id),
    enabled: !!id,
  });
}

/**
 * Best-effort parser for the JSON-encoded `checkpoints` field on a Script.
 * Returns [] on any failure so callers don't have to wrap each access.
 */
export function parseCheckpoints(raw: unknown): Checkpoint[] {
  if (!raw) return [];
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (!Array.isArray(parsed)) return [];
    return parsed.map((c: Record<string, unknown>) => ({
      section: typeof c.section === "number" ? c.section : null,
      name: String(c.name ?? ""),
      required: String(c.required ?? ""),
      key_phrases: Array.isArray(c.key_phrases) ? c.key_phrases.map(String) : [],
      customer_response_required: Boolean(c.customer_response_required),
      strictness: (typeof c.strictness === "string"
        ? (c.strictness as CheckpointStrictness)
        : "meaning_for_meaning") as CheckpointStrictness,
    }));
  } catch {
    return [];
  }
}
