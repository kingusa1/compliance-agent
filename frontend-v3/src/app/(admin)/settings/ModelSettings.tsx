"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Check } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { getModelSettingsQuery, type ModelSettings as ModelSettingsType } from "@/lib/queries/settings";
import { usePutModelSettings } from "@/lib/mutations/settings";

/**
 * ModelSettings — provider switcher.
 *
 * Backend now returns `{ active_provider, providers: { [name]: { label, model, … } } }`.
 * The form lets the admin pick:
 *   1. an active provider (radio list of `Object.keys(providers)`)
 *   2. the model for the chosen provider (single string per provider)
 *
 * Save → PUT /api/settings/model body `{ active_provider, model }`.
 *
 * NB: temperature + max_tokens were removed — backend doesn't expose
 * them. Keeping the UI honest is more useful than fake controls.
 */
export function ModelSettings() {
  const query = useQuery(getModelSettingsQuery());

  if (query.isLoading) return <ModelSettingsSkeleton />;
  if (query.isError || !query.data) {
    return (
      <div className="rounded-md border border-red-500/30 bg-red-500/5 p-4 text-[13px] text-red-400">
        Couldn’t load model settings.
      </div>
    );
  }

  return <ModelSettingsForm initial={query.data} />;
}

function ModelSettingsForm({ initial }: { initial: ModelSettingsType }) {
  const providerKeys = Object.keys(initial.providers);
  const [activeProvider, setActiveProvider] = useState<string>(initial.active_provider);
  const [model, setModel] = useState<string>(
    initial.providers[initial.active_provider]?.model ?? "",
  );

  // When the user picks a different provider, default the model to that
  // provider's stored model.
  useEffect(() => {
    const m = initial.providers[activeProvider]?.model ?? "";
    setModel(m);
  }, [activeProvider, initial]);

  const save = usePutModelSettings();
  const dirty =
    activeProvider !== initial.active_provider ||
    model !== (initial.providers[initial.active_provider]?.model ?? "");

  function reset() {
    setActiveProvider(initial.active_provider);
    setModel(initial.providers[initial.active_provider]?.model ?? "");
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    save.mutate({ active_provider: activeProvider, model });
  }

  // Backend gives one model per provider; if it ever returns models[]
  // we'll surface them as a Select. For now show the single model in a
  // text input so reviewers can edit it (e.g. switch between
  // "claude-sonnet-4-6" / "claude-haiku-4-5" inside Anthropic).
  const activeMeta = initial.providers[activeProvider];

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-6 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-5"
    >
      {/* Provider radio list */}
      <div className="space-y-3">
        <div>
          <label className="text-[13px] font-medium text-[var(--text-primary)]">
            Active provider
          </label>
          <p className="mt-1 text-[12px] text-[var(--text-muted)]">
            Which LLM backend Compliance Agent calls when scoring checkpoints.
          </p>
        </div>
        <div role="radiogroup" aria-label="LLM provider" className="grid gap-2 sm:grid-cols-2">
          {providerKeys.map((key) => {
            const p = initial.providers[key];
            const chosen = activeProvider === key;
            const ready = p.no_key_required || p.api_key_set;
            return (
              <button
                key={key}
                type="button"
                role="radio"
                aria-checked={chosen}
                onClick={() => setActiveProvider(key)}
                data-testid={`provider-radio-${key}`}
                className="flex items-start gap-3 rounded-md border px-3 py-2.5 text-left transition-colors"
                style={{
                  background: chosen ? "var(--bg-elev2)" : "transparent",
                  borderColor: chosen
                    ? "var(--emerald-pass)"
                    : "var(--border-subtle)",
                }}
              >
                <span
                  className="mt-0.5 inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border"
                  style={{
                    borderColor: chosen
                      ? "var(--emerald-pass)"
                      : "var(--border-strong)",
                    background: chosen ? "var(--emerald-pass)" : "transparent",
                  }}
                >
                  {chosen ? <Check className="h-2.5 w-2.5 text-[#04201a]" /> : null}
                </span>
                <span className="flex-1">
                  <span className="block text-[13px] font-medium text-[var(--text-primary)]">
                    {p.label}
                  </span>
                  <span className="mt-0.5 block font-mono text-[11px] text-[var(--text-muted)]">
                    {p.model}
                  </span>
                  <span
                    className="mt-1 inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wide"
                    style={{
                      color: ready ? "var(--emerald-pass)" : "var(--amber-review)",
                    }}
                  >
                    <span
                      className="inline-block h-1.5 w-1.5 rounded-full"
                      style={{
                        background: ready
                          ? "var(--emerald-pass)"
                          : "var(--amber-review)",
                      }}
                    />
                    {ready ? "key set" : "no key"}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Model dropdown — backend returns a single model per provider, so
          this is effectively a text override. We surface it as a Select
          when the provider exposes a known list, else as a free-text
          input. The shape today is single string. */}
      <div className="space-y-2">
        <label className="text-[13px] font-medium text-[var(--text-primary)]">
          Model
        </label>
        <p className="text-[12px] text-[var(--text-muted)]">
          The exact model identifier sent to{" "}
          <span className="font-mono text-[var(--text-primary)]">{activeMeta?.label}</span>.
        </p>
        <Select value={model} onValueChange={(v) => setModel(v ?? "")}>
          <SelectTrigger className="w-full" data-testid="model-select">
            <SelectValue placeholder={activeMeta?.model ?? "—"} />
          </SelectTrigger>
          <SelectContent>
            {/* Backend exposes a single canonical model per provider; show
                it here so the reviewer can confirm. If they want something
                else they can switch provider — text input override is out
                of scope for now. */}
            {activeMeta?.model ? (
              <SelectItem value={activeMeta.model}>{activeMeta.model}</SelectItem>
            ) : (
              <SelectItem value="" disabled>
                no model configured
              </SelectItem>
            )}
          </SelectContent>
        </Select>
      </div>

      <div className="flex items-center justify-end gap-3 pt-1">
        {!dirty && (
          <span className="text-[12px] text-[var(--text-dim)]">No changes</span>
        )}
        <button
          type="button"
          onClick={reset}
          disabled={!dirty || save.isPending}
          className="h-8 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev2)] px-3 text-[13px] text-[var(--text-primary)] disabled:opacity-50"
        >
          Reset
        </button>
        <button
          type="submit"
          disabled={!dirty || save.isPending}
          data-testid="model-save"
          className="h-8 rounded-md border px-3 text-[13px] font-medium disabled:opacity-50"
          style={{
            background: "var(--emerald)",
            color: "#04201a",
            borderColor: "var(--emerald)",
            boxShadow: "var(--shadow-sm)",
          }}
        >
          {save.isPending ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

function ModelSettingsSkeleton() {
  return (
    <div className="space-y-5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-5">
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-9 w-48" />
    </div>
  );
}

// Avoid unused-import error on ChevronDown for trees that strip dead deps.
void ChevronDown;
