/**
 * Scripts-lane mutation hooks.
 *
 *   useUploadScript()  — POST /api/scripts/upload (multipart "file")
 *                        Returns { filename, checkpoints, checkpoint_count }.
 *                        Backend parser only extracts checkpoints; the
 *                        user fills supplier_name / script_name / mode
 *                        in the preview before calling useSaveScript.
 *   useSaveScript()    — POST /api/scripts (creates a new Script row)
 *   useUpdateScript()  — PUT  /api/scripts/{id}
 *   useDeleteScript()  — DELETE /api/scripts/{id} (soft-delete, sets active=false)
 *
 * Each mutation invalidates ["scripts"] (and the per-id keys where
 * appropriate) so list + detail re-fetch automatically.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { postJson, putJson, deleteJson, uploadMultipart } from "@/lib/mutations";
import { scriptsKeys, type Checkpoint, type Script } from "@/lib/queries/scripts";

// ── Upload + parse ─────────────────────────────────────────────────
export type UploadScriptResponse = {
  filename: string;
  checkpoints: Checkpoint[];
  checkpoint_count: number;
};

export function useUploadScript() {
  return useMutation<UploadScriptResponse, Error, File>({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return uploadMultipart<UploadScriptResponse>("/api/scripts/upload", fd);
    },
  });
}

// ── Save (create) ──────────────────────────────────────────────────
export type SaveScriptPayload = {
  supplier_name: string;
  script_name: string;
  version?: string | null;
  mode?: string | null;
  checkpoints: Checkpoint[];
};

export function useSaveScript() {
  const qc = useQueryClient();
  return useMutation<Script, Error, SaveScriptPayload>({
    mutationFn: (body) => postJson<Script, SaveScriptPayload>("/api/scripts", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: scriptsKeys.list() });
    },
  });
}

// ── Update ─────────────────────────────────────────────────────────
export type UpdateScriptVars = {
  id: string;
  payload: SaveScriptPayload;
};

export function useUpdateScript() {
  const qc = useQueryClient();
  return useMutation<Script, Error, UpdateScriptVars>({
    mutationFn: ({ id, payload }) =>
      putJson<Script, SaveScriptPayload>(`/api/scripts/${encodeURIComponent(id)}`, payload),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: scriptsKeys.list() });
      qc.invalidateQueries({ queryKey: scriptsKeys.one(vars.id) });
      qc.invalidateQueries({ queryKey: scriptsKeys.markdown(vars.id) });
      qc.invalidateQueries({ queryKey: scriptsKeys.versions(vars.id) });
    },
  });
}

// ── Delete (soft) ──────────────────────────────────────────────────
export type DeleteScriptResponse = { status: string; [k: string]: unknown };

export function useDeleteScript() {
  const qc = useQueryClient();
  return useMutation<DeleteScriptResponse, Error, string>({
    mutationFn: (id) =>
      deleteJson<DeleteScriptResponse>(`/api/scripts/${encodeURIComponent(id)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: scriptsKeys.list() });
    },
  });
}
