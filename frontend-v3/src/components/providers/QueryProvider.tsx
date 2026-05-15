"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * TanStack Query provider. Defaults are tuned for a REAL-TIME compliance-
 * review console: the queue + tracker + rejections + call-detail pages
 * MUST never show stale state — what's in the DB right now is what
 * renders. Defaults below feed every useQuery unless the caller
 * overrides per-query.
 *
 * - `staleTime: 0`         — every fetch is treated as immediately
 *                             stale so window-focus + interval refetches
 *                             always go to network.
 * - `refetchOnWindowFocus: true` — clicking back into the tab refreshes.
 * - `refetchOnReconnect: true`   — Wi-Fi flicker no longer means stale.
 * - `refetchOnMount: "always"`   — every navigation hits the network.
 * - `refetchInterval: 5_000`     — global 5 s polling floor; pages that
 *                                   need tighter live updates (call detail
 *                                   while processing) override to 2 s.
 *                                   Heavy pages (dashboard intelligence
 *                                   aggregates) override upward.
 * - `refetchIntervalInBackground: false` — pause polling when tab is
 *                                   hidden to keep Railway invoice sane.
 */
export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 0,
            retry: 1,
            refetchOnWindowFocus: true,
            refetchOnReconnect: true,
            refetchOnMount: "always",
            refetchInterval: 5_000,
            refetchIntervalInBackground: false,
          },
          mutations: {
            retry: 0,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
