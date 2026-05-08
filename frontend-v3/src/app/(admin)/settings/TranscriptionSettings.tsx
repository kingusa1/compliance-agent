"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Switch as SwitchPrimitive } from "@base-ui/react/switch";

import { Skeleton } from "@/components/ui/skeleton";
import {
  getTranscriptionSettingsQuery,
  type TranscriptionProvider,
  type TranscriptionSettings as TranscriptionSettingsType,
} from "@/lib/queries/settings";
import { usePutTranscriptionSettings } from "@/lib/mutations/settings";

/**
 * TranscriptionSettings — list of ASR providers with role chip + agreement
 * percentage + per-provider enable toggle. Backend shape today:
 *   { providers: [{ id, label, role, agreement, enabled }] }
 *
 * Save → PUT /api/settings/transcription with the same shape.
 */
export function TranscriptionSettings() {
  const query = useQuery(getTranscriptionSettingsQuery());

  if (query.isLoading) return <TranscriptionSkeleton />;
  if (query.isError || !query.data) {
    return (
      <div className="rounded-md border border-red-500/30 bg-red-500/5 p-4 text-[13px] text-red-400">
        Couldn’t load transcription settings.
      </div>
    );
  }

  return <TranscriptionForm initial={query.data} />;
}

function TranscriptionForm({ initial }: { initial: TranscriptionSettingsType }) {
  const [providers, setProviders] = useState<TranscriptionProvider[]>(initial.providers);
  useEffect(() => setProviders(initial.providers), [initial.providers]);

  const save = usePutTranscriptionSettings();

  const dirty = providers.some(
    (p, i) => p.enabled !== initial.providers[i]?.enabled,
  );

  function toggle(id: string, next: boolean) {
    setProviders((arr) =>
      arr.map((p) => (p.id === id ? { ...p, enabled: next } : p)),
    );
  }

  function reset() {
    setProviders(initial.providers);
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    save.mutate({ providers });
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-5"
    >
      <div>
        <h3 className="mb-1 text-[14px] font-semibold text-[var(--text-primary)]">
          ASR providers
        </h3>
        <p className="text-[12px] text-[var(--text-muted)]">
          Primary handles every call. Alternates run when enabled and feed the consensus engine.
        </p>
      </div>

      <ul className="divide-y divide-[var(--border-subtle)]">
        {providers.map((p) => {
          const roleColor =
            p.role === "primary"
              ? "var(--emerald-pass)"
              : p.role === "alternate"
                ? "var(--amber-review)"
                : "var(--text-muted)";
          return (
            <li
              key={p.id}
              className="flex items-center justify-between gap-3 py-3"
              data-testid={`asr-provider-${p.id}`}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-medium text-[var(--text-primary)]">
                    {p.label}
                  </span>
                  <span
                    className="rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide"
                    style={{
                      color: roleColor,
                      background: `color-mix(in oklab, ${roleColor} 12%, transparent)`,
                    }}
                  >
                    {p.role}
                  </span>
                </div>
                <div className="mt-0.5 font-mono text-[11px] text-[var(--text-muted)]">
                  {p.agreement != null ? `${p.agreement.toFixed(1)}% agreement` : "—"}
                </div>
              </div>
              <SwitchPrimitive.Root
                checked={p.enabled}
                onCheckedChange={(v) => toggle(p.id, v)}
                aria-label={`Enable ${p.label}`}
                disabled={p.role === "primary"}
                className="relative inline-flex h-6 w-10 shrink-0 cursor-pointer items-center rounded-full border border-[var(--border-strong)] bg-[var(--bg-elev3)] transition-colors data-[checked]:border-emerald-500/50 data-[checked]:bg-emerald-500/30 outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:opacity-50"
              >
                <SwitchPrimitive.Thumb className="block size-5 translate-x-0.5 rounded-full bg-[var(--text-primary)] transition-transform data-[checked]:translate-x-[18px]" />
              </SwitchPrimitive.Root>
            </li>
          );
        })}
      </ul>

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
          data-testid="transcription-save"
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

function TranscriptionSkeleton() {
  return (
    <div className="space-y-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-5">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  );
}
