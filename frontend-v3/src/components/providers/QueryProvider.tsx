"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * TanStack Query provider.
 *
 * 2026-05-16 — REMOVED the global ``refetchInterval`` floor. Wholesale
 * polling re-renders the call-detail page every few seconds, which
 * re-mounts the <audio> element and resets playback to 0 — the bug
 * Mohamed reported tonight. We now refresh on window focus + reconnect
 * only; specific pages that genuinely need live append-on-upload
 * behaviour (queue) still override per-query. True real-time push
 * (SSE/WebSocket) is a follow-up — for now this restores stable
 * playback and avoids the visible "refresh flash" the user called
 * out.
 */
export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30 * 1000,
            retry: 1,
            // Click-back-to-tab refresh: cheap and feels live without
            // re-rendering while the user is actively interacting.
            refetchOnWindowFocus: true,
            refetchOnReconnect: true,
            refetchOnMount: false,
          },
          mutations: {
            retry: 0,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
