'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import { useQueryClient } from '@tanstack/react-query';

import { postJson } from '@/lib/mutations';
import { reviewerKeys } from '@/lib/queries/reviewer';

interface Props {
  callId: string;
  /**
   * True when the call has a stored transcript + word_data — the
   * backend's `reanalyze` route refuses to fire without these and
   * returns 422 `"Call lacks transcript / word_data — cannot
   * reanalyze."`. When false we disable the button + show a tooltip
   * explaining the user should use Retry instead (which re-transcribes
   * from scratch). Without this gate, a reviewer who opens a stuck/
   * failed call sees Reanalyze enabled, clicks it, and gets the 422
   * toast — pure UX regression (owner-reported 2026-05-28).
   */
  hasTranscript?: boolean;
}

export function ReanalyzeButton({ callId, hasTranscript = true }: Props) {
  const [pending, setPending] = useState(false);
  const qc = useQueryClient();

  async function handleClick() {
    setPending(true);
    try {
      // 2026-05-16 audit fix — was using NEXT_PUBLIC_API_BASE (undefined on
      // Vercel; only NEXT_PUBLIC_API_URL is set), then hitting Vercel
      // instead of Railway. Route via `postJson` so we share the
      // typed apiFetch base + auth header pipeline with every other call.
      const data = await postJson<{ run_id?: string; call_id?: string }>(
        `/api/calls/${encodeURIComponent(callId)}/reanalyze`,
      );
      const runTag = data?.run_id ? data.run_id.slice(0, 8) : 'unknown';
      toast.success(`Reanalyze enqueued (run ${runTag})`);
      // Invalidate so the user sees the new verdict without a manual refresh.
      // 2026-05-28 — prefix-invalidate every per-call slice so the
      // call-detail bundle key + segments key + checkpoints key all
      // refetch together.
      qc.invalidateQueries({ queryKey: ['call', callId] });
    } catch (err) {
      toast.error(
        `Reanalyze failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setPending(false);
    }
  }

  const disabled = pending || !hasTranscript;
  const tooltip = !hasTranscript
    ? 'No transcript yet — use Retry to re-transcribe and reanalyze from scratch'
    : 'Re-derive verdict from stored transcript (no re-transcription)';

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      title={tooltip}
      className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-2.5 py-1 text-[11px] hover:bg-[var(--bg-elev2)] disabled:opacity-60 disabled:cursor-not-allowed"
    >
      {pending ? 'Reanalyzing…' : '↻ Reanalyze'}
    </button>
  );
}
