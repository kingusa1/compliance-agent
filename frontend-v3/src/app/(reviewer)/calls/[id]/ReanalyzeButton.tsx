'use client';

import { useState } from 'react';
import { toast } from 'sonner';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? '';

interface Props {
  callId: string;
}

export function ReanalyzeButton({ callId }: Props) {
  const [pending, setPending] = useState(false);

  async function handleClick() {
    setPending(true);
    try {
      const res = await fetch(`${API_BASE}/api/calls/${callId}/reanalyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        toast.error(`Reanalyze failed: ${body.detail ?? res.statusText}`);
        return;
      }
      const data = (await res.json()) as { run_id: string; call_id: string };
      toast.success(`Reanalyze enqueued (run ${data.run_id.slice(0, 8)}). Refresh to see verdict.`);
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
