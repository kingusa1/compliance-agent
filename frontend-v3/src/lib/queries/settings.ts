/**
 * Settings TanStack Query keys + queryFns.
 *
 * Backs the /settings tabs (Model, Transcription, Density, Account).
 * The queryKey objects are tuples (NOT functions) so the components can
 * invalidate via `qc.invalidateQueries({ queryKey: settingsKeys.model })`
 * without parentheses.
 *
 * Backend shapes (verified 2026-05-01):
 *   GET /api/settings/model →
 *     {
 *       active_provider: "openrouter" | "anthropic" | "openai" | "gemini",
 *       providers: {
 *         [name]: { label, model, api_key_masked, api_key_set, no_key_required }
 *       }
 *     }
 *   PUT /api/settings/model body { active_provider, model? }
 *
 *   GET /api/settings/transcription →
 *     { providers: [{ id, label, role, agreement, enabled }] }
 *   PUT /api/settings/transcription body { providers: [...] }
 */
import { apiFetch } from "@/lib/api";

// ── Model settings ─────────────────────────────────────────────────
export type ModelProvider = {
  label: string;
  model: string;
  api_key_masked?: string;
  api_key_set?: boolean;
  no_key_required?: boolean;
};

export type ModelSettings = {
  active_provider: string;
  providers: Record<string, ModelProvider>;
};

export type ModelSettingsPatch = {
  active_provider: string;
  model?: string;
};

// ── Transcription settings ─────────────────────────────────────────
export type TranscriptionProvider = {
  id: string;
  label: string;
  role: "primary" | "alternate" | "fallback" | string;
  agreement?: number;
  enabled: boolean;
};

export type TranscriptionSettings = {
  providers: TranscriptionProvider[];
};

export const settingsKeys = {
  model: ["settings", "model"] as const,
  transcription: ["settings", "transcription"] as const,
};

// ── Fetchers ──────────────────────────────────────────────────────
export function fetchModelSettings(): Promise<ModelSettings> {
  return apiFetch<ModelSettings>("/api/settings/model");
}

export function fetchTranscriptionSettings(): Promise<TranscriptionSettings> {
  return apiFetch<TranscriptionSettings>("/api/settings/transcription");
}

export function updateModelSettings(body: ModelSettingsPatch): Promise<ModelSettings> {
  return apiFetch<ModelSettings>("/api/settings/model", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function updateTranscriptionSettings(
  body: TranscriptionSettings,
): Promise<TranscriptionSettings> {
  return apiFetch<TranscriptionSettings>("/api/settings/transcription", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

// ── Query option builders ─────────────────────────────────────────
export function getModelSettingsQuery() {
  return {
    queryKey: settingsKeys.model,
    queryFn: fetchModelSettings,
  };
}

export function getTranscriptionSettingsQuery() {
  return {
    queryKey: settingsKeys.transcription,
    queryFn: fetchTranscriptionSettings,
  };
}
