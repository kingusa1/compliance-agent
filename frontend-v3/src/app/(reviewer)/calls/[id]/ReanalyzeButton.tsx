'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import { useQueryClient } from '@tanstack/react-query';

import { postJson } from '@/lib/mutations';
import { reviewerKeys } from '@/lib/queries/reviewer';

interface Props {
  callId: string;
}

export function ReanalyzeButton({ callId }: Props) {
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
      qc.invalidateQueries({ queryKey: reviewerKeys.callDetail(callId) });
      qc.invalidateQueries({ queryKey: reviewerKeys.callCheckpoints(callId) });
      qc.invalidateQueries({ queryKey: ['call', callId, 'segments'] });
    } catch (err) {
      toast.error(
        `Reanalyze failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={pending}
      title="Re-derive verdict from stored transcript (no re-transcription)"
      className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-2.5 py-1 text-[11px] hover:bg-[var(--bg-elev2)] disabled:opacity-60 disabled:cursor-not-allowed"
    >
      {pending ? 'Reanalyzing…' : '↻ Reanalyze'}
    </button>
  );
}
