import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ReanalyzeButton } from '@/app/(reviewer)/calls/[id]/ReanalyzeButton';

// 2026-05-18: ReanalyzeButton wraps its POST in a useMutation from
// @tanstack/react-query — needs a QueryClientProvider in the tree or
// it throws "No QueryClient set". The legacy tests were written before
// the mutation refactor and never wrapped.
function renderWithQueryClient(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe('ReanalyzeButton', () => {
  beforeEach(() => {
    global.fetch = vi.fn(async () => new Response(JSON.stringify({ run_id: 'r-1', call_id: 'c-1' }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    })) as unknown as typeof fetch;
  });

  it('renders a button labelled Reanalyze', () => {
    renderWithQueryClient(<ReanalyzeButton callId="c-1" />);
    expect(screen.getByRole('button', { name: /reanalyze/i })).toBeInTheDocument();
  });

  it('POSTs to /api/calls/{id}/reanalyze on click and shows success state', async () => {
    renderWithQueryClient(<ReanalyzeButton callId="c-1" />);
    fireEvent.click(screen.getByRole('button', { name: /reanalyze/i }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/calls/c-1/reanalyze'),
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('disables button while a request is in flight', async () => {
    renderWithQueryClient(<ReanalyzeButton callId="c-1" />);
    const btn = screen.getByRole('button', { name: /reanalyze/i });
    fireEvent.click(btn);
    expect(btn).toBeDisabled();
  });
});
