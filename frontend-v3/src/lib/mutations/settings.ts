/**
 * Settings mutation hooks.
 *
 *   usePutModelSettings()        — PUT /api/settings/model
 *   usePutTranscriptionSettings() — PUT /api/settings/transcription
 *
 * Backend shapes confirmed 2026-05-01:
 *   model body: { active_provider: string, model?: string }
 *   transcription body: { providers: TranscriptionProvider[] }
 *
 * Both invalidate the matching queryKey + sonner success/error toast.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  settingsKeys,
  updateModelSettings,
  updateTranscriptionSettings,
  type ModelSettings,
  type ModelSettingsPatch,
  type TranscriptionSettings,
} from "@/lib/queries/settings";

export function usePutModelSettings() {
  const qc = useQueryClient();
  return useMutation<ModelSettings, Error, ModelSettingsPatch>({
    mutationFn: (body) => updateModelSettings(body),
    onSuccess: (saved) => {
      qc.setQueryData(settingsKeys.model, saved);
      qc.invalidateQueries({ queryKey: settingsKeys.model });
      toast.success("Model settings saved");
    },
    onError: (err) => {
      toast.error("Could not save model settings", {
        description: err instanceof Error ? err.message : "Unknown error",
      });
    },
  });
}

export function usePutTranscriptionSettings() {
  const qc = useQueryClient();
  return useMutation<TranscriptionSettings, Error, TranscriptionSettings>({
    mutationFn: (body) => updateTranscriptionSettings(body),
    onSuccess: (saved) => {
      qc.setQueryData(settingsKeys.transcription, saved);
      qc.invalidateQueries({ queryKey: settingsKeys.transcription });
      toast.success("Transcription settings saved");
    },
    onError: (err) => {
      toast.error("Could not save transcription settings", {
        description: err instanceof Error ? err.message : "Unknown error",
      });
    },
  });
}
